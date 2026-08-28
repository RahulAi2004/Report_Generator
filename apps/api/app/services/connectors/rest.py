"""
A base for JSON APIs authenticated with a token.

Most APIs worth connecting are the same shape: a base URL, a header carrying a
credential, JSON in, JSON out, and pagination done one of three or four ways.
Writing that out again per provider is how the fifth connector ends up with
subtly different error handling from the first -- and error handling is most of
the value here, because the person configuring a connector cannot see the
request that failed.

So the differences live in the subclass and nothing else does: which header,
which endpoints, how the provider paginates.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

import httpx

from app.services.connectors.base import ConnectorError, DatasetKind, Page, flatten

logger = logging.getLogger(__name__)

#: Long enough for a slow report endpoint, short enough that a hung provider
#: fails while somebody is still watching the screen.
DEFAULT_TIMEOUT = 45.0


class RestConnector:
    """Shared HTTP, error translation and pagination for token-auth JSON APIs."""

    provider = "rest"
    base_url = ""
    #: Datasets the subclass offers.
    datasets_offered: tuple[DatasetKind, ...] = ()

    def __init__(self, token: str, timeout: float = DEFAULT_TIMEOUT, **_: Any):
        if not token or not token.strip():
            raise ConnectorError("No API token was provided.")
        self._token = token.strip()
        self._timeout = timeout

    # -- to implement -------------------------------------------------------
    def auth_headers(self) -> dict[str, str]:
        raise NotImplementedError

    def datasets(self) -> tuple[DatasetKind, ...]:
        return self.datasets_offered

    # -- HTTP ---------------------------------------------------------------
    def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        try:
            response = httpx.get(
                url,
                params=params or {},
                headers={"Accept": "application/json", **self.auth_headers()},
                timeout=self._timeout,
                follow_redirects=True,
            )
        except httpx.TimeoutException as error:
            raise ConnectorError(
                f"{self.label()} did not answer within {int(self._timeout)} seconds.",
                retryable=True,
            ) from error
        except httpx.HTTPError as error:
            raise ConnectorError(
                f"Could not reach {self.label()}: {self._redact(str(error))}",
                retryable=True,
            ) from error

        if response.status_code >= 400:
            raise self._explain(response)

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as error:
            raise ConnectorError(
                f"{self.label()} returned something that was not JSON."
            ) from error

    def label(self) -> str:
        return self.provider.replace("_", " ").title()

    #: Below this, a "credential" is too short to redact usefully: replacing a
    #: two-character string everywhere destroys the message and hides nothing a
    #: reader could have used anyway.
    MIN_REDACTABLE = 8

    def _redact(self, text: str) -> str:
        """
        Keep the credential out of anything a person will read.

        Providers echo tokens in error bodies and httpx puts full URLs in its
        exceptions, so this runs over every message rather than only the ones
        known to contain one.
        """
        if not text or len(self._token) < self.MIN_REDACTABLE:
            return text
        return text.replace(self._token, "[the token]")

    def _explain(self, response: httpx.Response) -> ConnectorError:
        """
        Turn a status code into a sentence that says what to do.

        The generic mapping; a provider overrides where it has something more
        specific to say.
        """
        detail = self._detail(response)
        status = response.status_code

        if status in (401, 403):
            return ConnectorError(
                f"{self.label()} rejected this token. Check it is current and has "
                f"access to this data. {detail}".strip()
            )
        if status == 404:
            return ConnectorError(
                f"{self.label()} has nothing at that address. This usually means the "
                f"API version or endpoint has moved. {detail}".strip()
            )
        if status == 429:
            return ConnectorError(
                f"{self.label()} is rate-limiting this token. The next scheduled "
                "refresh will pick up where this one stopped.",
                retryable=True,
            )
        if status >= 500:
            return ConnectorError(
                f"{self.label()} is having trouble at their end. This will be retried.",
                retryable=True,
            )
        return ConnectorError(f"{self.label()} refused the request. {detail}".strip())

    def _detail(self, response: httpx.Response) -> str:
        """The provider's own words, if it gave any that are worth repeating."""
        try:
            body = response.json()
        except ValueError:
            return self._redact(response.text[:200])

        if isinstance(body, dict):
            for key in ("detail", "message", "error", "error_description", "Message"):
                value = body.get(key)
                if isinstance(value, str) and value.strip():
                    return self._redact(value.strip()[:220])
                if isinstance(value, dict):
                    inner = value.get("message") or value.get("detail")
                    if isinstance(inner, str):
                        return self._redact(inner.strip()[:220])
            # Field-level validation errors, which are the useful kind.
            if body:
                return self._redact(str(body)[:220])
        elif isinstance(body, list) and body:
            return self._redact(str(body[0])[:220])
        return ""

    # -- pagination ---------------------------------------------------------
    def _rows(self, body: Any, key: str | None = None) -> list[dict]:
        """
        The rows out of a response, whatever the provider wrapped them in.

        Some return a bare list, some wrap it in `results`, `data` or `items`.
        Guessing wrong yields zero rows and no error, so the shapes are handled
        explicitly rather than assumed.
        """
        if isinstance(body, list):
            return [flatten(item) for item in body if isinstance(item, dict)]
        if isinstance(body, dict):
            if key and isinstance(body.get(key), list):
                return [flatten(item) for item in body[key] if isinstance(item, dict)]
            for candidate in ("results", "data", "items", "records"):
                if isinstance(body.get(candidate), list):
                    return [
                        flatten(item) for item in body[candidate] if isinstance(item, dict)
                    ]
            # A single object is one row, not no rows.
            return [flatten(body)]
        return []


def bounded(rows: Iterable[dict], limit: int) -> list[dict]:
    """Take at most `limit` rows, so a runaway provider cannot fill the disk."""
    out: list[dict] = []
    for row in rows:
        out.append(row)
        if len(out) >= limit:
            break
    return out

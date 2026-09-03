"""
DIGI / RIIN connector.

Built without documentation, from a base URL, a secret key and the words "auth:
header". That is a common way to be handed an integration, and guessing at it
produces a connector that fails with "unauthorized" whether the header name is
wrong, the path is wrong, or the key is wrong -- three different problems with
one useless message.

So this one finds out instead. Discovery walks a small set of header forms and
candidate paths, distinguishing "wrong credential" (401) from "wrong address"
(404), and reports which combination answered. The endpoints it finds become
the resources a dataset can be built from, so what gets synced is whatever this
API actually serves rather than what someone assumed it would.
"""

from __future__ import annotations

import logging
from datetime import date

import httpx

from app.services.connectors.base import (
    ConnectorError,
    DatasetKind,
    Discovery,
    Page,
    Resource,
)
from app.services.connectors.rest import RestConnector

logger = logging.getLogger(__name__)

PAGE_SIZE = 250

#: The ways an API is commonly told to accept a key in a header, in the order
#: they are tried. Bearer first because it is much the most common.
HEADER_FORMS: tuple[tuple[str, str, str], ...] = (
    ("Authorization", "Bearer {key}", "Authorization: Bearer"),
    ("X-API-Key", "{key}", "X-API-Key"),
    ("Auth", "{key}", "Auth"),
    ("apikey", "{key}", "apikey"),
    ("X-Auth-Token", "{key}", "X-Auth-Token"),
    ("Authorization", "{key}", "Authorization (bare)"),
    ("X-Secret-Key", "{key}", "X-Secret-Key"),
)

#: Paths worth asking for on an apparel supplier's API. Being wrong about these
#: costs one 404 each, and being right saves reading documentation nobody sent.
CANDIDATE_PATHS: tuple[str, ...] = (
    "/api/products", "/api/orders", "/api/inventory", "/api/customers",
    "/api/styles", "/api/categories", "/api/invoices", "/api/shipments",
    "/products", "/orders", "/inventory", "/styles", "/categories",
    "/api/v1/products", "/api/v1/orders", "/api/v1/inventory",
)

DATASETS: tuple[DatasetKind, ...] = (
    DatasetKind(
        key="records",
        label="Records from an endpoint",
        description=(
            "Whatever one of this API's endpoints returns, as a table. The "
            "endpoint is chosen from the ones discovery found."
        ),
        # The resource *is* the endpoint, which is what makes this work for an
        # API whose shape nobody has written down.
        resource_kind="endpoint",
        time_series=True,
    ),
)


class RiinConnector(RestConnector):
    provider = "riin"
    base_url = "https://tshirt.riin.com"
    datasets_offered = DATASETS

    def __init__(self, token: str, header_form: str = "", base_url: str = "", **kwargs):
        super().__init__(token, **kwargs)
        #: Which header form is known to work, once discovery has found one.
        #: Stored so every later request uses it rather than searching again.
        self._header_form = header_form or ""
        if base_url:
            self.base_url = base_url.rstrip("/")

    def label(self) -> str:
        return "DIGI / RIIN"

    # -- auth ---------------------------------------------------------------
    def auth_headers(self) -> dict[str, str]:
        name, template, _ = self._form(self._header_form)
        return {name: template.format(key=self._token)}

    @staticmethod
    def _form(label: str) -> tuple[str, str, str]:
        for form in HEADER_FORMS:
            if form[2] == label:
                return form
        return HEADER_FORMS[0]

    def _redact(self, text: str) -> str:
        """Whatever header form is in use, the key is inside it."""
        cleaned = super()._redact(text)
        if len(self._token) >= self.MIN_REDACTABLE:
            cleaned = cleaned.replace(f"Bearer {self._token}", "[the key]")
        return cleaned

    # -- discovery ----------------------------------------------------------
    def discover(self) -> Discovery:
        """
        Work out how this API wants to be spoken to, then what it serves.

        Two questions in order, because they have different answers: which
        header form authenticates, and which paths exist. Asking them together
        is how "unauthorized" and "not found" get confused for each other.
        """
        found = Discovery()
        working_form = self._header_form or self._find_header_form()

        if working_form is None:
            raise ConnectorError(
                "None of the usual header forms were accepted. This API wants the "
                "key somewhere else -- ask whoever issued it for the exact header "
                "name, or for a documentation link, and it can be added."
            )

        self._header_form = working_form
        found.account_id = "riin"
        found.account_name = f"DIGI / RIIN ({self.base_url})"

        endpoints = self._find_endpoints()
        found.resources = [
            Resource(
                id=path,
                kind="endpoint",
                name=path,
                detail={"rows_in_sample": count, "header_form": working_form},
            )
            for path, count in endpoints
        ]

        if endpoints:
            found.detail = (
                f"Authenticated with {working_form}. "
                f"{len(endpoints)} endpoint{'' if len(endpoints) == 1 else 's'} "
                "answered and can be synced."
            )
        else:
            found.detail = (
                f"Authenticated with {working_form}, but none of the paths this "
                "connector knows about exist on this API. The key is right and the "
                "addresses are not -- a documentation link would settle it."
            )
        return found

    def _find_header_form(self) -> str | None:
        """
        Which header this API accepts.

        A 404 counts as authenticated: the server understood who we are and
        merely has nothing at that address. Treating it as a failed credential
        is what makes people regenerate keys that were never the problem.
        """
        probe = CANDIDATE_PATHS[0]
        for name, template, label in HEADER_FORMS:
            try:
                httpx_response = httpx.get(
                    f"{self.base_url}{probe}",
                    headers={
                        "Accept": "application/json",
                        name: template.format(key=self._token),
                    },
                    timeout=self._timeout,
                    follow_redirects=True,
                )
            except httpx.HTTPError:
                continue

            if httpx_response.status_code in (401, 403):
                continue  # this header form was not understood
            if httpx_response.status_code < 500:
                return label
        return None

    def _find_endpoints(self) -> list[tuple[str, int]]:
        """Which of the candidate paths return something."""
        found: list[tuple[str, int]] = []
        for path in CANDIDATE_PATHS:
            try:
                rows = self._rows(self._request(path, {"limit": 1}))
            except ConnectorError:
                continue
            except Exception:  # noqa: BLE001 -- one bad path must not stop the walk
                logger.debug("Probe failed for %s", path, exc_info=True)
                continue
            found.append((path, len(rows)))
        return found

    # -- fetching -----------------------------------------------------------
    def fetch(
        self,
        dataset: str,
        resource_id: str,
        since: date | None = None,
        until: date | None = None,
        cursor: str | None = None,
    ) -> Page:
        if dataset != "records":
            raise ConnectorError(f"DIGI / RIIN connector has no dataset called '{dataset}'.")
        if not resource_id.startswith("/"):
            raise ConnectorError(
                f"'{resource_id}' is not an endpoint path. It should start with a slash."
            )

        # Offset paging, which is the common default. If this API pages some
        # other way the first sync returns one page and stops, which is visible
        # in the row count rather than silently wrong.
        offset = int(cursor) if cursor and cursor.isdigit() else 0
        params: dict[str, object] = {"limit": PAGE_SIZE, "offset": offset}
        if since:
            params["startDate"] = since.isoformat()
        if until:
            params["endDate"] = until.isoformat()

        rows = self._rows(self._request(resource_id, params))
        next_offset = offset + len(rows) if len(rows) >= PAGE_SIZE else None
        return Page(rows=rows, cursor=str(next_offset) if next_offset else None)

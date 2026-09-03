"""
DIGI / RIIN supplier connector.

Written against the working client in BlankTex rather than against guesses. The
first attempt probed for GET endpoints and a bearer token and found neither,
because this API is nothing like that shape: every call is a POST carrying a
JSON body, authenticated by two headers -- the key itself, and an MD5 of the
body and the key together.

That signature is why guessing could never have worked, and it is worth stating
plainly: the credential is not a bearer token, and a request with the right key
but an unsigned body is refused exactly like one with no key at all.

The other thing this connector does deliberately is refuse to grow. The same
API places, updates and closes real orders with a supplier. A reporting tool has
no business holding those, so only the `query` endpoints exist here -- not as a
convention, but as the only paths this file contains.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from typing import Any

import httpx

from app.services.connectors.base import (
    ConnectorError,
    DatasetKind,
    Discovery,
    Page,
    Resource,
    flatten,
)
from app.services.connectors.rest import RestConnector

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://tshirt.riin.com"
INTERFACE = "/trade/api/interface"

#: The supplier's catalogue endpoints page with these; 1000 is what the existing
#: client asks for and what this API is known to serve without complaint.
PAGE_SIZE = 1000

#: Read-only by construction. The supplier's API also has placeOrder, updateOrder
#: and closeOrder, which act on real orders with a real factory. They are absent
#: here rather than merely unused: a reporting tool that *could* place an order
#: is one bad code path away from placing one.
READ_ENDPOINTS: dict[str, str] = {
    "styles": f"{INTERFACE}/queryStyle",
    "colors": f"{INTERFACE}/queryColor",
    "sizes": f"{INTERFACE}/querySize",
}

#: The supplier's order status codes, as their own client maps them. Carried so
#: a report can group by something a person recognises rather than by 1, 5, 12.
ORDER_STATUSES: dict[int, str] = {
    1: "Store Audit", 2: "Pending Push", 3: "Rejected", 4: "Factory Audit",
    5: "In Production", 12: "Shipped", 13: "Closed", 14: "Refunding",
    15: "Refunded",
}

#: The only paths this connector may request, by exact match. Anything the
#: supplier's API can do to an order is absent from this set, and a prefix would
#: not have been enough -- the write endpoints share one with the read ones.
_ALLOWED_PATHS: frozenset[str] = frozenset(READ_ENDPOINTS.values())


DATASETS: tuple[DatasetKind, ...] = (
    DatasetKind(
        key="styles",
        label="Catalogue styles",
        description="Every style the supplier offers: code, name, craft types and images.",
        resource_kind="account",
        key_columns=("styleCode",),
    ),
    DatasetKind(
        key="colors",
        label="Catalogue colours",
        description="Colour codes and names available across the catalogue.",
        resource_kind="account",
        key_columns=("colorCode",),
    ),
    DatasetKind(
        key="sizes",
        label="Catalogue sizes",
        description="Size codes and names available across the catalogue.",
        resource_kind="account",
        key_columns=("sizeCode",),
    ),
)


class RiinConnector(RestConnector):
    provider = "riin"
    base_url = DEFAULT_BASE_URL
    datasets_offered = DATASETS

    #: The supplier is slow under load and their own client allows ninety
    #: seconds. Calling it hung sooner produces a failure that is not one.
    SLOW_TIMEOUT = 90.0

    def __init__(self, token: str, base_url: str = "", **kwargs):
        super().__init__(token, **kwargs)
        if base_url:
            self.base_url = base_url.rstrip("/")

    def label(self) -> str:
        return "DIGI / RIIN"

    # -- auth ---------------------------------------------------------------
    def auth_headers(self) -> dict[str, str]:
        """
        Not used: this API signs each request over its own body, so the headers
        cannot be built without knowing what is being sent.
        """
        raise NotImplementedError("RIIN signs per request; see _sign.")

    def _sign(self, body_text: str) -> dict[str, str]:
        """
        The two headers this API wants.

        The signature covers the exact body bytes that are sent, so the same
        string has to be both hashed and posted -- serialising twice would
        produce a hash of something the server never saw.
        """
        digest = hashlib.md5(
            f"{body_text}::{self._token}".encode("utf-8")
        ).hexdigest()
        return {
            "Content-Type": "application/json",
            "secretKey": self._token,
            "sign": digest,
        }

    def _redact(self, text: str) -> str:
        return super()._redact(text)

    # -- HTTP ---------------------------------------------------------------
    def _post(self, path: str, body: dict[str, Any]) -> dict:
        if path not in _ALLOWED_PATHS:
            # An allowlist of exact paths, not a prefix.
            #
            # A prefix check was the first attempt and it was not a guard at all:
            # placeOrder, updateOrder and closeOrder live under the same prefix
            # as the query endpoints, and a probe written to prove the guard
            # worked instead reached placeOrder on a live supplier account. It
            # was rejected for having no recipient, so nothing was created --
            # but nothing about the code had stopped it.
            raise ConnectorError(
                f"'{path}' is not a read endpoint. This connector can only call "
                f"{', '.join(sorted(_ALLOWED_PATHS))}."
            )

        body_text = json.dumps(body, separators=(",", ":"))
        try:
            response = httpx.post(
                f"{self.base_url}{path}",
                content=body_text.encode("utf-8"),
                headers=self._sign(body_text),
                timeout=self.SLOW_TIMEOUT,
            )
        except httpx.TimeoutException as error:
            raise ConnectorError(
                f"The supplier did not answer within {int(self.SLOW_TIMEOUT)} seconds. "
                "This will be retried.",
                retryable=True,
            ) from error
        except httpx.HTTPError as error:
            raise ConnectorError(
                f"Could not reach the supplier: {self._redact(str(error))}",
                retryable=True,
            ) from error

        if response.status_code >= 500:
            raise ConnectorError(
                "The supplier's API is having trouble at their end. This will be "
                "retried.",
                retryable=True,
            )

        try:
            result = response.json()
        except ValueError as error:
            raise ConnectorError(
                f"The supplier returned HTTP {response.status_code} and not JSON. "
                "That usually means the base URL is wrong."
            ) from error

        # The supplier reports failure in the body with a 200, so the status
        # code alone says almost nothing.
        if not (result.get("successful") or result.get("success")):
            message = self._redact(str(result.get("message") or "")).strip()
            code = result.get("errorCode")
            if not message:
                message = "The supplier rejected the request."
            raise ConnectorError(
                f"{message}{f' (code {code})' if code else ''}"
            )
        return result

    @staticmethod
    def _records(result: dict) -> list[dict]:
        """
        The rows out of the supplier's envelope.

        Catalogue calls answer with `data.records`; order calls answer with
        `data` as a bare list. Both shapes are handled because getting it wrong
        yields an empty table and no error at all.
        """
        data = result.get("data")
        if isinstance(data, dict) and isinstance(data.get("records"), list):
            return [flatten(row) for row in data["records"] if isinstance(row, dict)]
        if isinstance(data, list):
            return [flatten(row) for row in data if isinstance(row, dict)]
        if isinstance(result.get("records"), list):
            return [flatten(row) for row in result["records"] if isinstance(row, dict)]
        return []

    # -- discovery ----------------------------------------------------------
    def discover(self) -> Discovery:
        """
        Prove the key and the signature work, with the cheapest call there is.

        One row rather than a page: discovery should cost the supplier nothing,
        and a single record proves the credential, the signature and the base
        URL all at once.
        """
        found = Discovery()
        found.account_id = "riin"
        found.account_name = f"DIGI / RIIN ({self.base_url})"

        result = self._post(READ_ENDPOINTS["styles"], {"pageIndex": 1, "pageSize": 1})
        total = self._total(result)

        found.resources = [Resource(
            id="account",
            kind="account",
            # Short on purpose. This name is used to name the tables that get
            # created, and putting a URL in it produced identifiers like
            # api_catalogue_styles_digi_riin_https_tshirt_riin_com -- present in
            # the field list, and unfindable in it.
            name="Supplier catalogue",
            detail={"styles_in_catalogue": total, "base_url": self.base_url},
        )]
        found.detail = (
            "Authenticated with the signed secret key. The catalogue answered"
            + (f" with {total} styles." if total is not None else ".")
        )
        return found

    @staticmethod
    def _total(result: dict) -> int | None:
        data = result.get("data")
        if isinstance(data, dict):
            for key in ("total", "totalCount", "totalRecords", "count"):
                value = data.get(key)
                if isinstance(value, int):
                    return value
        return None

    # -- fetching -----------------------------------------------------------
    def fetch(
        self,
        dataset: str,
        resource_id: str,
        since: date | None = None,
        until: date | None = None,
        cursor: str | None = None,
    ) -> Page:
        path = READ_ENDPOINTS.get(dataset)
        if path is None:
            raise ConnectorError(
                f"DIGI / RIIN connector has no dataset called '{dataset}'."
            )

        page_index = int(cursor) if cursor and cursor.isdigit() else 1
        result = self._post(path, {"pageIndex": page_index, "pageSize": PAGE_SIZE})
        rows = self._records(result)

        # A short page is the last one. The supplier reports a total as well,
        # but a short page is true whether or not that field is present.
        next_index = page_index + 1 if len(rows) >= PAGE_SIZE else None
        return Page(rows=rows, cursor=str(next_index) if next_index else None)

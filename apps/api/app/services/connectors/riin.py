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

The other thing this connector does deliberately is refuse to grow. Of the
fourteen endpoints the supplier documents, five write: placeOrder, updateOrder,
preShipped, closeOrder and updatePrintImage. Every one of them acts on a real
order with a real factory. A reporting tool has no business holding those, so
they are absent from this file rather than merely unused.

All nine that read are here, in two shapes.

Five can be asked what they hold: the catalogue endpoints page through styles,
colours, sizes, products and ship-from addresses.

The other four cannot. queryOrderStatus, queryOrderDelivery, queryOrderInfo and
queryProductShipAddress each take a list of identifiers and answer about exactly
those, and nothing lists them -- there is no "list my orders". For a while that
looked like a wall. It was not: the orders were placed from this company's own
system, which recorded every number it sent, and the product codes come from the
catalogue this connector already syncs. Those datasets declare a `key_source`
saying where to read the identifiers, and the sync supplies them in batches the
supplier accepts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date
from typing import Any

import httpx

from app.services.connectors.base import (
    ConnectorError,
    DatasetKind,
    KeySource,
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

#: Read-only by construction. A reporting tool that *could* place an order is
#: one bad code path away from placing one.
READ_ENDPOINTS: dict[str, str] = {
    "styles": f"{INTERFACE}/queryStyle",
    "colors": f"{INTERFACE}/queryColor",
    "sizes": f"{INTERFACE}/querySize",
    "products": f"{INTERFACE}/queryProduct",
    "ship_addresses": f"{INTERFACE}/queryShipAddress",
    "order_status": f"{INTERFACE}/queryOrderStatus",
    "order_delivery": f"{INTERFACE}/queryOrderDelivery",
    "order_info": f"{INTERFACE}/queryOrderInfo",
    "product_ship_addresses": f"{INTERFACE}/queryProductShipAddress",
}

#: Endpoints that will not tell you what they hold. Each takes a list of
#: identifiers and answers about exactly those; none of them can be asked "what
#: have you got". The name of the field carrying that list differs per
#: endpoint, which is the only reason this is a mapping and not a set.
KEYED_ENDPOINTS: dict[str, str] = {
    "order_status": "platformOidList",
    "order_delivery": "platformOidList",
    "order_info": "platformOidList",
    "product_ship_addresses": "productCodeList",
}

#: Arrays that are the row rather than a detail of it. queryOrderStatus answers
#: per order with a list of line statuses inside; keeping that as JSON text
#: would put goodsStatus in a column nobody can filter on. Exploding it gives
#: one row per line, with the order's own fields repeated -- which is what a
#: line-level table is.
EXPLODE: dict[str, str] = {
    "order_status": "childOrderStatus",
    "product_ship_addresses": "addressList",
}

#: Which of those take pageIndex/pageSize. queryShipAddress takes no request
#: fields at all and answers in one go; sending it a page number returns the
#: same rows again, which looks exactly like paging that works.
PAGED: frozenset[str] = frozenset({"styles", "colors", "sizes", "products"})

#: Ten requests per second per endpoint is the supplier's documented limit.
#: Spacing every call by a tenth of a second stays under it without having to
#: track which endpoint is being called -- and a sync that gets itself rate
#: limited reports an error the person configuring it cannot act on.
MIN_INTERVAL = 0.1

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
    DatasetKind(
        key="products",
        label="Catalogue products",
        description=(
            "Every style/colour/size combination the supplier stocks, with the "
            "weight and dimensions of each."
        ),
        resource_kind="account",
        key_columns=("productCode",),
    ),
    DatasetKind(
        key="ship_addresses",
        label="Factory ship-from addresses",
        description="The factory addresses orders can be dispatched from.",
        resource_kind="account",
        key_columns=("addressId",),
    ),
    DatasetKind(
        key="order_status",
        label="Order status (per line)",
        description=(
            "Where each order line has got to, in the supplier's own words: "
            "one row per line, with the order's status alongside."
        ),
        resource_kind="account",
        key_columns=("platformOid", "platformOllId"),
        key_source=KeySource(origin="operational", table="blanktex.purchases",
                             column="order_no", batch_size=100),
    ),
    DatasetKind(
        key="order_delivery",
        label="Order tracking and labels",
        description=(
            "Tracking number, carrier and the URL of the shipping label PDF, "
            "for orders that have shipped."
        ),
        resource_kind="account",
        key_columns=("platformOid",),
        key_source=KeySource(origin="operational", table="blanktex.purchases",
                             column="order_no", batch_size=100),
    ),
    DatasetKind(
        key="order_info",
        label="Order detail",
        description=(
            "The supplier's own copy of each order header: recipient, address, "
            "carrier, shop, and the times it moved between states."
        ),
        resource_kind="account",
        key_columns=("platformOid",),
        key_source=KeySource(origin="operational", table="blanktex.purchases",
                             column="order_no", batch_size=100),
    ),
    DatasetKind(
        key="product_ship_addresses",
        label="Which factory ships which product",
        description=(
            "One row per product and dispatch address. Ten product codes per "
            "request is the supplier's limit, so this is the slowest sync here."
        ),
        resource_kind="account",
        key_columns=("productCode", "addressId"),
        key_source=KeySource(origin="dataset", table="products",
                             column="productCode", batch_size=10),
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
        self._last_call = 0.0

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

        # ensure_ascii=False matters even though today's bodies are all ASCII.
        # Without it Python escapes a non-ASCII character to \uXXXX, the server
        # hashes the bytes it actually received, and the two signatures differ
        # -- reported as an authentication failure, with a key that is perfectly
        # good. The supplier's own example passes this flag.
        body_text = json.dumps(body, separators=(",", ":"), ensure_ascii=False)

        wait = MIN_INTERVAL - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

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
    def _raw_records(result: dict) -> list[dict]:
        """
        The records as the supplier sent them, before flattening.

        Catalogue calls answer with `data.records`; order calls answer with
        `data` as a bare list. Both shapes are handled because getting it wrong
        yields an empty table and no error at all.
        """
        data = result.get("data")
        if isinstance(data, dict) and isinstance(data.get("records"), list):
            source = data["records"]
        elif isinstance(data, list):
            source = data
        elif isinstance(result.get("records"), list):
            source = result["records"]
        else:
            source = []
        return [row for row in source if isinstance(row, dict)]

    @classmethod
    def _records(cls, result: dict) -> list[dict]:
        """One flat row per record."""
        return [flatten(row) for row in cls._raw_records(result)]

    @classmethod
    def _exploded(cls, result: dict, nested: str) -> list[dict]:
        """
        One row per entry of a record's detail array.

        queryOrderStatus answers per order with the line statuses inside it.
        Left alone, that array becomes JSON text in a single column and
        goodsStatus is something nobody can filter on. Opened out, it is a
        line-level table with the order's own fields repeated on each row --
        which is what it always was.
        """
        rows: list[dict] = []
        for record in cls._raw_records(result):
            children = record.get(nested)
            parent = flatten({k: v for k, v in record.items() if k != nested})
            if not isinstance(children, list) or not children:
                # An order with no lines yet, or a product that ships from
                # nowhere. Dropping it would make "nothing there" and "never
                # asked" look the same.
                rows.append(parent)
                continue
            for child in children:
                rows.append({**parent, **flatten(child)} if isinstance(child, dict)
                            else {**parent, nested: child})
        return rows

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

        # Cheap, and worth knowing before the first sync: a catalogue of eight
        # styles and one of eight thousand products are the same connection.
        try:
            products = self._total(
                self._post(READ_ENDPOINTS["products"], {"pageIndex": 1, "pageSize": 1})
            )
        except ConnectorError:
            # An older account may not have this endpoint enabled. That is not
            # a reason to fail a connection whose credential just worked.
            products = None

        found.resources = [Resource(
            id="account",
            kind="account",
            # Short on purpose. This name is used to name the tables that get
            # created, and putting a URL in it produced identifiers like
            # api_catalogue_styles_digi_riin_https_tshirt_riin_com -- present in
            # the field list, and unfindable in it.
            name="Supplier catalogue",
            detail={
                "styles_in_catalogue": total,
                "products_in_catalogue": products,
                "base_url": self.base_url,
            },
        )]
        found.detail = (
            "Authenticated with the signed secret key. The catalogue answered"
            + (f" with {total} styles." if total is not None else ".")
        )
        return found

    @staticmethod
    def _total(result: dict) -> int | None:
        """
        How many records the supplier says there are.

        A numeric string counts. This API is inconsistent about it -- craftType
        comes back as "1" and priceMode as 1 in the same row -- so insisting on
        an integer meant discovery could not report a count it had been given.
        """
        data = result.get("data")
        if not isinstance(data, dict):
            return None
        for key in ("total", "totalCount", "totalRecords", "count"):
            value = data.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
        return None

    # -- fetching -----------------------------------------------------------
    def fetch(
        self,
        dataset: str,
        resource_id: str,
        since: date | None = None,
        until: date | None = None,
        cursor: str | None = None,
        keys: list[str] | None = None,
    ) -> Page:
        path = READ_ENDPOINTS.get(dataset)
        if path is None:
            raise ConnectorError(
                f"DIGI / RIIN connector has no dataset called '{dataset}'."
            )

        nested = EXPLODE.get(dataset)

        field = KEYED_ENDPOINTS.get(dataset)
        if field is not None:
            if not keys:
                # Deliberately not an empty result. This endpoint answers an
                # empty list with an empty list, and a table that quietly
                # emptied itself is the failure this refuses to perform.
                raise ConnectorError(
                    f"'{dataset}' has to be told which identifiers to ask "
                    "about, and none were supplied."
                )
            result = self._post(path, {field: list(keys)})
            rows = (self._exploded(result, nested) if nested
                    else self._records(result))
            # One request, one batch. Batching is the sync's job: it knows how
            # many identifiers there are and this does not.
            return Page(rows=rows, cursor=None)

        if dataset not in PAGED:
            # No request fields, one answer, no next page.
            return Page(rows=self._records(self._post(path, {})), cursor=None)

        page_index = int(cursor) if cursor and cursor.isdigit() else 1
        result = self._post(path, {"pageIndex": page_index, "pageSize": PAGE_SIZE})
        rows = self._records(result)

        # A short page is the last one. The supplier reports a total as well,
        # but a short page is true whether or not that field is present.
        next_index = page_index + 1 if len(rows) >= PAGE_SIZE else None
        return Page(rows=rows, cursor=str(next_index) if next_index else None)

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

Two further kinds of table sit alongside them and call no endpoint at all. The
order lines and artwork exactly as they were sent exist only in the request
BlankTex recorded, so they are read from its database. And what the supplier's
codes mean -- 13 is Closed, "1,2" is front and back -- exists only in its
documentation, so those tables are written here.

And nothing the API returns is allowed to go missing on the way to a column.
Every API row carries raw_json, the record exactly as it arrived. Every field
the supplier's document names becomes a column even when no value has ever been
sent for it. The envelope and paging fields are kept as data in their own table,
and the document itself is a table: every field of all fourteen endpoints, with
where to find it.
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
from app.services.connectors.riin_fields import (
    FIELD_DICTIONARY,
    GOODS_LABEL_FIELDS,
    GOODS_LIST_FIELDS,
    IMAGE_LIST_FIELDS,
    PLACE_ORDER_FIELDS,
)

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


#: Every API row also carries the record exactly as the supplier sent it.
RAW_JSON = "raw_json"

#: The dataset that keeps the envelope and paging fields as data.
API_RESPONSES = "api_responses"


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


#: Guards every jsonb_array_elements below. A payload with no goodsList, or one
#: where it is not an array, contributes no rows instead of failing the query.
_LINES = """
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(p.supplier_payload -> 'goodsList') = 'array'
             then p.supplier_payload -> 'goodsList' else '[]'::jsonb end
    ) as g(line)"""

#: Each order line exactly as BlankTex sent it to placeOrder, with the order's
#: own declared fields alongside. Images are their own table.
ORDER_LINES_SENT_QUERY = f"""
select g.line - 'imageList' as line,
       g.line::text as raw_json,
       p.supplier_payload ->> 'sourcePlatformOid'    as "sourcePlatformOid",
       p.supplier_payload ->> 'platformType'         as "platformType",
       p.supplier_payload ->> 'platformOrderStatus'  as "platformOrderStatus",
       p.supplier_payload ->> 'platformRefundStatus' as "platformRefundStatus",
       p.supplier_payload ->> 'orderTime'            as "orderTime",
       jsonb_array_length(
           case when jsonb_typeof(g.line -> 'imageList') = 'array'
                then g.line -> 'imageList' else '[]'::jsonb end
       ) as "imageCount"
from blanktex.purchases p{_LINES}
"""

#: Every artwork and mockup image sent with each line, including the imageCode
#: BlankTex's own image table does not keep.
ORDER_LINE_IMAGES_SENT_QUERY = f"""
select g.line ->> 'platformOid'   as "platformOid",
       g.line ->> 'platformOllId' as "platformOllId",
       i.image,
       i.image::text as raw_json
from blanktex.purchases p{_LINES}
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(g.line -> 'imageList') = 'array'
             then g.line -> 'imageList' else '[]'::jsonb end
    ) as i(image)
"""

#: Each order's placeOrder body as BlankTex sent it, lines aside.
ORDERS_SENT_QUERY = """
select p.supplier_payload - 'goodsList' as "order",
       case when jsonb_typeof(p.supplier_payload -> 'goodsList') = 'array'
            then jsonb_array_length(p.supplier_payload -> 'goodsList') else 0 end as "lineCount",
       p.supplier_payload::text as raw_json
from blanktex.purchases p
where p.supplier_payload is not null
"""

_SUPPLIER_DOC = "Supplier API document"
_FIELD_DICTIONARY = "BlankTex field dictionary"

ORDER_STATUS_CODES = tuple(
    {"code": code, "meaning": meaning, "supplierText": chinese, "source": _SUPPLIER_DOC,
     "note": note}
    for code, meaning, chinese, note in (
        (1, "Store Audit", "店铺审核", ""),
        (2, "Pending Push", "店铺推送中", "The only state an order can still be edited in."),
        (3, "Rejected", "已驳回", "Returned by the factory; the order's reason field says why."),
        (4, "Factory Audit", "工厂审核", ""),
        (5, "In Production", "生产中", ""),
        (12, "Shipped", "已发货", ""),
        (13, "Closed", "已关闭", "Final. Set by closeOrder or by the supplier."),
        (14, "Refunding", "退款中", ""),
        (15, "Refunded", "已退款", "Final."),
    )
)

#: goodsStatus, platformOrderStatus and the refund statuses share one
#: vocabulary of string codes that never collide, so one table serves them all.
STATE_CODES = tuple(
    {"code": code, "meaning": meaning, "supplierText": chinese, "source": source}
    for code, meaning, chinese, source in (
        ("NOT_SHIPPED", "Not shipped", "未发货", _SUPPLIER_DOC),
        ("SHIPPED", "Shipped", "已发货", _SUPPLIER_DOC),
        ("CLOSE", "Closed", "", _SUPPLIER_DOC),
        ("CANCEL", "Cancelled", "", _SUPPLIER_DOC),
        ("COMPLETE", "Completed", "", _SUPPLIER_DOC),
        ("NO_REFUND", "No refund", "无退款", _FIELD_DICTIONARY),
    )
)

#: The two sources disagree here, and both readings are kept rather than one
#: being chosen quietly. The supplier's document says 1 is heat transfer and 2
#: is DTG; BlankTex's field dictionary says 1 is DTG / print and 2 is
#: embroidery. The supplier's reading is the one given as the meaning.
CRAFT_TYPE_CODES = (
    {"code": 1, "meaning": "Heat Transfer", "source": _SUPPLIER_DOC,
     "note": "BlankTex's field dictionary reads this as DTG / print. The two disagree."},
    {"code": 2, "meaning": "DTG (Direct-to-Garment)", "source": _SUPPLIER_DOC,
     "note": "BlankTex's field dictionary reads this as embroidery / a second craft. The two disagree."},
)

#: Text codes on purpose: "1,2" is a code, not the number twelve.
PRINT_POSITION_CODES = (
    {"code": "1", "meaning": "Front only", "source": _SUPPLIER_DOC},
    {"code": "2", "meaning": "Back only", "source": _SUPPLIER_DOC},
    {"code": "1,2", "meaning": "Front and back", "source": _SUPPLIER_DOC,
     "note": "Requires a back print and a back mockup image."},
)

IMAGE_TYPE_CODES = (
    {"code": 1, "meaning": "Print / artwork file", "source": _SUPPLIER_DOC,
     "note": "Uploaded to the supplier's image library; must be PNG."},
    {"code": 2, "meaning": "Mockup image", "source": _SUPPLIER_DOC},
)

UNIT_TYPE_CODES = (
    {"code": 1, "meaning": "Centimetres and kilograms", "source": _SUPPLIER_DOC,
     "note": "BlankTex's field dictionary records this split as undocumented to it."},
    {"code": 2, "meaning": "Inches and pounds", "source": _SUPPLIER_DOC},
)

CARRIER_CODES = (
    {"code": 200, "meaning": "USPS", "source": _FIELD_DICTIONARY},
    {"code": 201, "meaning": "UPS", "source": _FIELD_DICTIONARY},
)

#: Fields that only ever hold one value on this account. Kept so the value is
#: explained somewhere, rather than being a column of 15s nobody can read.
CONSTANT_CODES = (
    {"field": "goodsType", "code": "1", "meaning": "Standard goods", "source": _FIELD_DICTIONARY},
    {"field": "platformType", "code": "15", "meaning": "BlankTex's platform id at the supplier",
     "source": _FIELD_DICTIONARY},
    {"field": "priceMode", "code": "1", "meaning": "Standard pricing", "source": _FIELD_DICTIONARY},
)


def _codes(key: str, label: str, what: str, rows: tuple, key_columns=("code",)) -> DatasetKind:
    return DatasetKind(
        key=key,
        label=label,
        description=f"What the supplier's {what} codes mean. Reference data, not synced.",
        resource_kind="account",
        key_columns=key_columns,
        static_rows=rows,
    )


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
    DatasetKind(
        key="orders_sent",
        label="DIGI orders (as sent)",
        description=(
            "Every order exactly as BlankTex sent it to placeOrder, one row each, with a "
            "column for every order-level field the supplier documents -- empty where "
            "BlankTex has never sent a value. Read from BlankTex's database."
        ),
        resource_kind="account",
        key_columns=("platformOid",),
        operational_query=ORDERS_SENT_QUERY,
        documented_fields=PLACE_ORDER_FIELDS,
    ),
    DatasetKind(
        key="order_lines_sent",
        label="DIGI order lines (as sent)",
        description=(
            "Every order line exactly as BlankTex sent it to the supplier: style, "
            "colour and size codes and names, title, quantity, craft type, print "
            "position, and the states it declared. Read from BlankTex's database."
        ),
        resource_kind="account",
        key_columns=("platformOllId",),
        operational_query=ORDER_LINES_SENT_QUERY,
        documented_fields=GOODS_LIST_FIELDS + GOODS_LABEL_FIELDS,
    ),
    DatasetKind(
        key="order_line_images_sent",
        label="DIGI order line images (as sent)",
        description=(
            "Every artwork and mockup image sent with each order line, with its "
            "type, URL, code and name. Read from BlankTex's database."
        ),
        resource_kind="account",
        key_columns=("platformOllId", "imageCode"),
        operational_query=ORDER_LINE_IMAGES_SENT_QUERY,
        documented_fields=IMAGE_LIST_FIELDS,
    ),
    _codes("codes_order_status", "DIGI codes: order status", "order status", ORDER_STATUS_CODES),
    _codes("codes_states", "DIGI codes: order and line states", "shipping and refund state",
           STATE_CODES),
    _codes("codes_craft_type", "DIGI codes: craft type", "craft type", CRAFT_TYPE_CODES),
    _codes("codes_print_position", "DIGI codes: print position", "print position",
           PRINT_POSITION_CODES),
    _codes("codes_image_type", "DIGI codes: image type", "image type", IMAGE_TYPE_CODES),
    _codes("codes_unit_type", "DIGI codes: unit type", "unit of measure", UNIT_TYPE_CODES),
    _codes("codes_carrier", "DIGI codes: carrier", "carrier (expressCode)", CARRIER_CODES),
    _codes("codes_constants", "DIGI codes: constants", "single-valued field", CONSTANT_CODES,
           key_columns=("field", "code")),
    DatasetKind(
        key=API_RESPONSES,
        label="DIGI API responses",
        description=(
            "One live request to each catalogue endpoint, kept as the supplier answered "
            "it: successful, message, errorCode, traceId, pageIndex, pageSize, total and "
            "the data itself, with the request that produced it. Credentials are never kept."
        ),
        resource_kind="account",
        key_columns=("endpoint",),
        documented_fields=("successful", "message", "errorCode", "data", "traceId",
                           "pageIndex", "pageSize", "total"),
    ),
    DatasetKind(
        key="field_dictionary",
        label="DIGI API field dictionary",
        description=(
            "Every field the supplier's API document names -- all fourteen endpoints, "
            "request and response -- with its type, whether it is required, what it "
            "means, and which table and column holds it."
        ),
        resource_kind="account",
        key_columns=("endpoint", "direction", "level", "field"),
        static_rows=FIELD_DICTIONARY,
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
        """
        One flat row per record, carrying the record exactly as it arrived.

        Flattening decides what becomes a column; raw_json makes sure that
        deciding never loses anything. A nested list kept as text, or a field
        the supplier adds tomorrow, is still there in its original form.
        """
        return [{**flatten(row), RAW_JSON: _json(row)} for row in cls._raw_records(result)]

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
            parent = {**flatten({k: v for k, v in record.items() if k != nested}),
                      RAW_JSON: _json(record)}
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

    def _response_census(self) -> list[dict]:
        """
        One request to every endpoint that can be asked without identifiers, kept
        as the supplier answered it.

        This is where successful, message, errorCode, traceId, pageIndex, pageSize
        and total exist as data rather than as plumbing. The secret key and the
        signature travel as headers and are never recorded.
        """
        rows: list[dict] = []
        for key in ("styles", "colors", "sizes", "products", "ship_addresses"):
            path = READ_ENDPOINTS[key]
            body = {"pageIndex": 1, "pageSize": 1} if key in PAGED else {}
            started = time.perf_counter()
            result = self._post(path, body)
            data = result.get("data")
            records = self._raw_records(result)
            page = data if isinstance(data, dict) else {}
            rows.append({
                "endpoint": path.rsplit("/", 1)[1],
                "path": path,
                "requestBody": _json(body),
                "successful": result.get("successful"),
                "success": result.get("success"),
                "message": result.get("message"),
                "errorCode": result.get("errorCode"),
                "traceId": result.get("traceId"),
                "dataShape": ("page" if isinstance(data, dict)
                              else "list" if isinstance(data, list) else type(data).__name__),
                "pageIndex": page.get("pageIndex"),
                "pageSize": page.get("pageSize"),
                "total": page.get("total", len(data) if isinstance(data, list) else None),
                "recordsReturned": len(records),
                "responseKeys": ", ".join(sorted(result)),
                "dataKeys": ", ".join(sorted(page)),
                "recordKeys": ", ".join(sorted({k for r in records for k in r})),
                "data": _json(data),
                "durationMs": int((time.perf_counter() - started) * 1000),
            })
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
        if dataset == API_RESPONSES:
            return Page(rows=self._response_census(), cursor=None)

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

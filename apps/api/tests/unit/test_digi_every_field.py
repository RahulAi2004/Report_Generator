"""
Every field of the DIGI API, in whatever form it exists.

Asked for the last time for all of the API's fields, the gap turned out not to
be data that failed to arrive but fields with nowhere to go: optional inputs
BlankTex has never sent had no column, the envelope and paging were treated as
plumbing, nested arrays were flattened to text, and four write endpoints left no
trace at all. These tests pin each of those shut.
"""

from __future__ import annotations

import json

from app.services import connector_service
from app.services.connectors import riin
from app.services.connectors.base import DatasetKind
from app.services.connectors.riin_fields import (
    FIELD_DICTIONARY,
    GOODS_LABEL_FIELDS,
    GOODS_LIST_FIELDS,
    PLACE_ORDER_FIELDS,
)

PDF = "Supplier API document"


# ---------------------------------------------------------------------------
# Nothing returned is lost on the way to a column
# ---------------------------------------------------------------------------
def test_every_api_row_carries_the_record_as_it_arrived():
    record = {"styleCode": "DG001", "images": ["a.png", "b.png"], "extra": {"x": [{"y": 1}]}}
    rows = riin.RiinConnector._records({"data": {"records": [record]}})

    assert json.loads(rows[0][riin.RAW_JSON]) == record


def test_an_exploded_row_carries_its_whole_parent():
    record = {"platformOid": "A1", "childOrderStatus": [{"platformOllId": "A1001"},
                                                        {"platformOllId": "A1002"}]}
    rows = riin.RiinConnector._exploded({"data": [record]}, "childOrderStatus")

    assert len(rows) == 2
    assert all(json.loads(r[riin.RAW_JSON]) == record for r in rows)


def test_a_documented_field_nobody_sent_is_still_a_column():
    kind = DatasetKind(key="k", label="l", description="d", resource_kind="account",
                       static_rows=({"a": 1},), documented_fields=("a", "neverSent"))
    rows = connector_service._fetch_all(object(), kind, dataset=object())

    assert rows == [{"a": 1, "neverSent": None}]
    columns = {c["name"] for c in connector_service.infer_columns(rows)}
    assert columns == {"a", "neversent"}


# ---------------------------------------------------------------------------
# Every placeOrder field has a column
# ---------------------------------------------------------------------------
def test_orders_as_sent_have_every_order_level_placeorder_field():
    kind = next(d for d in riin.DATASETS if d.key == "orders_sent")
    for field in ("sellerFlag", "totalFee", "payment", "receivedPayment", "selfWaybillFlag",
                  "waybill", "receiverDistrict", "company", "orderPayTime", "postCode"):
        assert field in kind.documented_fields
    assert set(PLACE_ORDER_FIELDS) <= set(kind.documented_fields)


def test_lines_as_sent_have_every_goodslist_and_goodslabel_field():
    kind = next(d for d in riin.DATASETS if d.key == "order_lines_sent")
    for field in ("platformSpuId", "platformSkuId", "price", "sellPrice", "totalPrice",
                  "specification", "remark", "goodsLabel_europeanLabelUrl",
                  "goodsLabel_countryName"):
        assert field in kind.documented_fields
    assert set(GOODS_LIST_FIELDS) | set(GOODS_LABEL_FIELDS) <= set(kind.documented_fields)
    # The document's spelling is not the API's; a column under the document's
    # spelling would be a second, permanently empty line id.
    assert "platformOlId" not in kind.documented_fields


# ---------------------------------------------------------------------------
# The envelope, as data
# ---------------------------------------------------------------------------
def test_the_response_census_keeps_the_envelope_and_never_the_credentials(monkeypatch):
    secret = "a-very-secret-key-1234567890"
    connector = riin.RiinConnector(secret)
    reached: list[str] = []

    class Response:
        status_code = 200

        def __init__(self, url):
            self.url = url

        def json(self):
            if self.url.endswith("queryShipAddress"):
                return {"successful": True, "message": "", "errorCode": "", "traceId": "t2",
                        "data": [{"addressId": "x"}]}
            return {"successful": True, "message": "", "errorCode": "", "traceId": "t1",
                    "data": {"pageIndex": 1, "pageSize": 1, "total": "66",
                             "records": [{"styleCode": "DG001"}]}}

    def fake_post(url, content=None, headers=None, timeout=None):
        reached.append(url)
        return Response(url)

    monkeypatch.setattr("app.services.connectors.riin.httpx.post", fake_post)
    monkeypatch.setattr(riin, "MIN_INTERVAL", 0)

    rows = connector.fetch(riin.API_RESPONSES, "account").rows

    assert len(rows) == 5
    assert all(url.split("tshirt.riin.com")[1] in riin._ALLOWED_PATHS for url in reached)
    first = rows[0]
    for field in ("successful", "message", "errorCode", "traceId", "pageIndex", "pageSize",
                  "total", "data"):
        assert field in first
    assert first["total"] == "66"
    assert rows[-1]["dataShape"] == "list"
    # Neither the key nor anything derived from it is kept.
    assert secret not in json.dumps(rows)
    assert not any("sign" in key.lower() for row in rows for key in row)


# ---------------------------------------------------------------------------
# The document itself
# ---------------------------------------------------------------------------
def test_the_dictionary_holds_every_field_the_document_names():
    documented = [r for r in FIELD_DICTIONARY if r["source"] == PDF]
    endpoints = {r["endpoint"] for r in FIELD_DICTIONARY}

    # 178 parsed field rows plus the two authentication headers.
    assert len(documented) == 180
    assert {"placeOrder", "updateOrder", "preShipped", "queryOrderDelivery", "queryOrderStatus",
            "queryProduct", "queryStyle", "queryColor", "querySize", "queryShipAddress",
            "closeOrder", "queryProductShipAddress", "queryOrderInfo",
            "updatePrintImage"} <= endpoints
    names = {(r["endpoint"], r["field"]) for r in documented}
    for expected in (("placeOrder", "sellerFlag"), ("placeOrder", "europeanLabelUrl"),
                     ("updatePrintImage", "imageCode"), ("preShipped", "platformOid"),
                     ("General", "successful"), ("queryOrderStatus", "goodsStatusStr")):
        assert expected in names


def test_every_field_says_where_to_find_it():
    assert all(r["whereInReports"] for r in FIELD_DICTIONARY)


def test_the_misfiled_sections_were_corrected():
    status = {r["field"]: r for r in FIELD_DICTIONARY if r["endpoint"] == "queryOrderStatus"}
    assert status["orderStateStr"]["direction"] == "Response"
    assert status["platformOidList"]["direction"] == "Request"

    reprint = [r for r in FIELD_DICTIONARY if r["endpoint"] == "updatePrintImage"
               and r["field"] == "platformOid"]
    assert {r["level"] for r in reprint} == {"body", "goodsList[]"}


def test_credentials_are_documented_but_never_recorded():
    headers = {r["field"]: r for r in FIELD_DICTIONARY if r["level"] == "header"}
    assert set(headers) == {"secretKey", "sign"}
    assert all("Never recorded" in r["whereInReports"] for r in headers.values())

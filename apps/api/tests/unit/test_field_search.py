"""
Finding a field by the name the supplier's document gives it.

A screenshot settled what "the fields are still not showing" meant: somebody
typed platformRefundStatus into the Data Sources search and got "No tables
match". The field was in five tables. The search read table names only, and the
Fields panel showed the column as platformrefundstatus -- so a name copied
straight from the document found nothing anywhere, which is indistinguishable
from the field not existing.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.api.v1.schema import _column_payload, _matches, _table_payload
from app.domain.schema.registry import ColumnMeta, DataType, SchemaRegistry, TableMeta
from app.services import connector_service
from app.services.connectors import riin


def digi_table() -> TableMeta:
    return TableMeta(
        name="api_order_detail",
        schema="connectors",
        kind="upload",
        category="DIGI / RIIN",
        display_name="Order detail",
        columns=(
            ColumnMeta(table="api_order_detail", name="platformrefundstatus", data_type=DataType.TEXT,
                       physical_type="text", display_name="Platform Refund Status",
                       source_name="platformRefundStatus"),
            ColumnMeta(table="api_order_detail", name="postcode", data_type=DataType.TEXT,
                       physical_type="text", source_name="postCode"),
        ),
    )


def test_a_table_is_found_by_a_field_it_holds():
    table = digi_table()
    assert _matches(table, "platformRefundStatus")
    assert _matches(table, "refund")
    assert _matches(table, "Order detail")
    assert not _matches(table, "sellerFlag")


def test_the_table_list_carries_every_field_with_its_source_spelling():
    payload = _table_payload(digi_table())
    assert ["platformrefundstatus", "Platform Refund Status", "platformRefundStatus"] in payload["fields"]
    assert len(payload["fields"]) == payload["column_count"]


def test_a_column_says_how_its_source_spells_it():
    column = digi_table().columns[0]
    assert _column_payload(column)["source_name"] == "platformRefundStatus"


def test_the_source_spelling_survives_permission_filtering():
    """for_principal copies every column; a field it forgot would vanish from the UI."""
    registry = SchemaRegistry([digi_table()], [])
    narrowed = registry.for_principal(allowed_tables=None)
    assert narrowed.table("api_order_detail").columns[0].source_name == "platformRefundStatus"


def test_synced_tables_keep_the_api_key_as_the_source_name():
    dataset = SimpleNamespace(
        id="d1", display_name="Order detail", physical_table="api_d1", resource_name="Supplier catalogue",
        row_count=1, last_synced_at=datetime(2026, 9, 14),
        columns=[{"name": "platformrefundstatus", "label": "Platform Refund Status",
                  "data_type": "text", "source": "platformRefundStatus"}],
    )
    table = connector_service.as_table_meta(dataset, "riin")
    assert table.columns[0].source_name == "platformRefundStatus"


def test_the_census_keeps_the_id_lists_it_sends(monkeypatch):
    """platformOidList and productCodeList used to exist only inside a request."""
    connector = riin.RiinConnector("a-very-secret-key-1234567890")
    sent: list[tuple[str, bytes]] = []

    class Response:
        status_code = 200

        def __init__(self, url):
            self.url = url

        def json(self):
            if self.url.endswith("queryProduct"):
                return {"successful": True, "traceId": "t", "data": {
                    "pageIndex": 1, "pageSize": 10, "total": "2",
                    "records": [{"productCode": "DG001-BL01-M"}, {"productCode": "DG001-BL01-L"}]}}
            if self.url.endswith(("queryShipAddress", "queryOrderInfo", "queryOrderStatus",
                                  "queryOrderDelivery", "queryProductShipAddress")):
                return {"successful": True, "traceId": "t", "data": []}
            return {"successful": True, "traceId": "t", "data": {"pageIndex": 1, "pageSize": 1,
                                                                "total": "1", "records": []}}

    def fake_post(url, content=None, headers=None, timeout=None):
        sent.append((url, content))
        return Response(url)

    monkeypatch.setattr("app.services.connectors.riin.httpx.post", fake_post)
    monkeypatch.setattr(riin, "MIN_INTERVAL", 0)

    rows = connector.fetch(riin.API_RESPONSES, "account", keys=["ORD-1", "ORD-2"]).rows
    by_endpoint = {row["endpoint"]: row for row in rows}

    assert len(rows) == 9
    for endpoint in ("queryOrderInfo", "queryOrderStatus", "queryOrderDelivery"):
        assert by_endpoint[endpoint]["platformOidList"] == "ORD-1, ORD-2"
        assert by_endpoint[endpoint]["idsSent"] == 2
    assert by_endpoint["queryProductShipAddress"]["productCodeList"] == "DG001-BL01-M, DG001-BL01-L"
    assert all(url.split("tshirt.riin.com")[1] in riin._ALLOWED_PATHS for url, _ in sent)

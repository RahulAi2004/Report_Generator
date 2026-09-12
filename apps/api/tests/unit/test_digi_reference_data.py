"""
The last of DIGI's fields, and two values that were being quietly changed.

What was left once every endpoint was synced lived in two places no endpoint
reaches. The order lines and their artwork exactly as they were sent survive
only in the request BlankTex recorded. What the supplier's codes mean -- 13 is
Closed, "1,2" is front and back -- exists only in documentation.

And checking every field end to end turned up two corruptions of the craftType
kind: ZIP codes losing their leading zero, and 19-digit ids that are exact in the
database and wrong on screen.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.adapters.base import QueryResult
from app.domain.schema.registry import DataType
from app.domain.uploads.parser import MAX_SAFE_INTEGER, _as_decimal, _as_int, infer_type
from app.services import connector_service
from app.services.connectors import riin
from app.services.connectors.base import ConnectorError


# ---------------------------------------------------------------------------
# Numbers that are really identifiers
# ---------------------------------------------------------------------------
def test_a_zip_code_keeps_its_leading_zero():
    """Three of DIGI's orders went to 03064 and 06604, stored as 3064 and 6604."""
    assert _as_int("03064") is None
    assert _as_decimal("03064") is None
    assert infer_type(["92881", "03064", "95054"]) is DataType.TEXT


def test_an_id_too_long_for_a_browser_stays_text():
    """
    2077398282558521346 fits in a bigint and is rounded by JavaScript on
    arrival, so the order id on screen is a different one.
    """
    assert infer_type(["2077398282558521346"]) is DataType.TEXT
    assert _as_decimal("2077398282558521346") is None


def test_a_22_digit_tracking_number_stays_text():
    """Inferred as an integer, this would not even fit the column: the insert fails."""
    assert infer_type(["9200190371836134338153"]) is DataType.TEXT


def test_the_boundary_is_the_browsers_not_a_guess():
    assert _as_int(str(MAX_SAFE_INTEGER)) == MAX_SAFE_INTEGER
    assert _as_int(str(MAX_SAFE_INTEGER + 1)) is None


def test_ordinary_numbers_are_unaffected():
    assert _as_int("0") == 0
    assert _as_int("-7") == -7
    assert _as_int("1,234") == 1234
    assert _as_decimal("0.28") == Decimal("0.28")
    assert _as_decimal("$1,999.00") == Decimal("1999.00")
    assert infer_type(["1", "2", "15"]) is DataType.INTEGER


# ---------------------------------------------------------------------------
# Order lines as they were sent
# ---------------------------------------------------------------------------
def test_a_json_line_becomes_fields_of_the_row():
    row = connector_service._open_objects({
        "line": {"styleCode": "DG001", "num": 5, "printPosition": "1,2"},
        "sourcePlatformOid": "ORD-1",
        "imageCount": 2,
    })

    assert row["styleCode"] == "DG001"
    assert row["num"] == 5
    assert row["sourcePlatformOid"] == "ORD-1"
    assert "line" not in row


def test_rows_come_from_the_read_only_adapter(monkeypatch):
    class Adapter:
        def execute(self, statement, max_rows):
            return QueryResult(
                columns=["line", "sourcePlatformOid"],
                rows=[({"platformOllId": "ORD-1001", "styleCode": "DG001"}, "ORD-1")],
            )

    monkeypatch.setattr("app.services.schema_service.adapter_for", lambda session: Adapter())
    kind = next(d for d in riin.DATASETS if d.key == "order_lines_sent")

    rows = connector_service._operational_rows(object(), kind)
    assert rows == [{"platformOllId": "ORD-1001", "styleCode": "DG001",
                     "sourcePlatformOid": "ORD-1"}]


def test_a_truncated_read_is_refused_rather_than_stored(monkeypatch):
    class Adapter:
        def execute(self, statement, max_rows):
            return QueryResult(columns=["line"], rows=[({"a": 1},)], truncated=True)

    monkeypatch.setattr("app.services.schema_service.adapter_for", lambda session: Adapter())
    kind = next(d for d in riin.DATASETS if d.key == "order_lines_sent")

    with pytest.raises(ConnectorError):
        connector_service._operational_rows(object(), kind)


def test_the_database_queries_only_read():
    """Written in code and run on the read-only connection -- and still checked."""
    for dataset in riin.DATASETS:
        if not dataset.operational_query:
            continue
        query = dataset.operational_query.lower()
        assert query.lstrip().startswith("select"), dataset.key
        assert ";" not in query, dataset.key
        for word in ("insert ", "update ", "delete ", "drop ", "alter ", "truncate "):
            assert word not in query, (dataset.key, word)


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
def test_reference_tables_never_call_the_supplier():
    class Client:
        def fetch(self, **kwargs):
            raise AssertionError("a reference table reached the network")

    kind = next(d for d in riin.DATASETS if d.key == "codes_order_status")
    rows = connector_service._fetch_all(Client(), kind, dataset=object())
    assert len(rows) == 9


def test_every_code_table_has_one_row_per_code():
    for dataset in riin.DATASETS:
        if not dataset.static_rows:
            continue
        keys = [tuple(row[c] for c in dataset.key_columns) for row in dataset.static_rows]
        assert len(keys) == len(set(keys)), dataset.key


def test_order_status_meanings_match_the_connectors_own_map():
    """Two lists of the same nine codes must not drift apart."""
    table = {row["code"]: row["meaning"] for row in riin.ORDER_STATUS_CODES}
    assert set(table) == set(riin.ORDER_STATUSES)


def test_print_positions_are_text_so_front_and_back_is_not_twelve():
    codes = [str(row["code"]) for row in riin.PRINT_POSITION_CODES]
    assert infer_type(codes) is DataType.TEXT


def test_the_craft_type_disagreement_is_recorded_not_resolved_quietly():
    for row in riin.CRAFT_TYPE_CODES:
        assert "disagree" in row["note"]


# ---------------------------------------------------------------------------
# Long integers that already exist in a database column
# ---------------------------------------------------------------------------
def test_a_long_integer_reaches_the_browser_as_text():
    """
    Inference keeps new columns safe; a bigint column that already exists in a
    database still has to survive being sent as JSON.
    """
    from app.api.v1.reports import _serialize

    assert _serialize(2077398282558521346) == "2077398282558521346"
    assert _serialize(MAX_SAFE_INTEGER) == MAX_SAFE_INTEGER
    assert _serialize(42) == 42
    assert _serialize(True) is True


def test_a_long_integer_is_exported_as_text_so_excel_keeps_every_digit():
    from app.domain.report.exporters import _cell

    assert _cell(2077398282558521346) == "2077398282558521346"
    # Excel's limit is fifteen digits, not the browser's sixteen.
    assert _cell(1_000_000_000_000_000) == "1000000000000000"
    assert _cell(999_999_999_999_999) == 999_999_999_999_999

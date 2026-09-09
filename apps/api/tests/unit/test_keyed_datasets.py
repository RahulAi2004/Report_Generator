"""
Datasets whose API will not say what it holds.

The supplier's order endpoints each take a list of order numbers and answer
about exactly those. There is no "list my orders". Read alone, that API cannot
produce an orders table at all -- which is why it looked for a while as though
this data simply could not be reported on.

It could. The orders were placed from this company's own system, which recorded
every number it sent. These tests cover the half that had to be built: knowing
where the identifiers come from, sending them in batches the endpoint accepts,
and refusing rather than inventing an empty table when there are none.
"""

from __future__ import annotations

import pytest

from app.services.connector_service import _batched
from app.services.connectors.base import ConnectorError
from app.services.connectors.riin import (
    EXPLODE,
    KEYED_ENDPOINTS,
    READ_ENDPOINTS,
    _ALLOWED_PATHS,
    RiinConnector,
)


# ---------------------------------------------------------------------------
# Still read-only
# ---------------------------------------------------------------------------
def test_the_new_endpoints_are_all_reads(monkeypatch):
    """
    Four endpoints were added at once. Every one of them must be a query, and
    the allowlist must have grown to match -- a path that is offered as a
    dataset and missing from the allowlist is a dataset that cannot run.
    """
    for path in KEYED_ENDPOINTS:
        assert READ_ENDPOINTS[path].rsplit("/", 1)[1].startswith("query")
    assert set(READ_ENDPOINTS.values()) == set(_ALLOWED_PATHS)


def test_no_write_endpoint_became_reachable():
    for name in ("placeOrder", "updateOrder", "closeOrder", "preShipped",
                 "updatePrintImage"):
        assert f"/trade/api/interface/{name}" not in _ALLOWED_PATHS


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------
def test_identifiers_are_sent_in_batches_the_endpoint_accepts():
    assert list(_batched(list(range(5)), 2)) == [[0, 1], [2, 3], [4]]
    assert list(_batched([], 10)) == []
    assert len(list(_batched(list(range(2370)), 10))) == 237


def test_each_keyed_dataset_names_where_its_identifiers_come_from():
    from app.services.connectors.riin import DATASETS

    keyed = {d.key for d in DATASETS if d.key_source is not None}
    assert keyed == set(KEYED_ENDPOINTS)

    for dataset in DATASETS:
        if dataset.key_source is None:
            continue
        # The supplier documents 100 order ids per request and 10 product
        # codes. Sending more is not refused -- it is silently truncated.
        expected = 10 if dataset.key == "product_ship_addresses" else 100
        assert dataset.key_source.batch_size == expected


def test_asking_with_no_identifiers_is_refused_rather_than_answered_empty():
    """
    This endpoint answers an empty list with an empty list. Passing that
    through would replace a good table with nothing and call the sync a
    success.
    """
    connector = RiinConnector("a-long-enough-secret")
    with pytest.raises(ConnectorError) as raised:
        connector.fetch("order_status", "account", keys=[])
    assert "none were supplied" in str(raised.value)


# ---------------------------------------------------------------------------
# Nested detail becomes rows
# ---------------------------------------------------------------------------
def test_line_statuses_become_rows_not_json():
    """
    Left alone, childOrderStatus lands as JSON text and goodsStatus is a thing
    nobody can filter on.
    """
    result = {"data": [{
        "platformOid": "A1", "orderStatus": 5, "orderStateStr": "In Production",
        "childOrderStatus": [
            {"platformOllId": "A1001", "goodsStatus": "NOT_SHIPPED"},
            {"platformOllId": "A1002", "goodsStatus": "SHIPPED"},
        ],
    }]}
    rows = RiinConnector._exploded(result, EXPLODE["order_status"])

    assert len(rows) == 2
    assert [r["platformOllId"] for r in rows] == ["A1001", "A1002"]
    # The order's own fields are on every line, which is what makes the table
    # usable without a second join.
    assert all(r["platformOid"] == "A1" for r in rows)
    assert all(r["orderStateStr"] == "In Production" for r in rows)
    assert "childOrderStatus" not in rows[0]


def test_an_order_with_no_lines_still_appears():
    """Dropping it would make "nothing there" and "never asked" look the same."""
    result = {"data": [{"platformOid": "A2", "orderStatus": 1,
                        "childOrderStatus": []}]}
    rows = RiinConnector._exploded(result, EXPLODE["order_status"])

    assert len(rows) == 1
    assert rows[0]["platformOid"] == "A2"


def test_a_product_shipping_from_two_factories_is_two_rows():
    result = {"data": [{
        "productCode": "BR104-BLACK-M",
        "addressList": [
            {"addressId": "a1", "city": "Arcadia"},
            {"addressId": "a2", "city": "Middlesex"},
        ],
    }]}
    rows = RiinConnector._exploded(result, EXPLODE["product_ship_addresses"])

    assert [r["city"] for r in rows] == ["Arcadia", "Middlesex"]
    assert all(r["productCode"] == "BR104-BLACK-M" for r in rows)


def test_the_catalogue_datasets_are_untouched_by_any_of_this():
    """They page; they are not keyed; nothing above should have changed them."""
    from app.services.connectors.riin import DATASETS

    for key in ("styles", "colors", "sizes", "products", "ship_addresses"):
        dataset = next(d for d in DATASETS if d.key == key)
        assert dataset.key_source is None
        assert key not in EXPLODE or key == "ship_addresses"

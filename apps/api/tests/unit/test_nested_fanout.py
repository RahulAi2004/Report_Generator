"""
Fan-out correction below the first join.

The other fan-out tests read the generated SQL. That is how this bug survived
them: the SQL had a derived table, a `group by`, and every marker a correct
query has -- and still returned a customer's order count as 6 when the customer
had 5 orders, because one order carried two invoices and got counted once per
invoice inside the derived table.

So these tests execute the query against real rows and check the arithmetic. A
report is right when its numbers are right; the shape of the SQL is only ever
circumstantial evidence.
"""

from __future__ import annotations

import sqlalchemy as sa
import pytest

from app.domain.report.engine import EngineOptions, ReportEngine
from app.domain.report.ir import GroupBy, ReportColumn, ReportDefinition
from app.domain.schema.registry import Aggregation
from tests.fixtures.schema import build_registry


#: One customer, whose figures are all known by hand.
#:
#: Order 1 has two invoices, and the second of those has two payments -- so the
#: chain multiplies at two separate depths. Order 2 has three shipments, which
#: multiplies down a different branch of the same tree.
SEED = {
    "customers": [(1, "Jac Jean")],
    "sales_orders": [(10, 1, 1000.0), (20, 1, 500.0), (30, 1, 25.0)],
    "invoices": [(100, 10, 600.0), (200, 10, 400.0), (300, 20, 500.0)],
    "payments": [(1000, 200, 150.0), (2000, 200, 250.0), (3000, 300, 500.0)],
    "shipments": [(11, 10), (21, 20), (22, 20), (23, 20)],
}

#: Worked out from SEED by hand, not from the engine.
EXPECTED = {
    "orders": 3,            # 10, 20, 30 -- each counted once
    "order_value": 1525.0,  # 1000 + 500 + 25
    "invoiced": 1500.0,     # 600 + 400 + 500
    "paid": 900.0,          # 150 + 250 + 500
    "shipments": 4,         # 1 for order 10, 3 for order 20
}


@pytest.fixture
def connection():
    """An in-memory database holding SEED, shaped like the fixture registry."""
    engine = sa.create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(sa.text(
            "CREATE TABLE customers (customer_id INTEGER, customer_name TEXT)"))
        connection.execute(sa.text(
            "CREATE TABLE sales_orders (order_id INTEGER, customer_id INTEGER, "
            "total_amount REAL)"))
        connection.execute(sa.text(
            "CREATE TABLE invoices (invoice_id INTEGER, order_id INTEGER, "
            "total_amount REAL)"))
        connection.execute(sa.text(
            "CREATE TABLE payments (payment_id INTEGER, invoice_id INTEGER, amount REAL)"))
        connection.execute(sa.text(
            "CREATE TABLE shipments (shipment_id INTEGER, order_id INTEGER)"))

        for row in SEED["customers"]:
            connection.execute(sa.text(
                "INSERT INTO customers VALUES (:a, :b)"), {"a": row[0], "b": row[1]})
        for row in SEED["sales_orders"]:
            connection.execute(sa.text(
                "INSERT INTO sales_orders VALUES (:a, :b, :c)"),
                {"a": row[0], "b": row[1], "c": row[2]})
        for row in SEED["invoices"]:
            connection.execute(sa.text(
                "INSERT INTO invoices VALUES (:a, :b, :c)"),
                {"a": row[0], "b": row[1], "c": row[2]})
        for row in SEED["payments"]:
            connection.execute(sa.text(
                "INSERT INTO payments VALUES (:a, :b, :c)"),
                {"a": row[0], "b": row[1], "c": row[2]})
        for row in SEED["shipments"]:
            connection.execute(sa.text(
                "INSERT INTO shipments VALUES (:a, :b)"), {"a": row[0], "b": row[1]})

    with engine.connect() as connection:
        yield connection


def five_table_report() -> ReportDefinition:
    """Customers, their orders, those orders' invoices, payments and shipments."""
    return ReportDefinition(
        primary_table="customers",
        tables=["customers", "sales_orders", "invoices", "payments", "shipments"],
        columns=[
            ReportColumn(id="c1", table="customers", field="customer_name",
                         display_name="Customer"),
            ReportColumn(id="c2", table="sales_orders", field="order_id",
                         display_name="Orders", aggregation=Aggregation.COUNT),
            ReportColumn(id="c3", table="sales_orders", field="total_amount",
                         display_name="Order Value", aggregation=Aggregation.SUM),
            ReportColumn(id="c4", table="invoices", field="total_amount",
                         display_name="Invoiced", aggregation=Aggregation.SUM),
            ReportColumn(id="c5", table="payments", field="amount",
                         display_name="Paid", aggregation=Aggregation.SUM),
            ReportColumn(id="c6", table="shipments", field="shipment_id",
                         display_name="Shipments", aggregation=Aggregation.COUNT),
        ],
        group_by=[GroupBy(table="customers", field="customer_name")],
    )


def run(connection, definition) -> dict:
    engine = ReportEngine(build_registry(), EngineOptions(max_rows=1000, dialect="sqlite"))
    result = engine.build(definition)
    assert result.ok, [d.message for d in result.diagnostics]

    row = connection.execute(result.compiled.statement).first()
    assert row is not None, "the report returned no rows at all"
    return dict(row._mapping)


def test_a_chain_of_one_to_many_joins_does_not_inflate_any_figure(connection):
    """
    Every figure in a five-table report, against rows counted by hand.

    Before the fix this returned 4 orders and 2125.0 of order value: order 10
    carries two invoices, so it appeared twice inside the derived table and was
    added to the total twice over.
    """
    row = run(connection, five_table_report())
    values = list(row.values())

    assert values[0] == "Jac Jean"
    assert values[1] == EXPECTED["orders"]
    assert values[2] == pytest.approx(EXPECTED["order_value"])
    assert values[3] == pytest.approx(EXPECTED["invoiced"])
    assert values[4] == pytest.approx(EXPECTED["paid"])
    assert values[5] == EXPECTED["shipments"]


def test_the_flat_query_really_would_have_been_wrong(connection):
    """
    Control: with correction switched off the same report inflates.

    This is what proves the numbers above are the correction's doing rather than
    a property of the seed data.
    """
    definition = five_table_report()
    definition.disable_fanout_correction = True

    row = run(connection, definition)
    values = list(row.values())

    assert values[1] > EXPECTED["orders"]
    assert values[2] > EXPECTED["order_value"]


def test_an_average_down_a_chain_is_not_an_average_of_averages(connection):
    """
    AVG has to survive two roll-ups.

    Each level carries its sum and its count separately and only the outermost
    level divides. Dividing at each level would average per-order averages,
    which weights a one-payment order the same as a ten-payment one.
    """
    definition = five_table_report()
    definition.columns = [
        ReportColumn(id="c1", table="customers", field="customer_name",
                     display_name="Customer"),
        ReportColumn(id="c2", table="payments", field="amount",
                     display_name="Average Payment", aggregation=Aggregation.AVG),
    ]

    row = run(connection, definition)
    # 150 + 250 + 500 over three payments.
    assert list(row.values())[1] == pytest.approx(900.0 / 3)

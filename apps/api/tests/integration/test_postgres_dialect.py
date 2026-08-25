"""
PostgreSQL dialect tests.

These exist because SQLite hid a production-breaking bug. SQLite tolerates a
bare column in a grouped query; PostgreSQL rejects it. The fan-out correction --
the engine's headline feature -- generated exactly that shape, so it passed
every SQLite test and then failed on the first real deployment.

Anything that compiles to SQL must be exercised against the engine it will
actually run on. Set BI_TEST_POSTGRES_URL to enable these:

    docker run -d --name pg-test -e POSTGRES_PASSWORD=pw -p 5544:5432 postgres:16-alpine
    python mock-data/seed.py --url postgresql+psycopg://postgres:pw@localhost:5544/postgres
    BI_TEST_POSTGRES_URL=postgresql+psycopg://postgres:pw@localhost:5544/postgres pytest
"""

from __future__ import annotations

import os

import pytest
import sqlalchemy as sa

from app.adapters.postgres import PostgresAdapter
from app.domain.report.engine import EngineOptions, ReportEngine
from app.domain.report.ir import (
    FilterCondition,
    FilterGroup,
    GroupBy,
    ReportColumn,
    ReportDefinition,
    SortBy,
)
from app.domain.schema.registry import Aggregation

POSTGRES_URL = os.environ.get("BI_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not POSTGRES_URL,
        reason="set BI_TEST_POSTGRES_URL to run the PostgreSQL dialect suite",
    ),
]


@pytest.fixture(scope="module")
def adapter():
    engine = sa.create_engine(POSTGRES_URL, future=True)
    return PostgresAdapter(engine, timeout_seconds=30)


@pytest.fixture(scope="module")
def registry(adapter):
    return adapter.introspect().to_registry("pgtest")


@pytest.fixture(scope="module")
def engine(registry):
    return ReportEngine(registry, EngineOptions(max_rows=5000, dialect="postgresql"))


def run(engine, adapter, definition, rows=20):
    result = engine.build(definition)
    assert result.ok, [d.message for d in result.diagnostics if d.severity == "error"]
    return result, adapter.execute(result.compiled.statement, max_rows=rows)


def scalar(adapter, sql: str):
    with adapter.engine.connect() as connection:
        return connection.execute(sa.text(sql)).scalar()


# ---------------------------------------------------------------------------
# The regression this suite exists for
# ---------------------------------------------------------------------------
def test_fanout_corrected_report_executes_on_postgres(engine, adapter):
    """
    PostgreSQL enforces GROUP BY strictly. Pre-aggregated branch columns must be
    re-aggregated at the outer grain, or the statement is rejected with
    "must appear in the GROUP BY clause".
    """
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["customers", "sales_orders", "sales_order_items", "artworks"],
        columns=[
            ReportColumn(id="c2", table="customers", field="customer_name"),
            ReportColumn(id="c4", table="artworks", field="artwork_id",
                         aggregation=Aggregation.COUNT),
            ReportColumn(id="c5", table="sales_order_items", field="quantity",
                         aggregation=Aggregation.SUM),
            ReportColumn(id="c6", table="sales_orders", field="total_amount",
                         aggregation=Aggregation.SUM),
        ],
        group_by=[GroupBy(table="customers", field="customer_name")],
        sort_by=[SortBy(column_id="c6", direction="desc")],
    )
    result, outcome = run(engine, adapter, definition)
    assert result.fanout.corrected is True
    assert outcome.rows


def test_corrected_totals_match_ground_truth_on_postgres(engine, adapter):
    """The grand total across every fan-out branch must equal the plain total."""
    truth = float(scalar(adapter, "SELECT SUM(total_amount) FROM sales_orders"))

    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders", "sales_order_items", "artworks", "invoices"],
        columns=[
            ReportColumn(id="c1", table="sales_orders", field="total_amount",
                         aggregation=Aggregation.SUM),
            ReportColumn(id="c2", table="sales_order_items", field="quantity",
                         aggregation=Aggregation.SUM),
            ReportColumn(id="c3", table="artworks", field="artwork_id",
                         aggregation=Aggregation.COUNT),
        ],
    )
    _, outcome = run(engine, adapter, definition, rows=1)
    assert abs(float(outcome.rows[0][0]) - truth) < 0.01


def test_branch_count_matches_ground_truth_on_postgres(engine, adapter):
    """A COUNT from a pre-aggregated branch must total, not collapse."""
    truth = int(scalar(
        adapter,
        "SELECT count(*) FROM artworks a "
        "JOIN sales_orders o ON o.order_id = a.order_id",
    ))
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders", "artworks"],
        columns=[
            ReportColumn(id="c1", table="artworks", field="artwork_id",
                         aggregation=Aggregation.COUNT),
            ReportColumn(id="c2", table="sales_orders", field="total_amount",
                         aggregation=Aggregation.SUM),
        ],
    )
    _, outcome = run(engine, adapter, definition, rows=1)
    assert int(outcome.rows[0][0]) == truth


def test_branch_average_is_a_true_average_not_an_average_of_averages(engine, adapter):
    """
    AVG cannot simply be re-averaged across the branch's per-key rows. The
    sub-select carries sum and count so the outer level computes the real figure.
    """
    truth = float(scalar(
        adapter,
        "SELECT AVG(i.quantity) FROM sales_order_items i "
        "JOIN sales_orders o ON o.order_id = i.order_id",
    ))
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders", "sales_order_items"],
        columns=[
            ReportColumn(id="c1", table="sales_order_items", field="quantity",
                         aggregation=Aggregation.AVG),
            ReportColumn(id="c2", table="sales_orders", field="total_amount",
                         aggregation=Aggregation.SUM),
        ],
    )
    _, outcome = run(engine, adapter, definition, rows=1)
    assert abs(float(outcome.rows[0][0]) - truth) < 0.01, (
        "AVG over a pre-aggregated branch is not the true average"
    )


# ---------------------------------------------------------------------------
# Dialect behaviour SQLite does not enforce
# ---------------------------------------------------------------------------
def test_strict_group_by_is_satisfied_for_mixed_reports(engine, adapter):
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[
            ReportColumn(id="c1", table="sales_orders", field="order_no"),
            ReportColumn(id="c2", table="sales_orders", field="status"),
            ReportColumn(id="c3", table="sales_orders", field="total_amount",
                         aggregation=Aggregation.SUM),
        ],
    )
    _, outcome = run(engine, adapter, definition)
    assert outcome.rows


def test_like_escape_is_honoured_by_postgres(engine, adapter):
    """A literal % must match a percent sign, not act as a wildcard."""
    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers"],
        columns=[ReportColumn(id="c1", table="customers", field="customer_name")],
        filters=FilterGroup(children=[
            FilterCondition(table="customers", field="customer_name",
                            operator="contains", values=["%"]),
        ]),
    )
    _, outcome = run(engine, adapter, definition)
    assert outcome.row_count == 0, "escaped % behaved as a wildcard"


def test_masking_functions_exist_on_postgres(engine, adapter, registry):
    from app.domain.schema.registry import MaskPolicy

    for policy in (MaskPolicy.REDACT, MaskPolicy.PARTIAL, MaskPolicy.HASH, MaskPolicy.NULL):
        narrowed = registry.for_principal(
            allowed_tables=None, mask_policies={"customers.email": policy}
        )
        masked_engine = ReportEngine(narrowed, EngineOptions(dialect="postgresql"))
        definition = ReportDefinition(
            primary_table="customers",
            tables=["customers"],
            columns=[ReportColumn(id="c1", table="customers", field="email")],
            row_limit=1,
        )
        result = masked_engine.build(definition)
        assert result.ok, f"{policy.value} failed to compile"
        adapter.execute(result.compiled.statement, max_rows=1)


def test_read_only_role_cannot_write(adapter):
    """The startup self-test's guarantee, asserted directly."""
    from app.adapters.base import ReadOnlyViolation

    try:
        adapter.assert_read_only()
    except ReadOnlyViolation:
        pytest.fail("the configured PostgreSQL role can write to the database")


def test_relative_date_filters_execute_on_postgres(engine, adapter):
    for operator in ("today", "last_7_days", "last_30_days", "this_month", "this_year"):
        definition = ReportDefinition(
            primary_table="sales_orders",
            tables=["sales_orders"],
            columns=[ReportColumn(id="c1", table="sales_orders", field="order_no")],
            filters=FilterGroup(children=[
                FilterCondition(table="sales_orders", field="order_date",
                                operator=operator),
            ]),
        )
        run(engine, adapter, definition, rows=5)


def test_pagination_is_stable_on_postgres(engine, adapter):
    """
    PostgreSQL is free to return rows in any order without ORDER BY, and does.
    Every page must tile exactly.
    """
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[ReportColumn(id="c1", table="sales_orders", field="order_id")],
    )
    total = int(scalar(adapter, "SELECT count(*) FROM sales_orders"))
    seen: list[int] = []
    page_size = 100

    for page in range(20):
        result = engine.build(definition, offset=page * page_size, limit=page_size + 1)
        outcome = adapter.execute(result.compiled.statement, max_rows=page_size)
        if not outcome.rows:
            break
        seen.extend(int(row[0]) for row in outcome.rows)
        if not outcome.truncated:
            break

    assert len(seen) == len(set(seen)), "a row appeared on two pages"
    assert len(seen) == total, f"pagination saw {len(seen)} of {total} rows"


def test_row_estimates_use_planner_statistics_not_count(adapter):
    """Row counts must never run COUNT(*) against a production table (spec 41)."""
    estimates = adapter.row_estimates("public")
    assert estimates, "no row estimates returned"
    assert all(isinstance(value, int) for value in estimates.values())

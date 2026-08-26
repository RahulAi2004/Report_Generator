"""
Dashboard to report translation.

The behaviour worth pinning down is not that a dashboard produces a query -- it
is what happens at the edges: a window that means nothing without a date column,
a filter naming a table a card never reads, a comparison period that has to line
up with the period it is compared against.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.dashboard.builder import (
    METRIC_KEY,
    apply_to_report,
    metric_report,
    suggest_date_field,
)
from app.domain.dashboard.ir import (
    DashboardDefinition,
    DashboardFilter,
    DashboardReport,
    DateField,
    MetricCard,
    TimeRange,
)
from app.domain.report.engine import EngineOptions, ReportEngine
from app.domain.report.ir import ReportColumn, ReportDefinition
from app.domain.schema.registry import Aggregation
from tests.fixtures.schema import build_registry

TODAY = date(2026, 8, 26)


@pytest.fixture
def registry():
    return build_registry()


def dashboard(**kwargs) -> DashboardDefinition:
    base = dict(
        time_range=TimeRange(
            preset="daily", mode="last", periods=7,
            date_field=DateField(table="sales_orders", field="order_date"),
        ),
        metrics=[
            MetricCard(id="m1", title="Total Customers", table="customers",
                       field="customer_id", aggregation=Aggregation.COUNT, distinct=True),
        ],
    )
    base.update(kwargs)
    return DashboardDefinition(**base)


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------
def test_last_7_days_is_7_days_including_today():
    """Off-by-one here is an 8-day week that nobody would spot in a total."""
    window = TimeRange(preset="daily", mode="last", periods=7).resolve(TODAY)
    assert window == (date(2026, 8, 20), date(2026, 8, 26))
    assert (window[1] - window[0]).days + 1 == 7


def test_the_comparison_window_is_the_same_length_and_does_not_overlap():
    """
    "vs last 7 days" is only meaningful if the two windows are comparable.

    Equal length, and adjacent rather than overlapping -- an overlap would count
    the same days on both sides of the comparison.
    """
    span = TimeRange(preset="daily", mode="last", periods=7)
    current = span.resolve(TODAY)
    previous = span.previous(TODAY)

    assert previous == (date(2026, 8, 13), date(2026, 8, 19))
    assert (previous[1] - previous[0]).days == (current[1] - current[0]).days
    assert previous[1] < current[0]


def test_all_time_imposes_no_window_at_all():
    """None means unbounded. An empty window would mean the opposite."""
    assert TimeRange(preset="all_time").resolve(TODAY) is None
    assert TimeRange(preset="all_time").previous(TODAY) is None


def test_a_custom_range_entered_backwards_is_read_forwards():
    span = TimeRange(preset="custom", start=date(2026, 8, 26), end=date(2026, 8, 1))
    assert span.resolve(TODAY) == (date(2026, 8, 1), date(2026, 8, 26))


# ---------------------------------------------------------------------------
# Metric cards
# ---------------------------------------------------------------------------
def test_a_metric_card_becomes_one_aggregate_with_no_grouping(registry):
    definition, _ = metric_report(dashboard(), dashboard().metrics[0], registry, TODAY)

    assert len(definition.columns) == 1
    assert definition.columns[0].id == METRIC_KEY
    assert definition.columns[0].aggregation is Aggregation.COUNT_DISTINCT
    assert definition.group_by == []


def test_the_time_range_becomes_a_filter_on_the_chosen_date_column(registry):
    definition, _ = metric_report(dashboard(), dashboard().metrics[0], registry, TODAY)

    dates = [c for c in definition.filters.children
             if getattr(c, "field", None) == "order_date"]
    assert len(dates) == 1
    assert dates[0].operator == "between"
    assert dates[0].values == ["2026-08-20", "2026-08-26"]


def test_a_card_can_opt_out_of_the_time_range(registry):
    """'Total Order Value, all time' next to windowed cards is a real thing to want."""
    board = dashboard()
    board.metrics[0].ignore_time_range = True
    definition, _ = metric_report(board, board.metrics[0], registry, TODAY)

    assert not any(getattr(c, "field", None) == "order_date"
                   for c in definition.filters.children)


def test_the_comparison_report_differs_only_in_its_window(registry):
    """
    Current and previous must be the same question asked of two periods.

    If anything else differed the percentage would be measuring that instead.
    """
    board = dashboard()
    current, _ = metric_report(board, board.metrics[0], registry, TODAY)
    previous, _ = metric_report(
        board, board.metrics[0], registry, TODAY,
        window=board.time_range.previous(TODAY),
    )

    def without_dates(definition):
        return [c for c in definition.filters.children
                if getattr(c, "field", None) != "order_date"]

    assert current.columns == previous.columns
    assert current.tables == previous.tables
    assert without_dates(current) == without_dates(previous)

    current_dates = [c for c in current.filters.children
                     if getattr(c, "field", None) == "order_date"][0]
    previous_dates = [c for c in previous.filters.children
                      if getattr(c, "field", None) == "order_date"][0]
    assert current_dates.values == ["2026-08-20", "2026-08-26"]
    assert previous_dates.values == ["2026-08-13", "2026-08-19"]


# ---------------------------------------------------------------------------
# Dashboard filters
# ---------------------------------------------------------------------------
def test_an_all_filter_narrows_nothing_and_is_not_reported_as_applied(registry):
    """"Status: All" is a control without a value, not a constraint."""
    board = dashboard(filters=[
        DashboardFilter(id="f1", label="Status", table="sales_orders", field="status"),
    ])
    definition, applied = metric_report(board, board.metrics[0], registry, TODAY)

    assert applied.applied == []
    assert applied.not_applicable == []
    assert not any(getattr(c, "field", None) == "status"
                   for c in definition.filters.children)


def test_a_filter_on_another_table_pulls_that_table_in(registry):
    """
    Otherwise "Status: Paid" would mean different things on different cards.

    The card counts customers; the filter is on orders. Joining orders in is
    what makes the card read "customers with a paid order".
    """
    board = dashboard(filters=[
        DashboardFilter(id="f1", label="Status", table="sales_orders",
                        field="status", operator="equals", values=["Paid"]),
    ])
    definition, applied = metric_report(board, board.metrics[0], registry, TODAY)

    assert "sales_orders" in definition.tables
    assert applied.applied == ["Status"]
    assert applied.not_applicable == []


def test_a_filter_that_cannot_reach_a_panel_is_reported_rather_than_dropped(registry):
    """
    The honesty case.

    A chip reading "Status: Paid" over a panel that never filtered by status is
    worse than no chip. The panel says which filters did not reach it.
    """
    saved = ReportDefinition(
        primary_table="customers",
        tables=["customers"],
        columns=[ReportColumn(id="c1", table="customers", field="customer_name")],
    )
    board = dashboard(filters=[
        DashboardFilter(id="f1", label="Order Status", table="sales_orders",
                        field="status", operator="equals", values=["Paid"]),
    ])
    panel = DashboardReport(id="p1", report_id="r1")

    _, applied = apply_to_report(board, panel, saved, TODAY)
    assert applied.applied == []
    assert applied.not_applicable == ["Order Status"]
    # Nor could the window reach it: this report has no order date to measure.
    assert applied.time_range_applied is False


def test_applying_a_dashboard_does_not_modify_the_saved_report(registry):
    """The same report opened from Reports must look as its author saved it."""
    saved = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[ReportColumn(id="c1", table="sales_orders", field="order_no")],
    )
    before = saved.model_dump()

    board = dashboard(filters=[
        DashboardFilter(id="f1", label="Status", table="sales_orders",
                        field="status", operator="equals", values=["Paid"]),
    ])
    apply_to_report(board, DashboardReport(id="p1", report_id="r1"), saved, TODAY)

    assert saved.model_dump() == before


# ---------------------------------------------------------------------------
# The whole thing has to compile
# ---------------------------------------------------------------------------
def test_a_metric_card_compiles_through_the_ordinary_report_engine(registry):
    """
    The point of the translation: dashboards get the report engine's guarantees
    because they use it, rather than a second query path that would have to
    re-earn them.
    """
    board = dashboard(filters=[
        DashboardFilter(id="f1", label="Status", table="sales_orders",
                        field="status", operator="in", values=["Paid", "Shipped"]),
    ])
    definition, _ = metric_report(board, board.metrics[0], registry, TODAY)

    engine = ReportEngine(registry, EngineOptions(max_rows=10))
    result = engine.build(definition)
    assert result.ok, [d.message for d in result.diagnostics]

    sql = engine.render_sql(result.compiled, with_values=True).lower()
    assert "count(distinct" in sql
    # The filter's table is reached by EXISTS rather than a join. That is the
    # engine's own doing and it is the right answer: a customer with two paid
    # orders must still count once.
    assert "exists" in sql
    assert "sales_orders" in sql


def test_date_field_suggestion_prefers_a_real_business_date(registry):
    suggested = suggest_date_field("sales_orders", registry)
    assert suggested == DateField(table="sales_orders", field="order_date")


def test_a_table_with_no_date_column_gets_no_suggestion(registry):
    assert suggest_date_field("nonexistent_table", registry) is None

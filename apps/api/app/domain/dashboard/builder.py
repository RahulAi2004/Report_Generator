"""
Dashboard to report translation.

Nothing here executes anything. It turns a dashboard into ordinary
``ReportDefinition`` documents, which the report engine then compiles exactly as
it compiles a report someone built by hand. Every safety property of that path
-- parameterisation, masking, the read-only guard, fan-out correction -- applies
to dashboards because dashboards *are* reports by the time they reach it.

The one genuinely dashboard-shaped problem is deciding what a filter applies to.
A control labelled "Status: All" that silently misses half the panels is worse
than no control, so a filter is applied where its table is present and *reported
as not applied* where it is not. Nothing is dropped quietly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.domain.dashboard.ir import (
    DashboardDefinition,
    DashboardFilter,
    DashboardReport,
    DateField,
    MetricCard,
    TimeRange,
)
from app.domain.report.ir import (
    FilterCondition,
    FilterGroup,
    ReportColumn,
    ReportDefinition,
)
from app.domain.schema.registry import DataType, SchemaRegistry

#: The single metric column's id, wherever a metric report is built.
METRIC_KEY = "metric"


@dataclass
class Applied:
    """What a panel's filters actually did, so the UI can say so."""

    applied: list[str] = field(default_factory=list)
    #: Filters whose table this panel does not read, listed by label.
    not_applicable: list[str] = field(default_factory=list)
    #: Whether the dashboard's window reached this panel. False means the panel
    #: has no column the window could be measured against, so it is showing all
    #: time while everything around it is showing a period.
    time_range_applied: bool = True

    def as_payload(self) -> dict:
        return {
            "applied": self.applied,
            "not_applicable": self.not_applicable,
            "time_range_applied": self.time_range_applied,
        }


def date_conditions(
    date_field: DateField | None, window: tuple[date, date] | None
) -> list[FilterCondition]:
    """The time range as an ordinary between-filter on a real date column."""
    if date_field is None or window is None:
        return []
    start, end = window
    return [
        FilterCondition(
            table=date_field.table,
            field=date_field.field,
            operator="between",
            values=[start.isoformat(), end.isoformat()],
        )
    ]


def filter_conditions(
    filters: list[DashboardFilter],
    tables: set[str],
    outcome: Applied,
) -> list[FilterCondition]:
    """
    Dashboard filters narrowed to the ones this panel can honour.

    A filter with no value is "All" and narrows nothing, so it is neither
    applied nor reported as inapplicable -- it simply is not a constraint yet.
    """
    conditions: list[FilterCondition] = []
    for item in filters:
        if not item.active:
            continue
        if item.table not in tables:
            outcome.not_applicable.append(item.label)
            continue
        conditions.append(
            FilterCondition(
                table=item.table,
                field=item.field,
                operator=item.operator,
                values=list(item.values),
            )
        )
        outcome.applied.append(item.label)
    return conditions


def _combine(base: FilterGroup, extra: list[FilterCondition]) -> FilterGroup:
    """AND the panel's own filters together with the dashboard's."""
    if not extra:
        return base
    children = list(base.children) + list(extra)
    return FilterGroup(op="and", children=children)


def metric_report(
    dashboard: DashboardDefinition,
    card: MetricCard,
    registry: SchemaRegistry,
    today: date,
    window: tuple[date, date] | None = None,
    ignore_dashboard_filters: bool = False,
) -> tuple[ReportDefinition, Applied]:
    """
    One metric card as a report: a single aggregate, no grouping.

    ``window`` overrides the dashboard's own, which is how the comparison figure
    is produced -- the identical report over the preceding period, so the two
    numbers are comparable by construction rather than by hope.
    """
    outcome = Applied()
    tables = {card.table}

    date_field = card.date_field or dashboard.time_range.date_field
    if date_field is not None:
        tables.add(date_field.table)

    conditions: list[FilterCondition] = []
    if not ignore_dashboard_filters:
        # Filters may name a table the card does not otherwise read; joining it
        # in is what makes "customers on this dashboard" mean the same thing on
        # every card.
        for item in dashboard.filters:
            if item.active:
                tables.add(item.table)
        conditions += filter_conditions(dashboard.filters, tables, outcome)

    if not card.ignore_time_range:
        if window is None:
            window = dashboard.time_range.resolve(today)
        conditions += date_conditions(date_field, window)

    definition = ReportDefinition(
        primary_table=card.table,
        tables=sorted(tables),
        columns=[
            ReportColumn(
                id=METRIC_KEY,
                table=card.table,
                field=card.field,
                display_name=card.title,
                aggregation=card.effective_aggregation,
            )
        ],
        filters=_combine(card.filters, conditions),
        row_limit=1,
    )
    return definition, outcome


def apply_to_report(
    dashboard: DashboardDefinition,
    panel: DashboardReport,
    definition: ReportDefinition,
    today: date,
) -> tuple[ReportDefinition, Applied]:
    """
    A saved report as it appears on this dashboard.

    The saved report is not modified: this returns a copy carrying the
    dashboard's filters, so opening the same report from the Reports section
    still shows the report as its author saved it.
    """
    outcome = Applied()
    copy = definition.model_copy(deep=True)
    tables = set(copy.tables)

    conditions: list[FilterCondition] = []
    if not panel.ignore_dashboard_filters:
        conditions += filter_conditions(dashboard.filters, tables, outcome)

    if not panel.ignore_time_range:
        date_field = panel.date_field or dashboard.time_range.date_field
        if date_field is not None and date_field.table in tables:
            conditions += date_conditions(date_field, dashboard.time_range.resolve(today))
        elif dashboard.time_range.applies:
            # No column here to measure the window against. Saying so is the
            # whole point: this panel is showing all time next to panels that
            # are not.
            outcome.time_range_applied = False

    copy.filters = _combine(copy.filters, conditions)
    copy.row_limit = panel.page_size
    return copy, outcome


# ---------------------------------------------------------------------------
def suggest_date_field(table: str, registry: SchemaRegistry) -> DateField | None:
    """
    A sensible column for the time range to measure against.

    Guessing is acceptable here only because the choice is shown in the builder
    and can be changed; a time range silently measured against the wrong column
    would not be.
    """
    meta = registry.table(table)
    if meta is None:
        return None

    dated = [
        column for column in meta.columns
        if column.data_type in (DataType.DATE, DataType.DATETIME)
    ]
    if not dated:
        return None

    preferred = ("order_date", "invoice_date", "date", "placed_at", "issued_at",
                 "paid_at", "added_at", "created_at", "created_on")
    for name in preferred:
        for column in dated:
            if column.name.lower() == name:
                return DateField(table=table, field=column.name)
    return DateField(table=table, field=dated[0].name)

"""
Dashboard Intermediate Representation.

A dashboard is saved as this document and nothing else -- no SQL, for the same
reason reports are not (spec 16). Every panel of the builder edits one branch of
it, and the whole thing is re-derived into ordinary report definitions before a
single query runs.

That derivation is the point. A metric card is a report with one aggregated
column and no grouping; an embedded report is a report. So dashboards inherit
masking, the read-only guard, join planning and fan-out correction from the
report engine rather than reimplementing any of it, and there is one query path
in this product rather than two.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.report.ir import FilterGroup
from app.domain.schema.registry import Aggregation


class IRBase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------------------
# Time range
# ---------------------------------------------------------------------------
#: The granularity tabs. Each names the unit a window is counted in.
Preset = Literal["daily", "weekly", "monthly", "quarterly", "yearly", "all_time", "custom"]

#: Which side of today the window sits on.
Mode = Literal["last", "this", "previous"]

_UNIT_DAYS: dict[str, int] = {
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
    "quarterly": 91,
    "yearly": 365,
}

#: How many periods the window dropdown offers, per granularity.
PERIOD_CHOICES: dict[str, tuple[int, ...]] = {
    "daily": (1, 7, 14, 30, 60, 90),
    "weekly": (1, 2, 4, 8, 12, 26),
    "monthly": (1, 3, 6, 12, 24),
    "quarterly": (1, 2, 4, 8),
    "yearly": (1, 2, 3, 5),
}


class DateField(IRBase):
    """The column a time range is measured against."""

    table: str
    field: str


class TimeRange(IRBase):
    """
    The dashboard's window, in business terms rather than dates.

    Stored relative ("last 7 days") rather than resolved, so a dashboard opened
    next month reports next month -- a saved absolute range would quietly go
    stale and keep looking current.
    """

    preset: Preset = "daily"
    mode: Mode = "last"
    periods: int = Field(default=7, ge=1, le=400)
    start: date | None = None
    end: date | None = None
    #: What the range is measured against. A card may override it.
    date_field: DateField | None = None

    @model_validator(mode="after")
    def _custom_needs_dates(self) -> "TimeRange":
        if self.preset == "custom" and self.start and self.end and self.end < self.start:
            self.start, self.end = self.end, self.start
        return self

    @property
    def applies(self) -> bool:
        """Whether this range restricts anything at all."""
        return self.preset != "all_time"

    def label(self) -> str:
        if self.preset == "all_time":
            return "All Time"
        if self.preset == "custom":
            if self.start and self.end:
                return f"{self.start.isoformat()} to {self.end.isoformat()}"
            return "Custom"
        unit = {"daily": "Day", "weekly": "Week", "monthly": "Month",
                "quarterly": "Quarter", "yearly": "Year"}[self.preset]
        plural = unit if self.periods == 1 else f"{unit}s"
        prefix = {"last": "Last", "this": "This", "previous": "Previous"}[self.mode]
        if self.mode == "this":
            return f"This {unit}"
        return f"{prefix} {self.periods} {plural}"

    def resolve(self, today: date) -> tuple[date, date] | None:
        """
        The window as two dates, inclusive.

        ``None`` means the range imposes no bound at all, which is different
        from an empty window and must not be confused with one.
        """
        if self.preset == "all_time":
            return None
        if self.preset == "custom":
            if self.start is None or self.end is None:
                return None
            return self.start, self.end

        span = _UNIT_DAYS[self.preset] * self.periods
        match self.mode:
            case "this":
                return today - timedelta(days=_UNIT_DAYS[self.preset] - 1), today
            case "previous":
                end = today - timedelta(days=span)
                return end - timedelta(days=span - 1), end
            case _:  # "last"
                return today - timedelta(days=span - 1), today

    def previous(self, today: date) -> tuple[date, date] | None:
        """
        The window immediately before this one, of the same length.

        This is what "vs last 7 days" compares against. Without it a delta is
        being measured against nothing in particular.
        """
        window = self.resolve(today)
        if window is None:
            return None
        start, end = window
        span = (end - start).days + 1
        return start - timedelta(days=span), start - timedelta(days=1)


# ---------------------------------------------------------------------------
# Metric cards
# ---------------------------------------------------------------------------
#: How a metric's number is rendered. The value itself is never formatted
#: server-side -- the client formats, so the raw figure stays available.
MetricFormat = Literal["number", "currency", "percent", "duration"]

#: The small caption under a metric. Each is computed, never typed in, so it
#: cannot drift away from the number above it.
Comparison = Literal["none", "previous_period", "share_of_total"]


class MetricCard(IRBase):
    """One number on the strip across the top."""

    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    table: str
    field: str
    aggregation: Aggregation = Aggregation.COUNT
    #: COUNT(DISTINCT x) rather than COUNT(x). The screenshot's customer counts
    #: are distinct counts, and the difference is the whole number.
    distinct: bool = False

    #: Card-level filters, on top of the dashboard's own.
    filters: FilterGroup = Field(default_factory=FilterGroup)

    #: Overrides the dashboard's date field for this card only.
    date_field: DateField | None = None
    #: Opt out of the time range entirely -- "Total Order Value, all time"
    #: sitting next to windowed cards is a real thing to want.
    ignore_time_range: bool = False

    comparison: Comparison = "none"
    format: MetricFormat = "number"
    currency: str = Field(default="EUR", max_length=8)
    decimals: int = Field(default=0, ge=0, le=6)
    icon: str = Field(default="hash", max_length=32)
    tone: Literal["blue", "green", "amber", "violet", "rose", "slate"] = "blue"
    #: Higher is better. Decides whether an increase is shown as good or bad;
    #: for refunds or churn an increase is not a win.
    higher_is_better: bool = True

    @property
    def effective_aggregation(self) -> Aggregation:
        if self.distinct and self.aggregation is Aggregation.COUNT:
            return Aggregation.COUNT_DISTINCT
        return self.aggregation


# ---------------------------------------------------------------------------
# Dashboard-level filters
# ---------------------------------------------------------------------------
#: How the filter is presented. The control does not change the query, only how
#: a value is chosen for it.
Control = Literal["select", "multi_select", "text", "date", "date_range", "boolean"]


class DashboardFilter(IRBase):
    """
    A control in the right-hand panel that narrows everything below it.

    Its value is applied to every card and report whose tables include the
    filter's table. Where a panel does not include that table the filter cannot
    apply, and the dashboard says so rather than appearing to have filtered it.
    """

    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    table: str
    field: str
    control: Control = "select"
    operator: str = "equals"
    #: Empty means "All" -- the filter is present but not narrowing anything.
    values: list[object] = Field(default_factory=list)
    #: Offered in the dropdown. Empty means: read the column's distinct values.
    choices: list[str] = Field(default_factory=list)
    required: bool = False

    @property
    def active(self) -> bool:
        return bool(self.values)


# ---------------------------------------------------------------------------
# Embedded reports
# ---------------------------------------------------------------------------
class DashboardReport(IRBase):
    """A saved report shown on the dashboard, with the dashboard's filters applied."""

    id: str = Field(min_length=1, max_length=64)
    #: The saved report this panel shows.
    report_id: str
    title: str | None = Field(default=None, max_length=190)
    #: Column keys to show, in order. Empty means every column the report has.
    columns: list[str] = Field(default_factory=list)
    page_size: int = Field(default=10, ge=1, le=200)
    date_field: DateField | None = None
    ignore_time_range: bool = False
    ignore_dashboard_filters: bool = False


# ---------------------------------------------------------------------------
class DashboardSettings(IRBase):
    show_time_range: bool = True
    show_refresh: bool = True
    allow_export: bool = True
    allow_viewers_to_save: bool = False


class DashboardDefinition(IRBase):
    version: int = 1
    #: Placement, using the same taxonomy reports file into.
    app: str | None = Field(default=None, max_length=80)
    module: str | None = Field(default=None, max_length=80)

    time_range: TimeRange = Field(default_factory=TimeRange)
    metrics: list[MetricCard] = Field(default_factory=list)
    filters: list[DashboardFilter] = Field(default_factory=list)
    reports: list[DashboardReport] = Field(default_factory=list)
    settings: DashboardSettings = Field(default_factory=DashboardSettings)

    def summary(self) -> dict[str, int]:
        return {
            "metrics": len(self.metrics),
            "filters": len(self.filters),
            "active_filters": sum(1 for f in self.filters if f.active),
            "reports": len(self.reports),
        }

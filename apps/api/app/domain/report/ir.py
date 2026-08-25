"""
Report Intermediate Representation.

This is what a saved report *is* (spec 16: "Do NOT save only generated SQL").
Every panel in the report builder UI is a view over one branch of this document,
and it is the only thing the compiler accepts -- including from the AI layer,
which emits IR rather than SQL (ARCHITECTURE.md, section G).

Nothing here is trusted: field names are plain strings until the resolver checks
them against the schema registry.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.schema.registry import Aggregation, JoinType


class IRBase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
class FilterOperator:
    """Operator catalogue, grouped by the field type it applies to (spec 11)."""

    TEXT = ("equals", "not_equals", "contains", "not_contains", "starts_with",
            "ends_with", "in", "not_in", "is_empty", "is_not_empty")
    NUMBER = ("equals", "not_equals", "greater_than", "greater_or_equal",
              "less_than", "less_or_equal", "between", "in", "not_in")
    DATE = ("on", "before", "after", "between", "today", "yesterday", "this_week",
            "this_month", "this_year", "last_7_days", "last_30_days",
            "last_n_days", "year_to_date")
    BOOLEAN = ("is_true", "is_false")
    NULL = ("is_null", "is_not_null")

    #: Operators that take no operand at all.
    NO_VALUE = frozenset({
        "is_null", "is_not_null", "is_empty", "is_not_empty", "is_true", "is_false",
        "today", "yesterday", "this_week", "this_month", "this_year",
        "last_7_days", "last_30_days", "year_to_date",
    })
    #: Operators that require exactly two operands.
    TWO_VALUES = frozenset({"between"})
    #: Operators that take a list.
    LIST_VALUES = frozenset({"in", "not_in"})


class ParameterSpec(IRBase):
    """A filter value requested at run time (spec 11: 'Ask for values when running')."""

    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,62}$")
    prompt: str
    required: bool = False
    default: object | None = None


class FilterCondition(IRBase):
    kind: Literal["condition"] = "condition"
    id: str | None = None
    table: str
    field: str
    operator: str
    values: list[object] = Field(default_factory=list)
    #: When set, ``values`` acts as the default and the real value is supplied
    #: at run time. Values always become bound parameters, never SQL text.
    parameter: ParameterSpec | None = None

    @model_validator(mode="after")
    def _check_arity(self) -> "FilterCondition":
        operator = self.operator
        count = len(self.values)
        if operator in FilterOperator.NO_VALUE and count:
            raise ValueError(f"operator '{operator}' takes no values")
        if operator in FilterOperator.TWO_VALUES and count != 2 and self.parameter is None:
            raise ValueError(f"operator '{operator}' requires exactly 2 values")
        if operator in FilterOperator.LIST_VALUES and not count and self.parameter is None:
            raise ValueError(f"operator '{operator}' requires at least one value")
        if (
            operator not in FilterOperator.NO_VALUE
            and operator not in FilterOperator.TWO_VALUES
            and operator not in FilterOperator.LIST_VALUES
            and count != 1
            and self.parameter is None
        ):
            raise ValueError(f"operator '{operator}' requires exactly 1 value")
        return self


class FilterGroup(IRBase):
    """AND/OR nesting (spec 11). The screenshot's flat list is the depth-1 case."""

    kind: Literal["group"] = "group"
    id: str | None = None
    op: Literal["and", "or"] = "and"
    children: list["FilterNode"] = Field(default_factory=list)


FilterNode = Annotated[Union[FilterCondition, FilterGroup], Field(discriminator="kind")]
FilterGroup.model_rebuild()


# ---------------------------------------------------------------------------
# Columns, joins, grouping, sorting
# ---------------------------------------------------------------------------
class ColumnFormat(IRBase):
    kind: Literal["text", "number", "currency", "percent", "date", "datetime", "boolean"] = "text"
    decimals: int = Field(default=2, ge=0, le=10)
    currency: str = "USD"
    thousands_separator: bool = True
    date_pattern: str = "MMM d, yyyy"
    null_display: str = "-"
    prefix: str = ""
    suffix: str = ""


class ConditionalFormat(IRBase):
    operator: str
    value: object
    text_color: str | None = None
    background_color: str | None = None
    bold: bool = False


class ReportColumn(IRBase):
    id: str
    table: str
    field: str
    display_name: str | None = None
    aggregation: Aggregation = Aggregation.NONE
    format: ColumnFormat | None = None
    align: Literal["left", "center", "right"] | None = None
    width: int | None = Field(default=None, ge=40, le=800)
    visible: bool = True
    conditional_formats: list[ConditionalFormat] = Field(default_factory=list)


class CalculatedColumn(IRBase):
    """
    A safe calculated field (spec 46).

    ``expression`` is parsed by an allowlisted grammar and resolved against real
    columns -- it is never passed through to SQL as text.
    """

    id: str
    display_name: str
    expression: str = Field(max_length=1000)
    format: ColumnFormat | None = None
    align: Literal["left", "center", "right"] | None = None
    visible: bool = True


class ReportJoin(IRBase):
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    join_type: JoinType = JoinType.LEFT
    relationship_id: str | None = None


class GroupBy(IRBase):
    table: str
    field: str


class SortBy(IRBase):
    column_id: str
    direction: Literal["asc", "desc"] = "asc"


class Visualization(IRBase):
    type: Literal["table", "kpi", "bar", "line", "pie", "donut", "area"] = "table"
    dimension_column_id: str | None = None
    metric_column_id: str | None = None
    stacked: bool = False


# ---------------------------------------------------------------------------
# The report definition
# ---------------------------------------------------------------------------
class ReportDefinition(IRBase):
    version: int = 1
    connection_id: str | None = None
    primary_table: str
    tables: list[str] = Field(default_factory=list)
    joins: list[ReportJoin] = Field(default_factory=list)
    columns: list[ReportColumn] = Field(default_factory=list)
    calculated_columns: list[CalculatedColumn] = Field(default_factory=list)
    filters: FilterGroup = Field(default_factory=FilterGroup)
    group_by: list[GroupBy] = Field(default_factory=list)
    sort_by: list[SortBy] = Field(default_factory=list)
    visualization: Visualization = Field(default_factory=Visualization)
    row_limit: int = Field(default=50, ge=1, le=100_000)
    #: Opt out of automatic fan-out correction. Off by default: correctness wins.
    disable_fanout_correction: bool = False

    @model_validator(mode="after")
    def _primary_table_is_selected(self) -> "ReportDefinition":
        if self.primary_table and self.primary_table not in self.tables:
            self.tables = [self.primary_table, *self.tables]
        return self

    # -- derived counters shown on the workflow strip (spec 5.1) -----------
    @property
    def filter_count(self) -> int:
        def count(node) -> int:
            if isinstance(node, FilterGroup):
                return sum(count(child) for child in node.children)
            return 1
        return count(self.filters)

    def summary(self) -> dict[str, int]:
        return {
            "data_sources": len(self.tables),
            "fields_selected": len(self.columns) + len(self.calculated_columns),
            "relationships": len(self.joins),
            "filters": self.filter_count,
            "grouping": len(self.group_by),
            "sorting": len(self.sort_by),
        }

    def parameters(self) -> list[ParameterSpec]:
        found: list[ParameterSpec] = []

        def walk(node) -> None:
            if isinstance(node, FilterGroup):
                for child in node.children:
                    walk(child)
            elif node.parameter is not None:
                found.append(node.parameter)

        walk(self.filters)
        return found

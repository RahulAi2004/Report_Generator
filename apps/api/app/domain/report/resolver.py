"""
Resolution: untrusted IR strings -> typed metadata objects.

This is the boundary where user input stops being text. After this stage every
table and column in the pipeline is a registry object, so the compiler has no
way to emit an identifier the user was not permitted to see.

The registry passed in is already RBAC-narrowed, which is why "unknown column"
and "not permitted" are the same failure here: an unauthorized column simply
does not exist as far as the rest of the engine is concerned.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.report.diagnostics import Code, DiagnosticCollector
from app.domain.report.ir import (
    ColumnFormat,
    FilterCondition,
    FilterGroup,
    FilterOperator,
    ReportColumn,
    ReportDefinition,
)
from app.domain.schema.registry import (
    Aggregation,
    ColumnMeta,
    DataType,
    MaskPolicy,
    SchemaRegistry,
)


@dataclass(slots=True)
class ResolvedColumn:
    id: str
    table: str
    field: str
    meta: ColumnMeta
    aggregation: Aggregation
    display_name: str
    format: ColumnFormat
    align: str
    visible: bool
    conditional_formats: list = field(default_factory=list)
    #: Result-set key, assigned by the resolver and guaranteed unique across the
    #: report. Rows are returned to the UI as a dictionary keyed by this value,
    #: so a collision would make one column silently overwrite another.
    key: str = ""

    @property
    def output_key(self) -> str:
        return self.key or self.natural_key

    @property
    def natural_key(self) -> str:
        if self.aggregation is Aggregation.NONE:
            return f"{self.table}__{self.field}"
        return f"{self.table}__{self.field}__{self.aggregation.value}"

    @property
    def is_aggregate(self) -> bool:
        return self.aggregation is not Aggregation.NONE

    @property
    def is_masked(self) -> bool:
        return self.meta.mask_policy is not MaskPolicy.NONE


@dataclass(slots=True)
class ResolvedCondition:
    table: str
    field: str
    meta: ColumnMeta
    operator: str
    values: list
    parameter_name: str | None = None


@dataclass(slots=True)
class ResolvedGroup:
    op: str
    children: list


@dataclass(slots=True)
class ResolvedGroupBy:
    table: str
    field: str
    meta: ColumnMeta


@dataclass(slots=True)
class ResolvedSort:
    column: ResolvedColumn
    direction: str


@dataclass
class ResolvedReport:
    definition: ReportDefinition
    registry: SchemaRegistry
    columns: list[ResolvedColumn] = field(default_factory=list)
    filters: ResolvedGroup | None = None
    group_by: list[ResolvedGroupBy] = field(default_factory=list)
    sort_by: list[ResolvedSort] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)

    @property
    def has_aggregates(self) -> bool:
        return any(column.is_aggregate for column in self.columns)

    def filter_tables(self) -> set[str]:
        found: set[str] = set()

        def walk(node) -> None:
            if isinstance(node, ResolvedGroup):
                for child in node.children:
                    walk(child)
            else:
                found.add(node.table)

        if self.filters:
            walk(self.filters)
        return found


#: Which operator family applies to a normalized type.
_OPERATORS_BY_TYPE: dict[DataType, tuple[str, ...]] = {
    DataType.TEXT: FilterOperator.TEXT,
    DataType.UUID: FilterOperator.TEXT,
    DataType.INTEGER: FilterOperator.NUMBER,
    DataType.DECIMAL: FilterOperator.NUMBER,
    DataType.DATE: FilterOperator.DATE,
    DataType.DATETIME: FilterOperator.DATE,
    DataType.TIME: FilterOperator.DATE,
    DataType.BOOLEAN: FilterOperator.BOOLEAN,
    DataType.JSON: FilterOperator.TEXT,
    DataType.BINARY: (),
    DataType.UNKNOWN: FilterOperator.TEXT,
}


def legal_operators(data_type: DataType) -> tuple[str, ...]:
    return tuple(_OPERATORS_BY_TYPE.get(data_type, ())) + tuple(FilterOperator.NULL)


class Resolver:
    def __init__(self, registry: SchemaRegistry) -> None:
        self.registry = registry

    def resolve(
        self, definition: ReportDefinition, diagnostics: DiagnosticCollector
    ) -> ResolvedReport:
        resolved = ResolvedReport(definition=definition, registry=self.registry)

        resolved.tables = self._resolve_tables(definition, diagnostics)
        resolved.columns = self._resolve_columns(definition, diagnostics)
        resolved.filters = self._resolve_filters(definition.filters, diagnostics)
        resolved.group_by = self._resolve_group_by(definition, diagnostics)
        resolved.sort_by = self._resolve_sort(definition, resolved.columns, diagnostics)

        if not resolved.columns:
            diagnostics.error(
                Code.NO_COLUMNS,
                "This report has no columns yet. Pick a table on the left, then tick the "
                "fields you want to see.",
                section="columns",
            )
        return resolved

    # ------------------------------------------------------------------
    def _resolve_tables(
        self, definition: ReportDefinition, diagnostics: DiagnosticCollector
    ) -> list[str]:
        resolved: list[str] = []
        for name in dict.fromkeys([definition.primary_table, *definition.tables]):
            table = self.registry.table(name)
            if table is None:
                diagnostics.error(
                    Code.UNKNOWN_TABLE,
                    f"The table '{name}' is not available. It may have been removed from the "
                    "database, disabled for reporting, or you may not have access to it.",
                    section="tables",
                    target=name,
                )
                continue
            resolved.append(table.name)
        return resolved

    def _resolve_columns(
        self, definition: ReportDefinition, diagnostics: DiagnosticCollector
    ) -> list[ResolvedColumn]:
        resolved: list[ResolvedColumn] = []
        seen: set[str] = set()

        for spec in definition.columns:
            meta = self._lookup(spec.table, spec.field, diagnostics, section="columns",
                                target=spec.id)
            if meta is None:
                continue

            aggregation = spec.aggregation
            if aggregation not in meta.legal_aggregations:
                legal = ", ".join(a.value.upper() for a in meta.legal_aggregations
                                  if a is not Aggregation.NONE) or "none"
                diagnostics.error(
                    Code.INVALID_AGGREGATION,
                    f"{aggregation.value.upper()} cannot be applied to "
                    f"{meta.label} because it is a {meta.data_type.value} field. "
                    f"Available options: {legal}.",
                    section="columns",
                    target=spec.id,
                    fix={"action": "set_aggregation", "column_id": spec.id, "value": "none"},
                )
                continue

            key = f"{meta.qualified}:{aggregation.value}"
            if key in seen:
                diagnostics.warn(
                    Code.DUPLICATE_COLUMN,
                    f"{meta.label} appears more than once with the same aggregation.",
                    section="columns",
                    target=spec.id,
                )
            seen.add(key)

            if meta.mask_policy is not MaskPolicy.NONE:
                # SUM and AVG over masked data would compute on the real values
                # and publish the result, defeating the mask. MIN and MAX are
                # allowed because the compiler masks the value they return;
                # counting reveals nothing about the values themselves.
                if aggregation in (Aggregation.SUM, Aggregation.AVG):
                    diagnostics.error(
                        Code.MASKED_AGGREGATION,
                        f"{meta.label} is masked by a data policy, so it cannot be "
                        f"totalled or averaged. Use COUNT instead, or ask an "
                        f"administrator to grant access to this field.",
                        section="columns",
                        target=spec.id,
                        fix={"action": "set_aggregation", "column_id": spec.id,
                             "value": "count"},
                    )
                    continue
                diagnostics.info(
                    Code.MASKED_COLUMN,
                    f"{meta.label} contains sensitive data and will be shown masked.",
                    section="columns",
                    target=spec.id,
                )

            resolved.append(
                ResolvedColumn(
                    id=spec.id,
                    table=meta.table,
                    field=meta.name,
                    meta=meta,
                    aggregation=aggregation,
                    display_name=spec.display_name or meta.label,
                    format=spec.format or default_format(meta, aggregation),
                    align=spec.align or default_alignment(meta, aggregation),
                    visible=spec.visible,
                    conditional_formats=list(spec.conditional_formats),
                )
            )

        _assign_unique_keys(resolved)
        return resolved

    def _resolve_filters(
        self, node, diagnostics: DiagnosticCollector
    ) -> ResolvedGroup | None:
        if isinstance(node, FilterGroup):
            children = [
                resolved
                for child in node.children
                if (resolved := self._resolve_filters(child, diagnostics)) is not None
            ]
            return ResolvedGroup(op=node.op, children=children)

        condition: FilterCondition = node
        meta = self._lookup(
            condition.table, condition.field, diagnostics, section="filters",
            target=condition.id,
        )
        if meta is None:
            return None

        allowed = legal_operators(meta.data_type)
        if condition.operator not in allowed:
            diagnostics.error(
                Code.OPERATOR_TYPE_MISMATCH,
                f"'{condition.operator.replace('_', ' ')}' cannot be used with "
                f"{meta.label} ({meta.data_type.value}).",
                section="filters",
                target=condition.id,
            )
            return None

        return ResolvedCondition(
            table=meta.table,
            field=meta.name,
            meta=meta,
            operator=condition.operator,
            values=list(condition.values),
            parameter_name=condition.parameter.name if condition.parameter else None,
        )

    def _resolve_group_by(
        self, definition: ReportDefinition, diagnostics: DiagnosticCollector
    ) -> list[ResolvedGroupBy]:
        resolved: list[ResolvedGroupBy] = []
        for spec in definition.group_by:
            meta = self._lookup(spec.table, spec.field, diagnostics, section="group_by",
                                target=f"{spec.table}.{spec.field}")
            if meta is not None:
                resolved.append(ResolvedGroupBy(meta.table, meta.name, meta))
        return resolved

    def _resolve_sort(
        self,
        definition: ReportDefinition,
        columns: list[ResolvedColumn],
        diagnostics: DiagnosticCollector,
    ) -> list[ResolvedSort]:
        by_id = {column.id: column for column in columns}
        resolved: list[ResolvedSort] = []
        for spec in definition.sort_by:
            column = by_id.get(spec.column_id)
            if column is None:
                diagnostics.warn(
                    Code.SORT_NOT_IN_PROJECTION,
                    "A sort rule refers to a column that is no longer in this report; "
                    "it has been ignored.",
                    section="sort_by",
                    target=spec.column_id,
                    fix={"action": "remove_sort", "column_id": spec.column_id},
                )
                continue
            resolved.append(ResolvedSort(column=column, direction=spec.direction))
        return resolved

    # ------------------------------------------------------------------
    def _lookup(
        self,
        table: str,
        column: str,
        diagnostics: DiagnosticCollector,
        section: str,
        target: str | None,
    ) -> ColumnMeta | None:
        found_table = self.registry.table(table)
        if found_table is None:
            diagnostics.error(
                Code.UNKNOWN_TABLE,
                f"The table '{table}' is not available to you.",
                section=section,
                target=target,
            )
            return None
        found_column = found_table.column(column)
        if found_column is None:
            diagnostics.error(
                Code.UNKNOWN_COLUMN,
                f"'{column}' is not a field of {found_table.label}. It may have been "
                "renamed or removed, or you may not have access to it.",
                section=section,
                target=target,
            )
            return None
        return found_column


def _assign_unique_keys(columns: list[ResolvedColumn]) -> None:
    """
    Give every column a distinct result-set key.

    Two report columns can legitimately point at the same field with the same
    aggregation -- the same total shown twice with different formatting, for
    instance. They must not share a key, or the row dictionary sent to the UI
    keeps only the last one and a column silently renders another's values.
    """
    used: set[str] = set()
    for column in columns:
        key = column.natural_key
        if key in used:
            suffix = 2
            while f"{key}__{suffix}" in used:
                suffix += 1
            key = f"{key}__{suffix}"
        used.add(key)
        column.key = key


# ---------------------------------------------------------------------------
# Presentation defaults (spec 9). Chosen from the column's type so a new column
# looks right immediately instead of defaulting to raw text.
# ---------------------------------------------------------------------------
_CURRENCY_HINTS = ("amount", "total", "price", "cost", "value", "balance", "paid", "due",
                   "revenue", "subtotal", "discount", "fee", "charge")


def default_format(meta: ColumnMeta, aggregation: Aggregation) -> ColumnFormat:
    if meta.default_format:
        return ColumnFormat(**meta.default_format)

    if aggregation in (Aggregation.COUNT, Aggregation.COUNT_DISTINCT):
        return ColumnFormat(kind="number", decimals=0)

    match meta.data_type:
        case DataType.DECIMAL:
            if any(hint in meta.name.lower() for hint in _CURRENCY_HINTS):
                return ColumnFormat(kind="currency", decimals=2)
            return ColumnFormat(kind="number", decimals=2)
        case DataType.INTEGER:
            return ColumnFormat(kind="number", decimals=0)
        case DataType.DATE:
            return ColumnFormat(kind="date")
        case DataType.DATETIME:
            return ColumnFormat(kind="datetime")
        case DataType.BOOLEAN:
            return ColumnFormat(kind="boolean")
        case _:
            return ColumnFormat(kind="text")


def default_alignment(meta: ColumnMeta, aggregation: Aggregation) -> str:
    if aggregation in (Aggregation.COUNT, Aggregation.COUNT_DISTINCT, Aggregation.SUM,
                       Aggregation.AVG):
        return "right"
    if meta.data_type.is_numeric:
        return "right"
    return "left"

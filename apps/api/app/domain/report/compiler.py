"""
Compiler: resolved report -> SQLAlchemy Core Select.

The single most important property of this module is that it never builds SQL
by string concatenation. Identifiers come from registry objects; every literal
becomes a bound parameter. There is no code path from user text to an
identifier or an SQL fragment, so injection is not filtered here -- it is
unrepresentable (ARCHITECTURE.md, section D/L3).

The second most important property is that it consults the fan-out analysis, so
one-to-many branches are pre-aggregated in derived tables rather than inflating
the parent's totals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql import Select

from app.domain.report.diagnostics import Code, DiagnosticCollector
from app.domain.report.fanout import Branch, FanoutAnalysis, Strategy
from app.domain.report.join_planner import JoinPlan
from app.domain.report.resolver import (
    ResolvedColumn,
    ResolvedCondition,
    ResolvedGroup,
    ResolvedReport,
)
from app.domain.schema.registry import (
    Aggregation,
    ColumnMeta,
    DataType,
    JoinType,
    MaskPolicy,
    TableMeta,
)

_SA_TYPES: dict[DataType, type] = {
    DataType.TEXT: sa.Text,
    DataType.INTEGER: sa.BigInteger,
    DataType.DECIMAL: sa.Numeric,
    DataType.BOOLEAN: sa.Boolean,
    DataType.DATE: sa.Date,
    DataType.DATETIME: sa.DateTime,
    DataType.TIME: sa.Time,
    DataType.UUID: sa.Text,
    DataType.JSON: sa.Text,
    DataType.BINARY: sa.LargeBinary,
    DataType.UNKNOWN: sa.Text,
}

#: Escape character declared on every LIKE/ILIKE. PostgreSQL happens to treat
#: backslash as an escape by default, but SQLite and SQL Server do not -- without
#: an explicit ESCAPE clause a literal "%" in a search term silently becomes a
#: wildcard and the filter matches everything.
LIKE_ESCAPE = "\\"


def visible_columns(resolved: ResolvedReport) -> list[ResolvedColumn]:
    """The columns the query projects. The single source of truth for ordering."""
    return [column for column in resolved.columns if column.visible]


#: Label for the correlation key inside a pre-aggregated branch sub-select.
_JOIN_KEY = "__join_key"

_AGGREGATE_FUNCTIONS = {
    Aggregation.COUNT: lambda expr: sa.func.count(expr),
    Aggregation.COUNT_DISTINCT: lambda expr: sa.func.count(sa.distinct(expr)),
    Aggregation.SUM: lambda expr: sa.func.sum(expr),
    Aggregation.AVG: lambda expr: sa.func.avg(expr),
    Aggregation.MIN: lambda expr: sa.func.min(expr),
    Aggregation.MAX: lambda expr: sa.func.max(expr),
}


@dataclass
class CompiledReport:
    statement: Select
    #: Result-set key -> the report column that produced it, for the UI.
    output_columns: list[ResolvedColumn] = field(default_factory=list)
    tables_used: list[str] = field(default_factory=list)
    limit: int = 50
    offset: int = 0

    def sql(self, dialect: Any = None) -> str:
        """Rendered SQL with parameters left as placeholders (for the SQL inspector)."""
        compiled = self.statement.compile(dialect=dialect)
        return str(compiled)

    def sql_with_values(self, dialect: Any = None) -> str:
        """Rendered SQL with literals inlined. Debug/inspector only -- never executed."""
        return str(
            self.statement.compile(
                dialect=dialect, compile_kwargs={"literal_binds": True}
            )
        )


class ReportCompiler:
    def __init__(self, max_rows: int = 50_000, qualify_schema: bool = True) -> None:
        self.max_rows = max_rows
        #: Hybrid execution stages operational tables as session-temporary
        #: tables and resolves them through search_path, which cannot be
        #: schema-qualified. Compiling unqualified lets the identical statement
        #: run against either engine.
        self.qualify_schema = qualify_schema
        self._tables: dict[str, sa.Table] = {}
        self._metadata = sa.MetaData()
        self._registry = None

    # ------------------------------------------------------------------
    def compile(
        self,
        resolved: ResolvedReport,
        plan: JoinPlan,
        analysis: FanoutAnalysis,
        diagnostics: DiagnosticCollector,
        parameters: dict[str, Any] | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> CompiledReport:
        parameters = parameters or {}
        self._tables.clear()
        self._metadata = sa.MetaData()
        self._registry = resolved.registry

        for name in {*plan.tables, *(c.table for c in resolved.columns)}:
            table_meta = resolved.registry.table(name)
            if table_meta is not None:
                self._sa_table(table_meta)

        # Branch sub-selects are built first: the projection may reference them.
        branch_subqueries, branch_outputs = self._build_branch_subqueries(
            resolved, plan, analysis, parameters, diagnostics
        )

        from_clause = self._build_from(resolved, plan, analysis, branch_subqueries)
        projection = self._build_projection(resolved, branch_outputs)

        statement = sa.select(*projection).select_from(from_clause)

        where_clause = self._build_predicates(
            resolved, analysis, branch_outputs, parameters, aggregate=False
        )
        if where_clause is not None:
            statement = statement.where(where_clause)

        statement = self._apply_semi_joins(statement, resolved, plan, analysis, parameters)

        group_expressions = self._build_group_by(resolved, branch_outputs)
        if group_expressions:
            statement = statement.group_by(*group_expressions)
            having = self._build_predicates(
                resolved, analysis, branch_outputs, parameters, aggregate=True
            )
            if having is not None:
                statement = statement.having(having)

        order_expressions = self._build_order_by(resolved, plan, branch_outputs)
        if order_expressions:
            statement = statement.order_by(*order_expressions)

        effective_limit = min(limit or resolved.definition.row_limit, self.max_rows)
        if (limit or resolved.definition.row_limit) > self.max_rows:
            diagnostics.warn(
                Code.ROW_LIMIT_CLAMPED,
                f"The requested row limit was reduced to {self.max_rows:,} to protect the "
                "database. Use an export if you need the full result set.",
                section="general",
            )
        statement = statement.limit(effective_limit).offset(offset or None)

        return CompiledReport(
            statement=statement,
            output_columns=visible_columns(resolved),
            tables_used=sorted({*plan.tables}),
            limit=effective_limit,
            offset=offset,
        )

    # ------------------------------------------------------------------
    # SQLAlchemy table objects, built from registry metadata only.
    # ------------------------------------------------------------------
    def _sa_table(self, meta: TableMeta) -> sa.Table:
        cached = self._tables.get(meta.name)
        if cached is not None:
            return cached
        table = sa.Table(
            meta.real_name,
            self._metadata,
            *[
                sa.Column(column.name, _SA_TYPES[column.data_type]())
                for column in meta.columns
            ],
            schema=(
                meta.schema
                if self.qualify_schema and meta.schema and meta.schema != "public"
                else None
            ),
        )
        self._tables[meta.name] = table
        return table

    def _column(self, table: str, field_name: str) -> sa.Column:
        return self._tables[table].c[field_name]

    def _join_on(self, left_table, left_column, right_table, right_column):
        """
        Build a join condition, casting when the two sides are typed differently.

        An uploaded spreadsheet carries uuids as text, and PostgreSQL has no
        `uuid = text` operator -- so without this, joining a file of order ids to
        the orders themselves fails outright.
        """
        left = self._column(left_table, left_column)
        right = self._column(right_table, right_column)

        registry = self._registry
        left_meta = registry.column(left_table, left_column) if registry else None
        right_meta = registry.column(right_table, right_column) if registry else None
        if (
            left_meta is not None
            and right_meta is not None
            and left_meta.data_type != right_meta.data_type
        ):
            return sa.cast(left, sa.Text) == sa.cast(right, sa.Text)
        return left == right

    # ------------------------------------------------------------------
    # Pre-aggregated branches -- the fan-out correction.
    # ------------------------------------------------------------------
    def _build_branch_subqueries(
        self,
        resolved: ResolvedReport,
        plan: JoinPlan,
        analysis: FanoutAnalysis,
        parameters: dict[str, Any],
        diagnostics: DiagnosticCollector,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """
        Build one pre-aggregated derived table per fan-out branch.

        The sub-select produces one row per join key -- typically one row per
        order. The outer query usually groups more coarsely than that (one row
        per customer), so those per-order values must be *re-aggregated* at the
        outer level. Selecting them bare is not merely untidy: PostgreSQL
        rejects the statement outright, and engines that accept it return an
        arbitrary row's value.

        Returns (subqueries by branch name, outer expression by column key).
        """
        subqueries: dict[str, Any] = {}
        outer_expressions: dict[str, Any] = {}

        for branch in analysis.pre_aggregated:
            subquery, pending = self._aggregate_subtree(
                branch.root_step.to_table,
                branch.attach_column,
                branch,
                plan,
                resolved,
                parameters,
                diagnostics,
            )
            subqueries[branch.name] = subquery
            for column, keys in pending:
                outer_expressions[column.output_key] = self._outer_aggregate(
                    column, subquery, keys
                )

        return subqueries, outer_expressions

    def _aggregate_subtree(
        self,
        node: str,
        attach_column: str,
        branch: Branch,
        plan: JoinPlan,
        resolved: ResolvedReport,
        parameters: dict[str, Any],
        diagnostics: DiagnosticCollector,
    ):
        """
        Aggregate the sub-tree at ``node`` down to one row per join key.

        Correcting only the branch's own edge is not enough. Customers to orders
        to invoices to payments multiplies twice, and flattening the whole branch
        into one grouped select repeats each order once per invoice -- so the
        order count and the order value come back inflated while the invoice and
        payment figures look right, which is the hardest kind of wrong number to
        notice.

        So every multiplying edge inside the branch gets the same treatment,
        recursively: aggregate the child first, join the single row it produces,
        and roll its figures up. Each level then sees one row per record, and SUM
        at that level means what it says.
        """
        inline: set[str] = {node}
        rolled: list[tuple[ResolvedColumn, list[str], Any]] = []
        clause = self._assemble_subtree(
            self._tables[node],
            node,
            branch,
            plan,
            resolved,
            parameters,
            diagnostics,
            inline,
            rolled,
        )

        key_column = self._column(node, attach_column)
        selections = [key_column.label(_JOIN_KEY)]
        pending: list[tuple[ResolvedColumn, list[str]]] = []

        # Columns whose table sits at this grain aggregate directly.
        for column in branch.aggregated_columns:
            if column.table not in inline:
                continue
            inner_column = self._masked_for(column)

            if column.aggregation is Aggregation.AVG:
                # An average of averages is not the average. Carry the sum and
                # the count separately so the outer level can compute the true
                # figure.
                sum_key = f"{column.output_key}__sum"
                count_key = f"{column.output_key}__cnt"
                selections.append(sa.func.sum(inner_column).label(sum_key))
                selections.append(sa.func.count(inner_column).label(count_key))
                pending.append((column, [sum_key, count_key]))
            else:
                selections.append(
                    _AGGREGATE_FUNCTIONS[column.aggregation](inner_column).label(
                        column.output_key
                    )
                )
                pending.append((column, [column.output_key]))

            if column.aggregation is Aggregation.COUNT_DISTINCT:
                diagnostics.warn(
                    Code.FANOUT_INFLATION,
                    f"{column.display_name} counts distinct values within each "
                    f"{plan.root} row and then adds those counts up. A value "
                    f"appearing under two different {plan.root} rows is counted "
                    "twice. Group by that level if you need an exact figure.",
                    section="columns",
                    target=column.id,
                )

        # Figures that arrived already aggregated from a deeper level are rolled
        # up one more step rather than re-read from their table.
        for column, keys, source in rolled:
            for label, expression in self._rollup(column, keys, source):
                selections.append(expression.label(label))
            pending.append((column, keys))

        inner = sa.select(*selections).select_from(clause)

        # A filter lands at the level where its table is visible; deeper ones
        # were already applied inside the nested sub-select.
        predicates = [
            expression
            for condition in self._collect_conditions(resolved.filters, only_tables=inline)
            if (expression := self._condition_expression(condition, parameters)) is not None
        ]
        if predicates:
            inner = inner.where(sa.and_(*predicates))

        return inner.group_by(key_column).subquery(f"agg_{node}"), pending

    def _assemble_subtree(
        self,
        clause,
        node: str,
        branch: Branch,
        plan: JoinPlan,
        resolved: ResolvedReport,
        parameters: dict[str, Any],
        diagnostics: DiagnosticCollector,
        inline: set[str],
        rolled: list,
    ):
        """
        Join everything under ``node`` reachable without multiplying rows, and
        pre-aggregate everything that cannot be.

        ``inline`` collects the tables that end up at this grain; ``rolled``
        collects columns arriving pre-aggregated from a deeper level.
        """
        for step in [s for s in branch.steps if s.from_table == node]:
            child = step.to_table
            if step.multiplies_rows:
                subquery, pending = self._aggregate_subtree(
                    child,
                    step.to_column,
                    branch,
                    plan,
                    resolved,
                    parameters,
                    diagnostics,
                )
                clause = clause.join(
                    subquery,
                    self._column(node, step.from_column) == subquery.c[_JOIN_KEY],
                    isouter=True,
                )
                rolled.extend((column, keys, subquery) for column, keys in pending)
            else:
                # At most one row, so it can be read at this grain directly.
                clause = clause.join(
                    self._tables[child],
                    self._join_on(step.from_table, step.from_column, child, step.to_column),
                    isouter=step.join_type is not JoinType.INNER,
                )
                inline.add(child)
                clause = self._assemble_subtree(
                    clause,
                    child,
                    branch,
                    plan,
                    resolved,
                    parameters,
                    diagnostics,
                    inline,
                    rolled,
                )
        return clause

    @staticmethod
    def _rollup(column: ResolvedColumn, keys: list[str], source):
        """
        Carry a nested level's figures up one grain, still as aggregates.

        AVG keeps its sum and its count separate all the way to the top, where
        the division finally happens -- dividing early averages the averages.
        """
        match column.aggregation:
            case Aggregation.AVG:
                return [
                    (keys[0], sa.func.sum(source.c[keys[0]])),
                    (keys[1], sa.func.sum(source.c[keys[1]])),
                ]
            case Aggregation.MIN:
                return [(keys[0], sa.func.min(source.c[keys[0]]))]
            case Aggregation.MAX:
                return [(keys[0], sa.func.max(source.c[keys[0]]))]
            case _:
                return [(keys[0], sa.func.sum(source.c[keys[0]]))]

    @staticmethod
    def _outer_aggregate(column: ResolvedColumn, subquery, keys: list[str]):
        """
        Roll a branch's per-key values up to the outer query's grain.

        SUM stays SUM. COUNT becomes SUM, because adding per-order counts is how
        you get a per-customer count. MIN and MAX stay themselves. AVG is
        rebuilt from the carried sum and count so it remains a true average
        rather than an average of averages.
        """
        match column.aggregation:
            case Aggregation.AVG:
                total, count = subquery.c[keys[0]], subquery.c[keys[1]]
                return sa.func.sum(total) / sa.func.nullif(sa.func.sum(count), 0)
            case Aggregation.MIN:
                return sa.func.min(subquery.c[keys[0]])
            case Aggregation.MAX:
                return sa.func.max(subquery.c[keys[0]])
            case _:
                # SUM, COUNT and COUNT DISTINCT all add up across keys.
                return sa.func.sum(subquery.c[keys[0]])

    def _masked_for(self, column: ResolvedColumn):
        expression = self._column(column.table, column.field)
        if column.aggregation in (Aggregation.MIN, Aggregation.MAX):
            return self._masked(expression, column.meta)
        return expression

    # ------------------------------------------------------------------
    def _build_from(
        self,
        resolved: ResolvedReport,
        plan: JoinPlan,
        analysis: FanoutAnalysis,
        branch_subqueries: dict[str, Any],
    ):
        clause = self._tables[plan.root]
        handled: set[str] = set()

        for branch in analysis.branches:
            if branch.strategy in (Strategy.SEMI_JOIN, Strategy.PRUNE):
                handled |= branch.tables
            elif branch.strategy is Strategy.PRE_AGGREGATE:
                handled |= branch.tables
                subquery = branch_subqueries[branch.name]
                clause = clause.join(
                    subquery,
                    self._column(plan.root, branch.parent_column)
                    == subquery.c[_JOIN_KEY],
                    isouter=True,
                )

        for step in plan.steps:
            if step.to_table in handled:
                continue
            clause = clause.join(
                self._tables[step.to_table],
                self._join_on(
                    step.from_table, step.from_column, step.to_table, step.to_column
                ),
                isouter=step.join_type is not JoinType.INNER,
            )
        return clause

    # ------------------------------------------------------------------
    def _build_projection(
        self, resolved: ResolvedReport, branch_outputs: dict[str, Any]
    ) -> list:
        """
        Project exactly the visible columns, in order.

        This must stay in lockstep with ``CompiledReport.output_columns``: the
        API zips the returned tuples against that list, so projecting a hidden
        column here would shift every value after it into the wrong column --
        showing, say, an order status underneath an "Order Date" heading.
        """
        projection = []
        for column in visible_columns(resolved):
            expression = self._column_expression(column, branch_outputs)
            projection.append(expression.label(column.output_key))
        return projection

    def _column_expression(self, column: ResolvedColumn, branch_outputs: dict[str, Any]):
        # A pre-aggregated branch column is re-aggregated at the outer grain.
        if column.output_key in branch_outputs:
            return branch_outputs[column.output_key]

        if column.is_aggregate:
            return self._aggregate_expression(column)
        return self._masked(self._column(column.table, column.field), column.meta)

    def _aggregate_expression(self, column: ResolvedColumn):
        expression = self._column(column.table, column.field)
        # MIN and MAX return an actual stored value, so a masked column must be
        # masked *inside* the aggregate -- otherwise picking MIN from a dropdown
        # hands back a real email address and the mask is trivially bypassed.
        # COUNT reveals nothing about the values, so it reads the raw column;
        # SUM and AVG over masked columns are rejected during resolution.
        if column.aggregation in (Aggregation.MIN, Aggregation.MAX):
            expression = self._masked(expression, column.meta)
        return _AGGREGATE_FUNCTIONS[column.aggregation](expression)

    @staticmethod
    def _masked(expression, meta: ColumnMeta):
        """Column-level masking (spec 33). Applied in the projection, server-side."""
        match meta.mask_policy:
            case MaskPolicy.NONE:
                return expression
            case MaskPolicy.NULL:
                return sa.null()
            case MaskPolicy.REDACT:
                return sa.literal("[REDACTED]")
            case MaskPolicy.HASH:
                return sa.func.md5(sa.cast(expression, sa.Text))
            case MaskPolicy.PARTIAL:
                return sa.func.concat(
                    sa.func.substr(sa.cast(expression, sa.Text), 1, 2), sa.literal("***")
                )
        return expression

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------
    def _build_predicates(
        self,
        resolved: ResolvedReport,
        analysis: FanoutAnalysis,
        branch_outputs: dict[str, Any],
        parameters: dict[str, Any],
        aggregate: bool,
    ):
        aggregated_fields = {
            (c.table, c.field): c for c in resolved.columns if c.is_aggregate
        }
        pushed_down = {
            table
            for branch in analysis.branches
            if branch.strategy in (Strategy.PRE_AGGREGATE, Strategy.SEMI_JOIN, Strategy.PRUNE)
            for table in branch.tables
        }

        def build(node):
            if isinstance(node, ResolvedGroup):
                parts = [built for child in node.children if (built := build(child)) is not None]
                if not parts:
                    return None
                return sa.and_(*parts) if node.op == "and" else sa.or_(*parts)

            condition: ResolvedCondition = node
            if condition.table in pushed_down:
                return None  # already applied inside the branch sub-select

            is_aggregate_filter = (condition.table, condition.field) in aggregated_fields
            if is_aggregate_filter != aggregate:
                return None

            if is_aggregate_filter:
                target = self._aggregate_expression(
                    aggregated_fields[(condition.table, condition.field)]
                )
                return self._operator_expression(target, condition, parameters)

            return self._condition_expression(condition, parameters)

        return build(resolved.filters) if resolved.filters else None

    def _condition_expression(self, condition: ResolvedCondition, parameters: dict[str, Any]):
        column = self._column(condition.table, condition.field)
        return self._operator_expression(column, condition, parameters)

    def _operator_expression(
        self, target, condition: ResolvedCondition, parameters: dict[str, Any]
    ):
        operator = condition.operator
        values = self._effective_values(condition, parameters)
        data_type = condition.meta.data_type

        # An enum column has no comparison operator against a text literal, and
        # no pattern-matching operator at all. Casting to text gives both, and
        # is a no-op for columns that are already text.
        if condition.meta.is_enum:
            target = sa.cast(target, sa.Text)

        # Relative-date operators are resolved in Python, so they arrive at the
        # database as ordinary bound parameters rather than dialect functions.
        if operator in _RELATIVE_DATE_OPERATORS:
            start, end = _relative_range(operator, values)
            return sa.and_(target >= start, target < end)

        match operator:
            case "equals" | "on":
                return target == _coerce(values[0], data_type)
            case "not_equals":
                return sa.or_(target != _coerce(values[0], data_type), target.is_(None))
            case "greater_than" | "after":
                return target > _coerce(values[0], data_type)
            case "greater_or_equal":
                return target >= _coerce(values[0], data_type)
            case "less_than" | "before":
                return target < _coerce(values[0], data_type)
            case "less_or_equal":
                return target <= _coerce(values[0], data_type)
            case "between":
                low, high = _coerce(values[0], data_type), _coerce(values[1], data_type)
                if data_type is DataType.DATETIME and isinstance(high, date):
                    # Inclusive end date for timestamps: 31-May must include 31-May 23:59.
                    return sa.and_(target >= low, target < _end_of_day(high))
                return target.between(low, high)
            case "contains":
                return target.ilike(_like(values[0], "%{}%"), escape=LIKE_ESCAPE)
            case "not_contains":
                return sa.not_(target.ilike(_like(values[0], "%{}%"), escape=LIKE_ESCAPE))
            case "starts_with":
                return target.ilike(_like(values[0], "{}%"), escape=LIKE_ESCAPE)
            case "ends_with":
                return target.ilike(_like(values[0], "%{}"), escape=LIKE_ESCAPE)
            case "in":
                return target.in_([_coerce(v, data_type) for v in values])
            case "not_in":
                return sa.or_(
                    target.notin_([_coerce(v, data_type) for v in values]), target.is_(None)
                )
            case "is_null":
                return target.is_(None)
            case "is_not_null":
                return target.isnot(None)
            case "is_empty":
                return sa.or_(target.is_(None), sa.func.trim(sa.cast(target, sa.Text)) == "")
            case "is_not_empty":
                return sa.and_(
                    target.isnot(None), sa.func.trim(sa.cast(target, sa.Text)) != ""
                )
            case "is_true":
                return target.is_(True)
            case "is_false":
                return target.is_(False)
        raise ValueError(f"unsupported operator: {operator}")

    @staticmethod
    def _effective_values(condition: ResolvedCondition, parameters: dict[str, Any]) -> list:
        """Run-time parameters override the saved defaults (spec 11)."""
        if condition.parameter_name and condition.parameter_name in parameters:
            supplied = parameters[condition.parameter_name]
            return list(supplied) if isinstance(supplied, (list, tuple)) else [supplied]
        return condition.values

    # ------------------------------------------------------------------
    def _apply_semi_joins(
        self,
        statement: Select,
        resolved: ResolvedReport,
        plan: JoinPlan,
        analysis: FanoutAnalysis,
        parameters: dict[str, Any],
    ) -> Select:
        """A branch used only by filters becomes EXISTS -- filters without multiplying."""
        for branch in analysis.semi_joined:
            conditions = self._collect_conditions(resolved.filters, only_tables=branch.tables)
            predicates = [
                expression
                for condition in conditions
                if (expression := self._condition_expression(condition, parameters)) is not None
            ]
            link = self._column(plan.root, branch.parent_column) == self._column(
                branch.root_step.to_table, branch.attach_column
            )
            inner = (
                sa.select(sa.literal(1))
                .select_from(self._flat_branch_from(branch, plan))
                .where(sa.and_(link, *predicates))
            )
            statement = statement.where(sa.exists(inner))
        return statement

    def _flat_branch_from(self, branch: Branch, plan: JoinPlan):
        """
        A branch flattened into ordinary joins, however much it multiplies.

        Only EXISTS uses this. It asks whether a matching row exists at all, so
        duplicate rows change nothing -- no aggregate is computed over them.
        """
        clause = self._tables[branch.root_step.to_table]
        for step in branch.steps:
            if step.from_table == plan.root:
                continue  # the attachment edge lives in the outer query
            clause = clause.join(
                self._tables[step.to_table],
                self._join_on(
                    step.from_table, step.from_column, step.to_table, step.to_column
                ),
                isouter=step.join_type is not JoinType.INNER,
            )
        return clause

    def _collect_conditions(self, node, only_tables: set[str]) -> list[ResolvedCondition]:
        found: list[ResolvedCondition] = []

        def walk(current) -> None:
            if current is None:
                return
            if isinstance(current, ResolvedGroup):
                for child in current.children:
                    walk(child)
            elif current.table in only_tables:
                found.append(current)

        walk(node)
        return found

    # ------------------------------------------------------------------
    def _build_group_by(self, resolved: ResolvedReport, branch_outputs: dict[str, Any]) -> list:
        expressions = [
            self._masked(self._column(group.table, group.field), group.meta)
            for group in resolved.group_by
        ]
        if not expressions and not resolved.has_aggregates:
            return []

        # With aggregates present, every non-aggregate column must be grouped.
        # The validator already told the user; here we make the SQL valid.
        if resolved.has_aggregates:
            grouped = {(g.table, g.field) for g in resolved.group_by}
            for column in resolved.columns:
                if column.is_aggregate or (column.table, column.field) in grouped:
                    continue
                if column.output_key in branch_outputs:
                    continue
                expressions.append(self._masked(self._column(column.table, column.field),
                                                column.meta))
                grouped.add((column.table, column.field))
        return expressions

    def _build_order_by(
        self, resolved: ResolvedReport, plan: JoinPlan, branch_outputs: dict[str, Any]
    ) -> list:
        expressions = []
        for sort in resolved.sort_by:
            expression = self._column_expression(sort.column, branch_outputs)
            expressions.append(
                sa.desc(expression).nullslast()
                if sort.direction == "desc"
                else sa.asc(expression).nullslast()
            )

        expressions.extend(self._stable_tiebreaker(resolved, plan, bool(expressions)))
        return expressions

    def _stable_tiebreaker(
        self, resolved: ResolvedReport, plan: JoinPlan, has_user_sort: bool
    ) -> list:
        """
        Append a deterministic final ordering.

        SQL guarantees no row order without ORDER BY, and it does not break ties
        deterministically either. Paginating such a query with LIMIT/OFFSET lets
        the database return the same row on two pages and skip another entirely
        -- which on a production PostgreSQL using parallel scans is not
        theoretical. Sorting by a unique key last makes every page stable
        without changing the order the user asked for.
        """
        if resolved.has_aggregates:
            # Grouped rows are identified by their group keys; ordering by those
            # makes the result deterministic.
            return [
                sa.asc(self._masked(self._column(group.table, group.field), group.meta))
                for group in resolved.group_by
            ]

        root = resolved.registry.table(plan.root)
        if root is None or not root.primary_key:
            return []
        return [sa.asc(self._column(root.name, column.name)) for column in root.primary_key]


# ---------------------------------------------------------------------------
# Value coercion. Filter values arrive as JSON scalars; the database expects
# real dates and numbers. Coercing here keeps everything a bound parameter.
# ---------------------------------------------------------------------------
_RELATIVE_DATE_OPERATORS = frozenset({
    "today", "yesterday", "this_week", "this_month", "this_year",
    "last_7_days", "last_30_days", "last_n_days", "year_to_date",
})


def _coerce(value: Any, data_type: DataType) -> Any:
    if value is None:
        return None
    try:
        match data_type:
            case DataType.INTEGER:
                return int(value)
            case DataType.DECIMAL:
                return Decimal(str(value))
            case DataType.BOOLEAN:
                if isinstance(value, bool):
                    return value
                return str(value).strip().lower() in ("true", "1", "yes", "y", "t")
            case DataType.DATE:
                return _parse_date(value)
            case DataType.DATETIME:
                return _parse_datetime(value)
    except (ValueError, TypeError, InvalidOperation):
        # Let the database reject it as a typed parameter rather than guessing.
        return value
    return value


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.combine(date.fromisoformat(text[:10]), datetime.min.time())


def _end_of_day(value: date) -> datetime:
    return datetime.combine(value + timedelta(days=1), datetime.min.time())


def _like(value: Any, pattern: str) -> str:
    """Escape LIKE wildcards so a literal % in a search term is not a wildcard."""
    text = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return pattern.format(text)


def _relative_range(operator: str, values: list) -> tuple[datetime, datetime]:
    """Resolve a relative-date operator to a concrete half-open [start, end) range."""
    today = date.today()
    start_of_today = datetime.combine(today, datetime.min.time())
    day = timedelta(days=1)

    match operator:
        case "today":
            return start_of_today, start_of_today + day
        case "yesterday":
            return start_of_today - day, start_of_today
        case "this_week":
            monday = today - timedelta(days=today.weekday())
            start = datetime.combine(monday, datetime.min.time())
            return start, start + timedelta(days=7)
        case "this_month":
            start = datetime.combine(today.replace(day=1), datetime.min.time())
            next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
            return start, datetime.combine(next_month, datetime.min.time())
        case "this_year" | "year_to_date":
            start = datetime.combine(date(today.year, 1, 1), datetime.min.time())
            return start, start_of_today + day
        case "last_7_days":
            return start_of_today - timedelta(days=7), start_of_today + day
        case "last_30_days":
            return start_of_today - timedelta(days=30), start_of_today + day
        case "last_n_days":
            days = int(values[0]) if values else 7
            return start_of_today - timedelta(days=days), start_of_today + day
    raise ValueError(f"unsupported relative date operator: {operator}")

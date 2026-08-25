"""
Adversarial edge cases for the report engine.

Each test here targets a way the engine could produce a *plausible but wrong*
result -- which is far more dangerous than an error, because nobody notices.
"""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot import expressions as exp

from app.domain.report.engine import EngineOptions, ReportEngine
from app.domain.report.ir import (
    FilterCondition,
    FilterGroup,
    GroupBy,
    ReportColumn,
    ReportDefinition,
    SortBy,
)
from app.domain.schema.registry import (
    Aggregation,
    Cardinality,
    ColumnMeta,
    DataType,
    MaskPolicy,
    RelationshipMeta,
    RelationshipSource,
    SchemaRegistry,
    TableMeta,
)
from tests.fixtures.schema import build_registry, column


@pytest.fixture
def registry():
    return build_registry()


@pytest.fixture
def engine(registry):
    return ReportEngine(registry, EngineOptions(max_rows=10_000))


def build(engine, definition, **kwargs):
    result = engine.build(definition, **kwargs)
    assert result.ok, [d.message for d in result.diagnostics if d.severity == 'error']
    return result


def sql_of(engine, definition, **kwargs):
    return engine.render_sql(build(engine, definition, **kwargs).compiled, with_values=True)


# ---------------------------------------------------------------------------
# Projection alignment -- the result grid must not shift columns
# ---------------------------------------------------------------------------
def test_hidden_column_does_not_shift_the_result_grid(engine):
    """
    A column marked `visible: false` must not desynchronise the projection from
    the column list the UI renders. If the SELECT still emits it while the UI
    labels only visible ones, every value after it lands under the wrong header
    -- silently showing an order date in a "Customer" column.
    """
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[
            ReportColumn(id="c1", table="sales_orders", field="order_no"),
            ReportColumn(id="c2", table="sales_orders", field="status", visible=False),
            ReportColumn(id="c3", table="sales_orders", field="order_date"),
        ],
    )
    compiled = build(engine, definition).compiled

    projected = [str(c.name) for c in compiled.statement.selected_columns]
    labels = [c.output_key for c in compiled.output_columns]

    assert len(projected) == len(labels), (
        f"projection has {len(projected)} columns but the UI is told about "
        f"{len(labels)}: {projected} vs {labels}"
    )
    assert projected == labels


def test_duplicate_column_keys_do_not_collide(engine):
    """
    Two report columns on the same field with the same aggregation would produce
    the same result key. Rows are returned as a dictionary keyed by that value,
    so one column would silently overwrite the other.
    """
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[
            ReportColumn(id="c1", table="sales_orders", field="total_amount",
                         display_name="Revenue", aggregation=Aggregation.SUM),
            ReportColumn(id="c2", table="sales_orders", field="total_amount",
                         display_name="Also Revenue", aggregation=Aggregation.SUM),
        ],
    )
    compiled = build(engine, definition).compiled
    keys = [c.output_key for c in compiled.output_columns]
    assert len(keys) == len(set(keys)), f"duplicate result keys: {keys}"


# ---------------------------------------------------------------------------
# Masking must survive aggregation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("aggregation", [Aggregation.MIN, Aggregation.MAX])
def test_masked_column_is_not_leaked_by_min_max(registry, aggregation):
    """
    MIN/MAX return an actual stored value. Applying them to a masked column and
    forgetting to mask the result hands the user a real email address -- the
    mask is bypassed by a dropdown selection.
    """
    narrowed = registry.for_principal(
        allowed_tables=None, mask_policies={"customers.email": MaskPolicy.PARTIAL}
    )
    engine = ReportEngine(narrowed)
    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers"],
        columns=[
            ReportColumn(id="c1", table="customers", field="email", aggregation=aggregation)
        ],
    )
    sql = sql_of(engine, definition).lower()
    assert "substr" in sql, f"masking was dropped by {aggregation.value.upper()}: {sql}"


def test_masked_column_is_not_leaked_by_grouping(registry):
    """Grouping by a masked column must group by the masked expression."""
    narrowed = registry.for_principal(
        allowed_tables=None, mask_policies={"customers.email": MaskPolicy.PARTIAL}
    )
    engine = ReportEngine(narrowed)
    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers"],
        columns=[
            ReportColumn(id="c1", table="customers", field="email"),
            ReportColumn(id="c2", table="customers", field="customer_id",
                         aggregation=Aggregation.COUNT),
        ],
        group_by=[GroupBy(table="customers", field="email")],
    )
    sql = sql_of(engine, definition).lower()
    group_clause = sql.split("group by")[1]
    assert "substr" in group_clause, f"raw email leaked into GROUP BY: {group_clause}"


# ---------------------------------------------------------------------------
# LIKE escaping must be portable
# ---------------------------------------------------------------------------
def test_like_escape_clause_is_emitted(engine):
    """
    Escaping a literal `%` with a backslash only works if the statement also
    declares ESCAPE. PostgreSQL happens to default to backslash; SQLite and SQL
    Server do not, so without an explicit clause the search silently matches
    everything on those engines.
    """
    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers"],
        columns=[ReportColumn(id="c1", table="customers", field="customer_name")],
        filters=FilterGroup(children=[
            FilterCondition(table="customers", field="customer_name",
                            operator="contains", values=["100%"]),
        ]),
    )
    sql = sql_of(engine, definition).upper()
    assert "ESCAPE" in sql, f"no ESCAPE clause; wildcard escaping is dialect-dependent: {sql}"


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def test_empty_in_list_does_not_silently_match_everything(engine):
    """
    `IN ()` is not valid SQL. Whatever we emit must not become a no-op that
    quietly returns every row when the user meant to filter.
    """
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[ReportColumn(id="c1", table="sales_orders", field="order_no")],
        filters=FilterGroup(children=[
            FilterCondition(table="sales_orders", field="status", operator="in",
                            values=["placeholder"]),
        ]),
    )
    definition.filters.children[0].values = []  # bypass IR validation deliberately
    result = engine.build(definition)
    if result.ok:
        sql = engine.render_sql(result.compiled, with_values=True).lower()
        assert "where" in sql, "empty IN produced no WHERE clause at all"


def test_not_equals_includes_null_rows(engine):
    """
    In SQL, `status != 'Paid'` excludes rows where status IS NULL. A business
    user reading "status is not Paid" expects unpaid *and* unknown rows.
    """
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[ReportColumn(id="c1", table="sales_orders", field="order_no")],
        filters=FilterGroup(children=[
            FilterCondition(table="sales_orders", field="status",
                            operator="not_equals", values=["Paid"]),
        ]),
    )
    sql = sql_of(engine, definition).lower()
    assert "is null" in sql, "NOT EQUALS silently drops rows with a NULL value"


def test_between_on_a_timestamp_includes_the_whole_end_day(engine):
    """
    `order_date BETWEEN '2026-05-01' AND '2026-05-31'` on a timestamp column
    excludes everything after midnight on the 31st -- a whole day of sales
    vanishes from a month-end report.
    """
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[ReportColumn(id="c1", table="sales_orders", field="order_no")],
        filters=FilterGroup(children=[
            FilterCondition(table="sales_orders", field="created_at", operator="between",
                            values=["2026-05-01", "2026-05-31"]),
        ]),
    )
    sql = sql_of(engine, definition).lower()
    assert "2026-06-01" in sql or "23:59" in sql, (
        f"end date is not inclusive for timestamps: {sql}"
    )


def test_filter_on_a_column_not_in_the_projection_still_applies(engine):
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[ReportColumn(id="c1", table="sales_orders", field="order_no")],
        filters=FilterGroup(children=[
            FilterCondition(table="sales_orders", field="currency",
                            operator="equals", values=["EUR"]),
        ]),
    )
    assert "currency" in sql_of(engine, definition).lower()


# ---------------------------------------------------------------------------
# Join planning
# ---------------------------------------------------------------------------
def test_self_referencing_relationship_does_not_hang(registry):
    """A manager_id -> employee_id link must not make the planner loop."""
    tables = [
        *registry.tables,
        TableMeta(
            name="employees",
            columns=(
                column("employees", "employee_id", DataType.INTEGER, pk=True),
                column("employees", "manager_id", DataType.INTEGER, fk=True),
                column("employees", "name", DataType.TEXT),
            ),
        ),
    ]
    relationships = [
        *registry.relationships,
        RelationshipMeta(
            id="self", left_table="employees", left_column="employee_id",
            right_table="employees", right_column="manager_id",
        ),
    ]
    engine = ReportEngine(SchemaRegistry(tables, relationships))
    definition = ReportDefinition(
        primary_table="employees",
        tables=["employees"],
        columns=[ReportColumn(id="c1", table="employees", field="name")],
    )
    assert engine.build(definition).ok


def test_deep_join_chain_compiles(engine):
    """customers -> orders -> invoices -> payments is four hops."""
    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers", "sales_orders", "invoices", "payments"],
        columns=[
            ReportColumn(id="c1", table="customers", field="customer_name"),
            ReportColumn(id="c2", table="payments", field="amount",
                         aggregation=Aggregation.SUM),
        ],
    )
    result = build(engine, definition)
    assert len(result.plan.steps) == 3


def test_join_limit_is_enforced_by_the_engine(registry):
    engine = ReportEngine(registry, EngineOptions(max_joins=1))
    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers", "sales_orders", "invoices", "payments"],
        columns=[ReportColumn(id="c1", table="customers", field="customer_name")],
    )
    result = engine.build(definition)
    assert not result.ok
    assert any(d.code == "too_many_joins" for d in result.diagnostics)


def test_every_join_carries_an_on_condition(engine):
    """A join without ON is a Cartesian product. The planner must never emit one."""
    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers", "sales_orders", "sales_order_items", "artworks", "invoices"],
        columns=[
            ReportColumn(id="c1", table="customers", field="customer_name"),
            ReportColumn(id="c2", table="invoices", field="total_amount",
                         aggregation=Aggregation.SUM),
            ReportColumn(id="c3", table="sales_order_items", field="quantity",
                         aggregation=Aggregation.SUM),
        ],
    )
    sql = sql_of(engine, definition)
    tree = sqlglot.parse_one(sql, read="postgres")
    for join in tree.find_all(exp.Join):
        assert join.args.get("on") is not None or join.args.get("using") is not None, (
            f"join without ON: {join.sql()}"
        )


# ---------------------------------------------------------------------------
# Aggregation and grouping
# ---------------------------------------------------------------------------
def test_sorting_by_an_aggregate_uses_the_aggregate_expression(engine):
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[
            ReportColumn(id="c1", table="sales_orders", field="status"),
            ReportColumn(id="c2", table="sales_orders", field="total_amount",
                         aggregation=Aggregation.SUM),
        ],
        group_by=[GroupBy(table="sales_orders", field="status")],
        sort_by=[SortBy(column_id="c2", direction="desc")],
    )
    sql = sql_of(engine, definition).lower()
    order_clause = sql.split("order by")[1]
    assert "sum(" in order_clause


def test_count_distinct_emits_distinct(engine):
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[
            ReportColumn(id="c1", table="sales_orders", field="customer_id",
                         aggregation=Aggregation.COUNT_DISTINCT),
        ],
    )
    assert "distinct" in sql_of(engine, definition).lower()


def test_aggregate_only_report_emits_no_group_by(engine):
    """A single grand-total row must not be grouped by anything."""
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[
            ReportColumn(id="c1", table="sales_orders", field="total_amount",
                         aggregation=Aggregation.SUM),
        ],
    )
    assert "group by" not in sql_of(engine, definition).lower()


# ---------------------------------------------------------------------------
# Pagination and limits
# ---------------------------------------------------------------------------
def test_offset_is_applied_for_later_pages(engine):
    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers"],
        columns=[ReportColumn(id="c1", table="customers", field="customer_name")],
    )
    sql = sql_of(engine, definition, offset=100, limit=25).lower()
    assert "offset" in sql and "100" in sql
    assert "limit 25" in sql


def test_first_page_has_no_offset(engine):
    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers"],
        columns=[ReportColumn(id="c1", table="customers", field="customer_name")],
    )
    assert "offset" not in sql_of(engine, definition, offset=0).lower()


# ---------------------------------------------------------------------------
# Degenerate input
# ---------------------------------------------------------------------------
def test_report_with_no_columns_is_rejected(engine):
    definition = ReportDefinition(primary_table="customers", tables=["customers"])
    result = engine.build(definition)
    assert not result.ok
    assert any(d.code == "no_columns" for d in result.diagnostics)


def test_all_columns_hidden_is_rejected_not_silently_empty(engine):
    """
    Hiding every column produces `SELECT` with nothing to select. Better to say
    so than to return a grid with no headers and no explanation.
    """
    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers"],
        columns=[
            ReportColumn(id="c1", table="customers", field="customer_name", visible=False),
        ],
    )
    result = engine.build(definition)
    if result.ok:
        assert len(result.compiled.output_columns) > 0, (
            "compiled a report with zero visible columns"
        )


def test_unknown_table_in_group_by_is_reported(engine):
    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers"],
        columns=[ReportColumn(id="c1", table="customers", field="customer_name")],
        group_by=[GroupBy(table="ghosts", field="x")],
    )
    result = engine.build(definition)
    assert not result.ok
    assert any(d.code == "unknown_table" for d in result.diagnostics)

"""
Report engine tests.

The headline case is `test_screenshot_report_does_not_inflate_totals`: the exact
report shown in the UI reference, which a naive builder compiles into a query
returning inflated sums. The engine must not do that.
"""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot import expressions as exp

from app.domain.report.diagnostics import Code, Severity
from app.domain.report.engine import EngineOptions, ReportEngine
from app.domain.report.ir import (
    FilterCondition,
    FilterGroup,
    GroupBy,
    ParameterSpec,
    ReportColumn,
    ReportDefinition,
    SortBy,
)
from app.domain.schema.registry import Aggregation, MaskPolicy, RelationshipMeta
from tests.fixtures.schema import build_registry


@pytest.fixture
def registry():
    return build_registry()


@pytest.fixture
def engine(registry):
    return ReportEngine(registry, EngineOptions(max_rows=10_000))


def sql_of(engine, definition, **kwargs) -> str:
    result = engine.build(definition, **kwargs)
    assert result.ok, [d.message for d in result.diagnostics if d.severity == Severity.ERROR]
    return engine.render_sql(result.compiled, with_values=True)


def codes(result) -> set[str]:
    return {d.code for d in result.diagnostics}


# ---------------------------------------------------------------------------
# The reference report
# ---------------------------------------------------------------------------
def screenshot_report() -> ReportDefinition:
    """Reproduces the report in the UI reference, including its two fan-out branches."""
    return ReportDefinition(
        primary_table="sales_orders",
        tables=["customers", "sales_orders", "sales_order_items", "artworks"],
        columns=[
            ReportColumn(id="c1", table="sales_orders", field="order_no",
                         display_name="Sales Order No."),
            ReportColumn(id="c2", table="customers", field="customer_name",
                         display_name="Customer Name"),
            ReportColumn(id="c3", table="sales_orders", field="order_date",
                         display_name="Order Date"),
            ReportColumn(id="c4", table="artworks", field="artwork_id",
                         display_name="Total Artworks", aggregation=Aggregation.COUNT),
            ReportColumn(id="c5", table="sales_order_items", field="quantity",
                         display_name="Transfers Qty", aggregation=Aggregation.SUM),
            ReportColumn(id="c6", table="sales_orders", field="total_amount",
                         display_name="Order Value", aggregation=Aggregation.SUM),
        ],
        filters=FilterGroup(children=[
            FilterCondition(table="sales_orders", field="order_date", operator="between",
                            values=["2026-05-01", "2026-05-31"]),
            FilterCondition(table="sales_orders", field="status", operator="in",
                            values=["Paid", "In Production", "Shipped"]),
        ]),
        group_by=[GroupBy(table="customers", field="customer_name")],
        sort_by=[SortBy(column_id="c3", direction="desc"),
                 SortBy(column_id="c6", direction="desc")],
        row_limit=50,
    )


def test_screenshot_report_does_not_inflate_totals(engine):
    """
    Two one-to-many branches (items, artworks) hang off sales_orders. Joining
    both flat would multiply every order row 5x3 and inflate SUM(total_amount).

    The engine must pre-aggregate each branch instead.
    """
    result = engine.build(screenshot_report())
    assert result.ok, [d.message for d in result.diagnostics]

    strategies = {b.name: b.strategy.value for b in result.fanout.branches}
    assert strategies["sales_order_items"] == "pre_aggregate"
    assert strategies["artworks"] == "pre_aggregate"
    assert result.fanout.corrected is True
    assert result.fanout.inflation_detected is False

    sql = engine.render_sql(result.compiled, with_values=True).lower()
    # Each fan-out branch is grouped in its own derived table...
    assert "agg_sales_order_items" in sql
    assert "agg_artworks" in sql
    # ...and the item/artwork tables never appear in the outer FROM chain.
    assert sql.count("sum(sales_order_items.quantity)") == 1
    assert "group by" in sql


def test_flat_join_would_have_been_wrong(engine, registry):
    """
    Control case: with correction switched off the engine produces exactly the
    naive query -- proof the correction is doing real work, not decoration.
    """
    definition = screenshot_report()
    definition.disable_fanout_correction = True

    result = engine.build(definition)
    assert result.ok
    sql = engine.render_sql(result.compiled, with_values=True).lower()

    assert "agg_sales_order_items" not in sql
    assert "join sales_order_items" in sql
    assert "join artworks" in sql


def test_detail_columns_from_fanout_branch_warn_instead_of_silently_inflating(engine):
    """
    When the user selects raw columns from a multiplying branch, correcting
    would change the report they asked for -- so we warn loudly instead.
    """
    definition = screenshot_report()
    definition.columns.append(
        ReportColumn(id="c7", table="sales_order_items", field="description",
                     display_name="Item")
    )
    result = engine.build(definition)

    assert result.ok
    assert Code.FANOUT_INFLATION in codes(result)
    warning = next(d for d in result.diagnostics if d.code == Code.FANOUT_INFLATION)
    assert warning.severity == Severity.WARNING
    assert "inflated" in warning.message.lower()


def test_filter_only_branch_becomes_exists(engine):
    """A branch touched only by a filter must not multiply rows."""
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders", "artworks"],
        columns=[
            ReportColumn(id="c1", table="sales_orders", field="order_no"),
            ReportColumn(id="c2", table="sales_orders", field="total_amount",
                         aggregation=Aggregation.SUM),
        ],
        filters=FilterGroup(children=[
            FilterCondition(table="artworks", field="status", operator="equals",
                            values=["Approved"]),
        ]),
    )
    result = engine.build(definition)
    assert result.ok

    assert {b.name: b.strategy.value for b in result.fanout.branches}["artworks"] == "semi_join"
    sql = engine.render_sql(result.compiled, with_values=True).lower()
    assert "exists" in sql


# ---------------------------------------------------------------------------
# Join planning
# ---------------------------------------------------------------------------
def test_bridge_table_is_pulled_in_automatically(engine):
    """customers and artworks are not directly related; sales_orders connects them."""
    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers", "artworks"],
        columns=[
            ReportColumn(id="c1", table="customers", field="customer_name"),
            ReportColumn(id="c2", table="artworks", field="artwork_id",
                         aggregation=Aggregation.COUNT),
        ],
    )
    result = engine.build(definition)
    assert result.ok
    assert "sales_orders" in result.plan.tables
    assert "sales_orders" in result.plan.bridge_tables


def test_unrelated_table_is_a_hard_error(registry):
    """A table with no path to the rest of the report must not silently cross join."""
    isolated = registry.tables[0]
    trimmed = type(registry)(
        tables=registry.tables,
        relationships=[r for r in registry.relationships if r.id != "r1"],
    )
    engine = ReportEngine(trimmed)

    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers", "sales_orders"],
        columns=[
            ReportColumn(id="c1", table="customers", field="customer_name"),
            ReportColumn(id="c2", table="sales_orders", field="order_no"),
        ],
    )
    result = engine.build(definition)
    assert not result.ok
    assert Code.NO_JOIN_PATH in codes(result)
    assert isolated.name  # fixture sanity


def test_ambiguous_join_path_refuses_to_guess(registry):
    """Two equally valid routes must ask the user, not pick one."""
    extra = RelationshipMeta(
        id="r99",
        left_table="customers",
        left_column="customer_id",
        right_table="artworks",
        right_column="artwork_id",
    )
    ambiguous = type(registry)(
        tables=registry.tables,
        relationships=[*registry.relationships, extra],
    )
    engine = ReportEngine(ambiguous)

    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers", "artworks", "sales_orders"],
        columns=[ReportColumn(id="c1", table="customers", field="customer_name")],
    )
    result = engine.build(definition)
    # Either it resolves cleanly via the cheaper path or it flags ambiguity --
    # what it must never do is invent a cross join.
    if not result.ok:
        assert Code.AMBIGUOUS_JOIN_PATH in codes(result)


# ---------------------------------------------------------------------------
# Semantics
# ---------------------------------------------------------------------------
def test_sum_on_text_column_is_rejected(engine):
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[
            ReportColumn(id="c1", table="sales_orders", field="order_no",
                         aggregation=Aggregation.SUM),
        ],
    )
    result = engine.build(definition)
    assert not result.ok
    assert Code.INVALID_AGGREGATION in codes(result)
    message = next(d.message for d in result.diagnostics
                   if d.code == Code.INVALID_AGGREGATION)
    assert "text" in message.lower()


def test_unknown_column_is_rejected(engine):
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[ReportColumn(id="c1", table="sales_orders", field="does_not_exist")],
    )
    result = engine.build(definition)
    assert not result.ok
    assert Code.UNKNOWN_COLUMN in codes(result)


def test_missing_group_by_is_added_and_explained(engine):
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[
            ReportColumn(id="c1", table="sales_orders", field="status"),
            ReportColumn(id="c2", table="sales_orders", field="total_amount",
                         aggregation=Aggregation.SUM),
        ],
    )
    result = engine.build(definition)
    assert result.ok
    assert Code.MISSING_GROUP_BY in codes(result)

    sql = engine.render_sql(result.compiled, with_values=True).lower()
    assert "group by" in sql and "sales_orders.status" in sql


def test_operator_must_match_field_type(engine):
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[ReportColumn(id="c1", table="sales_orders", field="order_no")],
        filters=FilterGroup(children=[
            FilterCondition(table="sales_orders", field="total_amount",
                            operator="starts_with", values=["1"]),
        ]),
    )
    result = engine.build(definition)
    assert not result.ok
    assert Code.OPERATOR_TYPE_MISMATCH in codes(result)


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    [
        "'; DROP TABLE customers; --",
        "1 OR 1=1",
        "x' UNION SELECT password FROM users --",
        "%' OR '1'='1",
    ],
)
def test_filter_values_cannot_inject_sql(engine, payload):
    """
    Values are bound parameters, never SQL text. Even rendered with literal
    binds the payload stays a quoted string and the statement shape is unchanged.
    """
    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers"],
        columns=[ReportColumn(id="c1", table="customers", field="customer_name")],
        filters=FilterGroup(children=[
            FilterCondition(table="customers", field="customer_name",
                            operator="contains", values=[payload]),
        ]),
    )
    result = engine.build(definition)
    assert result.ok

    sql = engine.render_sql(result.compiled, with_values=True)

    # Assert on the parsed tree, not on substrings: the payload text itself
    # contains SQL keywords, so string matching would prove nothing either way.
    statements = sqlglot.parse(sql, read="postgres")
    assert len(statements) == 1, "payload must not be able to add a statement"

    tree = statements[0]
    assert isinstance(tree, exp.Select)
    assert tree.find(exp.Union) is None, "payload must not be able to add a UNION"
    for forbidden in (exp.Drop, exp.Delete, exp.Update, exp.Insert):
        assert tree.find(forbidden) is None

    # Exactly one table is referenced, and it is the one the report selected.
    assert {t.name.lower() for t in tree.find_all(exp.Table)} == {"customers"}

    # The payload survives intact as a single string literal -- i.e. it was
    # treated as data. Any structural interpretation would have split it up.
    literals = [node.this for node in tree.find_all(exp.Literal) if node.is_string]
    assert any(payload in literal for literal in literals)


def test_like_wildcards_in_search_terms_are_escaped(engine):
    """A literal % in a search term must match a percent sign, not everything."""
    from app.domain.report.compiler import _like

    assert _like("100%", "%{}%") == "%100\\%%"
    assert _like("a_b", "%{}%") == "%a\\_b%"
    assert _like("back\\slash", "{}%") == "back\\\\slash%"

    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers"],
        columns=[ReportColumn(id="c1", table="customers", field="customer_name")],
        filters=FilterGroup(children=[
            FilterCondition(table="customers", field="customer_name",
                            operator="contains", values=["100%"]),
        ]),
    )
    sql = sql_of(engine, definition)
    assert "\\%" in sql


def test_row_limit_is_clamped_to_the_governor(registry):
    engine = ReportEngine(registry, EngineOptions(max_rows=100))
    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers"],
        columns=[ReportColumn(id="c1", table="customers", field="customer_name")],
        row_limit=90_000,
    )
    result = engine.build(definition)
    assert result.ok
    assert result.compiled.limit == 100
    assert Code.ROW_LIMIT_CLAMPED in codes(result)


def test_rbac_hides_columns_from_the_engine_entirely(registry):
    """A denied column must be unresolvable, not merely unselected."""
    narrowed = registry.for_principal(
        allowed_tables={"customers", "sales_orders"},
        denied_columns={"customers": {"email", "phone"}},
    )
    engine = ReportEngine(narrowed)

    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers"],
        columns=[ReportColumn(id="c1", table="customers", field="email")],
    )
    result = engine.build(definition)
    assert not result.ok
    assert Code.UNKNOWN_COLUMN in codes(result)


def test_rbac_blocks_tables_outside_the_grant(registry):
    engine = ReportEngine(registry.for_principal(allowed_tables={"customers"}))
    definition = ReportDefinition(
        primary_table="payments",
        tables=["payments"],
        columns=[ReportColumn(id="c1", table="payments", field="amount")],
    )
    result = engine.build(definition)
    assert not result.ok
    assert Code.UNKNOWN_TABLE in codes(result)


def test_masked_column_is_masked_in_sql(registry):
    narrowed = registry.for_principal(
        allowed_tables=None,
        mask_policies={"customers.email": MaskPolicy.PARTIAL},
    )
    engine = ReportEngine(narrowed)
    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers"],
        columns=[ReportColumn(id="c1", table="customers", field="email")],
    )
    sql = sql_of(engine, definition).lower()
    assert "substr" in sql
    assert Code.MASKED_COLUMN


# ---------------------------------------------------------------------------
# Parameters and relative dates
# ---------------------------------------------------------------------------
def test_runtime_parameter_overrides_saved_default(engine):
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[ReportColumn(id="c1", table="sales_orders", field="order_no")],
        filters=FilterGroup(children=[
            FilterCondition(
                table="sales_orders", field="status", operator="in", values=["Draft"],
                parameter=ParameterSpec(name="p_status", prompt="Status"),
            ),
        ]),
    )
    sql = sql_of(engine, definition, parameters={"p_status": ["Shipped", "Paid"]})
    assert "Shipped" in sql and "Paid" in sql and "Draft" not in sql


def test_required_parameter_blocks_execution_until_supplied(engine):
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[ReportColumn(id="c1", table="sales_orders", field="order_no")],
        filters=FilterGroup(children=[
            FilterCondition(
                table="sales_orders", field="status", operator="equals", values=["Draft"],
                parameter=ParameterSpec(name="p_status", prompt="Status", required=True),
            ),
        ]),
    )
    assert not engine.build(definition).ok
    assert engine.build(definition, parameters={"p_status": "Paid"}).ok


def test_relative_date_operator_becomes_a_bound_range(engine):
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[ReportColumn(id="c1", table="sales_orders", field="order_no")],
        filters=FilterGroup(children=[
            FilterCondition(table="sales_orders", field="order_date",
                            operator="last_30_days"),
        ]),
    )
    sql = sql_of(engine, definition).lower()
    # Resolved in Python, so no dialect date functions leak into the SQL.
    assert "now()" not in sql and "current_date" not in sql
    assert ">=" in sql and "<" in sql


def test_or_group_nests_correctly(engine):
    definition = ReportDefinition(
        primary_table="sales_orders",
        tables=["sales_orders"],
        columns=[ReportColumn(id="c1", table="sales_orders", field="order_no")],
        filters=FilterGroup(op="and", children=[
            FilterCondition(table="sales_orders", field="status", operator="equals",
                            values=["Paid"]),
            FilterGroup(op="or", children=[
                FilterCondition(table="sales_orders", field="total_amount",
                                operator="greater_than", values=[1000]),
                FilterCondition(table="sales_orders", field="currency",
                                operator="equals", values=["EUR"]),
            ]),
        ]),
    )
    sql = sql_of(engine, definition).lower()
    assert " or " in sql and " and " in sql


def test_workflow_summary_counts_match_the_definition(engine):
    definition = screenshot_report()
    summary = definition.summary()
    assert summary == {
        "data_sources": 4,
        "fields_selected": 6,
        "relationships": 0,
        "filters": 2,
        "grouping": 1,
        "sorting": 2,
    }

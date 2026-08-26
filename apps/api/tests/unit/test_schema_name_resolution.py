"""
Names saved before a second schema was exposed.

Exposing another schema qualifies every colliding table name. Anything already
saved -- reports, dashboards, relationships -- still refers to the bare name,
and the tables it named have not gone anywhere. Failing those lookups turns a
naming change into data loss the user can see.
"""

from __future__ import annotations

import pytest

from app.domain.schema.registry import (
    ColumnMeta,
    DataType,
    SchemaRegistry,
    TableMeta,
)


def column(table: str, name: str) -> ColumnMeta:
    return ColumnMeta(
        name=name, table=table, data_type=DataType.TEXT, physical_type="text"
    )


def table(name: str, schema: str) -> TableMeta:
    return TableMeta(
        name=name,
        schema=schema,
        category="Test",
        display_name=name.title(),
        columns=(column(name, "id"), column(name, "label")),
    )


@pytest.fixture
def collided() -> SchemaRegistry:
    """Two schemas, each with a `customers`, plus one table unique to reporting."""
    return SchemaRegistry(
        tables=[
            table("customers", "reporting"),
            table("customers", "public"),
            table("shipments", "reporting"),
        ],
        relationships=[],
    )


def test_colliding_names_are_qualified_and_both_survive(collided):
    """Dropping the duplicate would hide a table someone needs."""
    keys = {meta.name for meta in collided.tables}
    assert keys == {"reporting.customers", "public.customers", "shipments"}


def test_a_bare_name_saved_earlier_still_resolves(collided):
    """
    The regression this exists for.

    Six saved reports stopped opening when a second schema was exposed, because
    they named `customers` and the registry had renamed it. The rows were fine;
    only the label had moved.
    """
    found = collided.table("customers")
    assert found is not None
    assert found.real_name == "customers"


def test_it_resolves_to_the_first_schema_listed(collided):
    """
    Ambiguous by construction, so the tie-break has to be the one that was in
    effect when the name was saved -- the first schema configured.
    """
    assert collided.table("customers").schema == "reporting"


def test_a_name_that_never_needed_qualifying_is_unaffected(collided):
    assert collided.table("shipments").schema == "reporting"


def test_a_qualified_name_still_reaches_the_schema_it_names(collided):
    """The fallback must not override an explicit choice."""
    assert collided.table("public.customers").schema == "public"
    assert collided.table("reporting.customers").schema == "reporting"


def test_a_table_that_genuinely_does_not_exist_is_still_missing(collided):
    """The fallback must not turn every typo into a silent match."""
    assert collided.table("no_such_table") is None
    assert collided.table("reporting.no_such_table") is None


def test_columns_resolve_through_a_bare_name_too(collided):
    """A report names a column as table.field; both halves have to land."""
    assert collided.column("customers", "label") is not None
    assert collided.has("customers", "label")
    assert not collided.has("customers", "no_such_column")


# ---------------------------------------------------------------------------
# Declared joins
# ---------------------------------------------------------------------------
def test_a_join_declared_under_a_bare_name_still_connects():
    """
    The second half of the same regression.

    Resolving the table list was not enough: a report that had chosen its own
    joins still named them bare, so the planner compared them against the
    resolved names, matched nothing, and reported a report with perfectly good
    joins as having none.
    """
    from app.domain.report.ir import ReportColumn, ReportDefinition, ReportJoin
    from app.domain.report.resolver import Resolver
    from app.domain.schema.registry import (
        Cardinality,
        RelationshipMeta,
        RelationshipSource,
    )

    registry = SchemaRegistry(
        tables=[
            table("orders", "reporting"),
            table("orders", "public"),
            table("invoices", "reporting"),
            table("invoices", "public"),
        ],
        relationships=[
            RelationshipMeta(
                id="r1",
                left_table="reporting.orders",
                left_column="id",
                right_table="reporting.invoices",
                right_column="label",
                cardinality=Cardinality.ONE_TO_MANY,
                source=RelationshipSource.INFERRED,
            )
        ],
    )

    definition = ReportDefinition(
        primary_table="orders",
        tables=["orders", "invoices"],
        joins=[
            ReportJoin(
                left_table="orders", left_column="id",
                right_table="invoices", right_column="label",
            )
        ],
        columns=[ReportColumn(id="c1", table="orders", field="label")],
    )

    joins = Resolver(registry).canonical_joins(definition)
    assert joins[0].left_table == "reporting.orders"
    assert joins[0].right_table == "reporting.invoices"
    # The columns are the user's choice and are not renamed.
    assert joins[0].left_column == "id"


def test_a_join_naming_a_table_that_does_not_exist_is_left_alone():
    """The table resolver reports that; renaming here would only reword it."""
    from app.domain.report.ir import ReportColumn, ReportDefinition, ReportJoin
    from app.domain.report.resolver import Resolver

    registry = SchemaRegistry(tables=[table("orders", "reporting")], relationships=[])
    definition = ReportDefinition(
        primary_table="orders",
        tables=["orders"],
        joins=[
            ReportJoin(
                left_table="orders", left_column="id",
                right_table="ghost_table", right_column="x",
            )
        ],
        columns=[ReportColumn(id="c1", table="orders", field="label")],
    )

    joins = Resolver(registry).canonical_joins(definition)
    assert joins[0].right_table == "ghost_table"

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

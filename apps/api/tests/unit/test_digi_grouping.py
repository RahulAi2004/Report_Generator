"""
One supplier, one heading.

DIGI's data lives in two places. The catalogue and the supplier's own answers
about orders come through its API; the orders themselves were placed from
BlankTex and live in its database. The field picker listed the first under
"DIGI / RIIN API" and scattered the second through "Purchasing" under names like
"Purchases" and "Suppliers (blanktex)" -- so the person looking at the DIGI
heading saw nine of sixteen tables and concluded the rest were missing.

Two things had to change for them to sit together, and both are tested here:
the API tables needed a heading that is not specific to APIs, and an admin
override had to be able to name one table when three schemas share its name.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.domain.schema.registry import ColumnMeta, DataType, TableMeta
from app.services.connector_service import _provider_category
from app.services.schema_service import _override_for


def table(name: str, schema: str) -> TableMeta:
    return TableMeta(
        name=name,
        schema=schema,
        category="Purchasing",
        display_name=None,
        columns=(ColumnMeta(name="id", table=name, data_type=DataType.TEXT,
                            physical_type="text"),),
    )


def override(label: str) -> SimpleNamespace:
    return SimpleNamespace(display_name=label, category="DIGI / RIIN")


# ---------------------------------------------------------------------------
# Overrides that can tell namesakes apart
# ---------------------------------------------------------------------------
def test_a_qualified_override_reaches_only_the_schema_it_names():
    """
    The case that made this necessary: `suppliers` exists in blanktex, public
    and reporting. Renaming the supplier record must leave the other two alone.
    """
    overrides = {"blanktex.suppliers": override("DIGI Supplier Record")}

    assert _override_for(table("suppliers", "blanktex"), overrides).display_name \
        == "DIGI Supplier Record"
    assert _override_for(table("suppliers", "public"), overrides) is None
    assert _override_for(table("suppliers", "reporting"), overrides) is None


def test_a_bare_override_still_applies_as_it_always_did():
    """Entries saved before this change must keep working."""
    overrides = {"purchases": override("Orders")}

    assert _override_for(table("purchases", "blanktex"), overrides).display_name == "Orders"


def test_the_qualified_entry_wins_when_both_exist():
    overrides = {
        "suppliers": override("Everyone's suppliers"),
        "blanktex.suppliers": override("DIGI Supplier Record"),
    }

    assert _override_for(table("suppliers", "blanktex"), overrides).display_name \
        == "DIGI Supplier Record"
    # A namesake without its own entry falls back to the bare one, unchanged.
    assert _override_for(table("suppliers", "public"), overrides).display_name \
        == "Everyone's suppliers"


# ---------------------------------------------------------------------------
# A heading that fits data from more than one place
# ---------------------------------------------------------------------------
def test_digi_tables_are_listed_under_the_suppliers_name():
    """Not "DIGI / RIIN API": the heading also holds BlankTex's DIGI tables."""
    assert _provider_category("riin") == "DIGI / RIIN"


def test_other_providers_keep_the_heading_they_had():
    """Nothing about Meta or Shippo changed, and their headings must not."""
    assert _provider_category("meta") == "Meta (Facebook & Instagram) API"
    assert _provider_category("shippo").endswith(" API")


def test_an_unknown_provider_still_gets_a_sensible_heading():
    assert _provider_category("stub") == "Stub API"

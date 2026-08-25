"""
Schema service.

Owns the cached schema registry: introspect once, serve many. Re-introspecting
per request would hammer the operational database's catalog and make the report
builder feel sluggish, so the snapshot is cached and refreshed explicitly.

Admin overrides stored in the metadata database (friendly names, categories,
masking, reporting flags) are layered on top of the physical snapshot here, so
the production database is never modified to make reports readable.
"""

from __future__ import annotations

import threading
import time

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.adapters.base import DatabaseAdapter, SchemaSnapshot
from app.adapters.factory import get_adapter
from app.core.security import Principal
from app.domain.schema.registry import (
    Cardinality,
    ColumnMeta,
    JoinType,
    MaskPolicy,
    RelationshipMeta,
    RelationshipSource,
    SchemaRegistry,
    TableMeta,
)
from app.models.metadata_models import (
    LogicalRelationship,
    SchemaColumnMeta,
    SchemaTable,
)

_lock = threading.Lock()
_snapshot: SchemaSnapshot | None = None
_scanned_at: float = 0.0
DEFAULT_CONNECTION_ID = "default"


def get_snapshot(refresh: bool = False) -> SchemaSnapshot:
    global _snapshot, _scanned_at
    with _lock:
        if _snapshot is None or refresh:
            adapter: DatabaseAdapter = get_adapter()
            _snapshot = adapter.introspect()
            _scanned_at = time.time()
        return _snapshot


def last_scanned_at() -> float:
    return _scanned_at


def build_registry(
    session: Session,
    principal: Principal | None = None,
    connection_id: str = DEFAULT_CONNECTION_ID,
    refresh: bool = False,
) -> SchemaRegistry:
    """Physical snapshot + admin overrides + logical relationships, then RBAC."""
    snapshot = get_snapshot(refresh=refresh)

    table_overrides = {
        row.physical_name: row
        for row in session.scalars(
            sa.select(SchemaTable).where(SchemaTable.connection_id == connection_id)
        )
    }
    column_overrides: dict[tuple[str, str], SchemaColumnMeta] = {
        (row.table_name, row.physical_name): row
        for row in session.scalars(
            sa.select(SchemaColumnMeta).where(
                SchemaColumnMeta.connection_id == connection_id
            )
        )
    }

    tables: list[TableMeta] = []
    for table in snapshot.tables:
        override = table_overrides.get(table.name)
        columns = tuple(
            _apply_column_override(column, column_overrides.get((table.name, column.name)))
            for column in table.columns
        )
        tables.append(
            TableMeta(
                name=table.name,
                schema=table.schema,
                kind=table.kind,
                display_name=(override.display_name if override else None) or table.display_name,
                category=(override.category if override else None) or table.category,
                description=(override.description if override else None) or table.description,
                estimated_rows=table.estimated_rows,
                columns=columns,
                enabled_for_reporting=override.enabled_for_reporting if override else True,
                enabled_for_ai=override.enabled_for_ai if override else True,
                is_sensitive=override.is_sensitive if override else False,
            )
        )

    relationships = list(snapshot.relationships)
    for logical in session.scalars(
        sa.select(LogicalRelationship).where(
            LogicalRelationship.connection_id == connection_id
        )
    ):
        relationships.append(
            RelationshipMeta(
                id=logical.id,
                left_table=logical.left_table,
                left_column=logical.left_column,
                right_table=logical.right_table,
                right_column=logical.right_column,
                cardinality=Cardinality(logical.cardinality),
                default_join_type=JoinType(logical.default_join_type),
                source=RelationshipSource(logical.source),
                confidence=logical.confidence,
            )
        )

    registry = SchemaRegistry(tables, relationships, connection_id)
    if principal is None:
        return registry

    return registry.for_principal(
        allowed_tables=principal.allowed_tables,
        denied_columns=principal.denied_columns,
        mask_policies=_mask_policies(column_overrides),
    )


def _apply_column_override(
    column: ColumnMeta, override: SchemaColumnMeta | None
) -> ColumnMeta:
    if override is None:
        return column
    return ColumnMeta(
        table=column.table,
        name=column.name,
        data_type=column.data_type,
        physical_type=column.physical_type,
        nullable=column.nullable,
        is_primary_key=column.is_primary_key,
        is_foreign_key=column.is_foreign_key,
        ordinal=column.ordinal,
        display_name=override.display_name or column.display_name,
        description=override.description or column.description,
        is_sensitive=override.is_sensitive,
        mask_policy=MaskPolicy(override.mask_policy),
        enabled_for_reporting=override.enabled_for_reporting,
        default_format=override.default_format,
    )


def _mask_policies(
    overrides: dict[tuple[str, str], SchemaColumnMeta]
) -> dict[str, MaskPolicy]:
    return {
        f"{table}.{column}".lower(): MaskPolicy(row.mask_policy)
        for (table, column), row in overrides.items()
        if row.mask_policy != MaskPolicy.NONE.value
    }


# ---------------------------------------------------------------------------
# Relationship inference (spec 1) -- proposed to an admin, never auto-applied.
# ---------------------------------------------------------------------------
def _identity_column(table) -> object | None:
    """
    The column another table would reference.

    Declared primary keys are preferred, but a view cannot have one -- and a
    schema of curated reporting views is exactly where relationship discovery
    matters most -- so fall back to the naming convention.
    """
    if len(table.primary_key) == 1:
        return table.primary_key[0]

    singular = table.name.lower().rstrip("s")
    for candidate in ("id", f"{singular}_id", f"{table.name.lower()}_id"):
        column = table.column(candidate)
        if column is not None:
            return column
    return None


def _entity_names(table_name: str) -> set[str]:
    """Forms of a table name that a `<stem>_id` column might refer to."""
    lowered = table_name.lower()
    names = {lowered}
    if lowered.endswith("ies"):
        names.add(lowered[:-3] + "y")
    if lowered.endswith("es"):
        names.add(lowered[:-2])
    if lowered.endswith("s"):
        names.add(lowered[:-1])
    return names


def infer_relationships(registry: SchemaRegistry) -> list[dict]:
    """
    Suggest links for schemas whose foreign keys were never declared.

    PostgreSQL views cannot carry foreign keys at all, so a reporting schema
    built from views arrives with no relationships whatsoever and no report can
    join anything until these are accepted.

    Only naming and type evidence is used, and every suggestion carries its
    reasoning. Nothing is activated until an administrator accepts it.
    """
    existing = {
        (r.left_table.lower(), r.left_column.lower(), r.right_table.lower(), r.right_column.lower())
        for r in registry.relationships
    }

    # entity name -> (table, its identity column)
    targets: dict[str, tuple] = {}
    for table in registry.tables:
        identity = _identity_column(table)
        if identity is None:
            continue
        for name in _entity_names(table.name):
            # A real table name wins over a singularised guess.
            if name not in targets or name == table.name.lower():
                targets[name] = (table, identity)

    suggestions: list[dict] = []
    for table in registry.tables:
        for column in table.columns:
            name = column.name.lower()
            if not name.endswith("_id") or column.is_primary_key:
                continue

            stem = name[:-3]
            target = targets.get(stem)
            if target is None:
                continue

            target_table, target_column = target
            if target_table.name.lower() == table.name.lower():
                continue  # self-reference; a person must confirm those
            if target_column.data_type != column.data_type:
                continue

            key = (
                target_table.name.lower(), target_column.name.lower(),
                table.name.lower(), name,
            )
            if key in existing:
                continue

            declared = bool(target_table.primary_key)
            suggestions.append({
                "left_table": target_table.name,
                "left_column": target_column.name,
                "right_table": table.name,
                "right_column": column.name,
                "cardinality": Cardinality.ONE_TO_MANY.value,
                "join_type": JoinType.LEFT.value,
                "confidence": 0.9 if declared else 0.75,
                "reason": (
                    f"{table.name}.{column.name} follows the naming convention for a "
                    f"reference to {target_table.name}.{target_column.name}, and both are "
                    f"{column.data_type.value}."
                    + ("" if declared else
                       f" {target_table.name} is a view, so its identity column was "
                       "identified by name rather than a declared primary key.")
                ),
            })

    suggestions.sort(key=lambda s: (s["right_table"], s["right_column"]))
    return suggestions


def invalidate() -> None:
    global _snapshot
    with _lock:
        _snapshot = None

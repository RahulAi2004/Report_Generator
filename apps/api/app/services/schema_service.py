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
def infer_relationships(registry: SchemaRegistry) -> list[dict]:
    """
    Suggest links for databases whose foreign keys were never declared.

    Only name-and-type evidence is used, and every suggestion is returned with
    its reasoning so an administrator can judge it. Nothing is activated until
    they accept it.
    """
    existing = {
        (r.left_table.lower(), r.left_column.lower(), r.right_table.lower(), r.right_column.lower())
        for r in registry.relationships
    }
    primary_keys = {
        table.name.lower(): table.primary_key[0]
        for table in registry.tables
        if len(table.primary_key) == 1
    }

    suggestions: list[dict] = []
    for table in registry.tables:
        for column in table.columns:
            if column.is_primary_key or not column.name.lower().endswith("_id"):
                continue

            stem = column.name.lower()[:-3]
            for candidate in (f"{stem}s", stem, f"{stem}es"):
                target_pk = primary_keys.get(candidate)
                if target_pk is None or candidate == table.name.lower():
                    continue
                if target_pk.data_type != column.data_type:
                    continue
                key = (candidate, target_pk.name.lower(), table.name.lower(), column.name.lower())
                if key in existing:
                    continue

                suggestions.append({
                    "left_table": candidate,
                    "left_column": target_pk.name,
                    "right_table": table.name,
                    "right_column": column.name,
                    "cardinality": Cardinality.ONE_TO_MANY.value,
                    "confidence": 0.8,
                    "reason": (
                        f"{table.name}.{column.name} matches the naming convention for a "
                        f"reference to {candidate}.{target_pk.name}, and the types agree."
                    ),
                })
                break
    return suggestions


def invalidate() -> None:
    global _snapshot
    with _lock:
        _snapshot = None

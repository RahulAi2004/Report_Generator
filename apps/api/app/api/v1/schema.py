"""
Schema explorer routes.

Everything the report builder renders on the left-hand side comes from here.
No table or column name is hardcoded anywhere in the frontend -- the UI is a
projection of whatever this endpoint returns for the signed-in user.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from app.core.db import get_session
from app.core.deps import current_principal, require, write_audit
from app.core.security import Permission, Principal
from app.domain.report.resolver import legal_operators
from app.domain.schema.registry import (
    Aggregation,
    DataType,
    RelationshipSource,
    TableMeta,
)
from app.models.metadata_models import LogicalRelationship
from app.adapters.factory import get_adapter
from app.core.config import settings
from app.services import schema_service

router = APIRouter(prefix="/schema", tags=["schema"])


def _table_payload(table: TableMeta, include_columns: bool = False) -> dict:
    payload = {
        "name": table.name,
        "label": table.label,
        "schema": table.schema,
        "kind": table.kind,
        "category": table.category,
        "description": table.description,
        "estimated_rows": table.estimated_rows,
        "column_count": len(table.columns),
        "primary_key": [column.name for column in table.primary_key],
        "is_sensitive": table.is_sensitive,
    }
    if include_columns:
        payload["columns"] = [_column_payload(column) for column in table.columns]
    return payload


def _column_payload(column) -> dict:
    return {
        "name": column.name,
        "label": column.label,
        "table": column.table,
        "data_type": column.data_type.value,
        "physical_type": column.physical_type,
        "nullable": column.nullable,
        "is_primary_key": column.is_primary_key,
        "is_foreign_key": column.is_foreign_key,
        "is_sensitive": column.is_sensitive,
        "is_masked": column.mask_policy.value != "none",
        # The UI never has to know which aggregations are legal for which type:
        # it renders exactly what the backend permits (spec 8).
        "aggregations": [a.value for a in column.legal_aggregations],
        "operators": list(legal_operators(column.data_type)),
    }


@router.get("/tables")
def list_tables(
    search: str | None = Query(default=None),
    category: str | None = Query(default=None),
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    """Tables grouped into business categories, as the Data Sources panel shows."""
    registry = schema_service.build_registry(db, principal)

    tables = registry.tables
    if search:
        # Name, label, category and description together.
        #
        # Searching only the name and label meant somebody who knew the data
        # came from a particular supplier searched for the supplier and found
        # nothing -- the tables were called "Catalogue styles" and it was the
        # category heading that carried the name they were looking for.
        needle = search.lower()
        tables = [
            table for table in tables
            if needle in table.name.lower()
            or needle in table.label.lower()
            or needle in table.category.lower()
            or needle in (table.schema or "").lower()
            or needle in (table.description or "").lower()
        ]
    if category:
        tables = [table for table in tables if table.category == category]

    grouped: dict[str, list[dict]] = {}
    for table in sorted(tables, key=lambda t: (t.category, t.label)):
        grouped.setdefault(table.category, []).append(_table_payload(table))

    return {
        "categories": [
            {"name": name, "tables": items} for name, items in sorted(grouped.items())
        ],
        "total_tables": len(tables),
    }


@router.get("/tables/{table_name}")
def get_table(
    table_name: str,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    registry = schema_service.build_registry(db, principal)
    table = registry.table(table_name)
    if table is None:
        raise HTTPException(status_code=404, detail="That table is not available to you.")
    return _table_payload(table, include_columns=True)


#: Distinct-value lookups are cached briefly. The filter editor asks for them as
#: the user types, and each one is a scan the operational database did not ask
#: for.
_VALUES_CACHE: dict[tuple, tuple[float, list]] = {}
_VALUES_TTL = 120.0
#: Beyond this a picker stops being useful and starts being a table dump.
MAX_DISTINCT_VALUES = 200


@router.get("/tables/{table_name}/columns/{column_name}/values")
def column_values(
    table_name: str,
    column_name: str,
    search: str | None = Query(default=None, max_length=100),
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    """
    The values a column actually holds.

    Without this a filter has to be typed from memory, and 'delivered' instead
    of 'Delivered' returns an empty report with nothing to explain why -- which
    is the most common way a correct-looking report is quietly wrong.
    """
    import time

    registry = schema_service.build_registry(db, principal)
    column = registry.column(table_name, column_name)
    if column is None:
        raise HTTPException(status_code=404, detail="That field is not available to you.")

    if column.data_type in (DataType.DATE, DataType.DATETIME, DataType.TIME,
                            DataType.JSON, DataType.BINARY):
        # Listing every timestamp helps nobody.
        return {"values": [], "supported": False, "reason": "not a categorical field"}

    key = (table_name.lower(), column_name.lower(), (search or "").lower())
    cached = _VALUES_CACHE.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _VALUES_TTL:
        return {"values": cached[1], "supported": True, "cached": True}

    table_meta = registry.table(table_name)
    adapter = schema_service.adapter_for(db)

    import sqlalchemy as sa

    from app.domain.report.compiler import _SA_TYPES, LIKE_ESCAPE

    source = sa.Table(
        # The physical table, not the registry key, which may carry a schema
        # prefix used only to keep colliding names apart.
        table_meta.real_name,
        sa.MetaData(),
        sa.Column(column.name, _SA_TYPES[column.data_type]()),
        schema=table_meta.schema if table_meta.schema not in (None, "public") else None,
    )
    target = source.c[column.name]
    # Enum columns have no comparison or pattern operator against text.
    comparable = sa.cast(target, sa.Text) if column.is_enum else target

    statement = sa.select(sa.distinct(comparable).label("value")).where(target.isnot(None))
    if search:
        # Reuses the compiler's escaping so a literal % in a search term behaves
        # the same here as it does in a report filter.
        from app.domain.report.compiler import _like

        statement = statement.where(
            sa.cast(target, sa.Text).ilike(_like(search, "%{}%"), escape=LIKE_ESCAPE)
        )
    statement = statement.order_by(sa.text("value")).limit(MAX_DISTINCT_VALUES + 1)

    try:
        if table_meta.kind == "upload":
            from app.services import hybrid_executor

            outcome = hybrid_executor._execute_local(statement, max_rows=MAX_DISTINCT_VALUES + 1)
        else:
            outcome = adapter.execute(statement, max_rows=MAX_DISTINCT_VALUES + 1)
    except Exception:
        # A picker that cannot load must not stop someone typing a value.
        return {"values": [], "supported": False, "reason": "could not be listed"}

    values = [row[0] for row in outcome.rows if row[0] is not None]
    truncated = len(values) > MAX_DISTINCT_VALUES
    values = [str(v) for v in values[:MAX_DISTINCT_VALUES]]

    _VALUES_CACHE[key] = (now, values)
    if len(_VALUES_CACHE) > 500:
        _VALUES_CACHE.clear()

    return {"values": values, "supported": True, "truncated": truncated}


@router.get("/relationships")
def list_relationships(
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    registry = schema_service.build_registry(db, principal)
    return {
        "relationships": [
            {
                "id": relationship.id,
                "left_table": relationship.left_table,
                "left_column": relationship.left_column,
                "right_table": relationship.right_table,
                "right_column": relationship.right_column,
                "cardinality": relationship.cardinality.value,
                "join_type": relationship.default_join_type.value,
                "source": relationship.source.value,
                "confidence": relationship.confidence,
            }
            for relationship in registry.relationships
        ]
    }


@router.get("/relationships/suggestions")
def suggest_relationships(
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_SCHEMA)),
):
    """Name-based suggestions for databases without declared foreign keys."""
    registry = schema_service.build_registry(db, principal)
    return {"suggestions": schema_service.infer_relationships(registry)}


class RelationshipInput(BaseModel):
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    cardinality: str = "1:N"
    join_type: str = "left"
    confidence: float = 0.8


@router.post("/relationships")
def create_relationships(
    payload: list[RelationshipInput],
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_SCHEMA)),
):
    """
    Accept logical relationships.

    Stored in the metadata database, never as constraints on the operational
    database (spec 1). This is the only way to make a schema of views joinable,
    since a view cannot carry a foreign key.
    """
    registry = schema_service.build_registry(db, principal)
    created, skipped = 0, 0

    for item in payload:
        # Both ends must exist and be visible to this admin.
        if not registry.has(item.left_table, item.left_column) or not registry.has(
            item.right_table, item.right_column
        ):
            skipped += 1
            continue

        duplicate = db.scalar(
            sa.select(LogicalRelationship).where(
                LogicalRelationship.connection_id == schema_service.DEFAULT_CONNECTION_ID,
                LogicalRelationship.left_table == item.left_table,
                LogicalRelationship.left_column == item.left_column,
                LogicalRelationship.right_table == item.right_table,
                LogicalRelationship.right_column == item.right_column,
            )
        )
        if duplicate is not None:
            skipped += 1
            continue

        left = registry.table(item.left_table)
        right = registry.table(item.right_table)
        db.add(
            LogicalRelationship(
                connection_id=schema_service.DEFAULT_CONNECTION_ID,
                left_table=item.left_table,
                left_column=item.left_column,
                right_table=item.right_table,
                right_column=item.right_column,
                left_schema=left.schema if left else None,
                right_schema=right.schema if right else None,
                cardinality=item.cardinality,
                default_join_type=item.join_type,
                source=RelationshipSource.INFERRED.value,
                confidence=item.confidence,
                created_by=principal.id,
            )
        )
        created += 1

    db.commit()
    write_audit(db, principal, "relationships_defined",
                payload={"created": created, "skipped": skipped})
    return {"created": created, "skipped": skipped}


@router.delete("/relationships/{relationship_id}")
def delete_relationship(
    relationship_id: str,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_SCHEMA)),
):
    """Remove a logical relationship. Physical foreign keys are not ours to delete."""
    row = db.get(LogicalRelationship, relationship_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Not found. Relationships discovered from foreign keys cannot be removed here.",
        )
    db.delete(row)
    db.commit()
    write_audit(db, principal, "relationship_removed", resource_id=relationship_id)
    return {"ok": True}


@router.post("/scan")
def rescan(
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_SCHEMA)),
):
    """Re-introspect the operational database and refresh the cached snapshot."""
    snapshot = schema_service.get_snapshot(refresh=True)
    write_audit(db, principal, "schema_scan", payload={"tables": len(snapshot.tables)})
    return {
        "tables": len(snapshot.tables),
        "relationships": len(snapshot.relationships),
        "dialect": snapshot.dialect,
    }


@router.get("/overview")
def overview(
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    """Headline numbers for the Data Sources landing page."""
    registry = schema_service.build_registry(db, principal)
    tables = registry.tables
    adapter = schema_service.adapter_for(db)
    return {
        # Which database these tables actually came from. Shown to signed-in
        # users so nobody builds a report against the wrong source. The host and
        # credentials are deliberately not included.
        "connection": {
            "database": settings.database_name,
            "dialect": adapter.dialect,
            "mode": settings.data_source_mode,
            "read_only_enforced": settings.database_enforce_read_only,
            "is_replica": settings.database_is_replica,
        },
        "table_count": len(tables),
        "column_count": sum(len(table.columns) for table in tables),
        "relationship_count": len(registry.relationships),
        "estimated_rows": sum(t.estimated_rows or 0 for t in tables),
        "categories": [
            {"name": name, "count": len(items)}
            for name, items in registry.categories().items()
        ],
        "tables_without_primary_key": [
            table.name for table in tables if not table.primary_key and table.kind == "table"
        ],
        "aggregations": [a.value for a in Aggregation],
    }

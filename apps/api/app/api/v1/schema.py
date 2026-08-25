"""
Schema explorer routes.

Everything the report builder renders on the left-hand side comes from here.
No table or column name is hardcoded anywhere in the frontend -- the UI is a
projection of whatever this endpoint returns for the signed-in user.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as DbSession

from app.core.db import get_session
from app.core.deps import current_principal, require, write_audit
from app.core.security import Permission, Principal
from app.domain.report.resolver import legal_operators
from app.domain.schema.registry import Aggregation, TableMeta
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
        needle = search.lower()
        tables = [
            table for table in tables
            if needle in table.name.lower() or needle in table.label.lower()
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
    adapter = get_adapter()
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
        "estimated_rows": sum(table.estimated_rows for table in tables),
        "categories": [
            {"name": name, "count": len(items)}
            for name, items in registry.categories().items()
        ],
        "tables_without_primary_key": [
            table.name for table in tables if not table.primary_key and table.kind == "table"
        ],
        "aggregations": [a.value for a in Aggregation],
    }

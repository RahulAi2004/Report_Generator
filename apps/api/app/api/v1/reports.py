"""
Report routes: validate, preview, run, save.

`/validate` is the endpoint the builder calls continuously as the user works. It
compiles the report without executing it, so diagnostics -- fan-out warnings,
ambiguous joins, illegal aggregations -- appear while the report is being built
rather than after a confusing result set comes back.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.adapters.base import QueryExecutionError
from app.adapters.factory import get_adapter
from app.core.config import settings
from app.core.db import get_session
from app.core.deps import client_ip, current_principal, require, write_audit
from app.core.security import Permission, Principal
from app.domain.report.engine import EngineOptions, ReportEngine
from app.domain.report.ir import ReportDefinition
from app.models.metadata_models import QueryHistory, Report, ReportRun
from app.services import schema_service

router = APIRouter(prefix="/reports", tags=["reports"])


# ---------------------------------------------------------------------------
class PreviewRequest(BaseModel):
    definition: ReportDefinition
    parameters: dict[str, Any] = Field(default_factory=dict)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=1000)


class SaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=190)
    description: str | None = None
    definition: ReportDefinition
    folder: str | None = None
    is_template: bool = False


def _engine(db: DbSession, principal: Principal) -> ReportEngine:
    registry = schema_service.build_registry(db, principal)
    adapter = get_adapter()
    return ReportEngine(
        registry,
        EngineOptions(
            max_joins=settings.query_max_joins,
            max_rows=settings.query_max_rows,
            max_subquery_depth=settings.query_max_subquery_depth + 1,
            dialect=adapter.dialect,
        ),
    )


def _serialize(value: Any) -> Any:
    """JSON-safe cell values. Formatting for display happens in the browser."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return "<binary>"
    return value


def _column_payload(column) -> dict:
    return {
        "id": column.id,
        "key": column.output_key,
        "label": column.display_name,
        "table": column.table,
        "field": column.field,
        "data_type": column.meta.data_type.value,
        "aggregation": column.aggregation.value,
        "align": column.align,
        "format": column.format.model_dump() if column.format else None,
        "is_masked": column.is_masked,
    }


# ---------------------------------------------------------------------------
@router.post("/validate")
def validate(
    payload: PreviewRequest,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    """Compile without executing. Powers the builder's live diagnostics bar."""
    engine = _engine(db, principal)
    result = engine.build(payload.definition, payload.parameters)

    return {
        "ok": result.ok,
        "summary": result.summary,
        "diagnostics": result.diagnostics_payload(),
        "join_plan": result.plan.as_dict() if result.plan else None,
        "fanout": {
            "corrected": result.fanout.corrected if result.fanout else False,
            "inflation_detected": result.fanout.inflation_detected if result.fanout else False,
            "branches": [
                {"table": branch.name, "strategy": branch.strategy.value,
                 "multiplies_rows": branch.multiplies}
                for branch in (result.fanout.branches if result.fanout else [])
            ],
        },
        "parameters": [p.model_dump() for p in payload.definition.parameters()],
        "columns": (
            [_column_payload(column) for column in result.compiled.output_columns]
            if result.compiled
            else []
        ),
    }


@router.post("/preview")
def preview(
    payload: PreviewRequest,
    request: Request,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.RUN_REPORT)),
):
    """Execute the report and return one page of rows (server-side pagination)."""
    engine = _engine(db, principal)
    offset = (payload.page - 1) * payload.page_size

    # Compile for one row beyond the page. The adapter then reports truncation
    # exactly, so the "Next" button is only offered when a next page really
    # exists -- rather than whenever the page happened to come back full.
    result = engine.build(
        payload.definition, payload.parameters, offset=offset, limit=payload.page_size + 1
    )
    if not result.ok:
        return {
            "ok": False,
            "diagnostics": result.diagnostics_payload(),
            "summary": result.summary,
            "columns": [],
            "rows": [],
        }

    adapter = get_adapter()
    started = time.perf_counter()
    try:
        outcome = adapter.execute(result.compiled.statement, max_rows=payload.page_size)
    except QueryExecutionError as error:
        # Query history keeps the technical cause; the response carries only the
        # user-safe message.
        _record_history(
            db, principal, engine, result, 0, 0, "error", error.detail or str(error)
        )
        write_audit(
            db, principal, "report_preview", success=False, ip=client_ip(request),
            payload={"tables": result.compiled.tables_used, "kind": error.kind},
        )
        raise HTTPException(status_code=400, detail=str(error)) from error

    duration_ms = int((time.perf_counter() - started) * 1000)
    columns = [_column_payload(column) for column in result.compiled.output_columns]
    keyed_rows = [
        {column["key"]: _serialize(value) for column, value in zip(columns, row)}
        for row in outcome.rows
    ]

    _record_history(
        db, principal, engine, result, duration_ms, outcome.row_count, "success", None
    )
    write_audit(
        db, principal, "report_preview", ip=client_ip(request), duration_ms=duration_ms,
        payload={"tables": result.compiled.tables_used, "rows": outcome.row_count},
    )

    return {
        "ok": True,
        "columns": columns,
        "rows": keyed_rows,
        "page": payload.page,
        "page_size": payload.page_size,
        "has_more": outcome.truncated,
        "duration_ms": duration_ms,
        "truncated": outcome.truncated,
        "diagnostics": result.diagnostics_payload(),
        "summary": result.summary,
        "fanout_corrected": result.fanout.corrected if result.fanout else False,
    }


@router.post("/sql")
def generated_sql(
    payload: PreviewRequest,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.VIEW_SQL)),
):
    """
    The SQL inspector (spec 45). Read-only, formatted, copyable.

    Literal values are shown only to users holding `view_query_values`, since a
    filter value can itself be sensitive.
    """
    engine = _engine(db, principal)
    result = engine.build(payload.definition, payload.parameters)
    if not result.ok:
        raise HTTPException(
            status_code=400,
            detail="This report cannot be compiled yet. Resolve the issues shown first.",
        )

    with_values = principal.can(Permission.VIEW_QUERY_VALUES)
    return {
        "sql": engine.render_sql(result.compiled, with_values=with_values),
        "values_included": with_values,
        "tables_used": result.compiled.tables_used,
        "joins": result.plan.as_dict()["steps"] if result.plan else [],
        "row_limit": result.compiled.limit,
    }


# ---------------------------------------------------------------------------
# Saved reports (spec 16)
# ---------------------------------------------------------------------------
@router.get("")
def list_reports(
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    rows = db.scalars(
        sa.select(Report)
        .where(Report.is_archived == False)  # noqa: E712
        .order_by(Report.updated_at.desc())
    ).all()
    return {
        "reports": [
            {
                "id": report.id,
                "name": report.name,
                "description": report.description,
                "folder": report.folder,
                "is_template": report.is_template,
                "is_favorite": report.is_favorite,
                "owner_id": report.owner_id,
                "updated_at": report.updated_at.isoformat(),
                "last_run_at": report.last_run_at.isoformat() if report.last_run_at else None,
                "run_count": report.run_count,
                "summary": ReportDefinition(**report.definition).summary(),
            }
            for report in rows
        ]
    }


@router.get("/{report_id}")
def get_report(
    report_id: str,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    report = db.get(Report, report_id)
    if report is None or report.is_archived:
        raise HTTPException(status_code=404, detail="Report not found.")
    return {
        "id": report.id,
        "name": report.name,
        "description": report.description,
        "folder": report.folder,
        "is_template": report.is_template,
        "is_favorite": report.is_favorite,
        "definition": report.definition,
        "updated_at": report.updated_at.isoformat(),
    }


@router.post("")
def create_report(
    payload: SaveRequest,
    request: Request,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.SAVE_REPORT)),
):
    report = Report(
        name=payload.name,
        description=payload.description,
        folder=payload.folder,
        is_template=payload.is_template,
        definition=payload.definition.model_dump(mode="json"),
        owner_id=principal.id,
    )
    db.add(report)
    db.commit()
    write_audit(db, principal, "report_created", resource_type="report",
                resource_id=report.id, ip=client_ip(request))
    return {"id": report.id, "name": report.name}


@router.put("/{report_id}")
def update_report(
    report_id: str,
    payload: SaveRequest,
    request: Request,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.SAVE_REPORT)),
):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found.")
    if report.owner_id != principal.id and not principal.is_admin:
        raise HTTPException(status_code=403, detail="This report belongs to someone else.")

    report.name = payload.name
    report.description = payload.description
    report.folder = payload.folder
    report.definition = payload.definition.model_dump(mode="json")
    db.commit()
    write_audit(db, principal, "report_modified", resource_type="report",
                resource_id=report.id, ip=client_ip(request))
    return {"id": report.id, "name": report.name}


@router.delete("/{report_id}")
def archive_report(
    report_id: str,
    request: Request,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.DELETE_REPORT)),
):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found.")
    # Archive rather than delete: a report someone relies on should never
    # vanish irrecoverably because of one click.
    report.is_archived = True
    db.commit()
    write_audit(db, principal, "report_archived", resource_type="report",
                resource_id=report.id, ip=client_ip(request))
    return {"ok": True}


# ---------------------------------------------------------------------------
def _record_history(
    db: DbSession,
    principal: Principal,
    engine: ReportEngine,
    result,
    duration_ms: int,
    row_count: int,
    status: str,
    error: str | None,
) -> None:
    try:
        db.add(
            QueryHistory(
                user_id=principal.id,
                generated_sql=(
                    engine.render_sql(result.compiled) if result.compiled else None
                ),
                tables_accessed=result.compiled.tables_used if result.compiled else [],
                duration_ms=duration_ms,
                row_count=row_count,
                status=status,
                error_message=error,
            )
        )
        db.add(
            ReportRun(
                user_id=principal.id,
                duration_ms=duration_ms,
                row_count=row_count,
                status=status,
                error_message=error,
            )
        )
        db.commit()
    except Exception:
        db.rollback()

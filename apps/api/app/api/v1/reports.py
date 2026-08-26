"""
Report routes: validate, preview, run, save.

`/validate` is the endpoint the builder calls continuously as the user works. It
compiles the report without executing it, so diagnostics -- fan-out warnings,
ambiguous joins, illegal aggregations -- appear while the report is being built
rather than after a confusing result set comes back.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.adapters.base import QueryExecutionError
from app.adapters.factory import get_adapter
from app.core.config import settings
from app.core.db import get_session
from app.core.deps import client_ip, current_principal, require, write_audit
from app.core.security import Permission, Principal
from app.domain.report.engine import EngineOptions, ReportEngine
from app.domain.report import exporters
from app.domain.report.ir import ReportDefinition
from app.models.metadata_models import QueryHistory, Report, ReportRun
from app.services import hybrid_executor, schema_service

router = APIRouter(prefix="/reports", tags=["reports"])


# ---------------------------------------------------------------------------
class PreviewRequest(BaseModel):
    definition: ReportDefinition
    parameters: dict[str, Any] = Field(default_factory=dict)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=1000)


class SaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=190)
    description: str | None = Field(default=None, max_length=2000)
    definition: ReportDefinition
    folder: str | None = None
    is_template: bool = False

    # Where it appears
    module: str | None = Field(default=None, max_length=80)
    section: str | None = Field(default=None, max_length=80)

    # Who can see it
    visibility: Literal["private", "team", "organization"] = "private"
    allow_duplicate: bool = True
    show_in_menu: bool = True

    # How it behaves
    save_filters_and_sorting: bool = True
    pin_to_dashboard: bool = False
    auto_refresh: bool = True
    is_draft: bool = False


def _engine(
    db: DbSession, principal: Principal, definition: ReportDefinition | None = None
) -> ReportEngine:
    registry = schema_service.build_registry(db, principal)
    adapter = get_adapter()

    # A report that mixes uploaded files with database tables is executed
    # against staged temporary tables, which are resolved through search_path
    # and therefore cannot be schema-qualified.
    qualify = True
    if definition is not None:
        tables = [definition.primary_table, *definition.tables]
        qualify = not hybrid_executor.needs_hybrid(registry, tables)

    return ReportEngine(
        registry,
        EngineOptions(
            max_joins=settings.query_max_joins,
            max_rows=settings.query_max_rows,
            max_subquery_depth=settings.query_max_subquery_depth + 1,
            dialect=adapter.dialect,
            qualify_schema=qualify,
        ),
    )


def _run(db: DbSession, principal: Principal, engine: ReportEngine, compiled, max_rows: int):
    """Execute wherever the report's sources live."""
    return hybrid_executor.execute(
        compiled, engine.registry, get_adapter(), max_rows=max_rows
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
    engine = _engine(db, principal, payload.definition)
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
    engine = _engine(db, principal, payload.definition)
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
        outcome = _run(db, principal, engine, result.compiled, payload.page_size)
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
    engine = _engine(db, principal, payload.definition)
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
# Total row count and exports
# ---------------------------------------------------------------------------
@router.post("/count")
def total_rows(
    payload: PreviewRequest,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.RUN_REPORT)),
):
    """
    Total rows the report would return, ignoring paging.

    A separate endpoint on purpose: counting means a second pass over the data,
    which is wasted work while someone is still adjusting columns. The builder
    asks for it once the preview settles, and the paginator degrades to
    "next page / previous page" if it is unavailable.
    """
    engine = _engine(db, principal, payload.definition)
    result = engine.build(payload.definition, payload.parameters, limit=1)
    if not result.ok:
        raise HTTPException(status_code=400, detail="This report is not valid yet.")

    adapter = get_adapter()
    inner = result.compiled.statement.limit(None).offset(None).order_by(None)
    counter = sa.select(sa.func.count()).select_from(inner.subquery("counted"))

    try:
        outcome = hybrid_executor.execute(
            _as_compiled(counter, result.compiled), engine.registry, adapter, max_rows=1
        )
    except QueryExecutionError as error:
        # Not being able to count is not a reason to fail the report.
        return {"total": None, "reason": str(error)}

    total = outcome.rows[0][0] if outcome.rows else None
    return {"total": int(total) if total is not None else None}


class ExportRequest(PreviewRequest):
    format: str = Field(default="csv", pattern="^(csv|xlsx|pdf)$")
    report_name: str = Field(default="report", max_length=120)


@router.post("/export")
def export_report(
    payload: ExportRequest,
    request: Request,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.EXPORT_DATA)),
):
    """
    Download the full report (spec 15).

    CSV streams straight from a server-side cursor, so file size is bounded by
    the governor rather than by memory. XLSX and PDF have to be assembled before
    they can be sent, so they are capped.
    """
    engine = _engine(db, principal, payload.definition)
    limit = settings.query_max_rows
    if payload.format == "pdf":
        limit = min(limit, exporters.MAX_PDF_ROWS)

    result = engine.build(payload.definition, payload.parameters, limit=limit)
    if not result.ok:
        raise HTTPException(
            status_code=400,
            detail="This report cannot be exported until the issues shown are resolved.",
        )

    adapter = get_adapter()
    columns = result.compiled.output_columns
    headers = [column.display_name for column in columns]
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", payload.report_name).strip("_") or "report"
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"{safe_name}_{stamp}.{payload.format}"

    write_audit(
        db, principal, "export_performed", ip=client_ip(request),
        payload={"format": payload.format, "tables": result.compiled.tables_used},
    )

    def row_stream():
        for _, chunk in adapter.stream(result.compiled.statement, chunk_size=2_000):
            yield from chunk

    disposition = {"Content-Disposition": f'attachment; filename="{filename}"'}

    try:
        if payload.format == "csv":
            return StreamingResponse(
                exporters.to_csv(headers, row_stream()),
                media_type="text/csv; charset=utf-8",
                headers=disposition,
            )

        rows = list(row_stream())
        if payload.format == "xlsx":
            content = exporters.to_xlsx(
                headers, rows, sheet_name=payload.report_name,
                number_formats=[_number_format(column) for column in columns],
            )
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            content = exporters.to_pdf(
                headers, rows, title=payload.report_name,
                subtitle=f"{len(rows):,} rows · generated {datetime.now():%d %b %Y %H:%M}",
            )
            media = "application/pdf"
    except QueryExecutionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return Response(content=content, media_type=media, headers=disposition)


def _as_compiled(statement, source):
    """Wrap a derived statement so it routes to the same engines as its source."""
    from dataclasses import replace

    return replace(source, statement=statement)


def _number_format(column) -> str | None:
    """Carry the report's formatting into the spreadsheet."""
    fmt = column.format
    if fmt is None:
        return None
    decimals = "0" * fmt.decimals
    tail = f".{decimals}" if fmt.decimals else ""
    match fmt.kind:
        case "currency":
            return f'#,##0{tail}'
        case "number":
            return f'#,##0{tail}' if fmt.thousands_separator else f'0{tail}'
        case "percent":
            return f'0{tail}"%"'
        case "date":
            return "dd mmm yyyy"
        case "datetime":
            return "dd mmm yyyy hh:mm"
    return None


# ---------------------------------------------------------------------------
# Saved reports (spec 16)
# ---------------------------------------------------------------------------
#: Default placement taxonomy. Stored in app settings once an administrator
#: edits it, so this is only the starting point rather than a fixed list.
DEFAULT_MODULES: list[dict] = [
    {"name": "CRM", "sections": ["Customers", "Leads", "Contacts", "Activity"]},
    {"name": "Sales", "sections": ["Orders", "Quotations", "Invoices", "Products"]},
    {"name": "Finance", "sections": ["Payments", "Receivables", "Reconciliation"]},
    {"name": "Purchasing", "sections": ["Purchase Orders", "Suppliers"]},
    {"name": "Fulfillment", "sections": ["Shipments", "Production", "Artwork"]},
]
MODULES_SETTING = "report_modules"


@router.get("/modules")
def list_modules(
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    """
    Where a saved report can be filed.

    Modules that reports already use are merged in, so a taxonomy edited
    directly in the database never orphans an existing report.
    """
    from app.models.metadata_models import AppSetting

    configured = db.get(AppSetting, MODULES_SETTING)
    modules = list(configured.value) if configured and configured.value else list(DEFAULT_MODULES)

    known = {module["name"]: set(module["sections"]) for module in modules}
    rows = db.execute(
        sa.select(Report.module, Report.section)
        .where(Report.module.isnot(None), Report.is_archived == False)  # noqa: E712
        .distinct()
    )
    for module, section in rows:
        known.setdefault(module, set())
        if section:
            known[module].add(section)

    order = [module["name"] for module in modules]
    return {
        "modules": [
            {"name": name, "sections": sorted(known[name])}
            for name in sorted(known, key=lambda n: (order.index(n) if n in order else 99, n))
        ]
    }


@router.get("")
def list_reports(
    module: str | None = Query(default=None),
    pinned: bool = Query(default=False),
    include_hidden: bool = Query(
        default=False,
        description="Include reports whose author kept them out of the menu.",
    ),
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    # "Private" has to mean private, not merely be labelled so. A private report
    # or an unfinished draft is visible to its owner and to an administrator.
    visible = sa.or_(
        Report.visibility.in_(("team", "organization")),
        Report.owner_id == principal.id,
    )
    if principal.is_admin:
        visible = sa.true()

    statement = (
        sa.select(Report)
        .where(Report.is_archived == False, visible)  # noqa: E712
        .where(sa.or_(Report.is_draft == False, Report.owner_id == principal.id))  # noqa: E712
    )

    # "Show in reports menu" is the author's choice about the listing, so it is
    # honoured here -- but never hides a report from its own owner.
    if not include_hidden:
        statement = statement.where(
            sa.or_(Report.show_in_menu == True, Report.owner_id == principal.id)  # noqa: E712
        )
    if module:
        statement = statement.where(Report.module == module)
    if pinned:
        statement = statement.where(Report.pin_to_dashboard == True)  # noqa: E712

    rows = db.scalars(statement.order_by(Report.updated_at.desc())).all()
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
                "module": report.module,
                "section": report.section,
                "visibility": report.visibility,
                "is_draft": report.is_draft,
                "pin_to_dashboard": report.pin_to_dashboard,
                "auto_refresh": report.auto_refresh,
                "show_in_menu": report.show_in_menu,
                "definition": report.definition if report.pin_to_dashboard else None,
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
        "module": report.module,
        "section": report.section,
        "visibility": report.visibility,
        "allow_duplicate": report.allow_duplicate,
        "show_in_menu": report.show_in_menu,
        "save_filters_and_sorting": report.save_filters_and_sorting,
        "pin_to_dashboard": report.pin_to_dashboard,
        "auto_refresh": report.auto_refresh,
        "is_draft": report.is_draft,
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
        definition=_definition_for_save(payload),
        owner_id=principal.id,
        **_placement(payload),
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
    report.definition = _definition_for_save(payload)
    for field, value in _placement(payload).items():
        setattr(report, field, value)
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
def _placement(payload: SaveRequest) -> dict:
    """The where/who/how fields, kept together so create and update agree."""
    return {
        "module": payload.module,
        "section": payload.section,
        "visibility": payload.visibility,
        "allow_duplicate": payload.allow_duplicate,
        "show_in_menu": payload.show_in_menu,
        "save_filters_and_sorting": payload.save_filters_and_sorting,
        "pin_to_dashboard": payload.pin_to_dashboard,
        "auto_refresh": payload.auto_refresh,
        "is_draft": payload.is_draft,
    }


def _definition_for_save(payload: SaveRequest) -> dict:
    """
    Strip filters and sorting when the author asked not to keep them.

    Otherwise everyone who opens the report inherits whatever the author
    happened to be looking at when they saved it.
    """
    definition = payload.definition
    if not payload.save_filters_and_sorting:
        definition = definition.model_copy(
            update={
                "filters": type(definition.filters)(),
                "sort_by": [],
            }
        )
    return definition.model_dump(mode="json")


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

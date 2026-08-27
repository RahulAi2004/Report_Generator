"""
Report Board routes.

The board is a management view of saved reports: what each one is, where it
appears, how much data it returns, and who can see it. Everything on it is
derived from the reports themselves -- nothing is stored twice, so the board
cannot disagree with the report it describes.

Two of its columns cost real database work. `records` and `empty_records` are
therefore not part of the listing: the board renders immediately from metadata,
then asks for counts separately, batched and cached. A count that cannot be
produced comes back as ``null`` and is rendered as a dash, never as zero -- "we
do not know" and "there are none" are different facts and must not look alike.
"""

from __future__ import annotations

import time
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.adapters.base import QueryExecutionError
from app.core.db import get_session
from app.core.deps import current_principal, require, write_audit
from app.core.security import Permission, Principal
from app.domain.report.ir import ReportDefinition
from app.models.metadata_models import Dashboard, Report, ReportRun, User
from app.services import hybrid_executor, schema_service

from app.api.v1.reports import _as_compiled, _engine

router = APIRouter(prefix="/board", tags=["board"])


# ---------------------------------------------------------------------------
def _visible(principal: Principal):
    """A private report is visible to its owner and to an administrator."""
    if principal.is_admin:
        return sa.true()
    return sa.or_(
        Report.visibility.in_(("team", "organization")),
        Report.owner_id == principal.id,
    )


def _dashboards_by_report(db: DbSession, principal: Principal) -> dict[str, list[dict]]:
    """
    Which dashboards show each report.

    Read out of the dashboard definitions rather than stored on the report, so
    removing a panel from a dashboard cannot leave the board claiming the report
    is still on it.
    """
    rows = db.scalars(
        sa.select(Dashboard).where(
            Dashboard.is_archived.is_(False),
            sa.or_(
                Dashboard.visibility != "private",
                Dashboard.owner_id == principal.id,
            ),
        )
    ).all()

    mapping: dict[str, list[dict]] = {}
    for dashboard in rows:
        for panel in (dashboard.definition or {}).get("reports", []):
            report_id = panel.get("report_id")
            if not report_id:
                continue
            entry = {"id": dashboard.id, "name": dashboard.name}
            listed = mapping.setdefault(report_id, [])
            if entry not in listed:
                listed.append(entry)
    return mapping


@router.get("/reports")
def board(
    module: str | None = Query(default=None),
    section: str | None = Query(default=None),
    search: str | None = Query(default=None, max_length=120),
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    """
    The board's rows: metadata only, so it renders without touching the
    operational database at all.
    """
    statement = (
        sa.select(Report)
        .where(Report.is_archived.is_(False), _visible(principal))
        .where(sa.or_(Report.is_draft.is_(False), Report.owner_id == principal.id))
    )
    if module:
        statement = statement.where(Report.module == module)
    if section:
        statement = statement.where(Report.section == section)
    if search:
        needle = f"%{search.lower()}%"
        statement = statement.where(
            sa.or_(
                sa.func.lower(Report.name).like(needle),
                sa.func.lower(sa.func.coalesce(Report.description, "")).like(needle),
            )
        )

    reports = db.scalars(statement.order_by(Report.name)).all()
    on_dashboards = _dashboards_by_report(db, principal)

    owners = {
        user.id: user.full_name
        for user in db.scalars(
            sa.select(User).where(User.id.in_({r.owner_id for r in reports} or {""}))
        ).all()
    }

    # The last successful run, for the "last run" column and as the fallback
    # record count when a live count is unavailable.
    last_runs: dict[str, ReportRun] = {}
    if reports:
        recent = db.scalars(
            sa.select(ReportRun)
            .where(
                ReportRun.report_id.in_([r.id for r in reports]),
                ReportRun.status == "success",
            )
            .order_by(ReportRun.started_at.desc())
        ).all()
        for run in recent:
            last_runs.setdefault(run.report_id, run)

    rows = []
    for report in reports:
        definition = report.definition or {}
        columns = definition.get("columns", [])
        rows.append({
            "id": report.id,
            "name": report.name,
            "description": report.description,
            "module": report.module,
            "section": report.section,
            "visibility": report.visibility,
            "is_draft": report.is_draft,
            "is_favorite": report.is_favorite,
            "show_in_menu": report.show_in_menu,
            "owner_id": report.owner_id,
            "owner_name": owners.get(report.owner_id),
            "is_mine": report.owner_id == principal.id,
            # Visible fields only: hidden columns are not part of the report as
            # anyone reading it experiences it.
            "field_count": sum(1 for c in columns if c.get("visible", True))
                           + len(definition.get("calculated_columns", [])),
            "table_count": len(definition.get("tables", [])),
            "dashboards": on_dashboards.get(report.id, []),
            "updated_at": report.updated_at.isoformat(),
            "last_run_at": report.last_run_at.isoformat() if report.last_run_at else None,
            "run_count": report.run_count,
        })

    return {
        "reports": rows,
        "total": len(rows),
        "can_delete": principal.can(Permission.DELETE_REPORT),
        "can_edit": principal.can(Permission.SAVE_REPORT),
    }


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------
class CountRequest(BaseModel):
    report_ids: list[str] = Field(min_length=1, max_length=50)
    #: Recompute rather than serving what is cached.
    refresh: bool = False


#: Counting is a full pass over the report's data. The board would otherwise
#: repeat that on every navigation, against an operational database that has
#: other work to do.
#:
#: In-process, so each worker warms its own: with N workers a count is repeated
#: at most N times per window rather than once. That is deliberate. A shared
#: cache means Redis, and a cache whose unavailability can fail a request is a
#: worse trade than counting twice -- measured at well under a second for the
#: whole board.
_COUNT_CACHE: dict[str, tuple[float, dict]] = {}
_COUNT_TTL = 300.0


def _count_report(
    db: DbSession, principal: Principal, definition: ReportDefinition
) -> dict[str, Any]:
    """
    How many rows the report returns, and how many of them have a gap.

    "Empty" means at least one of the report's visible columns is null on that
    row. It is a data-quality signal about the report as it is actually read,
    which is why hidden columns do not count towards it.
    """
    engine = _engine(db, principal, definition)
    result = engine.build(definition, limit=1)
    if not result.ok:
        return {
            "records": None,
            "empty_records": None,
            "error": next(
                (d["message"] for d in result.diagnostics_payload()
                 if d["severity"] == "error"),
                "This report is not valid.",
            ),
        }

    inner = result.compiled.statement.limit(None).offset(None).order_by(None).subquery("r")
    visible = [
        column for column in result.compiled.output_columns
        if getattr(column, "visible", True)
    ] or list(result.compiled.output_columns)

    gap = sa.or_(*[inner.c[column.output_key].is_(None) for column in visible]) \
        if visible else sa.false()

    counter = sa.select(
        sa.func.count().label("records"),
        # CASE rather than FILTER: FILTER is PostgreSQL-only and this has to
        # compile on every dialect the adapters support.
        sa.func.sum(sa.case((gap, 1), else_=0)).label("empty_records"),
    ).select_from(inner)

    try:
        outcome = hybrid_executor.execute(
            _as_compiled(counter, result.compiled), engine.registry,
            schema_service.adapter_for(db), max_rows=1
        )
    except QueryExecutionError as error:
        # Not being able to count is not a reason to fail the row.
        return {"records": None, "empty_records": None, "error": str(error)}

    if not outcome.rows:
        return {"records": 0, "empty_records": 0, "error": None}
    records, empty = outcome.rows[0]
    return {
        "records": int(records or 0),
        "empty_records": int(empty or 0),
        "error": None,
    }


@router.post("/counts")
def counts(
    payload: CountRequest,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.RUN_REPORT)),
):
    """
    Record counts for the reports on screen, in one request.

    Reports the caller may not see are simply absent from the response rather
    than reported as failures -- the board should not become a way to discover
    that a private report exists.
    """
    now = time.monotonic()
    results: dict[str, dict] = {}

    for report_id in dict.fromkeys(payload.report_ids):
        report = db.get(Report, report_id)
        if report is None:
            continue
        if (
            report.visibility == "private"
            and report.owner_id != principal.id
            and not principal.is_admin
        ):
            continue

        key = f"{report_id}:{principal.id}"
        cached = _COUNT_CACHE.get(key)
        if cached and not payload.refresh and now - cached[0] < _COUNT_TTL:
            results[report_id] = {**cached[1], "cached": True}
            continue

        try:
            definition = ReportDefinition.model_validate(report.definition)
        except Exception:
            results[report_id] = {
                "records": None, "empty_records": None,
                "error": "This report was saved in a form this version cannot read.",
            }
            continue

        outcome = _count_report(db, principal, definition)
        _COUNT_CACHE[key] = (now, outcome)
        results[report_id] = {**outcome, "cached": False}

    if len(_COUNT_CACHE) > 2000:
        _COUNT_CACHE.clear()

    return {"counts": results}


# ---------------------------------------------------------------------------
@router.post("/reports/{report_id}/duplicate")
def duplicate(
    report_id: str,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.SAVE_REPORT)),
):
    """
    Copy a report so it can be changed without touching the original.

    The author's "allow duplicate" choice is honoured: some reports are the
    agreed definition of a number, and a fork of one is how two teams end up
    quoting different figures for the same thing.
    """
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found.")
    if report.visibility == "private" and report.owner_id != principal.id:
        raise HTTPException(status_code=404, detail="Report not found.")
    if not report.allow_duplicate and report.owner_id != principal.id:
        raise HTTPException(
            status_code=403,
            detail="This report's author did not allow copies to be made of it.",
        )

    copy = Report(
        name=f"{report.name} (copy)",
        description=report.description,
        connection_id=report.connection_id,
        definition=report.definition,
        owner_id=principal.id,
        folder=report.folder,
        module=report.module,
        section=report.section,
        # A copy starts private whatever the original was: sharing is a decision
        # its new owner makes, not one inherited.
        visibility="private",
        allow_duplicate=report.allow_duplicate,
        show_in_menu=report.show_in_menu,
        save_filters_and_sorting=report.save_filters_and_sorting,
        auto_refresh=report.auto_refresh,
    )
    db.add(copy)
    db.commit()
    write_audit(db, principal, "report_duplicated", resource_id=copy.id,
                payload={"from": report_id})
    return {"id": copy.id, "name": copy.name}

"""
Dashboard routes: preview, run, save.

`/preview` is what the builder calls as the user works. It computes every metric
card and returns each one's number together with what it was actually measured
over -- the window, the filters that reached it, and the filters that could not.
A dashboard whose chips claim more than its queries did is the failure mode
worth engineering against, so every panel reports its own truth.

No SQL is written here. Cards and panels are translated into report definitions
and handed to the report engine, which is the only thing in this application
that talks to a database.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.adapters.base import QueryExecutionError
from app.core.db import get_session
from app.core.deps import client_ip, current_principal, require, write_audit
from app.core.security import Permission, Principal
from app.domain.dashboard import builder as dashboard_builder
from app.domain.dashboard.ir import (
    PERIOD_CHOICES,
    DashboardDefinition,
    MetricCard,
)
from app.domain.report.ir import ReportDefinition
from app.models.metadata_models import Dashboard, Report
from app.services import schema_service

from app.api.v1.reports import (
    _column_payload,
    _engine,
    _run,
    _serialize,
)

router = APIRouter(prefix="/dashboards", tags=["dashboards"])


def _today() -> date:
    """
    Server's date, resolved once per request.

    Once per request rather than once per card: a dashboard rendered across
    midnight would otherwise measure its cards over two different windows and
    the comparison between them would be meaningless.
    """
    return datetime.now(timezone.utc).date()


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class PreviewRequest(BaseModel):
    definition: DashboardDefinition


class PanelRequest(BaseModel):
    definition: DashboardDefinition
    #: Which embedded report panel to run.
    panel_id: str
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=200)


class SaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=190)
    description: str | None = Field(default=None, max_length=2000)
    definition: DashboardDefinition
    visibility: Literal["private", "team", "organization"] = "private"
    show_in_menu: bool = True
    is_default: bool = False


# ---------------------------------------------------------------------------
# Metric cards
# ---------------------------------------------------------------------------
def _run_metric(
    db: DbSession,
    principal: Principal,
    dashboard: DashboardDefinition,
    card: MetricCard,
    today: date,
    window: tuple[date, date] | None = None,
) -> tuple[Any, list[str], dashboard_builder.Applied]:
    """One card's number, plus anything that stopped it being computed."""
    registry = schema_service.build_registry(db, principal)
    definition, applied = dashboard_builder.metric_report(
        dashboard, card, registry, today, window=window
    )

    engine = _engine(db, principal, definition)
    result = engine.build(definition)
    if not result.ok:
        problems = [
            d["message"] for d in result.diagnostics_payload() if d["severity"] == "error"
        ]
        return None, problems, applied

    outcome = _run(db, principal, engine, result.compiled, 1)
    value = outcome.rows[0][0] if outcome.rows else None
    return _serialize(value), [], applied


def _delta(current: Any, previous: Any) -> dict | None:
    """
    The change between two periods.

    A percentage against a base of zero is not "infinite growth", it is
    undefined -- so the direction is reported and the percentage is left out
    rather than invented.
    """
    if current is None or previous is None:
        return None
    try:
        current_value, previous_value = float(current), float(previous)
    except (TypeError, ValueError):
        return None

    difference = current_value - previous_value
    if previous_value == 0:
        return {
            "previous": previous_value,
            "difference": difference,
            "percent": None,
            "direction": "up" if difference > 0 else "flat" if difference == 0 else "down",
        }
    percent = difference / abs(previous_value) * 100
    return {
        "previous": previous_value,
        "difference": difference,
        "percent": round(percent, 1),
        "direction": "up" if difference > 0 else "flat" if difference == 0 else "down",
    }


@router.post("/preview")
def preview(
    payload: PreviewRequest,
    request: Request,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.RUN_REPORT)),
):
    """Every metric card on the dashboard, with what each was measured over."""
    dashboard = payload.definition
    today = _today()
    started = time.perf_counter()

    window = dashboard.time_range.resolve(today)
    previous_window = dashboard.time_range.previous(today)

    cards: list[dict] = []
    for card in dashboard.metrics:
        card_window = None if card.ignore_time_range else window
        try:
            value, errors, applied = _run_metric(
                db, principal, dashboard, card, today, window=card_window
            )
        except QueryExecutionError as error:
            cards.append({
                "id": card.id, "title": card.title, "value": None,
                "errors": [str(error)], "filters": {},
            })
            continue

        entry: dict[str, Any] = {
            "id": card.id,
            "title": card.title,
            "value": value,
            "format": card.format,
            "currency": card.currency,
            "decimals": card.decimals,
            "icon": card.icon,
            "tone": card.tone,
            "errors": errors,
            "filters": applied.as_payload(),
            "window": (
                None if card.ignore_time_range or card_window is None
                else [card_window[0].isoformat(), card_window[1].isoformat()]
            ),
            "caption": "All time" if card.ignore_time_range or window is None
                       else dashboard.time_range.label(),
        }

        # The caption under the number, computed rather than typed.
        if not errors and card.comparison == "previous_period" and previous_window:
            try:
                before, _, _ = _run_metric(
                    db, principal, dashboard, card, today, window=previous_window
                )
                entry["delta"] = _delta(value, before)
                entry["caption"] = f"vs previous {dashboard.time_range.label().lower()}"
                entry["higher_is_better"] = card.higher_is_better
            except QueryExecutionError:
                entry["delta"] = None
        elif not errors and card.comparison == "share_of_total":
            try:
                # The same measure with neither the window nor the card's own
                # filters, which is what "of total" has to mean to be a share.
                whole = card.model_copy(deep=True)
                whole.filters = type(card.filters)()
                whole.ignore_time_range = True
                total, _, _ = _run_metric(db, principal, dashboard, whole, today)
                if total not in (None, 0) and value is not None:
                    entry["share"] = round(float(value) / float(total) * 100, 1)
                    entry["caption"] = f"of {_number(total)} total"
            except QueryExecutionError:
                entry["share"] = None

        cards.append(entry)

    duration_ms = int((time.perf_counter() - started) * 1000)
    write_audit(
        db, principal, "dashboard_preview", ip=client_ip(request),
        duration_ms=duration_ms, payload={"cards": len(cards)},
    )

    return {
        "ok": True,
        "metrics": cards,
        "time_range": {
            "label": dashboard.time_range.label(),
            "window": None if window is None else [window[0].isoformat(), window[1].isoformat()],
            "previous": (
                None if previous_window is None
                else [previous_window[0].isoformat(), previous_window[1].isoformat()]
            ),
        },
        "summary": dashboard.summary(),
        "duration_ms": duration_ms,
    }


def _number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{int(number):,}" if number == int(number) else f"{number:,.2f}"


# ---------------------------------------------------------------------------
# Embedded report panels
# ---------------------------------------------------------------------------
@router.post("/panel")
def run_panel(
    payload: PanelRequest,
    request: Request,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.RUN_REPORT)),
):
    """
    One embedded report, as the dashboard's filters leave it.

    The saved report is read but never written: what the dashboard narrows is a
    copy, so the report still opens unchanged from the Reports section.
    """
    dashboard = payload.definition
    panel = next((p for p in dashboard.reports if p.id == payload.panel_id), None)
    if panel is None:
        raise HTTPException(status_code=404, detail="That panel is not on this dashboard.")

    saved = db.get(Report, panel.report_id)
    if saved is None:
        raise HTTPException(
            status_code=404,
            detail="The report this panel shows has been deleted. Remove the panel or point it at another report.",
        )
    if saved.visibility == "private" and saved.owner_id != principal.id:
        raise HTTPException(status_code=403, detail="That report is private.")

    definition = ReportDefinition.model_validate(saved.definition)
    prepared, applied = dashboard_builder.apply_to_report(
        dashboard, panel, definition, _today()
    )

    engine = _engine(db, principal, prepared)
    offset = (payload.page - 1) * payload.page_size
    result = engine.build(prepared, {}, offset=offset, limit=payload.page_size + 1)
    if not result.ok:
        return {
            "ok": False,
            "title": panel.title or saved.name,
            "diagnostics": result.diagnostics_payload(),
            "columns": [],
            "rows": [],
            "filters": applied.as_payload(),
        }

    started = time.perf_counter()
    try:
        outcome = _run(db, principal, engine, result.compiled, payload.page_size)
    except QueryExecutionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    duration_ms = int((time.perf_counter() - started) * 1000)

    columns = [_column_payload(column) for column in result.compiled.output_columns]
    if panel.columns:
        # Column choice is a view over the report, not a change to it.
        chosen = set(panel.columns)
        keep = [column for column in columns if column["key"] in chosen]
        if keep:
            columns = keep

    keys = [column["key"] for column in columns]
    all_keys = [c["key"] for c in
                (_column_payload(c) for c in result.compiled.output_columns)]
    rows = [
        {key: _serialize(value) for key, value in zip(all_keys, row) if key in keys}
        for row in outcome.rows
    ]

    write_audit(
        db, principal, "dashboard_panel", ip=client_ip(request), duration_ms=duration_ms,
        resource_id=panel.report_id, payload={"rows": outcome.row_count},
    )

    return {
        "ok": True,
        "title": panel.title or saved.name,
        "source": saved.name,
        "report_id": saved.id,
        "columns": columns,
        "rows": rows,
        "page": payload.page,
        "page_size": payload.page_size,
        "has_more": outcome.truncated,
        "duration_ms": duration_ms,
        "filters": applied.as_payload(),
        "diagnostics": result.diagnostics_payload(),
    }


# ---------------------------------------------------------------------------
# Where dashboards can be filed, and what they can be built from
# ---------------------------------------------------------------------------
@router.get("/options")
def options(
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    """
    Everything the builder's dropdowns need, resolved from what actually exists.

    The apps and modules are the taxonomy reports already file into, so a
    dashboard and the reports on it end up in the same place.
    """
    from app.api.v1.reports import list_modules

    taxonomy = list_modules(db=db, principal=principal)
    reports = db.scalars(
        sa.select(Report)
        .where(Report.is_archived.is_(False), Report.is_draft.is_(False))
        .where(sa.or_(Report.visibility != "private", Report.owner_id == principal.id))
        .order_by(Report.name)
    ).all()

    return {
        "apps": [
            {"name": module["name"], "modules": module["sections"]}
            for module in taxonomy["modules"]
        ],
        "reports": [
            {
                "id": report.id,
                "name": report.name,
                "module": report.module,
                "section": report.section,
                "tables": list(report.definition.get("tables", [])),
            }
            for report in reports
        ],
        "period_choices": {key: list(value) for key, value in PERIOD_CHOICES.items()},
    }


@router.get("/suggest-date-field")
def suggest_date_field(
    table: str = Query(min_length=1),
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    """A date column the time range can sensibly be measured against."""
    registry = schema_service.build_registry(db, principal)
    suggestion = dashboard_builder.suggest_date_field(table, registry)
    return {"date_field": suggestion.model_dump() if suggestion else None}


# ---------------------------------------------------------------------------
# Saved dashboards
# ---------------------------------------------------------------------------
def _visible(principal: Principal):
    return sa.or_(Dashboard.visibility != "private", Dashboard.owner_id == principal.id)


def _payload(dashboard: Dashboard, include_definition: bool = False) -> dict:
    body = {
        "id": dashboard.id,
        "name": dashboard.name,
        "description": dashboard.description,
        "app": dashboard.app,
        "module": dashboard.module,
        "visibility": dashboard.visibility,
        "show_in_menu": dashboard.show_in_menu,
        "is_default": dashboard.is_default,
        "updated_at": dashboard.updated_at.isoformat(),
        "view_count": dashboard.view_count,
    }
    if include_definition:
        body["definition"] = dashboard.definition
    return body


@router.get("")
def list_dashboards(
    app: str | None = Query(default=None),
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    query = (
        sa.select(Dashboard)
        .where(Dashboard.is_archived.is_(False), _visible(principal))
        .order_by(Dashboard.is_default.desc(), Dashboard.name)
    )
    if app:
        query = query.where(Dashboard.app == app)
    return {"dashboards": [_payload(row) for row in db.scalars(query).all()]}


@router.post("")
def create_dashboard(
    payload: SaveRequest,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.SAVE_REPORT)),
):
    dashboard = Dashboard(
        name=payload.name.strip(),
        description=payload.description,
        definition=payload.definition.model_dump(mode="json"),
        owner_id=principal.id,
        app=payload.definition.app,
        module=payload.definition.module,
        visibility=payload.visibility,
        show_in_menu=payload.show_in_menu,
        is_default=payload.is_default,
    )
    if payload.is_default:
        _clear_default(db, dashboard.app)
    db.add(dashboard)
    db.commit()
    write_audit(db, principal, "dashboard_created", resource_id=dashboard.id,
                payload={"name": dashboard.name})
    return {"id": dashboard.id, "name": dashboard.name}


@router.get("/{dashboard_id}")
def get_dashboard(
    dashboard_id: str,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    dashboard = db.get(Dashboard, dashboard_id)
    if dashboard is None or (
        dashboard.visibility == "private" and dashboard.owner_id != principal.id
    ):
        raise HTTPException(status_code=404, detail="Dashboard not found.")

    dashboard.view_count += 1
    dashboard.last_viewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return _payload(dashboard, include_definition=True)


@router.put("/{dashboard_id}")
def update_dashboard(
    dashboard_id: str,
    payload: SaveRequest,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.SAVE_REPORT)),
):
    dashboard = db.get(Dashboard, dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Dashboard not found.")
    if dashboard.owner_id != principal.id and not principal.can(Permission.MANAGE_SCHEMA):
        raise HTTPException(status_code=403, detail="This dashboard belongs to someone else.")

    dashboard.name = payload.name.strip()
    dashboard.description = payload.description
    dashboard.definition = payload.definition.model_dump(mode="json")
    dashboard.app = payload.definition.app
    dashboard.module = payload.definition.module
    dashboard.visibility = payload.visibility
    dashboard.show_in_menu = payload.show_in_menu
    if payload.is_default and not dashboard.is_default:
        _clear_default(db, dashboard.app)
    dashboard.is_default = payload.is_default

    db.commit()
    write_audit(db, principal, "dashboard_updated", resource_id=dashboard.id)
    return {"id": dashboard.id, "name": dashboard.name}


@router.delete("/{dashboard_id}")
def delete_dashboard(
    dashboard_id: str,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.SAVE_REPORT)),
):
    dashboard = db.get(Dashboard, dashboard_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Dashboard not found.")
    if dashboard.owner_id != principal.id and not principal.can(Permission.MANAGE_SCHEMA):
        raise HTTPException(status_code=403, detail="This dashboard belongs to someone else.")

    # Archived rather than deleted: a dashboard people had bookmarked should be
    # recoverable, and the audit trail should still resolve its name.
    dashboard.is_archived = True
    db.commit()
    write_audit(db, principal, "dashboard_deleted", resource_id=dashboard_id)
    return {"ok": True}


def _clear_default(db: DbSession, app: str | None) -> None:
    """Only one dashboard per app opens by default."""
    db.execute(
        sa.update(Dashboard)
        .where(Dashboard.app == app, Dashboard.is_default.is_(True))
        .values(is_default=False)
    )

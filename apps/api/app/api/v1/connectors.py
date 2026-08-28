"""
API connector routes.

The order the screen follows, and the order these enforce: paste a credential,
find out what it can reach, choose from that, then sync. Nothing is typed from
memory, because a mistyped ad account id produces an empty table rather than an
error, and an empty table looks like a business with no spend.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.core.db import get_session
from app.core.deps import client_ip, current_principal, require, write_audit
from app.core.security import Permission, Principal
from app.core.secrets import SecretUnavailable, encrypt_password, encryption_available
from app.models.metadata_models import ApiConnector, ConnectorDataset
from app.services import connector_service, schema_service
from app.services.connectors import registry as provider_registry
from app.services.connectors.base import ConnectorError

router = APIRouter(prefix="/connectors", tags=["connectors"])


def _spec(provider: str):
    spec = provider_registry.spec(provider)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"'{provider}' cannot be connected.")
    return spec


# ---------------------------------------------------------------------------
class TokenInput(BaseModel):
    provider: str = Field(default="meta", max_length=40)
    token: str = Field(min_length=8, max_length=4000)
    api_version: str = Field(default="", max_length=20)
    #: Not secret -- it is in Meta's own URLs.
    app_id: str = Field(default="", max_length=60)
    app_secret: str = Field(default="", max_length=200)
    #: Trade a short-lived token for a sixty-day one before doing anything else.
    exchange_for_long_lived: bool = True


class ConnectorInput(BaseModel):
    provider: str = Field(default="meta", max_length=40)
    name: str = Field(min_length=1, max_length=120)
    token: str | None = Field(default=None, max_length=4000)
    app_id: str = Field(default="", max_length=60)
    #: Omitted on update means "keep the stored one".
    app_secret: str | None = Field(default=None, max_length=200)
    api_version: str = Field(default="", max_length=20)
    exchange_for_long_lived: bool = True
    sync_interval_minutes: int = Field(default=60, ge=15, le=1440)
    is_active: bool = True


class DatasetInput(BaseModel):
    dataset_key: str = Field(min_length=1, max_length=60)
    resource_id: str = Field(min_length=1, max_length=120)
    resource_name: str = Field(default="", max_length=190)
    display_name: str = Field(default="", max_length=190)
    lookback_days: int = Field(default=30, ge=1, le=730)


def _connector_payload(connector: ApiConnector, datasets: list[ConnectorDataset]) -> dict:
    return {
        "id": connector.id,
        "provider": connector.provider,
        "provider_label": (
            spec.label if (spec := provider_registry.spec(connector.provider))
            else connector.provider
        ),
        "name": connector.name,
        "api_version": connector.api_version,
        "app_id": connector.app_id,
        "has_app_secret": bool(connector.app_secret_encrypted),
        "token_expires_at": (
            connector.token_expires_at.isoformat()
            if connector.token_expires_at else None
        ),
        "is_active": connector.is_active,
        "sync_interval_minutes": connector.sync_interval_minutes,
        "last_checked_at": (
            connector.last_checked_at.isoformat() if connector.last_checked_at else None
        ),
        "last_error": connector.last_error,
        # What discovery last found. Never the token.
        "discovery": connector.discovery,
        "created_at": connector.created_at.isoformat(),
        "datasets": [_dataset_payload(dataset) for dataset in datasets],
    }


def _dataset_payload(dataset: ConnectorDataset) -> dict:
    return {
        "id": dataset.id,
        "connector_id": dataset.connector_id,
        "dataset_key": dataset.dataset_key,
        "resource_id": dataset.resource_id,
        "resource_name": dataset.resource_name,
        "display_name": dataset.display_name,
        "table_name": f"api_{dataset.display_name.lower().replace(' ', '_')}",
        "columns": dataset.columns,
        "column_count": len(dataset.columns),
        "row_count": dataset.row_count,
        "lookback_days": dataset.lookback_days,
        "is_enabled": dataset.is_enabled,
        "status": dataset.status,
        "last_error": dataset.last_error,
        "last_synced_at": (
            dataset.last_synced_at.isoformat() if dataset.last_synced_at else None
        ),
        "last_duration_ms": dataset.last_duration_ms,
    }


# ---------------------------------------------------------------------------
@router.get("/providers")
def providers(principal: Principal = Depends(current_principal)):
    """What can be connected, which credentials each needs, and what it offers."""
    return {
        "providers": [
            {
                "key": spec.key,
                "label": spec.label,
                "where_to_find": spec.where_to_find,
                "default_api_version": spec.default_api_version,
                "supports_token_exchange": spec.supports_token_exchange,
                "credentials": [
                    {
                        "key": field.key,
                        "label": field.label,
                        "secret": field.secret,
                        "required": field.required,
                        "placeholder": field.placeholder,
                        "help": field.help,
                        "multiline": field.multiline,
                    }
                    for field in spec.credentials
                ],
                "datasets": [
                    {
                        "key": dataset.key,
                        "label": dataset.label,
                        "description": dataset.description,
                        "resource_kind": dataset.resource_kind,
                        "required_permissions": list(dataset.required_permissions),
                        "time_series": dataset.time_series,
                    }
                    for dataset in spec.datasets
                ],
            }
            for spec in provider_registry.PROVIDERS.values()
        ]
    }


@router.post("/discover")
def discover(
    payload: TokenInput,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    """
    Ask the provider what this credential can reach, without storing anything.

    Nobody remembers what a credential was issued for. Showing the answer is
    what turns this from guesswork into a choice.
    """
    spec = _spec(payload.provider)
    client = spec.build(
        token=payload.token,
        api_version=payload.api_version or spec.default_api_version,
        app_id=payload.app_id,
        app_secret=payload.app_secret,
    )

    # Where a provider can trade a short-lived credential for a long one, that
    # happens first: otherwise the connector works today and not tomorrow.
    exchanged = False
    if (
        payload.exchange_for_long_lived
        and spec.supports_token_exchange
        and getattr(client, "has_app_credentials", False)
    ):
        try:
            client.exchange_for_long_lived()
            exchanged = True
        except ConnectorError:
            # Already long-lived, or not permitted. Neither should fail discovery.
            pass

    try:
        found = client.discover()
    except ConnectorError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    write_audit(db, principal, "connector_discovered",
                payload={"provider": payload.provider,
                         "resources": len(found.resources)})
    body = found.as_payload()
    body["exchanged_for_long_lived"] = exchanged
    body["has_app_credentials"] = bool(getattr(client, "has_app_credentials", False))
    return body


@router.get("")
def list_connectors(
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    connectors = db.scalars(
        sa.select(ApiConnector).order_by(ApiConnector.created_at.desc())
    ).all()
    datasets = db.scalars(sa.select(ConnectorDataset)).all()
    by_connector: dict[str, list[ConnectorDataset]] = {}
    for dataset in datasets:
        by_connector.setdefault(dataset.connector_id, []).append(dataset)

    return {
        "connectors": [
            _connector_payload(connector, by_connector.get(connector.id, []))
            for connector in connectors
        ],
        "can_store_tokens": encryption_available(),
    }


@router.post("")
def create_connector(
    payload: ConnectorInput,
    request: Request,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    """Store a credential, after confirming it works."""
    if not payload.token:
        raise HTTPException(status_code=422, detail="An access token is required.")
    if not encryption_available():
        raise HTTPException(
            status_code=503,
            detail="APP_SECRET is not configured, so tokens cannot be stored safely.",
        )

    spec = _spec(payload.provider)
    version = payload.api_version or spec.default_api_version
    client = spec.build(
        token=payload.token, api_version=version,
        app_id=payload.app_id, app_secret=payload.app_secret or "",
    )

    # The credential that gets stored is the long-lived one wherever that is
    # possible: storing a short-lived one would work now and stop by evening.
    stored_token = payload.token
    expires_at = None
    if (
        payload.exchange_for_long_lived
        and spec.supports_token_exchange
        and getattr(client, "has_app_credentials", False)
    ):
        try:
            stored_token, lifetime = client.exchange_for_long_lived()
            if lifetime:
                from datetime import timedelta

                expires_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=int(lifetime))
                ).replace(tzinfo=None)
        except ConnectorError:
            pass

    try:
        found = client.discover()
    except ConnectorError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    try:
        secret = encrypt_password(stored_token)
        app_secret_blob = (
            encrypt_password(payload.app_secret) if payload.app_secret else None
        )
    except SecretUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    connector = ApiConnector(
        provider=payload.provider,
        name=payload.name.strip(),
        token_encrypted=secret,
        app_id=payload.app_id,
        app_secret_encrypted=app_secret_blob,
        token_expires_at=expires_at,
        api_version=version,
        discovery=found.as_payload(),
        sync_interval_minutes=payload.sync_interval_minutes,
        is_active=payload.is_active,
        created_by=principal.id,
    )
    db.add(connector)
    db.commit()

    write_audit(db, principal, "connector_created", resource_id=connector.id,
                ip=client_ip(request), payload={"provider": payload.provider})
    return {"id": connector.id, "name": connector.name, "discovery": connector.discovery}


@router.post("/{connector_id}/refresh-discovery")
def refresh_discovery(
    connector_id: str,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    """Ask again what the credential can reach -- accounts and permissions change."""
    connector = _get(db, connector_id)
    try:
        found = connector_service.build_connector(connector).discover()
    except ConnectorError as error:
        connector.last_error = str(error)
        db.commit()
        raise HTTPException(status_code=400, detail=str(error)) from error

    connector.discovery = found.as_payload()
    connector.last_error = None
    db.commit()
    return connector.discovery


@router.put("/{connector_id}")
def update_connector(
    connector_id: str,
    payload: ConnectorInput,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    connector = _get(db, connector_id)
    connector.name = payload.name.strip()
    connector.sync_interval_minutes = payload.sync_interval_minutes
    connector.is_active = payload.is_active
    if payload.api_version:
        connector.api_version = payload.api_version
    # An omitted token means "keep the stored one".
    if payload.app_id:
        connector.app_id = payload.app_id
    if payload.app_secret:
        try:
            connector.app_secret_encrypted = encrypt_password(payload.app_secret)
        except SecretUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
    if payload.token:
        try:
            connector.token_encrypted = encrypt_password(payload.token)
        except SecretUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        connector.token_expires_at = None
        connector.last_error = None
    db.commit()
    write_audit(db, principal, "connector_updated", resource_id=connector.id)
    return {"id": connector.id, "name": connector.name}


@router.delete("/{connector_id}")
def delete_connector(
    connector_id: str,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    """Remove the credential and every table it produced."""
    connector = _get(db, connector_id)
    for dataset in db.scalars(
        sa.select(ConnectorDataset).where(ConnectorDataset.connector_id == connector_id)
    ).all():
        connector_service.drop_dataset(db, dataset)

    db.delete(connector)
    db.commit()
    schema_service.forget_snapshots()
    write_audit(db, principal, "connector_deleted", resource_id=connector_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------
@router.post("/{connector_id}/datasets")
def add_dataset(
    connector_id: str,
    payload: DatasetInput,
    background: BackgroundTasks,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    """
    Start producing a table from one of the provider's datasets.

    The first sync runs in the background: an ad account with two years of
    history takes longer than a request should wait, and a timeout here would
    leave a half-configured dataset behind.
    """
    connector = _get(db, connector_id)
    kind = connector_service.dataset_kind(connector.provider, payload.dataset_key)
    if kind is None:
        raise HTTPException(
            status_code=400,
            detail=f"'{payload.dataset_key}' is not something this provider offers.",
        )

    existing = db.scalar(
        sa.select(ConnectorDataset).where(
            ConnectorDataset.connector_id == connector_id,
            ConnectorDataset.dataset_key == payload.dataset_key,
            ConnectorDataset.resource_id == payload.resource_id,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=400,
            detail=f"'{kind.label}' is already being synced for that account.",
        )

    dataset = ConnectorDataset(
        connector_id=connector_id,
        dataset_key=payload.dataset_key,
        resource_id=payload.resource_id,
        resource_name=payload.resource_name,
        display_name=(
            payload.display_name.strip()
            or f"{kind.label} — {payload.resource_name or payload.resource_id}"
        ),
        lookback_days=payload.lookback_days,
        status="pending",
    )
    db.add(dataset)
    db.commit()

    background.add_task(_sync_in_background, dataset.id)
    write_audit(db, principal, "connector_dataset_added", resource_id=dataset.id,
                payload={"dataset": payload.dataset_key})
    return _dataset_payload(dataset)


@router.post("/datasets/{dataset_id}/sync")
def sync_now(
    dataset_id: str,
    background: BackgroundTasks,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    """The Refresh button. Runs in the background; the row reports its progress."""
    dataset = db.get(ConnectorDataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    if dataset.status == "syncing":
        return {"ok": True, "already_running": True}

    dataset.status = "syncing"
    db.commit()
    background.add_task(_sync_in_background, dataset_id)
    return {"ok": True, "already_running": False}


@router.put("/datasets/{dataset_id}")
def update_dataset(
    dataset_id: str,
    payload: DatasetInput,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    dataset = db.get(ConnectorDataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    if payload.display_name.strip():
        dataset.display_name = payload.display_name.strip()
    dataset.lookback_days = payload.lookback_days
    db.commit()
    schema_service.forget_snapshots()
    return _dataset_payload(dataset)


@router.delete("/datasets/{dataset_id}")
def delete_dataset(
    dataset_id: str,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    dataset = db.get(ConnectorDataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    connector_service.drop_dataset(db, dataset)
    schema_service.forget_snapshots()
    write_audit(db, principal, "connector_dataset_deleted", resource_id=dataset_id)
    return {"ok": True}


@router.get("/datasets/{dataset_id}/preview")
def preview_dataset(
    dataset_id: str,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    """The first rows, so it is obvious whether the right data arrived."""
    dataset = db.get(ConnectorDataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    if dataset.status != "ready" or not dataset.physical_table:
        return {"columns": [], "rows": [], "status": dataset.status}

    from app.core.db import get_engine

    table = connector_service._sa_table(dataset)
    with get_engine().connect() as connection:
        result = connection.execute(sa.select(table).limit(20))
        columns = list(result.keys())
        rows = [
            [None if value is None else str(value) for value in row]
            for row in result.fetchall()
        ]
    return {"columns": columns, "rows": rows, "status": dataset.status}


# ---------------------------------------------------------------------------
def _get(db: DbSession, connector_id: str) -> ApiConnector:
    connector = db.get(ApiConnector, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Connector not found.")
    return connector


def _sync_in_background(dataset_id: str) -> None:
    """
    Run a sync on its own session.

    The request's session is gone by the time this runs, and a background task
    holding a request-scoped session is how connection pools get exhausted.
    """
    import logging

    from app.core.db import session_scope

    logger = logging.getLogger(__name__)
    try:
        with session_scope() as session:
            dataset = session.get(ConnectorDataset, dataset_id)
            if dataset is None:
                return
            connector_service.sync_dataset(session, dataset)
        schema_service.forget_snapshots()
    except ConnectorError as error:
        logger.info("Connector sync failed for %s: %s", dataset_id, error)
    except Exception:  # noqa: BLE001 -- a background failure must not be silent
        logger.exception("Unexpected failure syncing connector dataset %s", dataset_id)

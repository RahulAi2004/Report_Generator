"""
Database connection routes.

Adding a connection is the one action in this application that can widen what it
can reach, so it is the most guarded. Only an administrator may do it, every
connection is write-probed before it is stored, and a connection that can write
is refused rather than saved with a warning.

Passwords go in encrypted and never come back out. There is no endpoint that
returns one, and none that echoes it into a URL.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.adapters import factory
from app.core.db import get_session
from app.core.deps import client_ip, require, write_audit
from app.core.security import Permission, Principal
from app.core.secrets import SecretUnavailable, encrypt_password, encryption_available
from app.models.metadata_models import DbConnection
from app.services import connection_service as connections
from app.services import schema_service

router = APIRouter(prefix="/connections", tags=["connections"])


# ---------------------------------------------------------------------------
class ConnectionInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    host: str = Field(min_length=1, max_length=190)
    port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=1, max_length=120)
    #: Optional on update: omitted means "keep the stored one".
    password: str | None = Field(default=None, max_length=400)
    ssl_mode: Literal["disable", "allow", "prefer", "require"] = "prefer"
    is_replica: bool = False


class TestInput(BaseModel):
    host: str = Field(min_length=1, max_length=190)
    port: int = Field(default=5432, ge=1, le=65535)
    #: Empty means "connect to the server's default database and list what is
    #: there", which is how the database picker is filled before one is chosen.
    database_name: str = Field(default="postgres", max_length=120)
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=400)
    ssl_mode: Literal["disable", "allow", "prefer", "require"] = "prefer"


def _payload(connection: DbConnection, selected: str) -> dict:
    return {
        "id": connection.id,
        "name": connection.name,
        "database_type": connection.database_type,
        "host": connection.host,
        "port": connection.port,
        "database_name": connection.database_name,
        "username": connection.username,
        "ssl_mode": connection.ssl_mode,
        "is_read_only": connection.is_read_only,
        "is_replica": connection.is_replica,
        "is_active": connection.is_active,
        "is_builtin": False,
        "is_selected": connection.id == selected,
        "schemas": [],
        "last_scanned_at": (
            connection.last_scanned_at.isoformat() if connection.last_scanned_at else None
        ),
        "created_at": connection.created_at.isoformat(),
    }


@router.get("")
def list_connections(
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    """Every connection, including the one configured in the server's environment."""
    selected = connections.active_connection_id(db)
    rows = db.scalars(sa.select(DbConnection).order_by(DbConnection.name)).all()
    return {
        "connections": [connections.builtin_payload(db)]
        + [_payload(row, selected) for row in rows],
        "active_id": selected,
        "can_store_passwords": encryption_available(),
    }


@router.post("/test")
def test_connection(
    payload: TestInput,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    """
    Try a connection without saving it, and report what is on the other side.

    Returns the databases the credentials can see, so the next step is choosing
    one from a list rather than typing a name and hoping.
    """
    url = connections.build_url(
        host=payload.host, port=payload.port, database=payload.database_name,
        username=payload.username, password=payload.password, ssl_mode=payload.ssl_mode,
    )
    result = connections.probe(url, host=payload.host)
    write_audit(
        db, principal, "connection_tested",
        success=result.reachable,
        payload={"host": payload.host, "database": payload.database_name,
                 "read_only": result.read_only},
    )
    return result.as_payload()


@router.post("")
def create_connection(
    payload: ConnectionInput,
    request: Request,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    """
    Save a connection, once it has proved it cannot write.

    The probe is repeated here rather than trusted from `/test`: the values could
    have changed between the two calls, and this is the gate that matters.
    """
    if not payload.password:
        raise HTTPException(status_code=422, detail="A password is required.")
    if not encryption_available():
        raise HTTPException(
            status_code=503,
            detail="APP_SECRET is not configured, so passwords cannot be stored safely. "
                   "Set it on the server before adding a connection.",
        )

    url = connections.build_url(
        host=payload.host, port=payload.port, database=payload.database_name,
        username=payload.username, password=payload.password, ssl_mode=payload.ssl_mode,
    )
    result = connections.probe(url, host=payload.host)

    if not result.reachable:
        raise HTTPException(status_code=400, detail=result.detail)
    if not result.read_only:
        # Refused, not saved-with-a-warning. On a server holding other people's
        # production data, "we warned you" is not a safety property.
        write_audit(db, principal, "connection_refused_writable", success=False,
                    ip=client_ip(request), payload={"host": payload.host})
        raise HTTPException(status_code=400, detail=result.detail)

    try:
        secret = encrypt_password(payload.password)
    except SecretUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    connection = DbConnection(
        name=payload.name.strip(),
        database_type="postgresql",
        host=payload.host,
        port=payload.port,
        database_name=payload.database_name,
        username=payload.username,
        password_encrypted=secret,
        ssl_mode=payload.ssl_mode,
        is_read_only=True,
        is_replica=payload.is_replica,
        is_active=True,
    )
    db.add(connection)
    db.commit()

    write_audit(db, principal, "connection_created", resource_id=connection.id,
                ip=client_ip(request),
                payload={"host": payload.host, "database": payload.database_name})
    return {"id": connection.id, "name": connection.name,
            "probe": result.as_payload()}


@router.put("/{connection_id}")
def update_connection(
    connection_id: str,
    payload: ConnectionInput,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    if connection_id == connections.BUILTIN_ID:
        raise HTTPException(
            status_code=400,
            detail="The server's own connection is part of the deployment and is "
                   "changed there, not here.",
        )
    connection = db.get(DbConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found.")

    # An omitted password means "keep the stored one"; anything else has to be
    # re-probed, because the point of the probe is the credentials.
    password = payload.password
    if password:
        secret = encrypt_password(password)
    else:
        from app.core.secrets import decrypt_password
        try:
            password = decrypt_password(connection.password_encrypted)
        except SecretUnavailable as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        secret = connection.password_encrypted

    result = connections.probe(
        connections.build_url(
            host=payload.host, port=payload.port, database=payload.database_name,
            username=payload.username, password=password, ssl_mode=payload.ssl_mode,
        ),
        host=payload.host,
    )
    if not result.reachable:
        raise HTTPException(status_code=400, detail=result.detail)
    if not result.read_only:
        raise HTTPException(status_code=400, detail=result.detail)

    connection.name = payload.name.strip()
    connection.host = payload.host
    connection.port = payload.port
    connection.database_name = payload.database_name
    connection.username = payload.username
    connection.password_encrypted = secret
    connection.ssl_mode = payload.ssl_mode
    connection.is_replica = payload.is_replica
    db.commit()

    # The cached engine still points at the old details.
    factory.reset_cache()
    schema_service.forget_snapshots()

    write_audit(db, principal, "connection_updated", resource_id=connection.id)
    return {"id": connection.id, "name": connection.name, "probe": result.as_payload()}


@router.post("/{connection_id}/activate")
def activate(
    connection_id: str,
    request: Request,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    """
    Point the application at a different database.

    Re-probed first: a connection that worked last week may not now, and
    switching to one that cannot answer would take every report down at once.
    """
    if connection_id != connections.BUILTIN_ID:
        connection = db.get(DbConnection, connection_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="Connection not found.")
        try:
            url = connections.connection_url(db, connection_id)
        except SecretUnavailable as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        result = connections.probe(url, host=connection.host)
        if not result.reachable:
            raise HTTPException(
                status_code=400,
                detail=f"Not switching: {result.detail}",
            )
        if not result.read_only:
            raise HTTPException(status_code=400, detail=result.detail)
        connection.last_scanned_at = datetime.now(timezone.utc).replace(tzinfo=None)

    connections.set_active(db, connection_id)
    db.commit()

    # Everything downstream caches per connection; both have to let go.
    factory.reset_cache()
    schema_service.forget_snapshots()

    write_audit(db, principal, "connection_activated", resource_id=connection_id,
                ip=client_ip(request))
    return {"active_id": connection_id}


@router.delete("/{connection_id}")
def delete_connection(
    connection_id: str,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    if connection_id == connections.BUILTIN_ID:
        raise HTTPException(
            status_code=400,
            detail="The server's own connection cannot be removed from here.",
        )
    connection = db.get(DbConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found.")

    if connections.active_connection_id(db) == connection_id:
        raise HTTPException(
            status_code=400,
            detail="This is the connection the application is reading from. Switch to "
                   "another one first, so reports do not stop working mid-deletion.",
        )

    db.delete(connection)
    db.commit()
    write_audit(db, principal, "connection_deleted", resource_id=connection_id)
    return {"ok": True}

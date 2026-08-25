"""FastAPI dependencies: authentication, authorization, audit context."""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session as DbSession

from app.core.db import get_session
from app.core.security import Principal, principal_from_user
from app.models.metadata_models import Session as SessionModel, User

SESSION_COOKIE = "bi_session"


def _extract_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(SESSION_COOKIE)


def current_principal(
    request: Request, db: DbSession = Depends(get_session)
) -> Principal:
    token = _extract_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in."
        )

    session = db.get(SessionModel, token)
    now = datetime.now(timezone.utc)
    if session is None or session.expires_at.replace(tzinfo=timezone.utc) < now:
        if session is not None:
            db.delete(session)
            db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has expired. Please sign in again.",
        )

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is not active."
        )

    session.last_seen_at = now
    db.commit()
    return principal_from_user(user)


def optional_principal(
    request: Request, db: DbSession = Depends(get_session)
) -> Principal | None:
    try:
        return current_principal(request, db)
    except HTTPException:
        return None


def require(*permissions: str):
    """Dependency factory enforcing that the caller holds every named permission."""

    def guard(principal: Principal = Depends(current_principal)) -> Principal:
        missing = [p for p in permissions if not principal.can(p)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to do this.",
            )
        return principal

    return guard


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def write_audit(
    db: DbSession,
    principal: Principal | None,
    action: str,
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
    success: bool = True,
    ip: str | None = None,
    duration_ms: int | None = None,
    payload: dict | None = None,
) -> None:
    """
    Append one hash-chained audit row (spec 34).

    Auditing must never break the request it is recording, so failures here are
    swallowed after the fact -- but the write is attempted for every important
    action, not only successful ones.
    """
    from app.core.security import audit_row_hash
    from app.models.metadata_models import AuditLog

    try:
        previous = db.scalar(
            sa.select(AuditLog.row_hash).order_by(AuditLog.id.desc()).limit(1)
        )
        entry = AuditLog(
            user_id=principal.id if principal else None,
            user_email=principal.email if principal else None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            success=success,
            ip_address=ip,
            duration_ms=duration_ms,
            payload=payload,
            prev_hash=previous,
        )
        entry.row_hash = audit_row_hash(
            previous,
            {
                "user_id": entry.user_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "success": success,
                "payload": payload,
            },
        )
        db.add(entry)
        db.commit()
    except Exception:
        db.rollback()

"""Authentication routes."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import (
    SESSION_COOKIE,
    client_ip,
    current_principal,
    write_audit,
)
from app.core.security import (
    Principal,
    hash_password,
    needs_rehash,
    new_session_token,
    session_expiry,
    verify_password,
)
from app.models.metadata_models import Session as SessionModel, User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    #: Deliberately a plain string rather than EmailStr. Internal deployments
    #: sign in with addresses on reserved TLDs (`.local`, `.internal`) that
    #: strict RFC validation rejects, and an LDAP/AD integration would supply
    #: `DOMAIN\\user`. Identity format is the directory's business, not ours.
    email: str = Field(min_length=3, max_length=190)
    password: str = Field(min_length=1, max_length=200)


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    permissions: list[str]


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession = Depends(get_session),
):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))

    # Verify against a dummy hash when the account is unknown so response time
    # does not reveal whether an email address exists.
    stored = user.password_hash if user else hash_password("no-such-account")
    valid = verify_password(payload.password, stored)

    if not user or not valid or not user.is_active:
        write_audit(
            db, None, "login", success=False, ip=client_ip(request),
            payload={"email": payload.email},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    token = new_session_token()
    db.add(
        SessionModel(
            token=token,
            user_id=user.id,
            expires_at=session_expiry(settings.session_absolute_timeout_hours),
            ip_address=client_ip(request),
        )
    )
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
        max_age=settings.session_absolute_timeout_hours * 3600,
        path="/",
    )
    write_audit(db, None, "login", resource_type="user", resource_id=user.id,
                ip=client_ip(request))

    from app.core.security import principal_from_user

    principal = principal_from_user(user)
    return {"token": token, "user": principal.as_dict()}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    token = request.cookies.get(SESSION_COOKIE) or (
        request.headers.get("authorization", "")[7:] or None
    )
    if token:
        session = db.get(SessionModel, token)
        if session:
            db.delete(session)
            db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    write_audit(db, principal, "logout", ip=client_ip(request))
    return {"ok": True}


@router.get("/me")
def me(principal: Principal = Depends(current_principal)):
    return principal.as_dict()

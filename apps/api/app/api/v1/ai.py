"""
AI Suggestions routes.

Two things the AI can do: propose reports from the schema, and turn a question
into one. Both return a report definition that has already been compiled and
marked runnable or not -- and neither executes anything. Running a suggestion is
the user opening it in the builder and pressing the button.

The model is sent the shape of the database and never its contents, and the
schema it is sent is the one this particular user may read, so the AI cannot
propose a report over a table they would be refused.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.core.db import get_session
from app.core.deps import client_ip, current_principal, require, write_audit
from app.core.security import Permission, Principal
from app.services import schema_service
from app.services.ai import context, engine
from app.services.ai.provider import AIError, DEFAULT_BASE_URL, DEFAULT_MODEL
from app.services.ai.provider import load_config, save_config

router = APIRouter(prefix="/ai", tags=["ai"])


class SuggestRequest(BaseModel):
    #: Narrow the schema to these tables. Cheaper, and the suggestions are
    #: better for it.
    tables: list[str] = Field(default_factory=list, max_length=20)
    #: What the user is interested in, in their own words. Optional.
    interest: str = Field(default="", max_length=1000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    tables: list[str] = Field(default_factory=list, max_length=20)


class SettingsRequest(BaseModel):
    base_url: str = Field(default=DEFAULT_BASE_URL, max_length=300)
    model: str = Field(default=DEFAULT_MODEL, max_length=120)
    #: Omitted means "keep the stored key".
    api_key: str | None = Field(default=None, max_length=400)
    enabled: bool = True


# ---------------------------------------------------------------------------
@router.get("/settings")
def get_settings(
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    """The provider settings, without the key."""
    config = load_config(db)
    return {
        "base_url": config.base_url,
        "model": config.model,
        "has_api_key": bool(config.api_key),
        "enabled": config.enabled,
        "configured": config.configured,
        "defaults": {"base_url": DEFAULT_BASE_URL, "model": DEFAULT_MODEL},
    }


@router.put("/settings")
def put_settings(
    payload: SettingsRequest,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    try:
        save_config(db, payload.base_url, payload.model, payload.api_key, payload.enabled)
    except AIError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    write_audit(db, principal, "ai_settings_updated",
                payload={"model": payload.model, "enabled": payload.enabled})
    return get_settings(db=db, principal=principal)


class ModelsRequest(BaseModel):
    base_url: str = Field(default=DEFAULT_BASE_URL, max_length=300)
    #: Omitted means "use the stored key" -- so the list can be refreshed
    #: without pasting the key again.
    api_key: str | None = Field(default=None, max_length=400)


@router.post("/models")
def models(
    payload: ModelsRequest,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_CONNECTIONS)),
):
    """
    Which models this key can use.

    Takes the key in the request so it can be tested before it is saved: being
    told the key works, and which models it reaches, is the difference between
    configuring this and guessing at it.
    """
    from app.services.ai.provider import AIConfig, OpenAICompatibleProvider

    stored = load_config(db)
    config = AIConfig(
        base_url=(payload.base_url or stored.base_url or DEFAULT_BASE_URL).rstrip("/"),
        model=stored.model or DEFAULT_MODEL,
        api_key=payload.api_key or stored.api_key,
        enabled=True,
    )
    if not config.api_key:
        raise HTTPException(status_code=400, detail="No API key to test.")

    try:
        found = OpenAICompatibleProvider(config).models()
    except AIError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return {"models": found, "base_url": config.base_url}


@router.get("/status")
def status(
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    """
    Whether the AI is usable, for anyone -- not just administrators.

    The screen needs to know whether to offer the feature or explain that it is
    not set up, and that is not privileged information.
    """
    config = load_config(db)
    return {
        "available": config.configured and config.enabled,
        "model": config.model if config.configured else None,
        "can_configure": principal.can(Permission.MANAGE_CONNECTIONS),
    }


# ---------------------------------------------------------------------------
@router.post("/suggest")
def suggest(
    payload: SuggestRequest,
    request: Request,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.BUILD_REPORT)),
):
    """Reports worth building, proposed from what the database actually holds."""
    registry = schema_service.build_registry(db, principal)
    try:
        found = engine.suggest(
            db, registry, principal, focus=payload.tables or None,
            extra=payload.interest,
        )
    except AIError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    write_audit(db, principal, "ai_suggested", ip=client_ip(request),
                payload={"count": len(found), "tables": payload.tables})
    return {
        "suggestions": [item.as_payload() for item in found],
        "runnable": sum(1 for item in found if item.runnable),
    }


@router.post("/ask")
def ask(
    payload: AskRequest,
    request: Request,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.BUILD_REPORT)),
):
    """One report answering one question, compiled but not run."""
    registry = schema_service.build_registry(db, principal)
    try:
        answer = engine.ask(
            db, registry, principal, payload.question, focus=payload.tables or None
        )
    except AIError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    write_audit(db, principal, "ai_asked", ip=client_ip(request),
                payload={"runnable": answer.runnable})
    return answer.as_payload()


@router.get("/context")
def schema_context(
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.BUILD_REPORT)),
):
    """
    Exactly what would be sent to the AI provider.

    Shown on the screen rather than described, because "it only sees the schema"
    is a claim, and this is the thing itself. Nobody has to take it on trust
    that no data leaves.
    """
    registry = schema_service.build_registry(db, principal)
    described = context.describe_in_full(registry)
    return {
        "context": described.text,
        "characters": len(described.text),
        "tables": described.included,
        "tables_total": described.total,
        "trimmed": described.trimmed,
        "note": (
            "This is everything sent to the AI provider: table names, column "
            "names and types, and how the tables relate. No row of data is "
            "included, and columns you cannot read are not here either."
        ),
    }

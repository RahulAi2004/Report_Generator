"""
Application entrypoint.

Startup performs the read-only self-test against the operational database. If
the connection turns out to be writable while DATABASE_ENFORCE_READ_ONLY is set,
the application refuses to serve. That is deliberate: a reporting tool that can
write to the production database is a liability, and failing loudly at boot is
far safer than discovering it later.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.adapters.base import ReadOnlyViolation
from app.adapters.factory import get_adapter
from app.api.v1 import auth, reports, schema
from app.core.config import settings
from app.core.db import DEV_ACCOUNTS, DEV_PASSWORD, init_database
from app.domain.report.diagnostics import ReportCompilationError

logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)
logger = logging.getLogger("bi.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database(seed_dev_users=settings.environment != "production")

    adapter = get_adapter()
    if settings.database_enforce_read_only and settings.data_source_mode == "live":
        try:
            adapter.assert_read_only()
            logger.info("Read-only self-test passed: the connection cannot write.")
        except ReadOnlyViolation as error:
            logger.critical("REFUSING TO START: %s", error)
            raise

    if settings.data_source_mode == "mock":
        logger.warning(
            "DEVELOPMENT DATA MODE -- serving the seeded demo database, not production."
        )
        logger.info("Demo sign-in: %s / %s", DEV_ACCOUNTS[0][0], DEV_PASSWORD)

    yield


app = FastAPI(
    title=settings.app_name,
    version="0.5.0",
    description="Internal BI, dynamic reporting and data-quality platform.",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Error handling (spec 44): users see plain language, logs keep the detail.
# ---------------------------------------------------------------------------
@app.exception_handler(ReportCompilationError)
async def handle_compilation_error(request: Request, error: ReportCompilationError):
    return JSONResponse(
        status_code=400,
        content={
            "detail": "This report is not valid yet.",
            "diagnostics": [d.as_dict() for d in error.diagnostics],
        },
    )


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, error: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Something went wrong. The technical details have been logged."
        },
    )


for router in (auth.router, schema.router, reports.router):
    app.include_router(router, prefix=settings.api_v1_prefix)


@app.get("/api/health", tags=["system"])
def health():
    adapter = get_adapter()
    return {
        "status": "ok",
        "mode": settings.data_source_mode,
        "dialect": adapter.dialect,
        "read_only_enforced": settings.database_enforce_read_only,
    }

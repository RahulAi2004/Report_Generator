"""
Adapter selection and connection management.

Engines are cached per connection: creating a pool per request would exhaust the
operational database's connection slots, which is exactly the kind of collateral
damage a reporting tool must never inflict on production.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from app.adapters.base import DatabaseAdapter
from app.adapters.postgres import PostgresAdapter
from app.adapters.sqlite_demo import SqliteDemoAdapter
from app.core.config import Settings, settings as default_settings

_ENGINES: dict[str, Engine] = {}
_ADAPTERS: dict[str, DatabaseAdapter] = {}


def _build_engine(url: str, timeout_seconds: int) -> Engine:
    if url.startswith("sqlite"):
        return sa.create_engine(url, future=True)
    return sa.create_engine(
        url,
        future=True,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={
            "connect_timeout": 10,
            "application_name": "bi-reporting",
        },
    )


def get_adapter(
    url: str | None = None,
    *,
    settings: Settings | None = None,
    cache_key: str = "default",
) -> DatabaseAdapter:
    """Return (and cache) the adapter for a connection URL."""
    config = settings or default_settings
    url = url or _resolve_url(config)

    cached = _ADAPTERS.get(cache_key)
    if cached is not None:
        return cached

    engine = _ENGINES.get(cache_key) or _build_engine(url, config.query_timeout_seconds)
    _ENGINES[cache_key] = engine

    dialect = engine.dialect.name
    if dialect == "postgresql":
        adapter: DatabaseAdapter = PostgresAdapter(
            engine, timeout_seconds=config.query_timeout_seconds
        )
    elif dialect == "sqlite":
        adapter = SqliteDemoAdapter(engine, timeout_seconds=config.query_timeout_seconds)
    else:
        raise NotImplementedError(
            f"No adapter for '{dialect}'. Supported: postgresql, sqlite (demo). "
            "MySQL and SQL Server adapters plug in here without touching the report engine."
        )

    _ADAPTERS[cache_key] = adapter
    return adapter


def _resolve_url(config: Settings) -> str:
    """
    Demo mode runs on a seeded SQLite file so the whole application works with no
    infrastructure at all. Live mode uses the configured operational database.
    """
    if config.data_source_mode == "mock":
        from pathlib import Path

        root = Path(__file__).resolve().parents[4]
        return f"sqlite:///{(root / 'mock-data' / 'decoinks_demo.db').as_posix()}"
    return config.operational_dsn


def reset_cache() -> None:
    for engine in _ENGINES.values():
        engine.dispose()
    _ENGINES.clear()
    _ADAPTERS.clear()

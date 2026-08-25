"""
SQLite adapter -- DEVELOPMENT AND DEMO ONLY.

Its purpose is that the whole platform runs with zero infrastructure: no Docker,
no server, no credentials. Crucially it is a *real* database engine, so demo mode
exercises the real introspector, the real join planner, the real compiler and
real SQL execution. Demo mode is therefore a genuine test of the system rather
than a mock that quietly diverges from it.

It is never used against production. The factory selects it only when
DATA_SOURCE_MODE=mock.
"""

from __future__ import annotations

import hashlib

import sqlalchemy as sa
from sqlalchemy import event

from app.adapters.base import DatabaseAdapter


class SqliteDemoAdapter(DatabaseAdapter):
    dialect = "sqlite"

    def __init__(self, engine, **kwargs) -> None:
        super().__init__(engine, **kwargs)
        _register_hash_function(engine)

    def session_guards(self) -> list[str]:
        # SQLite has no server-side statement timeout or read-only transaction
        # mode we can set per session. The connection is opened read-only at the
        # engine level instead, and this adapter never runs against production.
        return []

    def supports_write_probe(self) -> bool:
        # A local demo file is writable by definition; probing would fail the
        # startup self-test for no reason. Live adapters return True.
        return False

    def default_schema(self) -> str | None:
        return None

    def row_estimates(self, schema: str | None) -> dict[str, int]:
        """
        The demo dataset is small, so exact counts are cheap and give the UI
        realistic figures. A production adapter must never do this.
        """
        estimates: dict[str, int] = {}
        try:
            inspector = sa.inspect(self.engine)
            with self.engine.connect() as connection:
                for name in inspector.get_table_names():
                    count = connection.execute(
                        sa.select(sa.func.count()).select_from(sa.table(name))
                    ).scalar()
                    estimates[name] = int(count or 0)
        except Exception:
            return {}
        return estimates


def _register_hash_function(engine: sa.Engine) -> None:
    """
    Give SQLite an ``md5()`` so hash-masked columns behave as they do on
    PostgreSQL.

    Column masking compiles to a database function, and SQLite ships without
    one. Rather than making the compiler dialect-aware for a single policy, the
    adapter supplies the missing function -- which is exactly the kind of
    difference the adapter layer exists to absorb.
    """

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _record):  # pragma: no cover - driver hook
        def md5(value):
            if value is None:
                return None
            return hashlib.md5(str(value).encode("utf-8")).hexdigest()

        dbapi_connection.create_function("md5", 1, md5)

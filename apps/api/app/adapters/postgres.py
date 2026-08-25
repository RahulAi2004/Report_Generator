"""PostgreSQL adapter -- the production target."""

from __future__ import annotations

import sqlalchemy as sa

from app.adapters.base import DatabaseAdapter


class PostgresAdapter(DatabaseAdapter):
    dialect = "postgresql"

    def session_guards(self) -> list[str]:
        """
        Applied before every query. Belt and braces on top of the role's own
        ``default_transaction_read_only`` setting -- if the DBA has not set it,
        this session still cannot write.
        """
        return [
            "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
            f"SET statement_timeout = {int(self.timeout_seconds * 1000)}",
            "SET idle_in_transaction_session_timeout = 10000",
            "SET lock_timeout = 5000",
        ]

    def supports_write_probe(self) -> bool:
        return True

    def default_schema(self) -> str | None:
        return "public"

    def row_estimates(self, schema: str | None) -> dict[str, int]:
        """Planner statistics, not COUNT(*). Cheap on tables of any size."""
        query = sa.text(
            """
            SELECT c.relname AS table_name, GREATEST(c.reltuples, 0)::bigint AS rows
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = :schema AND c.relkind IN ('r', 'p')
            """
        )
        try:
            with self.engine.connect() as connection:
                return {
                    row.table_name: int(row.rows)
                    for row in connection.execute(query, {"schema": schema or "public"})
                }
        except Exception:
            return {}

    def explain_cost(self, statement) -> float | None:
        """
        Pre-flight cost check. A report the planner already thinks is enormous is
        rejected before it consumes production I/O rather than after.
        """
        try:
            compiled = statement.compile(
                dialect=self.engine.dialect, compile_kwargs={"literal_binds": True}
            )
            with self.engine.connect() as connection:
                for guard in self.session_guards():
                    connection.execute(sa.text(guard))
                result = connection.execute(sa.text(f"EXPLAIN (FORMAT JSON) {compiled}"))
                plan = result.scalar()
            if isinstance(plan, list) and plan:
                return float(plan[0]["Plan"]["Total Cost"])
            if isinstance(plan, dict):
                return float(plan["Plan"]["Total Cost"])
        except Exception:
            return None
        return None

    def cancel(self, backend_pid: int) -> None:
        with self.engine.connect() as connection:
            connection.execute(
                sa.text("SELECT pg_cancel_backend(:pid)"), {"pid": backend_pid}
            )

    def is_replica(self) -> bool:
        try:
            with self.engine.connect() as connection:
                return bool(connection.execute(sa.text("SELECT pg_is_in_recovery()")).scalar())
        except Exception:
            return False

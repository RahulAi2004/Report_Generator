"""
Database adapters.

Business logic depends on this protocol and nothing else, so adding a dialect
means adding one module rather than touching the report engine.

Introspection is generic (SQLAlchemy's Inspector speaks every supported
dialect); what genuinely differs per engine is the *safety* behaviour -- how you
force a read-only transaction, how you set a statement timeout, how you estimate
row counts without a full table scan, and how you cancel a runaway query. Those
are the abstract methods below.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator

import sqlalchemy as sa
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

from app.domain.schema.registry import (
    Cardinality,
    ColumnMeta,
    JoinType,
    RelationshipMeta,
    RelationshipSource,
    SchemaRegistry,
    TableMeta,
    normalize_type,
)


class QueryExecutionError(Exception):
    """A user-safe execution failure. Technical detail stays in ``detail`` for the log."""

    def __init__(self, message: str, detail: str | None = None, kind: str = "error") -> None:
        self.detail = detail
        self.kind = kind
        super().__init__(message)


class ReadOnlyViolation(Exception):
    """The operational connection turned out to be writable. Fatal by design."""


@dataclass
class QueryResult:
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    duration_ms: int = 0
    truncated: bool = False
    row_count: int = 0


@dataclass
class SchemaSnapshot:
    tables: list[TableMeta] = field(default_factory=list)
    relationships: list[RelationshipMeta] = field(default_factory=list)
    dialect: str = ""
    scanned_at: float = 0.0

    def to_registry(self, connection_id: str | None = None) -> SchemaRegistry:
        return SchemaRegistry(self.tables, self.relationships, connection_id)


class DatabaseAdapter(ABC):
    dialect: str = "generic"

    def __init__(self, engine: Engine, *, timeout_seconds: int = 30) -> None:
        self.engine = engine
        self.timeout_seconds = timeout_seconds

    # ------------------------------------------------------------------
    # Safety -- the part that actually differs between engines.
    # ------------------------------------------------------------------
    @abstractmethod
    def session_guards(self) -> list[str]:
        """Statements issued at the start of every query session (read-only, timeouts)."""

    @abstractmethod
    def supports_write_probe(self) -> bool:
        """Whether :meth:`assert_read_only` can prove writability on this engine."""

    def assert_read_only(self) -> None:
        """
        Startup self-test (ARCHITECTURE.md, section D/L1).

        Attempts a harmless write inside a transaction that is always rolled
        back. Success means the credentials are unsafe for a reporting tool, and
        we fail closed rather than trusting application code to be the only guard.
        """
        if not self.supports_write_probe():
            return
        with self.engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(sa.text("CREATE TEMP TABLE _bi_write_probe (x integer)"))
            except Exception:
                return  # blocked -- exactly what we want
            finally:
                transaction.rollback()
        raise ReadOnlyViolation(
            "The operational database connection has write access. Create a SELECT-only "
            "role before running this application against it."
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def execute(
        self,
        statement,
        max_rows: int,
        parameters: dict[str, Any] | None = None,
    ) -> QueryResult:
        started = time.perf_counter()
        try:
            with self.engine.connect() as connection:
                for guard in self.session_guards():
                    connection.execute(sa.text(guard))

                cursor = connection.execute(statement, parameters or {})
                columns = list(cursor.keys())
                # Fetch one extra row to detect truncation without a COUNT.
                rows = cursor.fetchmany(max_rows + 1)
                truncated = len(rows) > max_rows
                rows = rows[:max_rows]
        except Exception as error:
            translated = self.translate_error(error)
            # The user sees a plain-language message; the operator needs the
            # real one. Without this, "the technical details have been logged"
            # is a lie and production failures cannot be diagnosed.
            logger.error(
                "Query failed (%s): %s", translated.kind, translated.detail or error,
                exc_info=True,
            )
            raise translated from error

        duration_ms = int((time.perf_counter() - started) * 1000)
        return QueryResult(
            columns=columns,
            rows=[tuple(row) for row in rows],
            duration_ms=duration_ms,
            truncated=truncated,
            row_count=len(rows),
        )

    def stream(
        self, statement, chunk_size: int = 5_000, parameters: dict[str, Any] | None = None
    ) -> Iterator[tuple[list[str], list[tuple]]]:
        """Server-side cursor for exports -- never materializes the full result set."""
        with self.engine.connect().execution_options(stream_results=True) as connection:
            for guard in self.session_guards():
                connection.execute(sa.text(guard))
            cursor = connection.execute(statement, parameters or {})
            columns = list(cursor.keys())
            while chunk := cursor.fetchmany(chunk_size):
                yield columns, [tuple(row) for row in chunk]

    def explain_cost(self, statement) -> float | None:
        """Estimated cost, when the engine can produce one cheaply. None if unsupported."""
        return None

    def translate_error(self, error: Exception) -> QueryExecutionError:
        """
        Convert a driver exception into something a manager can act on (spec 44).

        Raw driver text is never shown to users -- it leaks schema details and
        means nothing to them.
        """
        text = str(error).lower()
        if "timeout" in text or "canceling statement" in text:
            return QueryExecutionError(
                f"This report took longer than the {self.timeout_seconds}-second limit and was "
                "stopped. Try narrowing the date range or adding a filter.",
                detail=str(error),
                kind="timeout",
            )
        if "read-only" in text or "readonly" in text:
            return QueryExecutionError(
                "This action was blocked because the reporting connection is read-only.",
                detail=str(error),
                kind="read_only",
            )
        if "permission denied" in text or "insufficient privilege" in text:
            return QueryExecutionError(
                "You do not have permission to read one of the tables in this report.",
                detail=str(error),
                kind="permission",
            )
        if "does not exist" in text or "no such table" in text or "no such column" in text:
            return QueryExecutionError(
                "This report refers to a table or field that no longer exists in the "
                "database. Re-scan the schema under Data Sources.",
                detail=str(error),
                kind="schema_drift",
            )
        if "connection" in text or "could not connect" in text:
            return QueryExecutionError(
                "The database could not be reached. Please try again shortly.",
                detail=str(error),
                kind="connection",
            )
        return QueryExecutionError(
            "This report could not be run. The technical details have been logged.",
            detail=str(error),
        )

    # ------------------------------------------------------------------
    # Introspection -- generic across dialects.
    # ------------------------------------------------------------------
    def introspect(self, schema: str | None = None) -> SchemaSnapshot:
        inspector = sa.inspect(self.engine)
        schema = schema or self.default_schema()
        estimates = self.row_estimates(schema)

        tables: list[TableMeta] = []
        relationships: list[RelationshipMeta] = []

        names = list(inspector.get_table_names(schema=schema))
        views = list(inspector.get_view_names(schema=schema))

        for name in [*names, *views]:
            is_view = name in views
            try:
                raw_columns = inspector.get_columns(name, schema=schema)
                pk = set(
                    (inspector.get_pk_constraint(name, schema=schema) or {})
                    .get("constrained_columns")
                    or []
                )
                foreign_keys = inspector.get_foreign_keys(name, schema=schema) if not is_view else []
            except Exception:
                continue  # a table we cannot read is simply not offered

            fk_columns = {
                column
                for foreign_key in foreign_keys
                for column in foreign_key.get("constrained_columns", [])
            }

            columns = tuple(
                ColumnMeta(
                    table=name,
                    name=raw["name"],
                    data_type=normalize_type(str(raw["type"])),
                    physical_type=str(raw["type"]),
                    nullable=bool(raw.get("nullable", True)),
                    is_primary_key=raw["name"] in pk,
                    is_foreign_key=raw["name"] in fk_columns,
                    ordinal=index,
                    description=raw.get("comment"),
                )
                for index, raw in enumerate(raw_columns)
            )

            tables.append(
                TableMeta(
                    name=name,
                    schema=schema or "public",
                    kind="view" if is_view else "table",
                    category=categorize(name),
                    estimated_rows=estimates.get(name),
                    columns=columns,
                )
            )

            for foreign_key in foreign_keys:
                target = foreign_key.get("referred_table")
                constrained = foreign_key.get("constrained_columns") or []
                referred = foreign_key.get("referred_columns") or []
                if not target or not constrained or not referred:
                    continue
                # Composite keys are recorded as their first column pair; the
                # metadata layer supports declaring the rest as a logical link.
                relationships.append(
                    RelationshipMeta(
                        id=f"fk_{name}_{constrained[0]}_{target}",
                        left_table=target,
                        left_column=referred[0],
                        right_table=name,
                        right_column=constrained[0],
                        cardinality=Cardinality.ONE_TO_MANY,
                        default_join_type=JoinType.LEFT,
                        source=RelationshipSource.PHYSICAL,
                        confidence=1.0,
                    )
                )

        return SchemaSnapshot(
            tables=tables,
            relationships=relationships,
            dialect=self.dialect,
            scanned_at=time.time(),
        )

    def default_schema(self) -> str | None:
        return None

    def row_estimates(self, schema: str | None) -> dict[str, int]:
        """Approximate row counts. Never COUNT(*) -- see spec 41."""
        return {}


# ---------------------------------------------------------------------------
# Business categorization (spec 6). Configurable: an admin can override any
# assignment in the metadata database; this only supplies the first guess.
# ---------------------------------------------------------------------------
DEFAULT_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    # Checked in order, so more specific patterns come first.
    ("System", ("history", "audit", "activity", "activity_log", "setting", "config",
                "migration", "webhook", "event", "counter", "meta_kv", "log")),
    ("Purchasing", ("purchase", "purchase_order", "po", "supplier", "vendor",
                    "procurement")),
    ("Customers", ("customer", "contact", "lead", "client", "party", "parties",
                   "portal_user")),
    ("Sales", ("sales_order", "salesorder", "order", "quotation", "quote",
               "invoice", "product")),
    ("Artwork", ("artwork", "artworks", "gangsheet", "gang_sheet", "design",
                 "mockup")),
    ("Payments", ("payment", "receipt", "refund", "allocation", "transaction")),
    ("Fulfillment", ("shipment", "shipping", "tracking", "delivery", "fulfilment",
                     "fulfillment")),
    ("Production", ("production", "print", "batch", "job", "task")),
    ("Communication", ("message", "conversation", "chat", "chatwoot", "note",
                       "notification", "email")),
    ("Files", ("file", "attachment", "asset", "upload", "document")),
    ("People", ("user", "users", "employee", "staff", "role", "permission",
                "token", "session")),
]


def _tokens(table_name: str) -> set[str]:
    """
    Split a table name into words, plus their singular forms.

    Matching on tokens rather than substrings matters: the naive check found
    "art" inside "p-art-y" and filed every `party*` table under Artwork.
    """
    parts = [p for p in table_name.lower().replace("-", "_").split("_") if p]
    tokens = set(parts)
    for part in parts:
        if part.endswith("ies") and len(part) > 4:
            tokens.add(part[:-3] + "y")
        elif part.endswith("es") and len(part) > 3:
            tokens.add(part[:-2])
        if part.endswith("s") and len(part) > 2:
            tokens.add(part[:-1])
    # The full name too, so multi-word keywords like "gang_sheet" still match.
    tokens.add(table_name.lower())
    return tokens


def categorize(table_name: str, rules: list[tuple[str, tuple[str, ...]]] | None = None) -> str:
    """
    Suggest a business category for a discovered table.

    Only a first guess -- an administrator can override any assignment in the
    metadata database without touching the production schema (spec 36).
    """
    tokens = _tokens(table_name)
    lowered = table_name.lower()

    for category, keywords in rules or DEFAULT_CATEGORY_RULES:
        for keyword in keywords:
            if keyword in tokens:
                return category
            # Multi-word keywords are matched against the whole name.
            if "_" in keyword and keyword in lowered:
                return category
    return "Other"

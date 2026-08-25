"""
Execution routing across two databases.

A report can now draw on two places: the operational database (read-only, not
ours) and uploaded spreadsheets (in our own metadata database). PostgreSQL
cannot join across two separate connections, and we must never write to the
operational side -- so a mixed report is executed by bringing the operational
rows to the uploads, never the other way round.

    operational only  ->  run on the operational connection, as always
    uploads only      ->  run on the metadata database
    mixed             ->  stage the operational side, then join locally

Staging uses session-temporary tables. They vanish when the connection closes,
they cannot collide between concurrent users, and they are resolved through
search_path -- which is why the statement is compiled without schema
qualification for this path.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from app.adapters.base import DatabaseAdapter, QueryExecutionError, QueryResult
from app.core.db import get_engine
from app.domain.report.compiler import CompiledReport
from app.domain.schema.registry import DataType, SchemaRegistry
from app.services.upload_service import UPLOAD_SCHEMA

logger = logging.getLogger(__name__)

#: Ceiling on rows copied from the operational database for one staged table.
#: Exceeding it is reported rather than silently truncated -- a join against a
#: partial table produces a wrong answer that looks complete.
MAX_STAGED_ROWS = 200_000

STAGE_BATCH = 5_000

_SA_TYPES: dict[DataType, type] = {
    DataType.TEXT: sa.Text,
    DataType.INTEGER: sa.BigInteger,
    DataType.DECIMAL: sa.Numeric,
    DataType.BOOLEAN: sa.Boolean,
    DataType.DATE: sa.Date,
    DataType.DATETIME: sa.DateTime,
    DataType.TIME: sa.Time,
    DataType.UUID: sa.Text,
    DataType.JSON: sa.Text,
    DataType.BINARY: sa.LargeBinary,
    DataType.UNKNOWN: sa.Text,
}


def classify(registry: SchemaRegistry, tables: list[str]) -> tuple[list[str], list[str]]:
    """Split the tables a report uses into (operational, uploaded)."""
    operational, uploaded = [], []
    for name in tables:
        meta = registry.table(name)
        if meta is not None and meta.kind == "upload":
            uploaded.append(name)
        else:
            operational.append(name)
    return operational, uploaded


def needs_hybrid(registry: SchemaRegistry, tables: list[str]) -> bool:
    operational, uploaded = classify(registry, tables)
    return bool(operational) and bool(uploaded)


def uploads_only(registry: SchemaRegistry, tables: list[str]) -> bool:
    operational, uploaded = classify(registry, tables)
    return bool(uploaded) and not operational


# ---------------------------------------------------------------------------
def execute(
    compiled: CompiledReport,
    registry: SchemaRegistry,
    adapter: DatabaseAdapter,
    max_rows: int,
) -> QueryResult:
    """Run a compiled report against whichever engine (or engines) it needs."""
    operational, uploaded = classify(registry, compiled.tables_used)

    if not uploaded:
        return adapter.execute(compiled.statement, max_rows=max_rows)

    if not operational:
        return _execute_local(compiled.statement, max_rows=max_rows)

    return _execute_hybrid(compiled, registry, adapter, operational, max_rows)


def _execute_local(statement, max_rows: int, connection=None) -> QueryResult:
    """Run entirely in the metadata database, where the uploads live."""
    import time

    started = time.perf_counter()
    own = connection is None
    connection = connection or get_engine().connect()
    try:
        if own and get_engine().dialect.name == "postgresql":
            connection.execute(sa.text(f'SET search_path TO pg_temp, "{UPLOAD_SCHEMA}", public'))
        cursor = connection.execute(statement)
        columns = list(cursor.keys())
        rows = cursor.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
    except Exception as error:
        logger.error("Local execution failed: %s", error, exc_info=True)
        raise QueryExecutionError(
            "This report could not be run. The technical details have been logged."
        ) from error
    finally:
        if own:
            connection.close()

    return QueryResult(
        columns=columns,
        rows=[tuple(row) for row in rows],
        duration_ms=int((time.perf_counter() - started) * 1000),
        truncated=truncated,
        row_count=len(rows),
    )


def _execute_hybrid(
    compiled: CompiledReport,
    registry: SchemaRegistry,
    adapter: DatabaseAdapter,
    operational: list[str],
    max_rows: int,
) -> QueryResult:
    """
    Copy the operational tables this report needs, then join them locally.

    Only the columns the report actually references are copied, which is usually
    a small fraction of a wide table.
    """
    engine = get_engine()
    if engine.dialect.name != "postgresql":
        raise QueryExecutionError(
            "Joining uploaded files with database tables needs PostgreSQL for the "
            "application database."
        )

    referenced = _referenced_columns(compiled, registry, operational)

    with engine.connect() as connection:
        connection.execute(
            sa.text(f'SET search_path TO pg_temp, "{UPLOAD_SCHEMA}", public')
        )

        for table_name in operational:
            meta = registry.table(table_name)
            if meta is None:
                continue
            columns = referenced.get(table_name) or [c.name for c in meta.columns]
            _stage_table(connection, adapter, meta, columns)

        connection.commit()
        return _execute_local(compiled.statement, max_rows=max_rows, connection=connection)


def _referenced_columns(
    compiled: CompiledReport, registry: SchemaRegistry, operational: list[str]
) -> dict[str, list[str]]:
    """
    Which columns of each operational table the statement actually touches.

    Copying a 70-column table to use three of them is the difference between a
    staged join being practical and being unusable.
    """
    wanted: dict[str, set[str]] = {name: set() for name in operational}

    for column in compiled.statement.selected_columns:
        for element in getattr(column, "base_columns", []) or []:
            table = getattr(element, "table", None)
            if table is not None and table.name in wanted:
                wanted[table.name].add(element.name)

    # Whole-statement sweep: joins, filters, grouping and ordering all matter.
    for element in compiled.statement.get_children(column_collections=False):
        for found in _walk_columns(element):
            table = getattr(found, "table", None)
            if table is not None and getattr(table, "name", None) in wanted:
                wanted[table.name].add(found.name)

    result: dict[str, list[str]] = {}
    for name in operational:
        meta = registry.table(name)
        names = wanted.get(name) or set()
        if not names and meta is not None:
            names = {c.name for c in meta.columns}
        # Preserve declared order so staged tables read naturally.
        order = [c.name for c in (meta.columns if meta else ())]
        result[name] = [c for c in order if c in names] or list(names)
    return result


def _walk_columns(element, depth: int = 0):
    """Yield every Column anywhere inside a SQLAlchemy expression tree."""
    if depth > 24 or element is None:
        return
    if isinstance(element, sa.Column):
        yield element
        return
    for child in getattr(element, "get_children", lambda **_: [])():
        yield from _walk_columns(child, depth + 1)


def _stage_table(connection, adapter: DatabaseAdapter, meta, column_names: list[str]) -> None:
    """Create a temporary copy of one operational table and fill it."""
    columns = [meta.column(name) for name in column_names]
    columns = [c for c in columns if c is not None]
    if not columns:
        raise QueryExecutionError(f"No usable columns found on {meta.name}.")

    staged = sa.Table(
        meta.name,
        sa.MetaData(),
        *[sa.Column(c.name, _SA_TYPES[c.data_type]()) for c in columns],
        prefixes=["TEMPORARY"],
    )
    staged.create(connection)

    source = sa.Table(
        meta.name,
        sa.MetaData(),
        *[sa.Column(c.name, _SA_TYPES[c.data_type]()) for c in columns],
        schema=meta.schema if meta.schema and meta.schema != "public" else None,
    )
    select = sa.select(*[source.c[c.name] for c in columns]).limit(MAX_STAGED_ROWS + 1)

    copied = 0
    names = [c.name for c in columns]
    for _, chunk in adapter.stream(select, chunk_size=STAGE_BATCH):
        if not chunk:
            continue
        if copied + len(chunk) > MAX_STAGED_ROWS:
            raise QueryExecutionError(
                f"{meta.display_name or meta.name} has more than "
                f"{MAX_STAGED_ROWS:,} rows, which is too many to combine with an "
                "uploaded file. Add a filter to narrow it down first.",
                kind="staging_limit",
            )
        connection.execute(staged.insert(), [dict(zip(names, row)) for row in chunk])
        copied += len(chunk)

    logger.info("Staged %s rows from %s for a hybrid report", copied, meta.name)

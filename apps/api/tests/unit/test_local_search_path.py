"""
What an unqualified table name resolves to on the local side.

A report that mixes a database table with a local one is compiled *without*
schema qualification, because the operational rows are staged into pg_temp and
temporary tables cannot be schema-qualified. Everything else in that statement
is then resolved through search_path -- so a schema missing from search_path is
a table the query cannot see.

That is exactly what happened. Uploads had a schema and were listed; API
connectors got their own schema later and were not. Joining anything in the
database to a Meta or supplier table failed with "This report could not be
run", which tells the person reading it nothing at all.

These tests are cheap and they run everywhere. The behaviour they stand in for
needs PostgreSQL, and a test that only runs when someone remembers to set an
environment variable is a test that reports success while checking nothing.
"""

from __future__ import annotations

import inspect
import re

from app.services import hybrid_executor
from app.services.connector_service import CONNECTOR_SCHEMA
from app.services.upload_service import UPLOAD_SCHEMA


def test_every_schema_holding_local_tables_is_on_the_search_path():
    """
    The invariant. Both places the application creates tables of its own must
    be reachable without qualification, because that is how they are compiled.
    """
    path = hybrid_executor.LOCAL_SEARCH_PATH

    assert f'"{UPLOAD_SCHEMA}"' in path
    assert f'"{CONNECTOR_SCHEMA}"' in path


def test_temporary_tables_are_resolved_first():
    """
    Staged operational tables carry the real table's name. If the schema
    holding a same-named permanent table came first, a mixed report would
    silently read the permanent one -- the wrong rows, and no error.
    """
    path = hybrid_executor.LOCAL_SEARCH_PATH

    assert path.split(",")[0].strip() == "pg_temp"


def test_the_search_path_is_defined_once():
    """
    It was set in two places with the same literal, and only one of them would
    have been updated. Both call sites must read the same constant, or this
    bug returns the next time a schema is added.
    """
    source = inspect.getsource(hybrid_executor)
    statements = re.findall(r"SET search_path TO ([^\"']+)", source)

    assert len(statements) == 2, "expected two places to set the search path"
    assert all("LOCAL_SEARCH_PATH" in statement for statement in statements)


def test_the_two_schemas_are_actually_different():
    """
    Guards the test above from passing for the wrong reason: if uploads and
    connectors ever named the same schema, listing one would satisfy the
    assertions while proving nothing.
    """
    assert UPLOAD_SCHEMA != CONNECTOR_SCHEMA

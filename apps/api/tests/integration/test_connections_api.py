"""
Connection management, against real PostgreSQL.

The write probe is the reason this file exists. It is the only thing standing
between "an administrator pasted a superuser password once" and a reporting tool
with write access to somebody else's production database, so it is tested
against a real server with real roles rather than a mock that always agrees.
"""

from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.main import app
from app.services import connection_service as connections

PASSWORD = "demo1234"
ADMIN = "admin@decoinks.local"
ANALYST = "analyst@decoinks.local"

#: The development metadata database, which this test suite already requires.
HOST = os.environ.get("TEST_PG_HOST", "127.0.0.1")
PORT = int(os.environ.get("TEST_PG_PORT", "5433"))
DATABASE = os.environ.get("TEST_PG_DB", "bi_metadata")
OWNER = os.environ.get("TEST_PG_USER", "bi_app")
OWNER_PASSWORD = os.environ.get("TEST_PG_PASSWORD", "bi_app_dev_password")

READER = "bi_probe_reader"
READER_PASSWORD = "probe_reader_password"


def owner_url(database: str = DATABASE) -> str:
    return connections.build_url(HOST, PORT, database, OWNER, OWNER_PASSWORD, "disable")


def reader_url(database: str = DATABASE) -> str:
    return connections.build_url(HOST, PORT, database, READER, READER_PASSWORD, "disable")


@pytest.fixture(scope="module")
def postgres():
    """Skip rather than fail when the development database is not running."""
    engine = sa.create_engine(owner_url(), future=True, poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception as error:  # noqa: BLE001
        pytest.skip(f"development PostgreSQL not reachable: {error}")
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def read_only_role(postgres):
    """
    A genuinely read-only role, made the way a real one should be.

    `default_transaction_read_only` is the right mechanism: it makes every
    transaction the role opens read-only, so no grant anywhere can accidentally
    let it write. Revoking individual privileges instead would mean revoking
    them from PUBLIC on a shared database, which would break everything else
    using it.
    """
    autocommit = postgres.execution_options(isolation_level="AUTOCOMMIT")
    _drop_reader(autocommit)
    with autocommit.connect() as connection:
        connection.execute(sa.text(
            f"CREATE ROLE {READER} LOGIN PASSWORD '{READER_PASSWORD}'"
        ))
        connection.execute(sa.text(
            f"ALTER ROLE {READER} SET default_transaction_read_only = on"
        ))
        connection.execute(sa.text(f"GRANT CONNECT ON DATABASE {DATABASE} TO {READER}"))
        connection.execute(sa.text(f"GRANT USAGE ON SCHEMA public TO {READER}"))
        connection.execute(sa.text(
            f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READER}"
        ))

    yield

    _drop_reader(autocommit)


def _drop_reader(autocommit) -> None:
    """
    Remove the role and everything granted to it.

    DROP ROLE alone fails once anything has been granted, which is how a failed
    run leaves a role behind that breaks every run after it.
    """
    with autocommit.connect() as connection:
        for statement in (
            f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {READER}",
            f"REVOKE ALL ON SCHEMA public FROM {READER}",
            f"REVOKE ALL ON DATABASE {DATABASE} FROM {READER}",
            f"DROP OWNED BY {READER}",
            f"DROP ROLE IF EXISTS {READER}",
        ):
            try:
                connection.execute(sa.text(statement))
            except Exception:  # noqa: BLE001 -- the role may not exist yet
                pass


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def auth(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture(scope="module")
def admin(client):
    return auth(client, ADMIN)


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------
def test_a_writable_connection_is_reported_as_writable(postgres):
    result = connections.probe(owner_url())
    assert result.reachable is True
    assert result.read_only is False
    assert "read-only" in result.detail.lower()


def test_a_read_only_connection_is_reported_as_read_only(postgres, read_only_role):
    result = connections.probe(reader_url())
    assert result.reachable is True, result.detail
    assert result.read_only is True, result.detail


def test_the_probe_leaves_the_database_exactly_as_it_found_it(postgres):
    """
    It creates a temporary table to find out whether it can. If that survived
    the probe, the tool would be modifying the database it promises not to.
    """
    before = _table_names(postgres)
    connections.probe(owner_url())
    assert _table_names(postgres) == before


def _table_names(engine) -> set[str]:
    with engine.connect() as connection:
        return {
            name for (name,) in connection.execute(sa.text(
                "SELECT tablename FROM pg_tables WHERE schemaname NOT LIKE 'pg\\_%'"
            ))
        }


def test_the_probe_lists_the_databases_on_the_server(postgres):
    """
    What makes the picker a picker: the databases the credentials can actually
    see, rather than a name typed from memory.
    """
    result = connections.probe(owner_url())
    assert DATABASE in result.databases
    assert "template0" not in result.databases
    assert "postgres" not in result.databases


def test_the_probe_lists_the_schemas_it_could_read(postgres):
    result = connections.probe(owner_url())
    assert "public" in result.schemas
    assert not any(s.startswith("pg_") for s in result.schemas)


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------
def test_the_listing_always_includes_the_server_s_own_connection(client, admin):
    body = client.get("/api/v1/connections", headers=admin).json()
    builtin = next(c for c in body["connections"] if c["is_builtin"])

    assert builtin["id"] == "default"
    # Infrastructure, not content: the host and credentials are never sent.
    assert builtin["host"] is None
    assert builtin["username"] is None
    assert body["active_id"] == "default"


def test_test_returns_the_databases_to_choose_from(client, admin, postgres):
    body = client.post("/api/v1/connections/test", headers=admin, json={
        "host": HOST, "port": PORT, "database_name": DATABASE,
        "username": OWNER, "password": OWNER_PASSWORD, "ssl_mode": "disable",
    }).json()

    assert body["reachable"] is True
    assert DATABASE in body["databases"]
    assert body["read_only"] is False


def test_a_writable_connection_is_refused_not_saved_with_a_warning(client, admin, postgres):
    """
    The decision this whole feature turns on.

    On a server hosting other people's production databases, saving a writable
    connection and showing a warning is not a safety property.
    """
    response = client.post("/api/v1/connections", headers=admin, json={
        "name": "Writable", "host": HOST, "port": PORT, "database_name": DATABASE,
        "username": OWNER, "password": OWNER_PASSWORD, "ssl_mode": "disable",
    })
    assert response.status_code == 400
    assert "read-only" in response.json()["detail"].lower()

    listing = client.get("/api/v1/connections", headers=admin).json()
    assert "Writable" not in [c["name"] for c in listing["connections"]]


def test_a_read_only_connection_saves_and_can_be_activated(
    client, admin, postgres, read_only_role
):
    created = client.post("/api/v1/connections", headers=admin, json={
        "name": "Probe Reader", "host": HOST, "port": PORT, "database_name": DATABASE,
        "username": READER, "password": READER_PASSWORD, "ssl_mode": "disable",
    })
    assert created.status_code == 200, created.text
    connection_id = created.json()["id"]

    listing = client.get("/api/v1/connections", headers=admin).json()
    row = next(c for c in listing["connections"] if c["id"] == connection_id)
    assert row["is_read_only"] is True
    # The password went in; nothing about it comes back out.
    assert "password" not in json_keys(row)

    switched = client.post(f"/api/v1/connections/{connection_id}/activate", headers=admin)
    assert switched.status_code == 200, switched.text
    assert client.get("/api/v1/connections", headers=admin).json()["active_id"] == connection_id

    # Deleting the live connection would leave the application pointed at nothing.
    blocked = client.delete(f"/api/v1/connections/{connection_id}", headers=admin)
    assert blocked.status_code == 400
    assert "switch" in blocked.json()["detail"].lower()

    client.post("/api/v1/connections/default/activate", headers=admin)
    assert client.delete(
        f"/api/v1/connections/{connection_id}", headers=admin
    ).status_code == 200


def json_keys(payload: dict) -> set[str]:
    return set(payload.keys())


def test_the_builtin_connection_cannot_be_edited_or_deleted(client, admin):
    """It is part of the deployment, and changing it there is deliberate."""
    assert client.delete("/api/v1/connections/default", headers=admin).status_code == 400
    assert client.put("/api/v1/connections/default", headers=admin, json={
        "name": "x", "host": "h", "database_name": "d", "username": "u", "password": "p",
    }).status_code == 400


def test_only_a_connection_manager_may_see_or_add_connections(client):
    """
    A connection listing is a map of the estate. An analyst who can build any
    report they like still has no business reading it.
    """
    analyst = auth(client, ANALYST)
    assert client.get("/api/v1/connections", headers=analyst).status_code == 403
    assert client.post("/api/v1/connections", headers=analyst, json={
        "name": "x", "host": HOST, "port": PORT, "database_name": DATABASE,
        "username": OWNER, "password": OWNER_PASSWORD,
    }).status_code == 403
    assert client.post("/api/v1/connections/test", headers=analyst, json={
        "host": HOST, "port": PORT, "username": OWNER, "password": OWNER_PASSWORD,
    }).status_code == 403


def test_an_unreachable_connection_is_refused_with_a_reason(client, admin):
    response = client.post("/api/v1/connections", headers=admin, json={
        "name": "Nowhere", "host": "127.0.0.1", "port": 1,
        "database_name": "nothing", "username": "n", "password": "n",
        "ssl_mode": "disable",
    })
    assert response.status_code == 400
    assert response.json()["detail"]


def test_activating_a_connection_that_does_not_exist_is_a_404(client, admin):
    assert client.post(
        "/api/v1/connections/nope/activate", headers=admin
    ).status_code == 404

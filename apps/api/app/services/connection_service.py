"""
Database connections: testing, probing, and choosing which one is live.

The whole point of this application is that it cannot write to the databases it
reads. Adding connections is where that guarantee is most easily lost, because
a connection is added once, in a hurry, by someone who has an admin password to
hand and no read-only role yet.

So the write probe is not advisory. A connection that can write is not saved --
not saved with a warning, not saved disabled. On a server hosting other
people's production databases, "we warned you" is not a safety property.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.core.secrets import SecretUnavailable, decrypt_password
from app.models.metadata_models import AppSetting, DbConnection

#: The connection configured in the server's environment. It is not a row in
#: the database and cannot be edited or deleted from the browser -- changing it
#: means changing the deployment, deliberately.
BUILTIN_ID = "default"

#: Which connection reports currently read from.
ACTIVE_SETTING = "active_connection_id"

#: A probe must never sit on a lock or hold a slot on somebody's production
#: database. Ten seconds is long enough for a reachable server and short enough
#: that an unreachable one fails while the user is still watching.
PROBE_TIMEOUT = 10

#: Databases every PostgreSQL server has, which nobody wants to report on.
SYSTEM_DATABASES = frozenset({"postgres", "template0", "template1"})


@dataclass
class ProbeResult:
    reachable: bool = False
    read_only: bool = False
    #: What the probe learned, in words a person can act on.
    detail: str = ""
    server_version: str | None = None
    database_name: str | None = None
    #: Every database on that server the credentials can see.
    databases: list[str] = field(default_factory=list)
    #: Schemas in the connected database.
    schemas: list[str] = field(default_factory=list)

    def as_payload(self) -> dict:
        return {
            "reachable": self.reachable,
            "read_only": self.read_only,
            "detail": self.detail,
            "server_version": self.server_version,
            "database_name": self.database_name,
            "databases": self.databases,
            "schemas": self.schemas,
        }


def build_url(
    host: str, port: int, database: str, username: str, password: str, ssl_mode: str
) -> str:
    """A PostgreSQL URL, with the password escaped rather than interpolated."""
    from urllib.parse import quote_plus

    return (
        f"postgresql+psycopg://{quote_plus(username)}:{quote_plus(password)}"
        f"@{host}:{port}/{quote_plus(database)}?sslmode={ssl_mode}"
    )


def _engine(url: str) -> Engine:
    return sa.create_engine(
        url,
        future=True,
        poolclass=sa.pool.NullPool,  # a probe must not leave a pool behind
        connect_args={"connect_timeout": PROBE_TIMEOUT, "application_name": "bi-probe"},
    )


def probe(url: str) -> ProbeResult:
    """
    Connect, find out what is there, and establish whether we can write.

    The write test creates a temporary table inside a transaction that is always
    rolled back. On a genuinely read-only role it fails and nothing happened; on
    a writable role it succeeds and is undone -- so the probe answers the
    question without leaving anything behind either way.
    """
    result = ProbeResult()
    engine = _engine(url)
    try:
        with engine.connect() as connection:
            # Set the moment the connection opens. Anything that fails after
            # this point is a fact about the database, not about reaching it,
            # and reporting it as unreachable would send someone to check the
            # firewall over a permissions problem.
            result.reachable = True
            result.server_version = str(
                connection.execute(sa.text("SELECT version()")).scalar_one()
            ).split(" on ")[0]
            result.database_name = connection.execute(
                sa.text("SELECT current_database()")
            ).scalar_one()

            result.databases = sorted(
                name for (name,) in connection.execute(sa.text(
                    "SELECT datname FROM pg_database "
                    "WHERE datallowconn AND NOT datistemplate"
                ))
                if name not in SYSTEM_DATABASES
            )
            result.schemas = sorted(
                name for (name,) in connection.execute(sa.text(
                    "SELECT nspname FROM pg_namespace "
                    "WHERE nspname NOT LIKE 'pg\\_%' AND nspname <> 'information_schema'"
                ))
            )

        result.read_only, result.detail = _write_probe(engine)
    except SQLAlchemyError as error:
        if not result.reachable:
            result.detail = _explain(error)
        else:
            result.read_only = False
            result.detail = _explain(error)
    except Exception as error:  # noqa: BLE001 -- a probe must never crash a request
        if not result.reachable:
            result.detail = str(error)[:300]
        else:
            result.read_only = False
            result.detail = str(error)[:300]
    finally:
        engine.dispose()

    return result


def _write_probe(engine: Engine) -> tuple[bool, str]:
    """
    True when the connection cannot write. Always leaves the database as found.

    On its own connection: reading the server's metadata first leaves an open
    transaction on that one, and a probe that has to reason about ambient
    transaction state is a probe that will eventually get it wrong.
    """
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(sa.text("CREATE TEMP TABLE bi_write_probe (x integer)"))
        except SQLAlchemyError:
            transaction.rollback()
            return True, "Read-only: this connection cannot create or modify anything."

        # It could write. Undo it, and say so plainly.
        transaction.rollback()

    return False, (
        "This account can write to the database. Reporting connections must be "
        "read-only, so this one was not saved. Create a role with SELECT only "
        "and connect with that instead."
    )


def _explain(error: SQLAlchemyError) -> str:
    """Turn a driver error into something a person can act on."""
    text = str(getattr(error, "orig", error))
    lowered = text.lower()
    if "could not translate host name" in lowered or "name or service not known" in lowered:
        return ("That host name could not be resolved. If the database runs in "
                "another Docker container, this application has to share its network.")
    if "connection refused" in lowered:
        return "Nothing is listening on that host and port."
    if "password authentication failed" in lowered or "authentication failed" in lowered:
        return "The username or password was not accepted."
    if "does not exist" in lowered and "database" in lowered:
        return "That database does not exist on this server."
    if "timeout" in lowered or "timed out" in lowered:
        return f"The server did not answer within {PROBE_TIMEOUT} seconds."
    if "no pg_hba.conf entry" in lowered:
        return ("The server refused the connection for this host. Its pg_hba.conf "
                "does not allow connections from here.")
    return text[:300]


# ---------------------------------------------------------------------------
# Which connection is live
# ---------------------------------------------------------------------------
def active_connection_id(db: DbSession) -> str:
    row = db.get(AppSetting, ACTIVE_SETTING)
    chosen = (row.value or {}).get("id") if row and isinstance(row.value, dict) else None
    if not chosen or chosen == BUILTIN_ID:
        return BUILTIN_ID

    # A connection that has since been deleted or disabled must not silently
    # keep the application pointed at nothing.
    connection = db.get(DbConnection, chosen)
    if connection is None or not connection.is_active:
        return BUILTIN_ID
    return chosen


def set_active(db: DbSession, connection_id: str) -> None:
    row = db.get(AppSetting, ACTIVE_SETTING)
    if row is None:
        db.add(AppSetting(key=ACTIVE_SETTING, value={"id": connection_id}))
    else:
        row.value = {"id": connection_id}


def connection_url(db: DbSession, connection_id: str) -> str:
    """
    The URL for a saved connection.

    Raises ``SecretUnavailable`` when the password cannot be read, which the
    caller must surface -- falling back to the built-in connection here would
    silently report on the wrong database.
    """
    if connection_id == BUILTIN_ID:
        return settings.operational_dsn

    connection = db.get(DbConnection, connection_id)
    if connection is None:
        raise SecretUnavailable("That connection no longer exists.")

    return build_url(
        host=connection.host,
        port=connection.port,
        database=connection.database_name,
        username=connection.username,
        password=decrypt_password(connection.password_encrypted),
        ssl_mode=connection.ssl_mode,
    )


def builtin_payload(db: DbSession) -> dict:
    """
    The environment's connection, described for the listing.

    Shown alongside the others so the list answers "what are we reading from"
    completely, rather than showing only what happens to have been added later.
    """
    return {
        "id": BUILTIN_ID,
        "name": f"{settings.database_name} (configured on the server)",
        "database_type": "postgresql",
        "host": None,          # never exposed: it is infrastructure, not content
        "port": None,
        "database_name": settings.database_name,
        "username": None,
        "ssl_mode": None,
        "is_read_only": settings.database_enforce_read_only,
        "is_replica": settings.database_is_replica,
        "is_active": True,
        "is_builtin": True,
        "is_selected": active_connection_id(db) == BUILTIN_ID,
        "schemas": [s.strip() for s in (settings.database_schema or "").split(",") if s.strip()],
        "last_scanned_at": None,
        "created_at": None,
    }

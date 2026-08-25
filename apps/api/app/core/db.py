"""Metadata database session management and first-run bootstrap."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.security import hash_password
from app.models.metadata_models import Base, Role, User

_engine = sa.create_engine(
    settings.metadata_dsn,
    future=True,
    connect_args={"check_same_thread": False}
    if settings.metadata_dsn.startswith("sqlite")
    else {},
)

SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def get_engine() -> sa.Engine:
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


#: Development seed accounts. Every role is represented so permission behaviour
#: can be exercised without an admin console. Production installs create the
#: first administrator through the first-run wizard instead (spec 57).
DEV_ACCOUNTS = [
    ("admin@decoinks.local", "Admin User", Role.SUPER_ADMIN),
    ("boss@decoinks.local", "Management", Role.MANAGEMENT),
    ("analyst@decoinks.local", "Data Analyst", Role.ANALYST),
    ("viewer@decoinks.local", "Report Viewer", Role.VIEWER),
]
DEV_PASSWORD = "demo1234"


#: Arbitrary but fixed key for the PostgreSQL advisory lock guarding schema
#: creation. Any constant works; it only has to be the same in every worker.
_SCHEMA_LOCK_KEY = 8_412_990_517


def init_database(seed_dev_users: bool = True) -> None:
    """
    Create the metadata schema, exactly once, no matter how many workers start.

    Production runs several gunicorn workers and they boot simultaneously. Left
    unguarded, each one calls create_all() at the same moment and they collide
    inside PostgreSQL's catalog -- one worker dies with a duplicate-key error on
    pg_type. An advisory lock serialises them: the first creates the schema, the
    rest wait, then find it already there.
    """
    is_postgres = _engine.dialect.name == "postgresql"

    if not is_postgres:
        Base.metadata.create_all(_engine)
    else:
        with _engine.begin() as connection:
            # Released automatically when the transaction ends.
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SCHEMA_LOCK_KEY}
            )
            Base.metadata.create_all(connection)

    if not seed_dev_users or settings.environment == "production":
        return

    with session_scope() as session:
        if session.scalar(sa.select(sa.func.count()).select_from(User)):
            return
        for email, name, role in DEV_ACCOUNTS:
            session.add(
                User(
                    email=email,
                    full_name=name,
                    role=role,
                    password_hash=hash_password(DEV_PASSWORD),
                )
            )

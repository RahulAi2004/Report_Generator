"""
The background refresh loop.

"Dynamically updating" means somebody has to ask the provider again on a timer.
This is that timer, deliberately small: it wakes, asks which datasets are due,
syncs them one at a time, and goes back to sleep.

One at a time and not in parallel, because the limit that matters is the
provider's rate limit rather than our CPU -- and a burst of parallel requests is
the fastest way to get a token throttled.

A PostgreSQL advisory lock keeps it to one worker. The API runs several, and
without the lock each would sync every dataset on its own schedule, multiplying
the calls made against somebody's API quota by the number of workers.
"""

from __future__ import annotations

import asyncio
import logging

import sqlalchemy as sa

from app.core.db import get_engine, session_scope
from app.services import connector_service, schema_service
from app.services.connectors.base import ConnectorError

logger = logging.getLogger(__name__)

#: Distinct from the schema-migration lock in core.db.
_LOCK_KEY = 0x2026_0827

#: How often to look for work. Not how often anything syncs -- that is each
#: connector's own interval; this is only the granularity of noticing.
TICK_SECONDS = 300


async def run(stop: asyncio.Event) -> None:
    """Wake periodically until asked to stop."""
    logger.info("Connector refresh loop started (checking every %ss)", TICK_SECONDS)
    while not stop.is_set():
        try:
            await asyncio.to_thread(_tick)
        except Exception:  # noqa: BLE001 -- the loop must outlive any one failure
            logger.exception("Connector refresh tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            pass
    logger.info("Connector refresh loop stopped")


def _tick() -> None:
    engine = get_engine()
    if engine.dialect.name != "postgresql":
        _sync_due()  # single-process development; no lock needed
        return

    with engine.connect() as connection:
        held = connection.execute(
            sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": _LOCK_KEY}
        ).scalar_one()
        if not held:
            return  # another worker is doing it
        try:
            _sync_due()
        finally:
            connection.execute(
                sa.text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY}
            )


def _sync_due() -> None:
    with session_scope() as session:
        due = connector_service.due_for_sync(session)
        if not due:
            return
        logger.info("Refreshing %d connector dataset(s)", len(due))
        ids = [dataset.id for dataset in due]

    refreshed = 0
    for dataset_id in ids:
        try:
            with session_scope() as session:
                dataset = session.get(type(due[0]), dataset_id)
                if dataset is None:
                    continue
                connector_service.sync_dataset(session, dataset)
                refreshed += 1
        except ConnectorError as error:
            # Recorded on the dataset by sync_dataset; the loop carries on so
            # one expired token does not stop every other connector.
            logger.info("Dataset %s did not refresh: %s", dataset_id, error)
        except Exception:  # noqa: BLE001
            logger.exception("Unexpected failure refreshing dataset %s", dataset_id)

    if refreshed:
        # The registry caches columns and row counts; both have just changed.
        schema_service.forget_snapshots()

"""
Turning a connector's rows into a table the report builder can see.

The whole design rests on one decision: API data is stored the same way uploaded
spreadsheets are -- as real tables in the metadata database, described by a
normal ``TableMeta``. Nothing in the report path learns that an API exists. The
schema registry lists the columns, the builder offers them as fields, the
compiler joins them to CRM data through the hybrid executor, and the credential
and masking rules apply unchanged.

Two properties are worth stating because they are easy to lose.

A sync replaces a window of data rather than appending to it. Meta restates
recent days -- yesterday's spend is not final -- so appending would accumulate
several versions of the same day and every total built on it would be wrong.

And a sync that fails leaves the previous data in place. A report showing
yesterday's figures is a report someone can still use; a report showing nothing
because a token expired at 3am is not.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.db import get_engine
from app.core.secrets import SecretUnavailable, decrypt_password
from app.domain.schema.registry import ColumnMeta, DataType, TableMeta
from app.domain.uploads.parser import coerce, infer_type, safe_identifier
from app.models.metadata_models import ApiConnector, ConnectorDataset
from app.services.connectors import registry as provider_registry
from app.services.connectors.base import ConnectorError, DatasetKind, union_columns

logger = logging.getLogger(__name__)

#: Separate from `uploads` so a person can tell at a glance whether a table came
#: from a spreadsheet somebody made or from an API somebody connected.
CONNECTOR_SCHEMA = "connectors"

INSERT_BATCH = 500

#: A safety limit. A misconfigured lookback on a large ad account can otherwise
#: pull millions of rows into the metadata database in one sync.
MAX_ROWS_PER_SYNC = 200_000

#: How many pages of an API to walk before stopping. Bounded so a provider that
#: returns a cursor forever cannot spin here indefinitely.
MAX_PAGES = 200

#: Kept as a plain mapping so a test can register a stub provider.
PROVIDERS: dict[str, tuple[DatasetKind, ...]] = {
    key: spec.datasets for key, spec in provider_registry.PROVIDERS.items()
}


def dataset_kinds(provider: str) -> tuple[DatasetKind, ...]:
    return PROVIDERS.get(provider, ())


def dataset_kind(provider: str, key: str) -> DatasetKind | None:
    return next((d for d in dataset_kinds(provider) if d.key == key), None)


def build_connector(connector: ApiConnector):
    """The client for a stored credential, whichever provider it is for."""
    spec = provider_registry.spec(connector.provider)
    if spec is None:
        raise ConnectorError(
            f"No connector is implemented for '{connector.provider}'."
        )

    try:
        token = decrypt_password(connector.token_encrypted)
    except SecretUnavailable as error:
        raise ConnectorError(str(error)) from error

    app_secret = ""
    if connector.app_secret_encrypted:
        try:
            app_secret = decrypt_password(connector.app_secret_encrypted)
        except SecretUnavailable:
            # Some providers work without it. Losing the second credential
            # should degrade rather than stop, and the call that needs it will
            # say so in its own words.
            logger.warning(
                "Second credential for connector %s could not be read", connector.id
            )

    return spec.build(
        token=token,
        api_version=connector.api_version or spec.default_api_version,
        app_id=connector.app_id or "",
        app_secret=app_secret,
        settings=connector.settings or {},
    )


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def ensure_schema() -> None:
    engine = get_engine()
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(sa.schema.CreateSchema(CONNECTOR_SCHEMA, if_not_exists=True))


def _physical_table(dataset_id: str) -> str:
    return f"api_{dataset_id}"


_SA_TYPES: dict[DataType, type] = {
    DataType.INTEGER: sa.BigInteger,
    DataType.DECIMAL: sa.Numeric,
    DataType.BOOLEAN: sa.Boolean,
    DataType.DATE: sa.Date,
    DataType.DATETIME: sa.DateTime,
    DataType.TEXT: sa.Text,
}


def _sa_table(dataset: ConnectorDataset, metadata: sa.MetaData | None = None) -> sa.Table:
    return sa.Table(
        dataset.physical_table,
        metadata or sa.MetaData(),
        *[
            sa.Column(
                column["name"],
                _SA_TYPES.get(DataType(column["data_type"]), sa.Text)(),
            )
            for column in dataset.columns
        ],
        schema=CONNECTOR_SCHEMA if get_engine().dialect.name == "postgresql" else None,
    )


def _humanise(name: str) -> str:
    """
    A column name a person would write.

    APIs return camelCase far more often than snake_case, and title-casing that
    gives "Stylecode" and "Crafttype" -- names nobody would choose and nobody
    reads twice. Splitting on the case change gives "Style Code".
    """
    import re

    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name.replace("_", " "))
    return " ".join(word for word in spaced.split()).strip().title() or name


def infer_columns(rows: list[dict]) -> list[dict]:
    """
    The schema, from what actually arrived.

    Every column any row had, not the first row's keys: providers omit fields
    with no value, so reading one row loses columns present on all the others.
    This is what "all the fields the API returns" means in practice.
    """
    names = union_columns(rows)
    columns: list[dict] = []
    used: set[str] = set()

    for name in names:
        safe = safe_identifier(name)
        candidate, suffix = safe, 2
        while candidate in used:
            candidate, suffix = f"{safe}_{suffix}", suffix + 1
        used.add(candidate)

        values = [
            "" if row.get(name) is None else str(row.get(name))
            for row in rows[:500]
        ]
        data_type = infer_type([v for v in values if v != ""]) if any(values) else DataType.TEXT
        columns.append({
            "name": candidate,
            "label": _humanise(name),
            "data_type": data_type.value,
            "nullable": True,
            "source": name,
        })
    return columns


# ---------------------------------------------------------------------------
# Syncing
# ---------------------------------------------------------------------------
def sync_dataset(session: Session, dataset: ConnectorDataset) -> ConnectorDataset:
    """
    Refresh one dataset.

    Everything is fetched first and written second. A partial write would leave
    a table that looks complete and is not, which is worse than a sync that
    failed and said so.
    """
    connector_row = session.get(ApiConnector, dataset.connector_id)
    if connector_row is None:
        raise ConnectorError("The connector this dataset belongs to has been removed.")

    kind = dataset_kind(connector_row.provider, dataset.dataset_key)
    if kind is None:
        raise ConnectorError(
            f"'{dataset.dataset_key}' is no longer a dataset this connector offers."
        )

    started = time.perf_counter()
    dataset.status = "syncing"
    session.commit()

    try:
        client = build_connector(connector_row)
        rows = _fetch_all(client, kind, dataset)
    except ConnectorError as error:
        # The previous data stays. Yesterday's figures beat no figures.
        dataset.status = "error"
        dataset.last_error = str(error)
        connector_row.last_error = str(error)
        connector_row.last_checked_at = _now()
        session.commit()
        raise

    if not rows:
        # Nothing came back. That is not the same as "there is nothing", so the
        # existing table is left alone and the outcome is recorded.
        dataset.status = "ready"
        dataset.last_error = (
            "The last sync returned no rows. The table still holds what was "
            "there before."
        )
        dataset.last_synced_at = _now()
        dataset.last_duration_ms = int((time.perf_counter() - started) * 1000)
        session.commit()
        return dataset

    columns = infer_columns(rows)
    _write_rows(session, dataset, columns, rows)

    dataset.columns = columns
    dataset.row_count = len(rows)
    dataset.status = "ready"
    dataset.last_error = None
    dataset.last_synced_at = _now()
    dataset.last_duration_ms = int((time.perf_counter() - started) * 1000)
    connector_row.last_error = None
    connector_row.last_checked_at = _now()
    session.commit()
    return dataset


def _fetch_all(client, kind: DatasetKind, dataset: ConnectorDataset) -> list[dict]:
    """Walk the provider's pages until they run out, or a limit is reached."""
    since = until = None
    if kind.time_series:
        until = date.today()
        since = until - timedelta(days=max(1, dataset.lookback_days))

    rows: list[dict] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        page = client.fetch(
            dataset=kind.key,
            resource_id=dataset.resource_id,
            since=since,
            until=until,
            cursor=cursor,
        )
        rows.extend(page.rows)
        if len(rows) >= MAX_ROWS_PER_SYNC:
            logger.warning(
                "Dataset %s hit the row limit at %s rows", dataset.id, len(rows)
            )
            rows = rows[:MAX_ROWS_PER_SYNC]
            break
        cursor = page.cursor
        if not cursor:
            break
    return rows


def _write_rows(
    session: Session, dataset: ConnectorDataset, columns: list[dict], rows: list[dict]
) -> None:
    """
    Replace the table's contents.

    Replace rather than append: providers restate recent data -- yesterday's ad
    spend is not final -- so appending would leave several versions of the same
    day in the table and every total built on it would be wrong.

    The old table is dropped and recreated inside one transaction, so a reader
    sees either the previous contents or the new ones, never half of each.
    """
    ensure_schema()
    if not dataset.physical_table:
        dataset.physical_table = _physical_table(dataset.id)
        session.flush()

    dataset.columns = columns
    table = _sa_table(dataset)
    types = {c["name"]: DataType(c["data_type"]) for c in columns}
    sources = {c["name"]: c.get("source", c["name"]) for c in columns}

    engine = get_engine()
    with engine.begin() as connection:
        table.drop(connection, checkfirst=True)
        table.create(connection)

        for start in range(0, len(rows), INSERT_BATCH):
            batch = rows[start : start + INSERT_BATCH]
            connection.execute(
                table.insert(),
                [
                    {
                        name: coerce(
                            _as_text(row.get(sources[name])), types[name]
                        )
                        for name in types
                    }
                    for row in batch
                ],
            )


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def drop_dataset(session: Session, dataset: ConnectorDataset) -> None:
    """Remove the table and forget the dataset."""
    if dataset.physical_table:
        try:
            with get_engine().begin() as connection:
                _sa_table(dataset).drop(connection, checkfirst=True)
        except Exception:  # noqa: BLE001 -- the record must go either way
            logger.warning("Could not drop %s", dataset.physical_table, exc_info=True)
    session.delete(dataset)
    session.commit()


# ---------------------------------------------------------------------------
# Making them visible to the report builder
# ---------------------------------------------------------------------------
def _provider_label(provider: str) -> str:
    """
    The provider's own name, not a guess made from its key.

    `provider.title()` turned "riin" into "Riin" and "ssactivewear" into
    "Ssactivewear", which is not what anybody calls them.
    """
    spec = provider_registry.spec(provider)
    return spec.label if spec else provider.title()


def as_table_meta(dataset: ConnectorDataset, provider: str) -> TableMeta:
    """
    A connector dataset as an ordinary table.

    Being a normal TableMeta is the whole trick: resolution, the join planner,
    the compiler and the hybrid executor need to know nothing about APIs.
    """
    # Bounded: a display name carrying a URL once produced a 52-character
    # identifier that was truncated everywhere it appeared.
    stem = safe_identifier(dataset.display_name)[:40].rstrip("_")
    name = f"api_{stem or dataset.id}"
    return TableMeta(
        name=name,
        schema=CONNECTOR_SCHEMA,
        physical_name=dataset.physical_table,
        kind="upload",  # executed locally, exactly like an uploaded file
        category=f"{_provider_label(provider)} API",
        display_name=dataset.display_name,
        description=(
            f"{dataset.resource_name} — last refreshed "
            f"{dataset.last_synced_at.isoformat(sep=' ', timespec='minutes')}"
            if dataset.last_synced_at else f"{dataset.resource_name} — not yet refreshed"
        ),
        estimated_rows=dataset.row_count,
        columns=tuple(
            ColumnMeta(
                table=name,
                name=column["name"],
                data_type=DataType(column["data_type"]),
                physical_type=column["data_type"],
                nullable=True,
                ordinal=index,
                display_name=column.get("label"),
            )
            for index, column in enumerate(dataset.columns)
        ),
    )


def load_datasets(session: Session) -> list[tuple[ConnectorDataset, str]]:
    """Every ready dataset, with the provider that produced it."""
    rows = session.execute(
        sa.select(ConnectorDataset, ApiConnector.provider)
        .join(ApiConnector, ApiConnector.id == ConnectorDataset.connector_id)
        .where(
            ConnectorDataset.is_enabled.is_(True),
            ConnectorDataset.status == "ready",
            ApiConnector.is_active.is_(True),
        )
    ).all()
    return [(dataset, provider) for dataset, provider in rows]


def due_for_sync(session: Session) -> list[ConnectorDataset]:
    """Datasets whose refresh interval has elapsed."""
    now = _now()
    due: list[ConnectorDataset] = []
    rows = session.execute(
        sa.select(ConnectorDataset, ApiConnector)
        .join(ApiConnector, ApiConnector.id == ConnectorDataset.connector_id)
        .where(
            ConnectorDataset.is_enabled.is_(True),
            ApiConnector.is_active.is_(True),
        )
    ).all()

    for dataset, connector_row in rows:
        interval = max(5, connector_row.sync_interval_minutes)
        if dataset.last_synced_at is None:
            due.append(dataset)
        elif dataset.last_synced_at + timedelta(minutes=interval) <= now:
            due.append(dataset)
    return due


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

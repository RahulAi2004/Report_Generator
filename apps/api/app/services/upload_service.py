"""
Uploaded dataset storage.

Each upload becomes a real table in the application's own metadata database,
inside an `uploads` schema. Real tables rather than blobs of JSON, because the
whole point is to query and join them with SQL -- the same compiler, the same
safety layer, the same governor as any other source.

Nothing is ever written to the operational database. That connection is
read-only and stays that way.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.db import get_engine
from app.domain.schema.registry import ColumnMeta, DataType, TableMeta
from app.domain.uploads.parser import ParsedFile, UploadError, safe_identifier
from app.models.metadata_models import UploadedDataset

logger = logging.getLogger(__name__)

UPLOAD_SCHEMA = "uploads"
UPLOAD_CATEGORY = "Uploaded Files"

#: Rows are inserted in batches; one statement per row would take minutes on a
#: large sheet, one statement for everything would exhaust memory.
INSERT_BATCH = 1_000

_SA_TYPES: dict[DataType, type] = {
    DataType.TEXT: sa.Text,
    DataType.INTEGER: sa.BigInteger,
    DataType.DECIMAL: sa.Numeric,
    DataType.BOOLEAN: sa.Boolean,
    DataType.DATE: sa.Date,
    DataType.DATETIME: sa.DateTime,
}


def ensure_schema() -> None:
    """Create the uploads schema if it does not exist. Metadata database only."""
    engine = get_engine()
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(sa.schema.CreateSchema(UPLOAD_SCHEMA, if_not_exists=True))


def _physical_table(dataset_id: str) -> str:
    """Table name derived from our own id, never from user input."""
    return f"upload_{dataset_id[:24]}"


def _sa_table(dataset: UploadedDataset, metadata: sa.MetaData | None = None) -> sa.Table:
    columns = [
        sa.Column(column["name"], _SA_TYPES.get(DataType(column["data_type"]), sa.Text)())
        for column in dataset.columns
    ]
    return sa.Table(
        dataset.physical_table,
        metadata or sa.MetaData(),
        *columns,
        schema=UPLOAD_SCHEMA if get_engine().dialect.name == "postgresql" else None,
    )


# ---------------------------------------------------------------------------
def create_dataset(
    session: Session,
    name: str,
    filename: str,
    parsed: ParsedFile,
    owner_id: str,
    size_bytes: int,
    description: str | None = None,
) -> UploadedDataset:
    """Persist a parsed file as a queryable table."""
    ensure_schema()

    dataset = UploadedDataset(
        name=name.strip() or filename,
        description=description,
        original_filename=filename,
        physical_table="",
        row_count=parsed.row_count,
        columns=[
            {
                "name": column.name,
                "label": column.label,
                "data_type": column.data_type.value,
                "nullable": column.nullable,
            }
            for column in parsed.columns
        ],
        size_bytes=size_bytes,
        uploaded_by=owner_id,
        status="ready",
    )
    session.add(dataset)
    session.flush()  # assigns the id we name the table after

    dataset.physical_table = _physical_table(dataset.id)
    table = _sa_table(dataset)

    engine = get_engine()
    try:
        with engine.begin() as connection:
            table.drop(connection, checkfirst=True)
            table.create(connection)

            column_names = [column["name"] for column in dataset.columns]
            for start in range(0, len(parsed.rows), INSERT_BATCH):
                batch = parsed.rows[start : start + INSERT_BATCH]
                connection.execute(
                    table.insert(),
                    [dict(zip(column_names, row)) for row in batch],
                )
    except Exception as error:
        logger.exception("Failed to store uploaded dataset %s", dataset.id)
        session.rollback()
        raise UploadError(
            "The file was read successfully but could not be stored. "
            "The technical details have been logged."
        ) from error

    session.commit()
    return dataset


def delete_dataset(session: Session, dataset: UploadedDataset) -> None:
    """Drop the data table and forget the dataset."""
    engine = get_engine()
    try:
        with engine.begin() as connection:
            _sa_table(dataset).drop(connection, checkfirst=True)
    except Exception:
        # A missing table should not block removing the record.
        logger.warning("Could not drop table for dataset %s", dataset.id, exc_info=True)

    session.delete(dataset)
    session.commit()


def sample_rows(dataset: UploadedDataset, limit: int = 20) -> tuple[list[str], list[tuple]]:
    """First few rows, for the preview on the uploads page."""
    table = _sa_table(dataset)
    with get_engine().connect() as connection:
        result = connection.execute(sa.select(table).limit(limit))
        return list(result.keys()), [tuple(row) for row in result]


# ---------------------------------------------------------------------------
def as_table_meta(dataset: UploadedDataset) -> TableMeta:
    """
    Expose a dataset to the report engine exactly like a database table.

    Being a normal TableMeta is what lets uploads flow through resolution, the
    join planner, RBAC and the compiler without any of them special-casing them.
    """
    columns = tuple(
        ColumnMeta(
            table=dataset.physical_table,
            name=column["name"],
            data_type=DataType(column["data_type"]),
            physical_type=column["data_type"],
            nullable=column.get("nullable", True),
            ordinal=index,
            display_name=column.get("label"),
        )
        for index, column in enumerate(dataset.columns)
    )
    return TableMeta(
        name=dataset.physical_table,
        schema=UPLOAD_SCHEMA,
        kind="upload",
        display_name=dataset.name,
        category=UPLOAD_CATEGORY,
        description=(
            f"Uploaded from {dataset.original_filename}"
            + (f" — {dataset.description}" if dataset.description else "")
        ),
        estimated_rows=dataset.row_count,
        columns=columns,
    )


def load_datasets(session: Session) -> list[UploadedDataset]:
    return list(
        session.scalars(
            sa.select(UploadedDataset)
            .where(UploadedDataset.status == "ready")
            .order_by(UploadedDataset.created_at.desc())
        )
    )


def suggest_name(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    return " ".join(word.capitalize() for word in safe_identifier(stem).split("_")) or "Upload"

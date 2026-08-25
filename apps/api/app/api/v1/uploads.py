"""
Uploaded dataset routes.

Files become real tables in the application's own database, so they can be
queried and joined exactly like any other source. The operational database is
never written to.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import client_ip, current_principal, require, write_audit
from app.core.security import Permission, Principal
from app.domain.uploads import parser
from app.models.metadata_models import UploadedDataset
from app.services import upload_service

router = APIRouter(prefix="/uploads", tags=["uploads"])

#: Anything larger belongs in the database, not a spreadsheet.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _payload(dataset: UploadedDataset) -> dict:
    return {
        "id": dataset.id,
        "name": dataset.name,
        "description": dataset.description,
        "original_filename": dataset.original_filename,
        "table_name": dataset.physical_table,
        "row_count": dataset.row_count,
        "column_count": len(dataset.columns or []),
        "columns": dataset.columns,
        "size_bytes": dataset.size_bytes,
        "created_at": dataset.created_at.isoformat(),
        "uploaded_by": dataset.uploaded_by,
    }


@router.get("")
def list_uploads(
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    return {"datasets": [_payload(d) for d in upload_service.load_datasets(db)]}


@router.post("")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(default=""),
    description: str = Form(default=""),
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_SCHEMA)),
):
    """
    Accept a CSV or Excel file and store it as a queryable table.

    Column types are inferred rather than assumed text: an amount stored as text
    cannot be summed, and a date stored as text sorts alphabetically.
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"That file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )

    try:
        parsed = parser.parse(file.filename or "upload.csv", content)
    except parser.UploadError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    try:
        dataset = upload_service.create_dataset(
            session=db,
            name=name.strip() or upload_service.suggest_name(file.filename or "upload"),
            filename=file.filename or "upload.csv",
            parsed=parsed,
            owner_id=principal.id,
            size_bytes=len(content),
            description=description.strip() or None,
        )
    except parser.UploadError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    write_audit(
        db, principal, "dataset_uploaded", resource_type="upload", resource_id=dataset.id,
        ip=client_ip(request),
        payload={"rows": dataset.row_count, "columns": len(dataset.columns)},
    )

    return {
        **_payload(dataset),
        "warnings": parsed.warnings,
        "detected": [
            {"name": c.name, "label": c.label, "data_type": c.data_type.value,
             "sample": c.sample}
            for c in parsed.columns
        ],
    }


@router.get("/{dataset_id}/preview")
def preview_upload(
    dataset_id: str,
    limit: int = 20,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    dataset = db.get(UploadedDataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="That upload no longer exists.")

    columns, rows = upload_service.sample_rows(dataset, limit=min(limit, 100))
    return {
        "columns": columns,
        "rows": [[_serialize(value) for value in row] for row in rows],
        "row_count": dataset.row_count,
    }


@router.delete("/{dataset_id}")
def delete_upload(
    dataset_id: str,
    request: Request,
    db: DbSession = Depends(get_session),
    principal: Principal = Depends(require(Permission.MANAGE_SCHEMA)),
):
    """
    Remove an upload and drop its table.

    Saved reports built on it will stop compiling and say which table is
    missing, rather than quietly returning nothing.
    """
    dataset = db.get(UploadedDataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="That upload no longer exists.")

    name = dataset.name
    upload_service.delete_dataset(db, dataset)
    write_audit(
        db, principal, "dataset_deleted", resource_type="upload", resource_id=dataset_id,
        ip=client_ip(request), payload={"name": name},
    )
    return {"ok": True}


def _serialize(value):
    from datetime import date, datetime
    from decimal import Decimal

    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return "<binary>"
    return value

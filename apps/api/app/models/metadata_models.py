"""
Application metadata models (spec 40).

These live in the application's own database. The operational business database
is never written to and never carries application tables -- it remains the
source of truth for business data and nothing else.

Report definitions are stored as JSON IR, not as generated SQL (spec 16), so a
saved report survives schema changes, permission changes and engine upgrades.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Identity and access
# ---------------------------------------------------------------------------
class Role:
    """Role names. Permissions are derived from these (spec 33)."""

    SUPER_ADMIN = "super_admin"
    MANAGEMENT = "management"
    ANALYST = "analyst"
    VIEWER = "viewer"

    ALL = (SUPER_ADMIN, MANAGEMENT, ANALYST, VIEWER)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(sa.String(190), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(sa.String(160))
    #: Argon2id hash. The plaintext password is never stored or logged.
    password_hash: Mapped[str] = mapped_column(sa.String(255))
    role: Mapped[str] = mapped_column(sa.String(40), default=Role.VIEWER)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)

    #: Optional table-level narrowing. Empty means "everything the role allows".
    allowed_tables: Mapped[list | None] = mapped_column(sa.JSON, nullable=True)
    denied_columns: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)

    reports: Mapped[list["Report"]] = relationship(back_populates="owner")


class Session(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(sa.String(32), sa.ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(sa.DateTime, default=utcnow)
    ip_address: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)


# ---------------------------------------------------------------------------
# Connections and schema metadata
# ---------------------------------------------------------------------------
class DbConnection(Base):
    __tablename__ = "db_connections"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(sa.String(120))
    database_type: Mapped[str] = mapped_column(sa.String(40), default="postgresql")
    host: Mapped[str] = mapped_column(sa.String(190), default="")
    port: Mapped[int] = mapped_column(sa.Integer, default=5432)
    database_name: Mapped[str] = mapped_column(sa.String(120), default="")
    username: Mapped[str] = mapped_column(sa.String(120), default="")
    #: AES-GCM ciphertext. Never returned by any API, never logged.
    password_encrypted: Mapped[bytes | None] = mapped_column(sa.LargeBinary, nullable=True)
    ssl_mode: Mapped[str] = mapped_column(sa.String(30), default="prefer")
    is_read_only: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    is_replica: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    last_scanned_at: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, default=utcnow)


class SchemaTable(Base):
    """
    Admin-editable metadata *about* a physical table (spec 36).

    Friendly names, categories and reporting flags are assigned here so the
    production database is never altered to make reports readable.
    """

    __tablename__ = "schema_tables"
    __table_args__ = (sa.UniqueConstraint("connection_id", "physical_name"),)

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    connection_id: Mapped[str] = mapped_column(sa.String(32), index=True)
    physical_name: Mapped[str] = mapped_column(sa.String(190))
    display_name: Mapped[str | None] = mapped_column(sa.String(190), nullable=True)
    category: Mapped[str] = mapped_column(sa.String(80), default="Other")
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    estimated_rows: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    enabled_for_reporting: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    enabled_for_ai: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    is_sensitive: Mapped[bool] = mapped_column(sa.Boolean, default=False)


class SchemaColumnMeta(Base):
    __tablename__ = "schema_columns"
    __table_args__ = (sa.UniqueConstraint("connection_id", "table_name", "physical_name"),)

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    connection_id: Mapped[str] = mapped_column(sa.String(32), index=True)
    table_name: Mapped[str] = mapped_column(sa.String(190), index=True)
    physical_name: Mapped[str] = mapped_column(sa.String(190))
    display_name: Mapped[str | None] = mapped_column(sa.String(190), nullable=True)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    is_sensitive: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    mask_policy: Mapped[str] = mapped_column(sa.String(20), default="none")
    enabled_for_reporting: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    default_format: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)


class LogicalRelationship(Base):
    """
    A relationship an admin declared because the physical database lacks the FK.

    Stored here, never as a constraint on the production database (spec 1).
    """

    __tablename__ = "logical_relationships"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    connection_id: Mapped[str] = mapped_column(sa.String(32), index=True)
    left_table: Mapped[str] = mapped_column(sa.String(190))
    left_column: Mapped[str] = mapped_column(sa.String(190))
    right_table: Mapped[str] = mapped_column(sa.String(190))
    right_column: Mapped[str] = mapped_column(sa.String(190))
    cardinality: Mapped[str] = mapped_column(sa.String(10), default="1:N")
    default_join_type: Mapped[str] = mapped_column(sa.String(10), default="left")
    source: Mapped[str] = mapped_column(sa.String(20), default="manual")
    confidence: Mapped[float] = mapped_column(sa.Float, default=1.0)
    created_by: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, default=utcnow)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(sa.String(190))
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    connection_id: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    #: The full report IR. Not SQL -- see spec 16.
    definition: Mapped[dict] = mapped_column(sa.JSON)
    owner_id: Mapped[str] = mapped_column(sa.String(32), sa.ForeignKey("users.id"))
    is_template: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    is_favorite: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    is_archived: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    folder: Mapped[str | None] = mapped_column(sa.String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime, default=utcnow, onupdate=utcnow)
    last_run_at: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)
    run_count: Mapped[int] = mapped_column(sa.Integer, default=0)

    owner: Mapped[User] = relationship(back_populates="reports")


class ReportRun(Base):
    __tablename__ = "report_runs"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    report_id: Mapped[str | None] = mapped_column(sa.String(32), index=True, nullable=True)
    user_id: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    started_at: Mapped[datetime] = mapped_column(sa.DateTime, default=utcnow)
    duration_ms: Mapped[int] = mapped_column(sa.Integer, default=0)
    row_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    status: Mapped[str] = mapped_column(sa.String(20), default="success")
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


class UploadedDataset(Base):
    """
    A spreadsheet somebody uploaded, stored as a real table in this database.

    Kept here rather than in the operational database because that connection is
    read-only -- and must stay so. The row data lives in its own table in the
    `uploads` schema; this record is the catalogue entry describing it.
    """

    __tablename__ = "uploaded_datasets"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(sa.String(190))
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    original_filename: Mapped[str] = mapped_column(sa.String(255))
    #: Physical table holding the rows. Derived from this record's id, never
    #: from the uploaded file's name.
    physical_table: Mapped[str] = mapped_column(sa.String(80))
    row_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    #: [{name, label, data_type, nullable}] -- the inferred schema.
    columns: Mapped[list] = mapped_column(sa.JSON, default=list)
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    status: Mapped[str] = mapped_column(sa.String(20), default="ready")
    uploaded_by: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, default=utcnow)


# ---------------------------------------------------------------------------
# Audit and query history (spec 34, 35)
# ---------------------------------------------------------------------------
class AuditLog(Base):
    """
    Append-only. The application role holds no UPDATE or DELETE grant on this
    table, and rows are hash-chained so tampering is detectable.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(sa.DateTime, default=utcnow, index=True)
    user_id: Mapped[str | None] = mapped_column(sa.String(32), index=True, nullable=True)
    user_email: Mapped[str | None] = mapped_column(sa.String(190), nullable=True)
    action: Mapped[str] = mapped_column(sa.String(60), index=True)
    resource_type: Mapped[str | None] = mapped_column(sa.String(60), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    success: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    ip_address: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    payload: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    #: sha256 of (prev_hash + this row's canonical fields).
    prev_hash: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    row_hash: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)


class QueryHistory(Base):
    __tablename__ = "query_history"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    executed_at: Mapped[datetime] = mapped_column(sa.DateTime, default=utcnow, index=True)
    user_id: Mapped[str | None] = mapped_column(sa.String(32), index=True, nullable=True)
    report_id: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    #: Present when the query originated from the natural-language assistant.
    question: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    generated_sql: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    tables_accessed: Mapped[list | None] = mapped_column(sa.JSON, nullable=True)
    duration_ms: Mapped[int] = mapped_column(sa.Integer, default=0)
    row_count: Mapped[int] = mapped_column(sa.Integer, default=0)
    status: Mapped[str] = mapped_column(sa.String(20), default="success")
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


# ---------------------------------------------------------------------------
# Anomalies (phase 7 -- schema defined now so migrations stay stable)
# ---------------------------------------------------------------------------
class AnomalyRule(Base):
    __tablename__ = "anomaly_rules"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(sa.String(80), unique=True)
    title: Mapped[str] = mapped_column(sa.String(190))
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    kind: Mapped[str] = mapped_column(sa.String(40))
    definition: Mapped[dict] = mapped_column(sa.JSON)
    severity: Mapped[str] = mapped_column(sa.String(20), default="medium")
    tolerance: Mapped[float] = mapped_column(sa.Float, default=0.01)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime, default=utcnow)


class DetectedAnomaly(Base):
    __tablename__ = "detected_anomalies"

    id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, default=new_id)
    #: sha256(rule_key, entity_type, entity_key). Prevents a rescan from
    #: recreating anomalies an analyst has already triaged.
    fingerprint: Mapped[str] = mapped_column(sa.String(64), unique=True, index=True)
    rule_key: Mapped[str] = mapped_column(sa.String(80), index=True)
    category: Mapped[str] = mapped_column(sa.String(40))
    title: Mapped[str] = mapped_column(sa.String(255))
    description: Mapped[str] = mapped_column(sa.Text)
    entity_type: Mapped[str] = mapped_column(sa.String(80))
    entity_key: Mapped[str] = mapped_column(sa.String(190))
    severity: Mapped[str] = mapped_column(sa.String(20), index=True)
    confidence: Mapped[float] = mapped_column(sa.Float, default=1.0)
    expected_value: Mapped[str | None] = mapped_column(sa.String(190), nullable=True)
    actual_value: Mapped[str | None] = mapped_column(sa.String(190), nullable=True)
    evidence: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    related_records: Mapped[list | None] = mapped_column(sa.JSON, nullable=True)
    #: The rule as it stood at detection time, so later edits never rewrite history.
    rule_snapshot: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    status: Mapped[str] = mapped_column(sa.String(30), default="new", index=True)
    assignee_id: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(sa.DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(sa.DateTime, default=utcnow)
    resolved_by: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

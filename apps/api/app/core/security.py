"""
Authentication, sessions, RBAC and audit hashing.

Passwords use Argon2id. Session tokens are opaque random strings stored
server-side rather than self-contained JWTs, so an administrator can revoke a
session immediately -- which matters for a tool that reads company-wide data.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.models.metadata_models import Role

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def session_expiry(hours: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


# ---------------------------------------------------------------------------
# Permissions (spec 33). Deny by default.
# ---------------------------------------------------------------------------
class Permission:
    VIEW_DASHBOARD = "view_dashboard"
    RUN_REPORT = "run_report"
    BUILD_REPORT = "build_report"
    SAVE_REPORT = "save_report"
    DELETE_REPORT = "delete_report"
    EXPORT_DATA = "export_data"
    VIEW_SQL = "view_sql"
    VIEW_QUERY_VALUES = "view_query_values"
    ASK_AI = "ask_ai"
    VIEW_ANOMALIES = "view_anomalies"
    RESOLVE_ANOMALIES = "resolve_anomalies"
    MANAGE_RULES = "manage_rules"
    MANAGE_SCHEMA = "manage_schema"
    MANAGE_CONNECTIONS = "manage_connections"
    MANAGE_USERS = "manage_users"
    VIEW_AUDIT = "view_audit"


_VIEWER = {Permission.VIEW_DASHBOARD, Permission.RUN_REPORT}
_ANALYST = _VIEWER | {
    Permission.BUILD_REPORT, Permission.SAVE_REPORT, Permission.EXPORT_DATA,
    Permission.VIEW_SQL, Permission.ASK_AI, Permission.VIEW_ANOMALIES,
    Permission.RESOLVE_ANOMALIES,
}
_MANAGEMENT = _ANALYST | {Permission.DELETE_REPORT, Permission.VIEW_QUERY_VALUES}
_SUPER_ADMIN = _MANAGEMENT | {
    Permission.MANAGE_RULES, Permission.MANAGE_SCHEMA, Permission.MANAGE_CONNECTIONS,
    Permission.MANAGE_USERS, Permission.VIEW_AUDIT,
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    Role.VIEWER: _VIEWER,
    Role.ANALYST: _ANALYST,
    Role.MANAGEMENT: _MANAGEMENT,
    Role.SUPER_ADMIN: _SUPER_ADMIN,
}


@dataclass
class Principal:
    """The authenticated caller, as the domain layer sees them."""

    id: str
    email: str
    full_name: str
    role: str
    permissions: set[str] = field(default_factory=set)
    #: None means no table-level restriction beyond the role's own limits.
    allowed_tables: set[str] | None = None
    denied_columns: dict[str, set[str]] = field(default_factory=dict)

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    @property
    def is_admin(self) -> bool:
        return self.role == Role.SUPER_ADMIN

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "permissions": sorted(self.permissions),
        }


def principal_from_user(user) -> Principal:
    return Principal(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        permissions=set(ROLE_PERMISSIONS.get(user.role, set())),
        allowed_tables=set(user.allowed_tables) if user.allowed_tables else None,
        denied_columns={
            table: set(columns) for table, columns in (user.denied_columns or {}).items()
        },
    )


# ---------------------------------------------------------------------------
# Audit hash chain (spec 34)
# ---------------------------------------------------------------------------
def audit_row_hash(previous: str | None, payload: dict) -> str:
    """
    Chain each audit row to its predecessor.

    Deleting or editing a row breaks every hash after it, so tampering is
    detectable even by someone with database access.
    """
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256()
    digest.update((previous or "genesis").encode("utf-8"))
    digest.update(canonical.encode("utf-8"))
    return digest.hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def anomaly_fingerprint(rule_key: str, entity_type: str, entity_key: str) -> str:
    return hashlib.sha256(
        f"{rule_key}|{entity_type}|{entity_key}".encode("utf-8")
    ).hexdigest()

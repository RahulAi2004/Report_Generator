#!/usr/bin/env python3
"""
Read-only environment probe for the centralized operational database.

Answers the open architecture questions without a DBA:
  Q2  Is this a read replica or the primary?
  Q3  What privileges does our role actually have? (can it write?)
  Q6  Can we create a separate metadata database?
  Q8  What tables exist, how big are they, and which matter?

SAFETY
  Every statement below is a SELECT against catalog views, executed inside an
  explicit READ ONLY transaction. The one write attempt (`_probe_write_access`)
  is deliberate, harmless, and always rolled back -- its purpose is to PROVE
  whether the role can write, which is the single most important security fact
  about this deployment.

USAGE
  set -a; source .env; set +a          # or supply --dsn
  python scripts/probe_database.py
  python scripts/probe_database.py --json probe_report.json

Requires: pip install "psycopg[binary]"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - guidance path
    sys.exit(
        'Missing driver. Install it with:\n\n    pip install "psycopg[binary]"\n'
    )

# --------------------------------------------------------------------------
# Business-domain keywords from the specification (section 1). Used only to
# *suggest* which discovered tables are likely business-critical. Nothing is
# hardcoded -- if a keyword matches nothing, that is itself a useful finding.
# --------------------------------------------------------------------------
DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Sales": ("sales_order", "salesorder", "order", "quotation", "quote"),
    "Customers": ("customer", "contact", "lead", "client"),
    "Artwork": ("artwork", "art_work", "gang_sheet", "gangsheet", "design"),
    "Invoicing": ("invoice", "bill"),
    "Payments": ("payment", "transaction", "receipt", "refund"),
    "Fulfillment": ("shipment", "shipping", "tracking", "delivery", "fulfil"),
    "Production": ("production", "print", "job", "batch"),
    "Purchasing": ("purchase", "supplier", "vendor", "procurement"),
    "People": ("user", "employee", "staff", "account", "role", "permission"),
    "History": ("history", "log", "audit", "activity", "status"),
}

TIMESTAMP_HINTS = ("created_at", "updated_at", "created_on", "modified_at", "date_created")
AUTH_HINTS = ("password", "password_hash", "passwd", "hashed_password", "api_key", "token")


@dataclass
class ProbeReport:
    """Structured findings. Serialized to JSON; never contains credentials."""

    connection: dict[str, Any] = field(default_factory=dict)
    replica: dict[str, Any] = field(default_factory=dict)
    privileges: dict[str, Any] = field(default_factory=dict)
    metadata_db: dict[str, Any] = field(default_factory=dict)
    schema: dict[str, Any] = field(default_factory=dict)
    tables: list[dict[str, Any]] = field(default_factory=list)
    relationships: dict[str, Any] = field(default_factory=dict)
    conventions: dict[str, Any] = field(default_factory=dict)
    extensions: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "connection": self.connection,
            "replica": self.replica,
            "privileges": self.privileges,
            "metadata_db": self.metadata_db,
            "schema": self.schema,
            "tables": self.tables,
            "relationships": self.relationships,
            "conventions": self.conventions,
            "extensions": self.extensions,
            "warnings": self.warnings,
        }


def _q(cur, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cur.execute(sql, params)
    return cur.fetchall()


def _q1(cur, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    rows = _q(cur, sql, params)
    return rows[0] if rows else None


# --------------------------------------------------------------------------
# Q2 -- replica or primary
# --------------------------------------------------------------------------
def probe_replica(cur, report: ProbeReport) -> None:
    row = _q1(
        cur,
        """
        SELECT pg_is_in_recovery()                         AS in_recovery,
               current_setting('hot_standby', true)        AS hot_standby,
               current_setting('transaction_read_only')    AS session_read_only,
               current_setting('default_transaction_read_only') AS default_read_only
        """,
    )
    in_recovery = bool(row["in_recovery"])
    report.replica = {
        "is_read_replica": in_recovery,
        "hot_standby": row["hot_standby"],
        "session_read_only": row["session_read_only"],
        "default_transaction_read_only": row["default_read_only"],
        "verdict": (
            "READ REPLICA -- ideal. Reporting cannot affect production writes."
            if in_recovery
            else "PRIMARY -- reporting shares resources with live operations."
        ),
    }
    if not in_recovery:
        report.warnings.append(
            "Connected to the PRIMARY database. Reporting queries will compete with "
            "live operations for CPU and I/O. Ask whether a read replica exists; if "
            "not, keep the query governor limits conservative."
        )

    # Does a replica exist that we simply are not pointed at?
    try:
        standbys = _q(
            cur,
            "SELECT client_addr::text AS client_addr, state, sync_state "
            "FROM pg_stat_replication",
        )
        report.replica["known_standbys"] = standbys
        if standbys and not in_recovery:
            report.warnings.append(
                f"This primary has {len(standbys)} streaming standby(s). A read replica "
                "very likely EXISTS -- point the reporting connection at it instead."
            )
    except psycopg.Error:
        report.replica["known_standbys"] = "not visible to this role"


# --------------------------------------------------------------------------
# Q3 -- what can this role actually do?
# --------------------------------------------------------------------------
def _probe_write_access(conn) -> dict[str, Any]:
    """
    Deliberately attempt a harmless write, then ALWAYS roll back.

    This is the startup self-test described in ARCHITECTURE.md section D/L1.
    If this SUCCEEDS, the connection is NOT safe for a reporting tool.
    """
    result = {"attempted": "CREATE TEMP TABLE (rolled back)", "can_write": None}
    try:
        with conn.transaction() as tx:
            with conn.cursor() as cur:
                cur.execute("CREATE TEMP TABLE _bi_probe_rollback_me (x int)")
            result["can_write"] = True
            tx.rollback()  # never leave anything behind
    except psycopg.errors.ReadOnlySqlTransaction:
        result["can_write"] = False
        result["blocked_by"] = "read-only transaction (default_transaction_read_only)"
    except psycopg.errors.InsufficientPrivilege:
        result["can_write"] = False
        result["blocked_by"] = "insufficient privilege"
    except psycopg.Rollback:
        pass  # our own explicit rollback -- expected
    except psycopg.Error as exc:
        result["can_write"] = False
        result["blocked_by"] = type(exc).__name__
    return result


def probe_privileges(conn, cur, report: ProbeReport) -> None:
    role = _q1(
        cur,
        """
        SELECT r.rolname          AS role_name,
               r.rolsuper         AS is_superuser,
               r.rolcreatedb      AS can_create_db,
               r.rolcreaterole    AS can_create_role,
               r.rolbypassrls     AS bypasses_rls,
               r.rolconnlimit     AS connection_limit
        FROM pg_roles r
        WHERE r.rolname = current_user
        """,
    )

    # Sample a handful of real tables and ask Postgres directly what we may do.
    grants = _q(
        cur,
        """
        SELECT c.relname AS table_name,
               has_table_privilege(c.oid, 'SELECT') AS can_select,
               has_table_privilege(c.oid, 'INSERT') AS can_insert,
               has_table_privilege(c.oid, 'UPDATE') AS can_update,
               has_table_privilege(c.oid, 'DELETE') AS can_delete,
               has_table_privilege(c.oid, 'TRUNCATE') AS can_truncate
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r'
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY c.relname
        LIMIT 25
        """,
    )

    writable = [
        g["table_name"]
        for g in grants
        if g["can_insert"] or g["can_update"] or g["can_delete"] or g["can_truncate"]
    ]

    report.privileges = {
        "role": role,
        "sampled_tables": len(grants),
        "tables_this_role_can_modify": writable,
        "write_probe": _probe_write_access(conn),
    }

    if role and role["is_superuser"]:
        report.warnings.append(
            "CRITICAL: this role is a SUPERUSER. It can drop the entire database. "
            "Do not ship the application with these credentials -- create a "
            "dedicated bi_readonly role (see ARCHITECTURE.md section D/L1)."
        )
    if writable:
        report.warnings.append(
            f"CRITICAL: this role can modify {len(writable)} of the {len(grants)} sampled "
            "tables. The read-only guarantee currently rests on application code alone. "
            "Create a SELECT-only role before connecting this tool to production."
        )
    if report.privileges["write_probe"].get("can_write"):
        report.warnings.append(
            "The write probe SUCCEEDED (and was rolled back). With these credentials the "
            "application's startup self-test will refuse to boot, by design."
        )


# --------------------------------------------------------------------------
# Q6 -- can we host the application metadata database?
# --------------------------------------------------------------------------
def probe_metadata_db_option(cur, report: ProbeReport) -> None:
    role = _q1(cur, "SELECT rolcreatedb, rolsuper FROM pg_roles WHERE rolname = current_user")
    databases = _q(
        cur,
        """
        SELECT datname AS name,
               pg_size_pretty(pg_database_size(datname)) AS size
        FROM pg_database
        WHERE datistemplate = false
        ORDER BY pg_database_size(datname) DESC
        """,
    )
    version = _q1(cur, "SELECT current_setting('server_version') AS v")

    can_create = bool(role and (role["rolcreatedb"] or role["rolsuper"]))
    report.metadata_db = {
        "server_version": version["v"] if version else None,
        "existing_databases": databases,
        "current_role_can_create_database": can_create,
        "recommendation": (
            "Run the metadata database as a SEPARATE PostgreSQL container via Docker "
            "Compose. It keeps application data fully outside the operational server "
            "(spec section 40) and needs no privilege on the production instance."
        ),
    }


# --------------------------------------------------------------------------
# Q8 -- what is actually in there?
# --------------------------------------------------------------------------
def categorize(table_name: str) -> str:
    lowered = table_name.lower()
    for category, keywords in DOMAIN_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return category
    return "Uncategorized"


def probe_schema(cur, report: ProbeReport) -> None:
    schemas = _q(
        cur,
        """
        SELECT n.nspname AS schema_name, count(c.oid) AS table_count
        FROM pg_namespace n
        LEFT JOIN pg_class c ON c.relnamespace = n.oid AND c.relkind IN ('r', 'p')
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname NOT LIKE 'pg_%'
        GROUP BY n.nspname
        ORDER BY table_count DESC
        """,
    )

    # Estimated row counts only -- never COUNT(*) on production (spec section 41).
    tables = _q(
        cur,
        """
        SELECT n.nspname                                   AS schema_name,
               c.relname                                   AS table_name,
               c.relkind                                   AS kind,
               GREATEST(c.reltuples, 0)::bigint            AS estimated_rows,
               pg_total_relation_size(c.oid)               AS size_bytes,
               pg_size_pretty(pg_total_relation_size(c.oid)) AS size_pretty,
               (SELECT count(*) FROM pg_attribute a
                 WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped)
                                                           AS column_count,
               EXISTS (SELECT 1 FROM pg_constraint pk
                        WHERE pk.conrelid = c.oid AND pk.contype = 'p')
                                                           AS has_primary_key,
               obj_description(c.oid, 'pg_class')          AS description
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p', 'v', 'm')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname NOT LIKE 'pg_%'
        ORDER BY pg_total_relation_size(c.oid) DESC
        """,
    )

    for table in tables:
        table["category"] = categorize(table["table_name"])
        table["kind"] = {"r": "table", "p": "partitioned", "v": "view", "m": "matview"}.get(
            table["kind"], table["kind"]
        )

    report.schema = {
        "schemas": schemas,
        "total_objects": len(tables),
        "tables_without_primary_key": [
            t["table_name"] for t in tables if t["kind"] == "table" and not t["has_primary_key"]
        ],
        "category_counts": {
            category: sum(1 for t in tables if t["category"] == category)
            for category in sorted({t["category"] for t in tables})
        },
    }
    report.tables = tables

    if report.schema["tables_without_primary_key"]:
        report.warnings.append(
            f"{len(report.schema['tables_without_primary_key'])} table(s) have no primary key. "
            "Record 360 and reconciliation need a stable row identity -- these tables will "
            "need a logical key defined in the metadata layer."
        )


def probe_relationships(cur, report: ProbeReport) -> None:
    fks = _q(
        cur,
        """
        SELECT con.conname                        AS constraint_name,
               src_ns.nspname || '.' || src.relname  AS source_table,
               tgt_ns.nspname || '.' || tgt.relname  AS target_table,
               pg_get_constraintdef(con.oid)      AS definition
        FROM pg_constraint con
        JOIN pg_class src        ON src.oid = con.conrelid
        JOIN pg_namespace src_ns ON src_ns.oid = src.relnamespace
        JOIN pg_class tgt        ON tgt.oid = con.confrelid
        JOIN pg_namespace tgt_ns ON tgt_ns.oid = tgt.relnamespace
        WHERE con.contype = 'f'
        ORDER BY source_table
        """,
    )

    base_tables = sum(1 for t in report.tables if t["kind"] in ("table", "partitioned"))
    connected: set[str] = set()
    for fk in fks:
        connected.add(fk["source_table"].split(".", 1)[1])
        connected.add(fk["target_table"].split(".", 1)[1])

    orphan_tables = [
        t["table_name"]
        for t in report.tables
        if t["kind"] in ("table", "partitioned") and t["table_name"] not in connected
    ]

    report.relationships = {
        "physical_foreign_keys": len(fks),
        "tables_in_fk_graph": len(connected),
        "base_tables": base_tables,
        "tables_with_no_foreign_key": orphan_tables,
        "foreign_keys": fks,
    }

    if base_tables and len(fks) < base_tables * 0.5:
        report.warnings.append(
            f"Only {len(fks)} foreign keys for {base_tables} tables. Relationships are largely "
            "implicit, so the join planner will depend on LOGICAL relationships defined by an "
            "admin (spec section 1). Expect meaningful setup work in Phase 3."
        )


def probe_conventions(cur, report: ProbeReport) -> None:
    """Check the assumptions recorded in ARCHITECTURE.md section L."""
    columns = _q(
        cur,
        """
        SELECT c.relname AS table_name, a.attname AS column_name
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE a.attnum > 0 AND NOT a.attisdropped
          AND c.relkind IN ('r', 'p')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        """,
    )

    by_table: dict[str, set[str]] = {}
    for col in columns:
        by_table.setdefault(col["table_name"], set()).add(col["column_name"].lower())

    with_timestamps = [t for t, cols in by_table.items() if any(h in cols for h in TIMESTAMP_HINTS)]
    auth_tables = sorted(
        {t for t, cols in by_table.items() if any(h in cols for h in AUTH_HINTS)}
    )

    # Composite primary keys break assumption A2.
    composite_pks = _q(
        cur,
        """
        SELECT c.relname AS table_name, array_length(con.conkey, 1) AS key_columns
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE con.contype = 'p'
          AND array_length(con.conkey, 1) > 1
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        """,
    )

    report.conventions = {
        "A3_tables_with_audit_timestamps": len(with_timestamps),
        "A3_total_tables": len(by_table),
        "A3_verdict": (
            "OK -- incremental anomaly scans and staleness rules are viable."
            if len(with_timestamps) >= len(by_table) * 0.6
            else "WEAK -- many tables lack created_at/updated_at; staleness rules and "
            "incremental scans will need status-history tables instead."
        ),
        "A2_composite_primary_keys": composite_pks,
        "Q4_possible_existing_auth_tables": auth_tables,
        "Q4_verdict": (
            f"Credential-like columns found in: {', '.join(auth_tables)}. There may already be "
            "an application user store -- review before building a separate login."
            if auth_tables
            else "No credential columns found. We own authentication (assumption A5 holds)."
        ),
    }


def probe_extensions(cur, report: ProbeReport) -> None:
    installed = _q(cur, "SELECT extname AS name, extversion AS version FROM pg_extension")
    available = _q(
        cur,
        "SELECT name FROM pg_available_extensions "
        "WHERE name IN ('pg_trgm', 'pg_stat_statements', 'fuzzystrmatch', 'unaccent')",
    )
    report.extensions = {
        "installed": installed,
        "useful_and_available": [row["name"] for row in available],
        "note": "pg_trgm is used for fuzzy duplicate detection. We install it in the "
        "METADATA database, not this one -- nothing is added to production.",
    }


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def print_summary(report: ProbeReport) -> None:
    def header(text: str) -> None:
        print(f"\n{'=' * 74}\n  {text}\n{'=' * 74}")

    header("CONNECTION")
    for key, value in report.connection.items():
        print(f"  {key:.<28} {value}")

    header("Q2  READ REPLICA?")
    print(f"  {report.replica['verdict']}")
    print(f"  default_transaction_read_only ... {report.replica['default_transaction_read_only']}")
    standbys = report.replica.get("known_standbys")
    if isinstance(standbys, list) and standbys:
        print(f"  streaming standbys .............. {len(standbys)} (a replica exists!)")

    header("Q3  PRIVILEGES OF THIS ROLE")
    role = report.privileges.get("role") or {}
    print(f"  role ............................ {role.get('role_name')}")
    print(f"  superuser ....................... {role.get('is_superuser')}")
    print(f"  can create databases ............ {role.get('can_create_db')}")
    probe = report.privileges.get("write_probe", {})
    verdict = {True: "YES -- NOT SAFE", False: "no -- good", None: "inconclusive"}[
        probe.get("can_write")
    ]
    print(f"  write probe (rolled back) ....... {verdict}")
    modifiable = report.privileges.get("tables_this_role_can_modify", [])
    print(f"  sampled tables it can modify .... {len(modifiable)}")

    header("Q6  APPLICATION METADATA DATABASE")
    print(f"  PostgreSQL version .............. {report.metadata_db.get('server_version')}")
    print(f"  role can CREATE DATABASE ........ {report.metadata_db.get('current_role_can_create_database')}")
    print(f"  existing databases .............. "
          f"{', '.join(d['name'] for d in report.metadata_db.get('existing_databases', []))}")
    print(f"  -> {report.metadata_db.get('recommendation')}")

    header("Q8  SCHEMA INVENTORY")
    print(f"  schemas ......................... "
          f"{', '.join(f'{s['schema_name']}({s['table_count']})' for s in report.schema['schemas'])}")
    print(f"  total objects ................... {report.schema['total_objects']}")
    print(f"  physical foreign keys ........... {report.relationships['physical_foreign_keys']}")
    print(f"  tables with no FK at all ........ {len(report.relationships['tables_with_no_foreign_key'])}")
    print(f"  tables without a primary key .... {len(report.schema['tables_without_primary_key'])}")
    print("\n  By business category:")
    for category, count in sorted(report.schema["category_counts"].items(), key=lambda kv: -kv[1]):
        print(f"    {category:.<26} {count}")

    print("\n  Largest 20 objects (estimated rows -- no COUNT(*) was run):")
    print(f"    {'TABLE':<38} {'CATEGORY':<14} {'ROWS':>12}  {'SIZE':>10}")
    for table in report.tables[:20]:
        print(
            f"    {table['table_name'][:37]:<38} {table['category']:<14} "
            f"{table['estimated_rows']:>12,}  {table['size_pretty']:>10}"
        )

    header("SCHEMA CONVENTIONS (validates architecture assumptions)")
    conventions = report.conventions
    print(f"  audit timestamps ................ "
          f"{conventions['A3_tables_with_audit_timestamps']}/{conventions['A3_total_tables']} tables")
    print(f"  -> {conventions['A3_verdict']}")
    print(f"  composite primary keys .......... {len(conventions['A2_composite_primary_keys'])}")
    print(f"  -> {conventions['Q4_verdict']}")

    if report.warnings:
        header(f"WARNINGS ({len(report.warnings)})")
        for index, warning in enumerate(report.warnings, 1):
            print(f"  {index}. {warning}\n")
    else:
        print("\n  No warnings. This connection looks appropriate for read-only reporting.")


def build_dsn(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    """Build a DSN from --dsn, DATABASE_URL, or discrete env vars. Never logs the password."""
    dsn = args.dsn or os.getenv("DATABASE_URL")
    if dsn:
        safe = re.sub(r"://([^:]+):[^@]*@", r"://\1:***@", dsn)
        return dsn, {"source": "DATABASE_URL / --dsn", "dsn": safe}

    host = args.host or os.getenv("DATABASE_HOST", "localhost")
    port = args.port or os.getenv("DATABASE_PORT", "5432")
    name = args.database or os.getenv("DATABASE_NAME")
    user = args.user or os.getenv("DATABASE_USER")
    password = os.getenv("DATABASE_PASSWORD", "")
    sslmode = os.getenv("DATABASE_SSL", "prefer")

    if not name or not user:
        sys.exit(
            "Missing connection details.\n\n"
            "Set them in .env (copy .env.example) or pass flags:\n"
            "  python scripts/probe_database.py --host HOST --database decoinks --user USER\n"
            "  (password is read from DATABASE_PASSWORD, never from the command line)\n"
        )

    dsn = f"postgresql://{user}:{password}@{host}:{port}/{name}?sslmode={sslmode}"
    return dsn, {
        "source": "discrete env vars",
        "host": host,
        "port": port,
        "database": name,
        "user": user,
        "sslmode": sslmode,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only probe of the operational database.")
    parser.add_argument("--dsn", help="Full connection string (overrides discrete vars)")
    parser.add_argument("--host")
    parser.add_argument("--port")
    parser.add_argument("--database")
    parser.add_argument("--user")
    parser.add_argument("--json", metavar="PATH", help="Also write the full report as JSON")
    args = parser.parse_args()

    dsn, connection_info = build_dsn(args)
    report = ProbeReport(connection=connection_info)

    print("Connecting (read-only)...")
    try:
        with psycopg.connect(dsn, connect_timeout=10, application_name="bi-probe") as conn:
            conn.read_only = True  # belt: every transaction on this session is READ ONLY
            with conn.cursor(row_factory=dict_row) as cur:
                probe_replica(cur, report)
                probe_metadata_db_option(cur, report)
                probe_schema(cur, report)
                probe_relationships(cur, report)
                probe_conventions(cur, report)
                probe_extensions(cur, report)
                probe_privileges(conn, cur, report)
    except psycopg.OperationalError as exc:
        # Never echo the DSN -- it carries the password.
        print(f"\nCould not connect: {str(exc).strip().splitlines()[0]}", file=sys.stderr)
        print(
            "\nCheck: host reachable, port open, database name, user, password, and that "
            "pg_hba.conf permits this client address.",
            file=sys.stderr,
        )
        return 2

    print_summary(report)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report.as_dict(), handle, indent=2, default=str)
        print(f"\nFull report written to {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

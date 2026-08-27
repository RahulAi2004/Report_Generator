"""
Schema metadata registry.

The in-memory, RBAC-filterable view of a connected database's structure. Every
identifier the report engine is allowed to emit must come from here -- this is
the allowlist that makes SQL injection unrepresentable rather than merely
filtered (ARCHITECTURE.md, section D/L3).

The registry is populated by the introspector from a schema snapshot stored in
the metadata database. It is never built from user input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable


class DataType(StrEnum):
    """Normalized types. Physical dialect types map onto these."""

    TEXT = "text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"
    UUID = "uuid"
    JSON = "json"
    BINARY = "binary"
    UNKNOWN = "unknown"

    @property
    def is_numeric(self) -> bool:
        return self in (DataType.INTEGER, DataType.DECIMAL)

    @property
    def is_temporal(self) -> bool:
        return self in (DataType.DATE, DataType.DATETIME, DataType.TIME)


class Aggregation(StrEnum):
    NONE = "none"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


#: Which aggregations are legal for which normalized type (spec 8: "Do not
#: expose invalid aggregation choices for incompatible data types").
_ALWAYS = (Aggregation.NONE, Aggregation.COUNT, Aggregation.COUNT_DISTINCT)
_ORDERABLE = _ALWAYS + (Aggregation.MIN, Aggregation.MAX)
_SUMMABLE = _ORDERABLE + (Aggregation.SUM, Aggregation.AVG)

LEGAL_AGGREGATIONS: dict[DataType, tuple[Aggregation, ...]] = {
    DataType.TEXT: _ORDERABLE,
    DataType.INTEGER: _SUMMABLE,
    DataType.DECIMAL: _SUMMABLE,
    DataType.BOOLEAN: _ALWAYS,
    DataType.DATE: _ORDERABLE,
    DataType.DATETIME: _ORDERABLE,
    DataType.TIME: _ORDERABLE,
    DataType.UUID: _ALWAYS,
    DataType.JSON: _ALWAYS,
    DataType.BINARY: _ALWAYS,
    DataType.UNKNOWN: _ALWAYS,
}


class Cardinality(StrEnum):
    ONE_TO_ONE = "1:1"
    ONE_TO_MANY = "1:N"
    MANY_TO_ONE = "N:1"
    MANY_TO_MANY = "N:M"


class JoinType(StrEnum):
    INNER = "inner"
    LEFT = "left"
    RIGHT = "right"
    FULL = "full"


class RelationshipSource(StrEnum):
    """Where a relationship came from -- drives join-planner edge cost."""

    PHYSICAL = "physical"   # a real foreign key
    MANUAL = "manual"       # an admin defined it in our metadata database
    INFERRED = "inferred"   # we suggested it; an admin accepted it


class MaskPolicy(StrEnum):
    NONE = "none"
    REDACT = "redact"      # 'REDACTED'
    PARTIAL = "partial"    # 'jo***@***.com'
    HASH = "hash"
    NULL = "null"


#: Columns no report may return, whatever anyone configures.
#:
#: Everything else about what a report may read is a policy decision left to an
#: administrator -- which columns are sensitive, who may see them, whether they
#: are masked. These are not. A password hash, a session token or an API key has
#: no reporting use at all, and the cost of one appearing in an exported
#: spreadsheet is not recoverable. So they are excluded in code, before any
#: configuration is consulted, and there is no setting that turns them back on.
#:
#: Matched on the column name against the whole name or a word inside it, so
#: `password`, `password_hash` and `reset_password_token` are all caught while
#: `password_changed_at` -- a date, and legitimately reportable -- is not.
CREDENTIAL_COLUMNS: frozenset[str] = frozenset({
    "password", "passwd", "pwd", "password_hash", "password_digest",
    "hashed_password", "encrypted_password", "salt", "password_salt",
    "token", "token_hash", "access_token", "refresh_token", "refresh_token_hash",
    "session_token", "api_key", "api_secret", "apikey", "secret", "secret_key",
    "private_key", "client_secret", "otp", "otp_hash", "two_factor_secret",
    "totp_secret", "mfa_secret", "recovery_code", "reset_token",
    "verification_token", "signing_key", "webhook_secret",
})


def is_credential(column_name: str) -> bool:
    """Whether a column holds a credential and must never reach a report."""
    return column_name.strip().lower() in CREDENTIAL_COLUMNS


@dataclass(frozen=True, slots=True)
class ColumnMeta:
    table: str
    name: str
    data_type: DataType
    physical_type: str
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    #: A database ENUM. Reflection reports these as VARCHAR, but PostgreSQL has
    #: no `enum = varchar` operator, so comparing one to a literal fails unless
    #: the column is cast first.
    is_enum: bool = False
    ordinal: int = 0
    display_name: str | None = None
    description: str | None = None
    is_sensitive: bool = False
    mask_policy: MaskPolicy = MaskPolicy.NONE
    enabled_for_reporting: bool = True
    default_format: dict | None = None

    @property
    def label(self) -> str:
        return self.display_name or humanize(self.name)

    @property
    def qualified(self) -> str:
        return f"{self.table}.{self.name}"

    @property
    def legal_aggregations(self) -> tuple[Aggregation, ...]:
        return LEGAL_AGGREGATIONS[self.data_type]


@dataclass(frozen=True, slots=True)
class TableMeta:
    #: How this table is addressed everywhere above the adapter: unique across
    #: the registry. When one name exists in several schemas -- `messages` in
    #: app, public and technocas, say -- this becomes "schema.name" so a report
    #: cannot silently refer to the wrong one.
    name: str
    schema: str = "public"
    #: The real table name in the database. Differs from `name` only when the
    #: name had to be qualified to stay unique.
    physical_name: str | None = None
    kind: str = "table"
    display_name: str | None = None
    category: str = "Uncategorized"
    description: str | None = None
    #: None when the engine has no cheap estimate -- views have no planner
    #: statistics, and reporting "0 rows" for a populated view is worse than
    #: admitting we do not know.
    estimated_rows: int | None = None
    columns: tuple[ColumnMeta, ...] = ()
    enabled_for_reporting: bool = True
    enabled_for_ai: bool = True
    is_sensitive: bool = False

    @property
    def real_name(self) -> str:
        """The identifier to emit in SQL."""
        return self.physical_name or self.name

    @property
    def label(self) -> str:
        return self.display_name or humanize(self.real_name)

    @property
    def primary_key(self) -> tuple[ColumnMeta, ...]:
        return tuple(column for column in self.columns if column.is_primary_key)

    def column(self, name: str) -> ColumnMeta | None:
        lowered = name.lower()
        return next((c for c in self.columns if c.name.lower() == lowered), None)


@dataclass(frozen=True, slots=True)
class RelationshipMeta:
    """A join edge. Directed left(parent/one) -> right(child/many) by convention."""

    id: str
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    cardinality: Cardinality = Cardinality.ONE_TO_MANY
    default_join_type: JoinType = JoinType.LEFT
    source: RelationshipSource = RelationshipSource.PHYSICAL
    confidence: float = 1.0
    #: Which schema each side came from. Needed once several schemas are
    #: exposed: a relationship stored as "customers" is ambiguous the moment
    #: two schemas both have one.
    left_schema: str | None = None
    right_schema: str | None = None

    @property
    def cost(self) -> float:
        """Join-planner edge weight. Real foreign keys are preferred over guesses."""
        base = {
            RelationshipSource.PHYSICAL: 1.0,
            RelationshipSource.MANUAL: 1.5,
            RelationshipSource.INFERRED: 3.0,
        }[self.source]
        return base / max(self.confidence, 0.1)

    def connects(self, table_a: str, table_b: str) -> bool:
        return {self.left_table, self.right_table} == {table_a, table_b}

    def other_side(self, table: str) -> str:
        return self.right_table if table == self.left_table else self.left_table

    def columns_for(self, from_table: str) -> tuple[str, str]:
        """Return (column on from_table, column on the other table)."""
        if from_table == self.left_table:
            return self.left_column, self.right_column
        return self.right_column, self.left_column

    def cardinality_from(self, from_table: str) -> Cardinality:
        """Cardinality as seen when traversing outward from ``from_table``."""
        if from_table == self.left_table:
            return self.cardinality
        return {
            Cardinality.ONE_TO_MANY: Cardinality.MANY_TO_ONE,
            Cardinality.MANY_TO_ONE: Cardinality.ONE_TO_MANY,
            Cardinality.ONE_TO_ONE: Cardinality.ONE_TO_ONE,
            Cardinality.MANY_TO_MANY: Cardinality.MANY_TO_MANY,
        }[self.cardinality]


class SchemaRegistry:
    """
    The allowlist. Resolution failures here are hard errors, never warnings.

    An instance is scoped to one connection and, once RBAC-filtered via
    :meth:`for_principal`, to one user -- so downstream stages physically cannot
    reference a table or column the user may not see.
    """

    def __init__(
        self,
        tables: Iterable[TableMeta],
        relationships: Iterable[RelationshipMeta] = (),
        connection_id: str | None = None,
    ) -> None:
        self.connection_id = connection_id
        self._tables: dict[str, TableMeta] = {
            t.name.lower(): t for t in _disambiguate(tables)
        }
        #: Physical name -> the table holding it, first schema wins. This is
        #: what lets a definition saved before a collision existed keep working.
        self._by_real_name: dict[str, TableMeta] = {}
        for table in self._tables.values():
            self._by_real_name.setdefault(table.real_name.lower(), table)

        self._relationships: list[RelationshipMeta] = _resolve_relationships(
            relationships, self._tables
        )
        self._by_table: dict[str, list[RelationshipMeta]] = {}
        for relationship in self._relationships:
            self._by_table.setdefault(relationship.left_table.lower(), []).append(relationship)
            self._by_table.setdefault(relationship.right_table.lower(), []).append(relationship)

    # -- lookups -----------------------------------------------------------
    @property
    def tables(self) -> list[TableMeta]:
        return list(self._tables.values())

    @property
    def relationships(self) -> list[RelationshipMeta]:
        return list(self._relationships)

    def table(self, name: str) -> TableMeta | None:
        found = self._tables.get(name.lower())
        if found is not None:
            return found
        # A name saved before another schema was exposed. Qualification only
        # happens on collision, so a bare name that no longer resolves is one
        # whose table has since acquired a namesake -- not one that has gone.
        # Reports and dashboards written back then still mean the schema that
        # was configured then, which is the first one listed.
        return self._by_real_name.get(_strip_schema(name).lower())

    def column(self, table: str, column: str) -> ColumnMeta | None:
        found = self.table(table)
        return found.column(column) if found else None

    def has(self, table: str, column: str | None = None) -> bool:
        if column is None:
            return self.table(table) is not None
        return self.column(table, column) is not None

    def edges_for(self, table: str) -> list[RelationshipMeta]:
        return self._by_table.get(table.lower(), [])

    def edges_between(self, table_a: str, table_b: str) -> list[RelationshipMeta]:
        return [r for r in self.edges_for(table_a) if r.connects(table_a, table_b)]

    def categories(self) -> dict[str, list[TableMeta]]:
        grouped: dict[str, list[TableMeta]] = {}
        for table in self._tables.values():
            grouped.setdefault(table.category, []).append(table)
        for tables in grouped.values():
            tables.sort(key=lambda t: t.label)
        return dict(sorted(grouped.items()))

    # -- RBAC --------------------------------------------------------------
    def for_principal(
        self,
        allowed_tables: set[str] | None,
        denied_columns: dict[str, set[str]] | None = None,
        mask_policies: dict[str, MaskPolicy] | None = None,
    ) -> "SchemaRegistry":
        """
        Return a narrowed registry containing only what this principal may see.

        ``allowed_tables=None`` means "no table restriction" (a super admin).
        Denied columns are removed outright; masked columns are retained with a
        policy the compiler applies in the projection.
        """
        denied_columns = {k.lower(): {c.lower() for c in v} for k, v in (denied_columns or {}).items()}
        mask_policies = mask_policies or {}
        allowed = {t.lower() for t in allowed_tables} if allowed_tables is not None else None

        tables: list[TableMeta] = []
        for table in self._tables.values():
            if not table.enabled_for_reporting:
                continue
            if allowed is not None and table.name.lower() not in allowed:
                continue

            blocked = denied_columns.get(table.name.lower(), set())
            columns = []
            for column in table.columns:
                # Checked first, and not overridable: see CREDENTIAL_COLUMNS.
                if is_credential(column.name):
                    continue
                if column.name.lower() in blocked or not column.enabled_for_reporting:
                    continue
                policy = mask_policies.get(column.qualified.lower())
                columns.append(
                    ColumnMeta(**{**_as_dict(column), "mask_policy": policy})
                    if policy
                    else column
                )
            tables.append(TableMeta(**{**_as_dict(table), "columns": tuple(columns)}))

        visible = {t.name.lower() for t in tables}
        relationships = [
            r
            for r in self._relationships
            if r.left_table.lower() in visible and r.right_table.lower() in visible
        ]
        return SchemaRegistry(tables, relationships, self.connection_id)


def _strip_schema(name: str) -> str:
    """The bare table name, whether or not it arrived qualified."""
    return name.rsplit(".", 1)[-1]


def _disambiguate(tables: Iterable[TableMeta]) -> list[TableMeta]:
    """
    Give every table a registry-unique name.

    Exposing several schemas at once means the same name can appear more than
    once. Rather than dropping the duplicates -- which would quietly hide a
    table someone needs -- the colliding ones are qualified with their schema,
    and their columns are re-pointed at the new name so resolution still works.
    """
    tables = list(tables)
    seen: dict[str, int] = {}
    for table in tables:
        seen[table.name.lower()] = seen.get(table.name.lower(), 0) + 1

    out: list[TableMeta] = []
    for table in tables:
        if seen[table.name.lower()] == 1:
            out.append(
                table if table.physical_name else
                TableMeta(**{**_as_dict(table), "physical_name": table.name})
            )
            continue

        qualified = f"{table.schema}.{table.name}"
        columns = tuple(
            ColumnMeta(**{**_as_dict(column), "table": qualified}) for column in table.columns
        )
        out.append(
            TableMeta(
                **{
                    **_as_dict(table),
                    "name": qualified,
                    "physical_name": table.physical_name or table.name,
                    "columns": columns,
                }
            )
        )
    return out


def _resolve_relationships(
    relationships: Iterable[RelationshipMeta], tables: dict[str, TableMeta]
) -> list[RelationshipMeta]:
    """
    Point every relationship at the names the registry actually uses.

    Relationships are stored by table name, but a name gets qualified the moment
    two schemas both contain it -- so a relationship saved before that would
    silently stop matching, and every report using it would report that the
    tables are unconnected.

    Where a side names its schema, that wins. Where it does not and the bare
    name is ambiguous, the first schema holding it is used, which is the one
    that was in effect when the relationship was created.
    """
    by_real: dict[str, list[str]] = {}
    for key, table in tables.items():
        by_real.setdefault(table.real_name.lower(), []).append(key)

    def resolve(name: str, schema: str | None) -> str | None:
        lowered = name.lower()
        if lowered in tables:
            return lowered
        if schema:
            qualified = f"{schema}.{name}".lower()
            if qualified in tables:
                return qualified
        candidates = by_real.get(lowered)
        return candidates[0] if candidates else None

    out: list[RelationshipMeta] = []
    for relationship in relationships:
        left = resolve(relationship.left_table, relationship.left_schema)
        right = resolve(relationship.right_table, relationship.right_schema)
        if left is None or right is None or left == right:
            continue
        if left == relationship.left_table.lower() and right == relationship.right_table.lower():
            out.append(relationship)
            continue
        out.append(
            RelationshipMeta(
                **{
                    **{slot: getattr(relationship, slot) for slot in relationship.__slots__},
                    "left_table": tables[left].name,
                    "right_table": tables[right].name,
                }
            )
        )
    return out


def _as_dict(obj) -> dict:
    """dataclasses.asdict recurses into nested dataclasses; we want a shallow copy."""
    return {slot: getattr(obj, slot) for slot in obj.__slots__}


# ---------------------------------------------------------------------------
# Naming helpers -- physical names become readable labels without touching the
# production database (spec 36).
# ---------------------------------------------------------------------------
_ACRONYMS = {
    "id": "ID", "no": "No.", "qty": "Qty", "url": "URL", "sku": "SKU",
    "po": "PO", "so": "SO", "vat": "VAT", "tax": "Tax", "pdf": "PDF",
}


def humanize(identifier: str) -> str:
    """``sales_order_items`` -> ``Sales Order Items``; ``order_no`` -> ``Order No.``"""
    words = [w for w in identifier.replace("-", "_").split("_") if w]
    return " ".join(_ACRONYMS.get(w.lower(), w.capitalize()) for w in words) or identifier


#: Physical PostgreSQL types -> normalized types.
POSTGRES_TYPE_MAP: dict[str, DataType] = {
    "smallint": DataType.INTEGER, "integer": DataType.INTEGER, "bigint": DataType.INTEGER,
    "smallserial": DataType.INTEGER, "serial": DataType.INTEGER, "bigserial": DataType.INTEGER,
    "numeric": DataType.DECIMAL, "decimal": DataType.DECIMAL, "real": DataType.DECIMAL,
    "double precision": DataType.DECIMAL, "money": DataType.DECIMAL,
    "character varying": DataType.TEXT, "varchar": DataType.TEXT, "character": DataType.TEXT,
    "char": DataType.TEXT, "text": DataType.TEXT, "citext": DataType.TEXT, "name": DataType.TEXT,
    "boolean": DataType.BOOLEAN,
    "date": DataType.DATE,
    "timestamp without time zone": DataType.DATETIME,
    "timestamp with time zone": DataType.DATETIME,
    "timestamp": DataType.DATETIME,
    "time without time zone": DataType.TIME, "time with time zone": DataType.TIME,
    "uuid": DataType.UUID,
    "json": DataType.JSON, "jsonb": DataType.JSON,
    "bytea": DataType.BINARY,
}


#: Aliases seen across dialects and from SQLAlchemy's own type reprs
#: (``VARCHAR(160)``, ``NUMERIC(12, 2)``, ``TINYINT``, ...).
_TYPE_ALIASES: dict[str, DataType] = {
    "int": DataType.INTEGER, "int2": DataType.INTEGER, "int4": DataType.INTEGER,
    "int8": DataType.INTEGER, "tinyint": DataType.INTEGER, "mediumint": DataType.INTEGER,
    "year": DataType.INTEGER,
    "float": DataType.DECIMAL, "float4": DataType.DECIMAL, "float8": DataType.DECIMAL,
    "double": DataType.DECIMAL, "number": DataType.DECIMAL, "smallmoney": DataType.DECIMAL,
    "nvarchar": DataType.TEXT, "nchar": DataType.TEXT, "ntext": DataType.TEXT,
    "string": DataType.TEXT, "clob": DataType.TEXT, "longtext": DataType.TEXT,
    "mediumtext": DataType.TEXT, "tinytext": DataType.TEXT, "enum": DataType.TEXT,
    "bool": DataType.BOOLEAN, "bit": DataType.BOOLEAN,
    "datetime2": DataType.DATETIME, "smalldatetime": DataType.DATETIME,
    "timestamptz": DataType.DATETIME, "datetimeoffset": DataType.DATETIME,
    "timetz": DataType.TIME,
    "uniqueidentifier": DataType.UUID,
    "blob": DataType.BINARY, "varbinary": DataType.BINARY, "binary": DataType.BINARY,
    "image": DataType.BINARY,
}


def normalize_type(physical_type: str) -> DataType:
    """
    Map a dialect type name onto our normalized set.

    Handles the parameterized spellings that come back from introspection --
    ``VARCHAR(160)``, ``NUMERIC(12, 2)``, ``TIMESTAMP WITHOUT TIME ZONE`` -- so
    the same column classifies identically whichever engine reports it.
    """
    text = physical_type.strip().lower()
    if not text:
        return DataType.UNKNOWN

    if (exact := POSTGRES_TYPE_MAP.get(text)) is not None:
        return exact

    base = text.split("(", 1)[0].strip()
    if (found := POSTGRES_TYPE_MAP.get(base) or _TYPE_ALIASES.get(base)) is not None:
        return found

    # Multi-word spellings such as "double precision(8)" or vendor suffixes.
    head = base.split()[0] if base.split() else base
    if (found := POSTGRES_TYPE_MAP.get(head) or _TYPE_ALIASES.get(head)) is not None:
        return found

    for keyword, data_type in (
        ("timestamp", DataType.DATETIME), ("datetime", DataType.DATETIME),
        ("date", DataType.DATE), ("time", DataType.TIME),
        ("char", DataType.TEXT), ("text", DataType.TEXT),
        ("int", DataType.INTEGER), ("serial", DataType.INTEGER),
        ("numeric", DataType.DECIMAL), ("decimal", DataType.DECIMAL),
        ("real", DataType.DECIMAL), ("double", DataType.DECIMAL),
        ("bool", DataType.BOOLEAN), ("uuid", DataType.UUID),
        ("json", DataType.JSON), ("binary", DataType.BINARY), ("blob", DataType.BINARY),
    ):
        if keyword in base:
            return data_type

    return DataType.UNKNOWN

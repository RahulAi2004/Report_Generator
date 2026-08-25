"""
Report diagnostics.

Surfaced in the builder's diagnostics bar. Every message is written for a
business user: it says what is wrong, which part of the report it concerns, and
where possible offers a machine-applicable fix the UI can apply in one click.

Errors block execution. Warnings do not -- but a warning that numbers may be
inflated is the single most valuable thing this tool tells a manager, so they
are rendered prominently rather than tucked away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Code(StrEnum):
    # Resolution
    UNKNOWN_TABLE = "unknown_table"
    UNKNOWN_COLUMN = "unknown_column"
    TABLE_NOT_PERMITTED = "table_not_permitted"
    COLUMN_NOT_PERMITTED = "column_not_permitted"
    NO_COLUMNS = "no_columns"
    NO_VISIBLE_COLUMNS = "no_visible_columns"
    MASKED_AGGREGATION = "masked_aggregation"

    # Join planning
    NO_JOIN_PATH = "no_join_path"
    AMBIGUOUS_JOIN_PATH = "ambiguous_join_path"
    CIRCULAR_JOIN = "circular_join"
    TOO_MANY_JOINS = "too_many_joins"
    UNRELATED_TABLE = "unrelated_table"
    INFERRED_RELATIONSHIP = "inferred_relationship"

    # Correctness
    FANOUT_INFLATION = "fanout_inflation"
    FANOUT_CORRECTED = "fanout_corrected"
    CARTESIAN_RISK = "cartesian_risk"

    # Semantics
    INVALID_AGGREGATION = "invalid_aggregation"
    MISSING_GROUP_BY = "missing_group_by"
    SORT_NOT_IN_PROJECTION = "sort_not_in_projection"
    DUPLICATE_COLUMN = "duplicate_column"
    UNKNOWN_OPERATOR = "unknown_operator"
    OPERATOR_TYPE_MISMATCH = "operator_type_mismatch"
    MISSING_PARAMETER = "missing_parameter"

    # Governance
    ROW_LIMIT_CLAMPED = "row_limit_clamped"
    INTERNAL_SAFETY_FAILURE = "internal_safety_failure"
    MASKED_COLUMN = "masked_column"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: Code
    severity: Severity
    message: str
    #: Which part of the report this concerns: "columns", "joins", "filters",
    #: "group_by", "sort_by", "tables". Lets the UI focus the right panel.
    section: str = "general"
    #: IR identifier (column id, table name) the message attaches to.
    target: str | None = None
    #: Structured, machine-applicable remedy, e.g.
    #: ``{"action": "add_group_by", "table": "customers", "field": "name"}``
    fix: dict | None = None

    def as_dict(self) -> dict:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "message": self.message,
            "section": self.section,
            "target": self.target,
            "fix": self.fix,
        }


@dataclass
class DiagnosticCollector:
    items: list[Diagnostic] = field(default_factory=list)

    def error(self, code: Code, message: str, **kwargs) -> None:
        self.items.append(Diagnostic(code, Severity.ERROR, message, **kwargs))

    def warn(self, code: Code, message: str, **kwargs) -> None:
        self.items.append(Diagnostic(code, Severity.WARNING, message, **kwargs))

    def info(self, code: Code, message: str, **kwargs) -> None:
        self.items.append(Diagnostic(code, Severity.INFO, message, **kwargs))

    def extend(self, others: list[Diagnostic]) -> None:
        self.items.extend(others)

    @property
    def has_errors(self) -> bool:
        return any(item.severity is Severity.ERROR for item in self.items)

    @property
    def errors(self) -> list[Diagnostic]:
        return [item for item in self.items if item.severity is Severity.ERROR]

    def as_list(self) -> list[dict]:
        return [item.as_dict() for item in self.items]


class ReportCompilationError(Exception):
    """Raised when a report cannot be compiled. Carries user-safe diagnostics."""

    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        self.diagnostics = diagnostics
        summary = "; ".join(d.message for d in diagnostics[:3]) or "report is not valid"
        super().__init__(summary)

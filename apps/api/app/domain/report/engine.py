"""
Report engine -- the orchestrator.

    IR -> resolve -> plan joins -> analyze fan-out -> validate -> compile -> guard

Callers never touch the individual stages. The API layer hands in a report
definition and a principal; it gets back either a compiled statement with
diagnostics, or diagnostics explaining why not.

The final AST guard pass is deliberately redundant: the compiler cannot emit
unsafe SQL by construction, so if the guard ever trips it means a bug in the
compiler, and we would far rather fail closed than send it to production.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.dialects import postgresql

from app.domain.report.compiler import CompiledReport, ReportCompiler
from app.domain.report.diagnostics import (
    Code,
    Diagnostic,
    DiagnosticCollector,
    ReportCompilationError,
)
from app.domain.report.fanout import FanoutAnalysis, FanoutAnalyzer
from app.domain.report.ir import ReportDefinition
from app.domain.report.join_planner import JoinPlan, JoinPlanner
from app.domain.report.resolver import Resolver
from app.domain.report.validator import ReportValidator
from app.domain.safety.ast_guard import GuardPolicy, SqlAstGuard, SqlSafetyError
from app.domain.schema.registry import SchemaRegistry

logger = logging.getLogger(__name__)

_DIALECTS = {"postgresql": postgresql.dialect(), "postgres": postgresql.dialect()}


@dataclass
class EngineOptions:
    max_joins: int = 8
    max_rows: int = 50_000
    max_subquery_depth: int = 4
    dialect: str = "postgresql"
    #: False when the statement will run against staged temporary tables.
    qualify_schema: bool = True


@dataclass
class BuildResult:
    """Everything the API needs: the statement, why it looks the way it does, and warnings."""

    compiled: CompiledReport | None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    plan: JoinPlan | None = None
    fanout: FanoutAnalysis | None = None
    summary: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.compiled is not None

    def diagnostics_payload(self) -> list[dict]:
        return [d.as_dict() for d in self.diagnostics]


class ReportEngine:
    def __init__(self, registry: SchemaRegistry, options: EngineOptions | None = None) -> None:
        self.registry = registry
        self.options = options or EngineOptions()
        self.guard = SqlAstGuard(dialect="postgres")

    # ------------------------------------------------------------------
    def build(
        self,
        definition: ReportDefinition,
        parameters: dict[str, Any] | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> BuildResult:
        diagnostics = DiagnosticCollector()
        summary = definition.summary()

        resolved = Resolver(self.registry).resolve(definition, diagnostics)
        if diagnostics.has_errors:
            return BuildResult(None, diagnostics.items, summary=summary)

        plan = JoinPlanner(self.registry, self.options.max_joins).plan(
            tables=resolved.tables,
            primary_table=resolved.tables[0],
            explicit_joins=definition.joins or None,
            diagnostics=diagnostics,
        )
        if diagnostics.has_errors:
            return BuildResult(None, diagnostics.items, plan=plan, summary=summary)

        analysis = FanoutAnalyzer(
            enabled=not definition.disable_fanout_correction
        ).analyze(
            plan=plan,
            resolved_columns=resolved.columns,
            filter_tables=resolved.filter_tables(),
            group_by_tables={group.table for group in resolved.group_by},
            diagnostics=diagnostics,
        )

        ReportValidator().validate(resolved, diagnostics, parameters)
        if diagnostics.has_errors:
            return BuildResult(None, diagnostics.items, plan=plan, fanout=analysis,
                               summary=summary)

        compiled = ReportCompiler(
            max_rows=self.options.max_rows,
            qualify_schema=self.options.qualify_schema,
        ).compile(
            resolved=resolved,
            plan=plan,
            analysis=analysis,
            diagnostics=diagnostics,
            parameters=parameters,
            offset=offset,
            limit=limit,
        )

        try:
            self._assert_safe(compiled)
        except SqlSafetyError as error:
            # Fail closed. A compiler bug must never reach the operational database.
            logger.error("Compiler emitted SQL that failed the safety guard: %s", error)
            diagnostics.error(
                code=Code.INTERNAL_SAFETY_FAILURE,
                message="This report could not be run because it failed an internal "
                        "safety check. The technical details have been logged.",
                section="general",
            )
            compiled = None

        return BuildResult(
            compiled=compiled,
            diagnostics=diagnostics.items,
            plan=plan,
            fanout=analysis,
            summary=summary,
        )

    # ------------------------------------------------------------------
    def build_or_raise(
        self,
        definition: ReportDefinition,
        parameters: dict[str, Any] | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[CompiledReport, list[Diagnostic]]:
        result = self.build(definition, parameters, offset, limit)
        if result.compiled is None:
            raise ReportCompilationError(
                [d for d in result.diagnostics if d.severity == "error"] or result.diagnostics
            )
        return result.compiled, result.diagnostics

    # ------------------------------------------------------------------
    def render_sql(self, compiled: CompiledReport, with_values: bool = False) -> str:
        dialect = _DIALECTS.get(self.options.dialect, postgresql.dialect())
        raw = (
            compiled.sql_with_values(dialect) if with_values else compiled.sql(dialect)
        )
        return SqlAstGuard.format(raw, dialect="postgres")

    def _assert_safe(self, compiled: CompiledReport) -> None:
        dialect = _DIALECTS.get(self.options.dialect, postgresql.dialect())
        sql = compiled.sql_with_values(dialect)
        self.guard.validate(
            sql,
            GuardPolicy(
                max_joins=self.options.max_joins + 4,  # sub-selects add their own joins
                max_subquery_depth=self.options.max_subquery_depth,
                allowed_tables={t.name.lower() for t in self.registry.tables},
            ),
        )



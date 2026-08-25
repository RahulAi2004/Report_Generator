"""
Semantic validation.

Resolution proves the identifiers exist; this proves the report *means*
something. Most findings carry a machine-applicable fix so the builder can
offer "Fix this" rather than only complaining.
"""

from __future__ import annotations

from app.domain.report.diagnostics import Code, DiagnosticCollector
from app.domain.report.ir import ReportDefinition
from app.domain.report.resolver import ResolvedGroup, ResolvedReport


class ReportValidator:
    def validate(
        self,
        resolved: ResolvedReport,
        diagnostics: DiagnosticCollector,
        parameters: dict | None = None,
    ) -> None:
        self._check_visibility(resolved, diagnostics)
        self._check_grouping(resolved, diagnostics)
        self._check_parameters(resolved.definition, parameters or {}, diagnostics)
        self._check_visualization(resolved, diagnostics)

    # ------------------------------------------------------------------
    def _check_visibility(
        self, resolved: ResolvedReport, diagnostics: DiagnosticCollector
    ) -> None:
        """
        A report with columns but none visible compiles to a SELECT with nothing
        to select. Saying so beats returning an empty grid with no explanation.
        """
        if resolved.columns and not any(column.visible for column in resolved.columns):
            diagnostics.error(
                Code.NO_VISIBLE_COLUMNS,
                "Every column in this report is hidden, so there is nothing to show. "
                "Tick 'Visible in Report' on at least one column.",
                section="columns",
                fix={"action": "show_column", "column_id": resolved.columns[0].id},
            )

    def _check_grouping(self, resolved: ResolvedReport, diagnostics: DiagnosticCollector) -> None:
        if not resolved.has_aggregates:
            if resolved.group_by:
                diagnostics.info(
                    Code.MISSING_GROUP_BY,
                    "Grouping has no effect until at least one column uses an aggregation "
                    "such as SUM or COUNT.",
                    section="group_by",
                )
            return

        grouped = {(g.table, g.field) for g in resolved.group_by}
        ungrouped = [
            column
            for column in resolved.columns
            if not column.is_aggregate and (column.table, column.field) not in grouped
        ]
        if not ungrouped:
            return

        names = ", ".join(column.display_name for column in ungrouped[:4])
        if len(ungrouped) > 4:
            names += f" and {len(ungrouped) - 4} more"

        diagnostics.warn(
            Code.MISSING_GROUP_BY,
            f"This report mixes totals with detail columns. {names} will be added to the "
            "grouping automatically, which means one row per distinct combination of those "
            "values. Add them to Group By yourself if you want to control the order.",
            section="group_by",
            fix={
                "action": "add_group_by",
                "fields": [
                    {"table": column.table, "field": column.field}
                    for column in ungrouped
                ],
            },
        )

    def _check_parameters(
        self,
        definition: ReportDefinition,
        parameters: dict,
        diagnostics: DiagnosticCollector,
    ) -> None:
        for spec in definition.parameters():
            if spec.required and spec.name not in parameters and spec.default is None:
                diagnostics.error(
                    Code.MISSING_PARAMETER,
                    f"This report needs a value for '{spec.prompt}' before it can run.",
                    section="filters",
                    target=spec.name,
                    fix={"action": "prompt_parameter", "name": spec.name,
                         "prompt": spec.prompt},
                )

    def _check_visualization(
        self, resolved: ResolvedReport, diagnostics: DiagnosticCollector
    ) -> None:
        visualization = resolved.definition.visualization
        if visualization.type == "table":
            return

        by_id = {column.id: column for column in resolved.columns}
        metric = by_id.get(visualization.metric_column_id or "")
        dimension = by_id.get(visualization.dimension_column_id or "")

        if metric is None:
            diagnostics.error(
                Code.NO_COLUMNS,
                f"A {visualization.type} chart needs a metric column. Choose which value "
                "should be plotted.",
                section="visualization",
            )
            return

        if not metric.meta.data_type.is_numeric and not metric.is_aggregate:
            diagnostics.error(
                Code.INVALID_AGGREGATION,
                f"{metric.display_name} is not numeric, so it cannot be used as the chart "
                "metric. Pick a number field, or apply COUNT to this one.",
                section="visualization",
                target=metric.id,
            )

        if visualization.type != "kpi" and dimension is None:
            diagnostics.error(
                Code.NO_COLUMNS,
                f"A {visualization.type} chart needs a dimension column to plot against.",
                section="visualization",
            )

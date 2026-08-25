"""
Fan-out analysis -- the correctness feature.

Joining a one-to-many branch multiplies parent rows. An order with 5 items and
3 artworks produces 15 rows, so ``SUM(orders.total_amount)`` returns 15x the
real figure and ``SUM(items.quantity)`` returns 3x. Flat report builders emit
exactly this query and hand management a confidently wrong number.

The reference screenshot this project is modelled on contains that very bug: it
counts artworks, sums item quantity, and sums order value across two separate
one-to-many branches in one flat query.

This module decides, per branch, how to keep the arithmetic honest:

  PRE_AGGREGATE  aggregate the branch in a derived table keyed on its join
                 column, then join the single resulting row per parent
  SEMI_JOIN      the branch is only used by filters -> EXISTS, which filters
                 without multiplying
  DETAIL         the user selected raw columns from the branch, so row
                 multiplication is what they actually asked for; any aggregate
                 elsewhere is then genuinely ambiguous and we say so
  PRUNE          the branch contributes nothing and is optional -> drop it
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.report.diagnostics import Code, DiagnosticCollector
from app.domain.report.join_planner import JoinPlan, JoinStep
from app.domain.schema.registry import Aggregation


class Strategy(StrEnum):
    PRE_AGGREGATE = "pre_aggregate"
    SEMI_JOIN = "semi_join"
    DETAIL = "detail"
    PRUNE = "prune"


@dataclass
class Branch:
    """A sub-tree hanging off the root table via one direct join step."""

    root_step: JoinStep
    tables: set[str] = field(default_factory=set)
    steps: list[JoinStep] = field(default_factory=list)
    multiplies: bool = False

    aggregated_columns: list = field(default_factory=list)
    plain_columns: list = field(default_factory=list)
    filter_tables: set[str] = field(default_factory=set)
    group_by_tables: set[str] = field(default_factory=set)

    strategy: Strategy = Strategy.DETAIL

    @property
    def name(self) -> str:
        return self.root_step.to_table

    @property
    def attach_column(self) -> str:
        """Column on the branch side that links back to the root."""
        return self.root_step.to_column

    @property
    def parent_column(self) -> str:
        return self.root_step.from_column


@dataclass
class FanoutAnalysis:
    branches: list[Branch] = field(default_factory=list)
    inflation_detected: bool = False
    corrected: bool = False

    @property
    def pre_aggregated(self) -> list[Branch]:
        return [b for b in self.branches if b.strategy is Strategy.PRE_AGGREGATE]

    @property
    def semi_joined(self) -> list[Branch]:
        return [b for b in self.branches if b.strategy is Strategy.SEMI_JOIN]

    @property
    def pruned(self) -> list[Branch]:
        return [b for b in self.branches if b.strategy is Strategy.PRUNE]

    def strategy_for(self, table: str) -> Strategy | None:
        for branch in self.branches:
            if table in branch.tables:
                return branch.strategy
        return None

    def branch_containing(self, table: str) -> Branch | None:
        for branch in self.branches:
            if table in branch.tables:
                return branch
        return None


class FanoutAnalyzer:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def analyze(
        self,
        plan: JoinPlan,
        resolved_columns: list,
        filter_tables: set[str],
        group_by_tables: set[str],
        diagnostics: DiagnosticCollector,
    ) -> FanoutAnalysis:
        analysis = FanoutAnalysis()
        if not plan.steps:
            return analysis

        analysis.branches = self._build_branches(plan)
        self._attribute_usage(
            analysis, plan, resolved_columns, filter_tables, group_by_tables
        )

        multiplying = [b for b in analysis.branches if b.multiplies]
        has_aggregates = any(
            column.aggregation is not Aggregation.NONE for column in resolved_columns
        )

        for branch in analysis.branches:
            branch.strategy = self._choose(branch, has_aggregates)

        # Inflation occurs when a row-multiplying branch stays a real join while
        # aggregates are computed anywhere in the query.
        detail_multipliers = [
            b for b in multiplying if b.strategy is Strategy.DETAIL
        ]
        analysis.inflation_detected = bool(detail_multipliers and has_aggregates)
        analysis.corrected = any(
            b.strategy in (Strategy.PRE_AGGREGATE, Strategy.SEMI_JOIN) for b in multiplying
        )

        self._report(analysis, detail_multipliers, multiplying, diagnostics)
        return analysis

    # ------------------------------------------------------------------
    def _build_branches(self, plan: JoinPlan) -> list[Branch]:
        by_name: dict[str, Branch] = {}
        for step in plan.steps:
            if step.from_table == plan.root:
                by_name[step.to_table] = Branch(root_step=step, tables={step.to_table})

        # Attach deeper steps to whichever branch their parent belongs to.
        for step in plan.steps:
            if step.from_table == plan.root:
                by_name[step.to_table].steps.append(step)
                continue
            owner = plan.branch_of.get(step.from_table)
            branch = by_name.get(owner)
            if branch is not None:
                branch.tables.add(step.to_table)
                branch.steps.append(step)

        for branch in by_name.values():
            branch.multiplies = any(
                plan.fans_out.get(table, False) for table in branch.tables
            )
        return list(by_name.values())

    def _attribute_usage(
        self,
        analysis: FanoutAnalysis,
        plan: JoinPlan,
        resolved_columns: list,
        filter_tables: set[str],
        group_by_tables: set[str],
    ) -> None:
        for branch in analysis.branches:
            for column in resolved_columns:
                if column.table not in branch.tables:
                    continue
                if column.aggregation is Aggregation.NONE:
                    branch.plain_columns.append(column)
                else:
                    branch.aggregated_columns.append(column)
            branch.filter_tables = branch.tables & filter_tables
            branch.group_by_tables = branch.tables & group_by_tables

    def _choose(self, branch: Branch, has_aggregates: bool) -> Strategy:
        if not branch.multiplies:
            # A many-to-one branch adds columns without multiplying rows.
            return Strategy.DETAIL

        if not self.enabled:
            return Strategy.DETAIL

        # Raw columns or grouping from the branch mean the user wants the detail
        # rows; correcting would change the report they asked for.
        if branch.plain_columns or branch.group_by_tables:
            return Strategy.DETAIL

        if branch.aggregated_columns:
            return Strategy.PRE_AGGREGATE

        if branch.filter_tables:
            return Strategy.SEMI_JOIN

        if not has_aggregates:
            return Strategy.DETAIL

        return Strategy.PRUNE

    # ------------------------------------------------------------------
    def _report(
        self,
        analysis: FanoutAnalysis,
        detail_multipliers: list[Branch],
        multiplying: list[Branch],
        diagnostics: DiagnosticCollector,
    ) -> None:
        for branch in analysis.pre_aggregated:
            names = ", ".join(sorted(branch.tables))
            diagnostics.info(
                Code.FANOUT_CORRECTED,
                f"{names} is aggregated separately before joining, so totals from other "
                "tables stay correct.",
                section="joins",
                target=branch.name,
            )

        for branch in analysis.semi_joined:
            diagnostics.info(
                Code.FANOUT_CORRECTED,
                f"{branch.name} is used only for filtering, so it is applied as an "
                "existence check rather than a join. This avoids duplicating rows.",
                section="joins",
                target=branch.name,
            )

        if analysis.inflation_detected:
            names = ", ".join(sorted(b.name for b in detail_multipliers))
            diagnostics.warn(
                Code.FANOUT_INFLATION,
                f"Totals in this report are likely inflated. {names} returns multiple rows "
                "per record, and you have also selected detail columns from it, so each "
                "parent row is repeated and its values counted more than once. Either "
                "remove the detail columns from that table, or read the totals as "
                "per-line figures rather than per-order.",
                section="columns",
                fix={
                    "action": "review_fanout",
                    "tables": sorted(b.name for b in detail_multipliers),
                },
            )

        if len(multiplying) > 1 and all(b.strategy is Strategy.DETAIL for b in multiplying):
            names = " and ".join(sorted(b.name for b in multiplying))
            diagnostics.warn(
                Code.CARTESIAN_RISK,
                f"{names} both return multiple rows per record. Joining them together "
                "multiplies the row count. Consider building two reports instead of one.",
                section="joins",
            )

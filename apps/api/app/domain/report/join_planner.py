"""
Join planner.

Turns "the user picked these four tables" into a concrete, ordered join tree,
using only relationships that exist in the registry. Because every join carries
an ON clause derived from a registered relationship, a Cartesian product is not
something we guard against -- it is something the planner cannot express.

Three failure modes matter and each is handled explicitly rather than guessed:

  * no path            -> error naming the disconnected tables
  * several equal paths -> ambiguity; we refuse to pick and ask the user
  * cycles              -> broken at the highest-cost edge, with a warning

The planner also records, for every table, whether reaching it from the root
crosses a one-to-many edge. That flag is what the fan-out analyzer consumes to
stop the engine returning inflated sums (ARCHITECTURE.md, section E.4).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from app.domain.report.diagnostics import Code, DiagnosticCollector
from app.domain.report.ir import ReportJoin
from app.domain.schema.registry import (
    Cardinality,
    JoinType,
    RelationshipMeta,
    RelationshipSource,
    SchemaRegistry,
)


@dataclass(frozen=True, slots=True)
class JoinStep:
    """One edge of the join tree, oriented away from the root."""

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    join_type: JoinType
    relationship: RelationshipMeta
    #: Cardinality looking outward: N means ``to_table`` can multiply rows.
    cardinality: Cardinality

    @property
    def multiplies_rows(self) -> bool:
        return self.cardinality in (Cardinality.ONE_TO_MANY, Cardinality.MANY_TO_MANY)

    def as_dict(self) -> dict:
        return {
            "from_table": self.from_table,
            "from_column": self.from_column,
            "to_table": self.to_table,
            "to_column": self.to_column,
            "join_type": self.join_type.value,
            "cardinality": self.cardinality.value,
            "relationship_id": self.relationship.id,
            "relationship_source": self.relationship.source.value,
            "multiplies_rows": self.multiplies_rows,
        }


@dataclass
class JoinPlan:
    root: str
    steps: list[JoinStep] = field(default_factory=list)
    #: Tables pulled in to connect the selection that the user did not choose.
    bridge_tables: list[str] = field(default_factory=list)
    #: table -> True when the path from root to it crosses a one-to-many edge.
    fans_out: dict[str, bool] = field(default_factory=dict)
    #: table -> the immediate child-of-root table its branch hangs from. Two
    #: aggregates on *different* branch roots is the inflation case.
    branch_of: dict[str, str] = field(default_factory=dict)

    @property
    def tables(self) -> list[str]:
        return [self.root, *[step.to_table for step in self.steps]]

    def as_dict(self) -> dict:
        return {
            "root": self.root,
            "steps": [step.as_dict() for step in self.steps],
            "bridge_tables": self.bridge_tables,
            "fans_out": self.fans_out,
        }


@dataclass(frozen=True, slots=True)
class _PathEdge:
    relationship: RelationshipMeta
    from_table: str
    to_table: str


class JoinPlanner:
    def __init__(self, registry: SchemaRegistry, max_joins: int = 8) -> None:
        self.registry = registry
        self.max_joins = max_joins

    # ------------------------------------------------------------------
    def plan(
        self,
        tables: list[str],
        primary_table: str,
        explicit_joins: list[ReportJoin] | None = None,
        diagnostics: DiagnosticCollector | None = None,
    ) -> JoinPlan:
        diagnostics = diagnostics or DiagnosticCollector()
        required = [t for t in dict.fromkeys([primary_table, *tables])]

        if explicit_joins:
            plan = self._plan_from_explicit(required, primary_table, explicit_joins, diagnostics)
        else:
            plan = self._plan_automatically(required, primary_table, diagnostics)

        if len(plan.steps) > self.max_joins:
            diagnostics.error(
                Code.TOO_MANY_JOINS,
                f"This report needs {len(plan.steps)} joins but the limit is {self.max_joins}. "
                "Remove some tables, or ask an administrator to raise the limit.",
                section="joins",
            )

        self._annotate_branches(plan)
        self._warn_on_inferred(plan, diagnostics)
        return plan

    # ------------------------------------------------------------------
    # The user (or a previously saved report) already chose the joins.
    # Their choice wins; we only verify it is legal.
    # ------------------------------------------------------------------
    def _plan_from_explicit(
        self,
        required: list[str],
        root: str,
        explicit: list[ReportJoin],
        diagnostics: DiagnosticCollector,
    ) -> JoinPlan:
        plan = JoinPlan(root=root)
        placed = {root}
        pending = list(explicit)

        # Orient each declared join away from the root, adding them in an order
        # where the "from" side is already part of the tree.
        progress = True
        while pending and progress:
            progress = False
            for join in list(pending):
                for near, far, near_col, far_col in (
                    (join.left_table, join.right_table, join.left_column, join.right_column),
                    (join.right_table, join.left_table, join.right_column, join.left_column),
                ):
                    if near in placed and far not in placed:
                        relationship = self._match_relationship(join)
                        if relationship is None:
                            diagnostics.error(
                                Code.NO_JOIN_PATH,
                                f"No registered relationship connects {join.left_table}."
                                f"{join.left_column} to {join.right_table}.{join.right_column}. "
                                "Define it under Data Sources first.",
                                section="joins",
                                target=f"{join.left_table}->{join.right_table}",
                            )
                            pending.remove(join)
                            progress = True
                            break
                        plan.steps.append(
                            JoinStep(
                                from_table=near,
                                from_column=near_col,
                                to_table=far,
                                to_column=far_col,
                                join_type=join.join_type,
                                relationship=relationship,
                                cardinality=relationship.cardinality_from(near),
                            )
                        )
                        placed.add(far)
                        pending.remove(join)
                        progress = True
                        break

        for join in pending:
            diagnostics.warn(
                Code.CIRCULAR_JOIN,
                f"The join between {join.left_table} and {join.right_table} was skipped: it "
                "either duplicates an existing path or forms a loop.",
                section="joins",
            )

        for table in required:
            if table not in placed:
                diagnostics.error(
                    Code.UNRELATED_TABLE,
                    f"{self._label(table)} is not connected to the rest of the report. "
                    "Add a relationship or remove the table.",
                    section="joins",
                    target=table,
                )
        return plan

    # ------------------------------------------------------------------
    # Nothing declared: grow a minimum-cost tree covering every required table.
    # ------------------------------------------------------------------
    def _plan_automatically(
        self, required: list[str], root: str, diagnostics: DiagnosticCollector
    ) -> JoinPlan:
        plan = JoinPlan(root=root)
        connected = {root}
        outstanding = [t for t in required if t != root]

        while outstanding:
            best_target: str | None = None
            best_path: list[_PathEdge] | None = None
            best_cost = float("inf")
            ambiguous_for: dict[str, list[list[_PathEdge]]] = {}

            for target in outstanding:
                paths, cost = self._shortest_paths(connected, target)
                if not paths:
                    continue
                if len(paths) > 1:
                    ambiguous_for[target] = paths
                if cost < best_cost:
                    best_cost, best_target, best_path = cost, target, paths[0]

            if best_target is None or best_path is None:
                for table in outstanding:
                    diagnostics.error(
                        Code.NO_JOIN_PATH,
                        f"{self._label(table)} has no relationship path to "
                        f"{self._label(root)}. Define a logical relationship under "
                        "Data Sources, or remove the table from this report.",
                        section="joins",
                        target=table,
                        fix={"action": "define_relationship", "table": table},
                    )
                break

            if best_target in ambiguous_for:
                options = ambiguous_for[best_target]
                diagnostics.error(
                    Code.AMBIGUOUS_JOIN_PATH,
                    f"There are {len(options)} equally valid ways to join "
                    f"{self._label(best_target)} into this report. Choose one under "
                    "Edit Relationships -- picking automatically would silently change "
                    "your numbers.",
                    section="joins",
                    target=best_target,
                    fix={
                        "action": "choose_join_path",
                        "table": best_target,
                        "options": [
                            [
                                {
                                    "from": edge.from_table,
                                    "to": edge.to_table,
                                    "relationship_id": edge.relationship.id,
                                }
                                for edge in option
                            ]
                            for option in options
                        ],
                    },
                )

            for edge in best_path:
                if edge.to_table in connected:
                    continue
                relationship = edge.relationship
                from_column, to_column = relationship.columns_for(edge.from_table)
                cardinality = relationship.cardinality_from(edge.from_table)
                plan.steps.append(
                    JoinStep(
                        from_table=edge.from_table,
                        from_column=from_column,
                        to_table=edge.to_table,
                        to_column=to_column,
                        join_type=self._default_join_type(relationship, cardinality),
                        relationship=relationship,
                        cardinality=cardinality,
                    )
                )
                connected.add(edge.to_table)
                if edge.to_table not in required:
                    plan.bridge_tables.append(edge.to_table)
                    diagnostics.info(
                        Code.UNRELATED_TABLE,
                        f"{self._label(edge.to_table)} was added automatically to connect "
                        f"{self._label(best_target)} to the rest of the report.",
                        section="joins",
                        target=edge.to_table,
                    )

            outstanding = [t for t in outstanding if t not in connected]

        return plan

    # ------------------------------------------------------------------
    def _shortest_paths(
        self, sources: set[str], target: str
    ) -> tuple[list[list[_PathEdge]], float]:
        """
        Multi-source Dijkstra returning *all* minimum-cost paths to ``target``.

        Returning every optimal path (not just one) is what lets the caller
        detect ambiguity instead of silently committing to an arbitrary join.
        """
        distances: dict[str, float] = {source: 0.0 for source in sources}
        predecessors: dict[str, list[_PathEdge]] = {source: [] for source in sources}
        queue: list[tuple[float, str]] = [(0.0, source) for source in sources]
        heapq.heapify(queue)
        visited: set[str] = set()

        while queue:
            cost, table = heapq.heappop(queue)
            if table in visited:
                continue
            visited.add(table)

            for relationship in self.registry.edges_for(table):
                neighbour = relationship.other_side(table)
                if neighbour == table:  # self-referencing relationship
                    continue
                new_cost = cost + relationship.cost
                edge = _PathEdge(relationship, table, neighbour)
                known = distances.get(neighbour)
                if known is None or new_cost < known - 1e-9:
                    distances[neighbour] = new_cost
                    predecessors[neighbour] = [edge]
                    heapq.heappush(queue, (new_cost, neighbour))
                elif abs(new_cost - known) < 1e-9 and neighbour not in sources:
                    # A second, equally good way in -- candidate ambiguity.
                    if not any(
                        existing.relationship.id == relationship.id
                        for existing in predecessors[neighbour]
                    ):
                        predecessors[neighbour].append(edge)

        if target not in distances:
            return [], float("inf")

        paths: list[list[_PathEdge]] = []
        self._walk_back(target, sources, predecessors, [], paths)
        return paths, distances[target]

    def _walk_back(
        self,
        table: str,
        sources: set[str],
        predecessors: dict[str, list[_PathEdge]],
        suffix: list[_PathEdge],
        out: list[list[_PathEdge]],
        depth: int = 0,
    ) -> None:
        if table in sources or depth > 12:
            out.append(list(suffix))
            return
        for edge in predecessors.get(table, []):
            self._walk_back(edge.from_table, sources, predecessors, [edge, *suffix], out, depth + 1)

    # ------------------------------------------------------------------
    def _match_relationship(self, join: ReportJoin) -> RelationshipMeta | None:
        if join.relationship_id:
            for relationship in self.registry.relationships:
                if relationship.id == join.relationship_id:
                    return relationship
        for relationship in self.registry.edges_between(join.left_table, join.right_table):
            columns = {
                (relationship.left_table, relationship.left_column),
                (relationship.right_table, relationship.right_column),
            }
            if columns == {
                (join.left_table, join.left_column),
                (join.right_table, join.right_column),
            }:
                return relationship
        return None

    @staticmethod
    def _default_join_type(relationship: RelationshipMeta, cardinality: Cardinality) -> JoinType:
        """
        LEFT when the far side is optional, INNER when the foreign key is NOT NULL.

        Defaulting to LEFT matters: an INNER join silently drops orders that have
        no shipment yet, which is exactly the kind of quiet omission that makes a
        manager mistrust a report.
        """
        if cardinality in (Cardinality.ONE_TO_MANY, Cardinality.MANY_TO_MANY):
            return JoinType.LEFT
        return relationship.default_join_type

    def _annotate_branches(self, plan: JoinPlan) -> None:
        plan.fans_out[plan.root] = False
        plan.branch_of[plan.root] = plan.root
        for step in plan.steps:
            parent_fans = plan.fans_out.get(step.from_table, False)
            plan.fans_out[step.to_table] = parent_fans or step.multiplies_rows
            plan.branch_of[step.to_table] = (
                step.to_table
                if step.from_table == plan.root
                else plan.branch_of.get(step.from_table, step.to_table)
            )

    def _warn_on_inferred(self, plan: JoinPlan, diagnostics: DiagnosticCollector) -> None:
        for step in plan.steps:
            if step.relationship.source is RelationshipSource.INFERRED:
                diagnostics.warn(
                    Code.INFERRED_RELATIONSHIP,
                    f"The link between {self._label(step.from_table)} and "
                    f"{self._label(step.to_table)} was inferred from column names, not a "
                    "database foreign key. Confirm it is correct before trusting these numbers.",
                    section="joins",
                    target=step.to_table,
                )

    def _label(self, table: str) -> str:
        found = self.registry.table(table)
        return found.label if found else table

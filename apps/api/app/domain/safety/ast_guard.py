"""
SQL AST guard (ARCHITECTURE.md, section D/L4).

Reports built through the compiler never produce SQL text from user input, so
this guard exists for the paths that *do* involve textual SQL: admin-authored
queries, the AI fallback mode, and as a final assertion on everything the
compiler emits before it reaches the database.

Keyword blocklists are not used. They miss the cases that matter -- most
notably data-modifying CTEs, where `WITH x AS (DELETE FROM ...) SELECT * FROM x`
begins with the word SELECT. We parse the statement and inspect the tree.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import expressions as exp

#: Any of these anywhere in the tree -- including inside a CTE -- is fatal.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter, exp.Create,
    exp.TruncateTable, exp.Grant, exp.Merge, exp.Command,
)

#: Functions that read files, sleep, or reach outside the database.
FORBIDDEN_FUNCTIONS: frozenset[str] = frozenset({
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_sleep",
    "pg_stat_file", "lo_import", "lo_export", "dblink", "dblink_exec",
    "xp_cmdshell", "sp_executesql", "openrowset", "opendatasource",
    "load_file", "sys_exec", "sys_eval", "copy_from_program",
})

#: Catalog surface a reporting user has no business querying directly.
FORBIDDEN_SCHEMAS: frozenset[str] = frozenset({
    "pg_catalog", "information_schema", "pg_toast",
})


class SqlSafetyError(Exception):
    """Raised when a statement fails validation. The message is user-safe."""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason)


@dataclass
class GuardPolicy:
    max_joins: int = 8
    max_subquery_depth: int = 3
    allow_set_operations: bool = True
    #: None means "no table restriction". Otherwise a lowercase allowlist.
    allowed_tables: set[str] | None = None
    allowed_columns: dict[str, set[str]] | None = None


@dataclass
class GuardResult:
    tables: list[str] = field(default_factory=list)
    join_count: int = 0
    max_depth: int = 0
    has_limit: bool = False


class SqlAstGuard:
    def __init__(self, dialect: str = "postgres") -> None:
        self.dialect = dialect

    # ------------------------------------------------------------------
    def validate(self, sql: str, policy: GuardPolicy | None = None) -> GuardResult:
        policy = policy or GuardPolicy()

        try:
            statements = sqlglot.parse(sql, read=self.dialect)
        except sqlglot.ParseError as error:
            raise SqlSafetyError(
                "This query could not be understood.", str(error)
            ) from error

        statements = [s for s in statements if s is not None]
        if len(statements) != 1:
            raise SqlSafetyError(
                "Only a single statement may be executed. "
                f"Found {len(statements)}."
            )

        tree = statements[0]
        self._assert_read_only(tree)
        self._assert_is_select(tree)
        self._assert_no_forbidden_functions(tree)
        self._assert_no_select_into(tree)

        result = GuardResult()
        result.tables = self._collect_tables(tree, policy)
        result.join_count = len(list(tree.find_all(exp.Join)))
        result.max_depth = self._subquery_depth(tree)
        result.has_limit = tree.find(exp.Limit) is not None

        if result.join_count > policy.max_joins:
            raise SqlSafetyError(
                f"This query joins {result.join_count} tables; the limit is "
                f"{policy.max_joins}."
            )
        if result.max_depth > policy.max_subquery_depth:
            raise SqlSafetyError(
                f"This query nests {result.max_depth} levels deep; the limit is "
                f"{policy.max_subquery_depth}."
            )
        self._assert_no_bare_cross_join(tree)

        if policy.allowed_columns is not None:
            self._assert_columns_allowed(tree, policy)

        return result

    # ------------------------------------------------------------------
    def enforce_limit(self, sql: str, max_rows: int) -> str:
        """
        Clamp or inject LIMIT by rewriting the tree.

        String-appending ' LIMIT n' is unsafe -- it silently corrupts UNION
        queries and statements that already carry a LIMIT.
        """
        tree = sqlglot.parse_one(sql, read=self.dialect)
        existing = tree.args.get("limit")
        if existing is not None:
            try:
                current = int(existing.expression.name)
                if current <= max_rows:
                    return tree.sql(dialect=self.dialect)
            except (AttributeError, ValueError):
                pass
        return tree.limit(max_rows).sql(dialect=self.dialect)

    @staticmethod
    def format(sql: str, dialect: str = "postgres") -> str:
        """Pretty-print for the SQL inspector (spec 45)."""
        try:
            return sqlglot.transpile(sql, read=dialect, write=dialect, pretty=True)[0]
        except sqlglot.ParseError:
            return sql

    # ------------------------------------------------------------------
    def _assert_read_only(self, tree: exp.Expression) -> None:
        for node_type in FORBIDDEN_NODES:
            found = tree.find(node_type)
            if found is not None:
                raise SqlSafetyError(
                    "Only read operations are permitted. This query contains a "
                    f"{node_type.__name__.upper()} operation.",
                    detail=type(found).__name__,
                )
        # `SET`, `CALL` and similar arrive as Command nodes, already covered above.

    @staticmethod
    def _assert_is_select(tree: exp.Expression) -> None:
        if isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            return
        if isinstance(tree, exp.Subquery) and isinstance(tree.this, exp.Select):
            return
        raise SqlSafetyError(
            "Only SELECT queries may be executed here.",
            detail=type(tree).__name__,
        )

    @staticmethod
    def _assert_no_forbidden_functions(tree: exp.Expression) -> None:
        for node in tree.find_all(exp.Anonymous):
            name = (node.name or "").lower()
            if name in FORBIDDEN_FUNCTIONS:
                raise SqlSafetyError(f"The function {name}() is not permitted.")
        for node in tree.find_all(exp.Func):
            name = (node.sql_name() or "").lower()
            if name in FORBIDDEN_FUNCTIONS:
                raise SqlSafetyError(f"The function {name}() is not permitted.")

    @staticmethod
    def _assert_no_select_into(tree: exp.Expression) -> None:
        if tree.find(exp.Into) is not None:
            raise SqlSafetyError("SELECT ... INTO is not permitted.")

    @staticmethod
    def _assert_no_bare_cross_join(tree: exp.Expression) -> None:
        for join in tree.find_all(exp.Join):
            kind = (join.args.get("kind") or "").upper()
            side = (join.args.get("side") or "").upper()
            has_condition = join.args.get("on") is not None or join.args.get("using") is not None
            if not has_condition and kind != "CROSS" and not side:
                raise SqlSafetyError(
                    "A join without an ON condition would multiply every row against "
                    "every other row. This is not permitted."
                )
            if kind == "CROSS":
                raise SqlSafetyError("CROSS JOIN is not permitted.")

    def _collect_tables(self, tree: exp.Expression, policy: GuardPolicy) -> list[str]:
        cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        found: list[str] = []

        for table in tree.find_all(exp.Table):
            name = (table.name or "").lower()
            schema = (table.db or "").lower()
            if not name or name in cte_names:
                continue

            if schema in FORBIDDEN_SCHEMAS:
                raise SqlSafetyError(
                    "System catalog tables cannot be queried through reporting."
                )
            if policy.allowed_tables is not None and name not in policy.allowed_tables:
                raise SqlSafetyError(
                    f"The table '{name}' is not available to you.",
                    detail="not in allowlist",
                )
            if name not in found:
                found.append(name)
        return found

    @staticmethod
    def _assert_columns_allowed(tree: exp.Expression, policy: GuardPolicy) -> None:
        allowed = policy.allowed_columns or {}
        every_column = {column for columns in allowed.values() for column in columns}

        for column in tree.find_all(exp.Column):
            name = (column.name or "").lower()
            if not name or name == "*":
                continue
            qualifier = (column.table or "").lower()
            if qualifier:
                permitted = allowed.get(qualifier)
                if permitted is not None and name not in permitted:
                    raise SqlSafetyError(
                        f"The field '{qualifier}.{name}' is not available to you."
                    )
            elif every_column and name not in every_column:
                raise SqlSafetyError(f"The field '{name}' is not available to you.")

    @staticmethod
    def _subquery_depth(tree: exp.Expression) -> int:
        def depth(node: exp.Expression, current: int = 0) -> int:
            deepest = current
            for child in node.args.values():
                children = child if isinstance(child, list) else [child]
                for item in children:
                    if not isinstance(item, exp.Expression):
                        continue
                    step = 1 if isinstance(item, (exp.Subquery, exp.CTE)) else 0
                    deepest = max(deepest, depth(item, current + step))
            return deepest

        return depth(tree)

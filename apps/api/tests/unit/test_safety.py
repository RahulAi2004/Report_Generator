"""
Query safety layer tests.

These are adversarial. The guard exists for textual SQL paths (admin queries,
the AI fallback) and as a final assertion on compiler output, so every test here
is an attack that must be refused -- particularly the ones that begin with the
word SELECT and would sail past a keyword blocklist.
"""

from __future__ import annotations

import pytest

from app.domain.safety.ast_guard import GuardPolicy, SqlAstGuard, SqlSafetyError


@pytest.fixture
def guard():
    return SqlAstGuard(dialect="postgres")


# ---------------------------------------------------------------------------
# Statements that must be refused
# ---------------------------------------------------------------------------
BLOCKED = [
    pytest.param("DROP TABLE customers", id="drop"),
    pytest.param("DELETE FROM customers", id="delete"),
    pytest.param("UPDATE customers SET email = 'x'", id="update"),
    pytest.param("INSERT INTO customers (id) VALUES (1)", id="insert"),
    pytest.param("TRUNCATE TABLE customers", id="truncate"),
    pytest.param("ALTER TABLE customers ADD COLUMN x int", id="alter"),
    pytest.param("CREATE TABLE evil (x int)", id="create"),
    pytest.param("GRANT ALL ON customers TO PUBLIC", id="grant"),
    # Multi-statement: the classic injection payload.
    pytest.param("SELECT 1; DROP TABLE customers", id="stacked"),
    pytest.param("SELECT 1; SELECT 2", id="two_selects"),
    # Data-modifying CTE -- starts with SELECT-ish syntax and defeats
    # keyword blocklists entirely. This is the case that matters most.
    pytest.param(
        "WITH gone AS (DELETE FROM customers RETURNING *) SELECT * FROM gone",
        id="delete_cte",
    ),
    pytest.param(
        "WITH x AS (UPDATE customers SET email='a' RETURNING *) SELECT * FROM x",
        id="update_cte",
    ),
    pytest.param(
        "WITH x AS (INSERT INTO customers VALUES (1) RETURNING *) SELECT * FROM x",
        id="insert_cte",
    ),
    # File and command execution
    pytest.param("SELECT pg_read_file('/etc/passwd')", id="read_file"),
    pytest.param("SELECT pg_sleep(60)", id="sleep"),
    pytest.param("SELECT lo_import('/etc/passwd')", id="lo_import"),
    pytest.param("SELECT * FROM dblink('host=evil', 'SELECT 1') AS t(x int)", id="dblink"),
    # Catalog surface
    pytest.param("SELECT * FROM pg_catalog.pg_shadow", id="pg_catalog"),
    pytest.param("SELECT * FROM information_schema.columns", id="information_schema"),
    # Cartesian products
    pytest.param("SELECT * FROM customers CROSS JOIN sales_orders", id="cross_join"),
    # Writing results out
    pytest.param("SELECT * INTO evil FROM customers", id="select_into"),
]


@pytest.mark.parametrize("sql", BLOCKED)
def test_dangerous_statements_are_refused(guard, sql):
    with pytest.raises(SqlSafetyError):
        guard.validate(sql, GuardPolicy(allowed_tables={"customers", "sales_orders"}))


ALLOWED = [
    "SELECT customer_name FROM customers",
    "SELECT c.customer_name, o.order_no FROM customers c JOIN sales_orders o ON o.customer_id = c.customer_id",
    "SELECT count(*) FROM customers WHERE city = 'Madrid'",
    "WITH recent AS (SELECT * FROM sales_orders WHERE order_date > '2026-01-01') SELECT count(*) FROM recent",
    "SELECT customer_name FROM customers ORDER BY customer_name LIMIT 10",
    "SELECT customer_name FROM customers UNION SELECT customer_name FROM customers",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_legitimate_reads_are_permitted(guard, sql):
    result = guard.validate(sql, GuardPolicy(allowed_tables={"customers", "sales_orders", "c", "o"}))
    assert result.tables


# ---------------------------------------------------------------------------
# Allowlisting
# ---------------------------------------------------------------------------
def test_unlisted_table_is_refused(guard):
    with pytest.raises(SqlSafetyError, match="not available"):
        guard.validate(
            "SELECT * FROM salaries", GuardPolicy(allowed_tables={"customers"})
        )


def test_unlisted_table_hidden_inside_a_subquery_is_still_refused(guard):
    """A denied table must not be reachable by nesting it one level down."""
    with pytest.raises(SqlSafetyError, match="not available"):
        guard.validate(
            "SELECT * FROM customers WHERE id IN (SELECT user_id FROM salaries)",
            GuardPolicy(allowed_tables={"customers"}),
        )


def test_unlisted_table_hidden_inside_a_cte_is_still_refused(guard):
    with pytest.raises(SqlSafetyError, match="not available"):
        guard.validate(
            "WITH leak AS (SELECT * FROM salaries) SELECT * FROM leak",
            GuardPolicy(allowed_tables={"customers"}),
        )


def test_cte_names_are_not_mistaken_for_tables(guard):
    """A CTE alias is not a physical table and must not need an allowlist entry."""
    result = guard.validate(
        "WITH recent AS (SELECT * FROM customers) SELECT * FROM recent",
        GuardPolicy(allowed_tables={"customers"}),
    )
    assert result.tables == ["customers"]


def test_denied_column_is_refused(guard):
    with pytest.raises(SqlSafetyError, match="not available"):
        guard.validate(
            "SELECT customers.salary FROM customers",
            GuardPolicy(
                allowed_tables={"customers"},
                allowed_columns={"customers": {"customer_name", "city"}},
            ),
        )


# ---------------------------------------------------------------------------
# Resource limits
# ---------------------------------------------------------------------------
def test_join_limit_is_enforced(guard):
    sql = "SELECT * FROM a " + " ".join(
        f"JOIN t{i} ON t{i}.id = a.id" for i in range(6)
    )
    allowed = {"a", *(f"t{i}" for i in range(6))}
    with pytest.raises(SqlSafetyError, match="joins"):
        guard.validate(sql, GuardPolicy(max_joins=3, allowed_tables=allowed))


def test_subquery_depth_limit_is_enforced(guard):
    sql = "SELECT * FROM a WHERE x IN (SELECT x FROM a WHERE y IN (SELECT y FROM a WHERE z IN (SELECT z FROM a)))"
    with pytest.raises(SqlSafetyError, match="deep"):
        guard.validate(sql, GuardPolicy(max_subquery_depth=2, allowed_tables={"a"}))


def test_join_without_on_is_refused(guard):
    """An unconditioned join multiplies every row against every other row."""
    with pytest.raises(SqlSafetyError):
        guard.validate(
            "SELECT * FROM customers, sales_orders",
            GuardPolicy(allowed_tables={"customers", "sales_orders"}),
        )


def test_limit_is_injected_when_absent(guard):
    out = guard.enforce_limit("SELECT * FROM customers", 100)
    assert "LIMIT 100" in out.upper()


def test_existing_smaller_limit_is_preserved(guard):
    out = guard.enforce_limit("SELECT * FROM customers LIMIT 10", 100)
    assert "LIMIT 10" in out


def test_oversized_limit_is_clamped(guard):
    out = guard.enforce_limit("SELECT * FROM customers LIMIT 999999", 100)
    assert "LIMIT 100" in out
    assert "999999" not in out


def test_limit_clamp_survives_a_union(guard):
    """
    String-appending ' LIMIT n' would attach to the last branch of a UNION only.
    Rewriting the tree must clamp the statement as a whole.
    """
    out = guard.enforce_limit(
        "SELECT a FROM customers UNION SELECT a FROM sales_orders", 50
    )
    assert out.upper().count("LIMIT") == 1
    assert "LIMIT 50" in out.upper()


def test_unparseable_sql_is_refused_not_executed(guard):
    with pytest.raises(SqlSafetyError):
        guard.validate("SELECT FROM WHERE ((((", GuardPolicy())


def test_formatting_never_raises_on_bad_input(guard):
    """The SQL inspector must degrade gracefully rather than 500."""
    assert SqlAstGuard.format("not valid sql at all ((") is not None

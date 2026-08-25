"""
API-level integration tests.

These run the real application against the real seeded database through the real
HTTP stack, so they catch the failures unit tests cannot: authentication,
permission enforcement, error translation, and malformed input reaching the
report engine from outside.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

PASSWORD = "demo1234"
ADMIN = "admin@decoinks.local"
VIEWER = "viewer@decoinks.local"
ANALYST = "analyst@decoinks.local"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def auth(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture(scope="module")
def admin(client):
    return auth(client, ADMIN)


SIMPLE_REPORT = {
    "primary_table": "customers",
    "tables": ["customers"],
    "columns": [{"id": "c1", "table": "customers", "field": "customer_name"}],
    "row_limit": 5,
}

REFERENCE_REPORT = {
    "primary_table": "sales_orders",
    "tables": ["customers", "sales_orders", "sales_order_items", "artworks"],
    "columns": [
        {"id": "c2", "table": "customers", "field": "customer_name"},
        {"id": "c4", "table": "artworks", "field": "artwork_id", "aggregation": "count"},
        {"id": "c5", "table": "sales_order_items", "field": "quantity", "aggregation": "sum"},
        {"id": "c6", "table": "sales_orders", "field": "total_amount", "aggregation": "sum"},
    ],
    "group_by": [{"table": "customers", "field": "customer_name"}],
    "sort_by": [{"column_id": "c6", "direction": "desc"}],
    "row_limit": 5,
}


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
def test_health_needs_no_auth(client):
    assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/v1/schema/tables"),
        ("get", "/api/v1/schema/overview"),
        ("get", "/api/v1/reports"),
        ("post", "/api/v1/reports/preview"),
        ("post", "/api/v1/reports/validate"),
        ("post", "/api/v1/reports/sql"),
    ],
)
def test_every_data_route_requires_authentication(method, path):
    """A fresh client holds no cookie, so this is genuinely anonymous."""
    with TestClient(app) as anonymous:
        response = (
            anonymous.post(path, json={"definition": SIMPLE_REPORT})
            if method == "post"
            else anonymous.get(path)
        )
        assert response.status_code == 401, f"{path} answered {response.status_code}"


def test_forged_token_is_rejected(client):
    response = client.get(
        "/api/v1/schema/tables", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


def test_wrong_password_is_rejected_without_revealing_which_part(client):
    unknown = client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x"}
    )
    wrong = client.post("/api/v1/auth/login", json={"email": ADMIN, "password": "wrong"})
    assert unknown.status_code == wrong.status_code == 401
    # Identical wording: the response must not disclose that an account exists.
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_logout_invalidates_the_session(client):
    headers = auth(client, ANALYST)
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    client.post("/api/v1/auth/logout", headers=headers)
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def test_viewer_cannot_see_generated_sql(client):
    headers = auth(client, VIEWER)
    response = client.post("/api/v1/reports/sql", headers=headers,
                           json={"definition": SIMPLE_REPORT})
    assert response.status_code == 403


def test_viewer_cannot_save_reports(client):
    headers = auth(client, VIEWER)
    response = client.post("/api/v1/reports", headers=headers,
                           json={"name": "nope", "definition": SIMPLE_REPORT})
    assert response.status_code == 403


def test_viewer_can_still_run_reports(client):
    headers = auth(client, VIEWER)
    response = client.post("/api/v1/reports/preview", headers=headers,
                           json={"definition": SIMPLE_REPORT})
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_analyst_can_see_sql_but_not_manage_schema(client):
    headers = auth(client, ANALYST)
    assert client.post("/api/v1/reports/sql", headers=headers,
                       json={"definition": SIMPLE_REPORT}).status_code == 200
    assert client.post("/api/v1/schema/scan", headers=headers).status_code == 403


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def test_schema_is_discovered_not_hardcoded(client, admin):
    payload = client.get("/api/v1/schema/tables", headers=admin).json()
    assert payload["total_tables"] > 0
    names = {t["name"] for c in payload["categories"] for t in c["tables"]}
    assert "sales_orders" in names

    columns = client.get("/api/v1/schema/tables/sales_orders", headers=admin).json()
    assert columns["primary_key"] == ["order_id"]

    by_name = {c["name"]: c for c in columns["columns"]}
    # Aggregation legality is derived from the discovered type, not a guess.
    assert "sum" in by_name["total_amount"]["aggregations"]
    assert "sum" not in by_name["order_no"]["aggregations"]


def test_unknown_table_returns_404_not_500(client, admin):
    assert client.get("/api/v1/schema/tables/no_such_table", headers=admin).status_code == 404


# ---------------------------------------------------------------------------
# Report engine through HTTP
# ---------------------------------------------------------------------------
def test_reference_report_returns_corrected_totals(client, admin):
    response = client.post("/api/v1/reports/preview", headers=admin,
                           json={"definition": REFERENCE_REPORT, "page_size": 5})
    body = response.json()
    assert body["ok"] is True
    assert body["fanout_corrected"] is True
    assert len(body["rows"]) == 5


def test_preview_rows_align_with_declared_columns(client, admin):
    """Every returned row must carry exactly the declared column keys."""
    definition = {
        **SIMPLE_REPORT,
        "tables": ["customers"],
        "columns": [
            {"id": "c1", "table": "customers", "field": "customer_name"},
            {"id": "c2", "table": "customers", "field": "city", "visible": False},
            {"id": "c3", "table": "customers", "field": "country"},
        ],
    }
    body = client.post("/api/v1/reports/preview", headers=admin,
                       json={"definition": definition, "page_size": 3}).json()
    assert body["ok"] is True
    keys = {c["key"] for c in body["columns"]}
    for row in body["rows"]:
        assert set(row.keys()) == keys
    # The hidden column must not be delivered at all.
    assert not any(c["field"] == "city" for c in body["columns"])


def test_pagination_returns_different_rows(client, admin):
    first = client.post("/api/v1/reports/preview", headers=admin,
                        json={"definition": SIMPLE_REPORT, "page": 1, "page_size": 5}).json()
    second = client.post("/api/v1/reports/preview", headers=admin,
                         json={"definition": SIMPLE_REPORT, "page": 2, "page_size": 5}).json()
    assert first["rows"] and second["rows"]
    assert first["rows"] != second["rows"]


def test_validate_does_not_execute(client, admin):
    """Validation must be cheap: it compiles, it does not run the query."""
    body = client.post("/api/v1/reports/validate", headers=admin,
                       json={"definition": REFERENCE_REPORT}).json()
    assert body["ok"] is True
    assert "rows" not in body
    assert body["summary"]["data_sources"] == 4


def test_invalid_report_returns_diagnostics_not_a_crash(client, admin):
    definition = {
        "primary_table": "sales_orders",
        "tables": ["sales_orders"],
        "columns": [
            {"id": "c1", "table": "sales_orders", "field": "order_no", "aggregation": "sum"}
        ],
    }
    body = client.post("/api/v1/reports/validate", headers=admin,
                       json={"definition": definition}).json()
    assert body["ok"] is False
    assert any(d["code"] == "invalid_aggregation" for d in body["diagnostics"])


def test_malformed_definition_is_a_422_not_a_500(client, admin):
    response = client.post("/api/v1/reports/preview", headers=admin,
                           json={"definition": {"nonsense": True}})
    assert response.status_code == 422


def test_unknown_ir_field_is_rejected(client, admin):
    """`extra=forbid` on the IR stops silently ignored typos in saved reports."""
    response = client.post(
        "/api/v1/reports/validate",
        headers=admin,
        json={"definition": {**SIMPLE_REPORT, "smuggled_sql": "DROP TABLE customers"}},
    )
    assert response.status_code == 422


def test_row_limit_beyond_the_maximum_is_refused_by_validation(client, admin):
    response = client.post("/api/v1/reports/preview", headers=admin,
                           json={"definition": {**SIMPLE_REPORT, "row_limit": 10_000_000}})
    assert response.status_code == 422


def test_page_size_is_capped(client, admin):
    response = client.post("/api/v1/reports/preview", headers=admin,
                           json={"definition": SIMPLE_REPORT, "page_size": 100_000})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Injection attempts arriving through HTTP
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    ["'; DROP TABLE customers; --", "' OR 1=1 --", "x' UNION SELECT 1 --"],
)
def test_injection_via_filter_value_is_inert(client, admin, payload):
    definition = {
        **SIMPLE_REPORT,
        "filters": {
            "kind": "group", "op": "and",
            "children": [{
                "kind": "condition", "table": "customers", "field": "customer_name",
                "operator": "contains", "values": [payload],
            }],
        },
    }
    body = client.post("/api/v1/reports/preview", headers=admin,
                       json={"definition": definition}).json()
    assert body["ok"] is True
    assert body["rows"] == []  # treated as a literal search term, matching nothing

    # And the table is still there afterwards.
    after = client.post("/api/v1/reports/preview", headers=admin,
                        json={"definition": SIMPLE_REPORT}).json()
    assert len(after["rows"]) > 0


def test_injection_via_field_name_is_rejected(client, admin):
    definition = {
        "primary_table": "customers",
        "tables": ["customers"],
        "columns": [{
            "id": "c1", "table": "customers",
            "field": "customer_name FROM customers; DROP TABLE customers; --",
        }],
    }
    body = client.post("/api/v1/reports/validate", headers=admin,
                       json={"definition": definition}).json()
    assert body["ok"] is False
    assert any(d["code"] == "unknown_column" for d in body["diagnostics"])


def test_injection_via_table_name_is_rejected(client, admin):
    definition = {
        "primary_table": "customers; DROP TABLE customers",
        "tables": ["customers; DROP TABLE customers"],
        "columns": [{"id": "c1", "table": "customers; DROP TABLE customers",
                     "field": "customer_name"}],
    }
    body = client.post("/api/v1/reports/validate", headers=admin,
                       json={"definition": definition}).json()
    assert body["ok"] is False


# ---------------------------------------------------------------------------
# Saved reports
# ---------------------------------------------------------------------------
def test_save_load_roundtrip_preserves_the_definition(client, admin):
    created = client.post("/api/v1/reports", headers=admin,
                          json={"name": "Roundtrip", "definition": REFERENCE_REPORT}).json()
    loaded = client.get(f"/api/v1/reports/{created['id']}", headers=admin).json()

    assert loaded["name"] == "Roundtrip"
    assert loaded["definition"]["primary_table"] == REFERENCE_REPORT["primary_table"]
    assert len(loaded["definition"]["columns"]) == len(REFERENCE_REPORT["columns"])

    # A saved report must still run after a reload -- proving we stored config,
    # not a frozen SQL string.
    body = client.post("/api/v1/reports/preview", headers=admin,
                       json={"definition": loaded["definition"]}).json()
    assert body["ok"] is True


def test_missing_report_returns_404(client, admin):
    assert client.get("/api/v1/reports/does-not-exist", headers=admin).status_code == 404


# ---------------------------------------------------------------------------
# Pagination boundaries
# ---------------------------------------------------------------------------
def test_last_page_does_not_offer_a_next_page(client, admin):
    """
    With 18 suppliers and a page size of 6, page 3 is exactly full but final.
    Reporting `has_more` from "the page came back full" would offer a Next
    button leading to an empty grid.
    """
    definition = {
        "primary_table": "suppliers",
        "tables": ["suppliers"],
        "columns": [{"id": "c1", "table": "suppliers", "field": "supplier_id"}],
        "sort_by": [],
    }
    seen: list[str] = []
    page = 1
    while page < 10:
        body = client.post(
            "/api/v1/reports/preview",
            headers=admin,
            json={"definition": definition, "page": page, "page_size": 6},
        ).json()
        assert body["ok"] is True
        assert body["rows"], f"page {page} was offered but returned no rows"
        seen.extend(r["suppliers__supplier_id"] for r in body["rows"])
        if not body["has_more"]:
            break
        page += 1

    assert len(seen) == len(set(seen)), "pagination returned the same row twice"
    assert len(seen) == 18, f"pagination lost rows: saw {len(seen)} of 18"
    assert page < 10, "pagination never terminated"

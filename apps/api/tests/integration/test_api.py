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


# ---------------------------------------------------------------------------
# Session cookie transport
# ---------------------------------------------------------------------------
def test_session_cookie_secure_flag_follows_the_transport():
    """
    A Secure cookie is discarded by the browser over plain HTTP. Marking it
    Secure because the environment is called "production", while the site is
    actually served over HTTP, means sign-in appears to succeed and then bounces
    straight back to the login page -- with no error anywhere to explain it.

    The flag must track the real scheme, not the environment name.
    """
    from app.core.config import Settings

    over_http = Settings(environment="production", public_origin="http://bi.internal")
    assert over_http.cookies_are_secure is False

    over_https = Settings(environment="production", public_origin="https://bi.internal")
    assert over_https.cookies_are_secure is True

    # An explicit override wins, for TLS terminated by a proxy in front.
    overridden = Settings(
        environment="production",
        public_origin="http://bi.internal",
        session_cookie_secure=True,
    )
    assert overridden.cookies_are_secure is True


def test_login_sets_a_usable_cookie_over_http(client):
    """The cookie the server sets must actually authenticate the next request."""
    fresh = TestClient(app)
    response = fresh.post(
        "/api/v1/auth/login", json={"email": ADMIN, "password": PASSWORD}
    )
    assert response.status_code == 200

    header = response.headers.get("set-cookie", "")
    assert "bi_session=" in header
    assert "HttpOnly" in header

    # Over http:// the test client would drop a Secure cookie, so this both
    # checks the flag and proves the resulting session works.
    if "Secure" not in header:
        assert fresh.get("/api/v1/auth/me").status_code == 200


def test_blank_boolean_env_var_does_not_crash_startup():
    """
    Compose renders an unset variable as an empty string. Treating that as an
    invalid boolean took the whole service down over a value nobody had set.
    """
    from app.core.config import Settings

    settings = Settings(
        environment="production",
        public_origin="http://bi.internal",
        session_cookie_secure="",
    )
    assert settings.session_cookie_secure is None
    assert settings.cookies_are_secure is False


# ---------------------------------------------------------------------------
# Total count and exports
# ---------------------------------------------------------------------------
def test_total_count_is_reported(client, admin):
    body = client.post("/api/v1/reports/count", headers=admin,
                       json={"definition": SIMPLE_REPORT}).json()
    assert isinstance(body["total"], int)
    assert body["total"] > 0


def test_count_ignores_the_page_limit(client, admin):
    """The count is of the whole result, not of one page."""
    definition = {**SIMPLE_REPORT, "row_limit": 5}
    total = client.post("/api/v1/reports/count", headers=admin,
                        json={"definition": definition}).json()["total"]
    page = client.post("/api/v1/reports/preview", headers=admin,
                       json={"definition": definition, "page_size": 5}).json()
    assert len(page["rows"]) == 5
    assert total > 5


@pytest.mark.parametrize(
    "fmt,media,magic",
    [
        ("csv", "text/csv", b"\xef\xbb\xbf"),   # BOM, so Excel reads UTF-8
        ("xlsx", "spreadsheet", b"PK"),
        ("pdf", "application/pdf", b"%PDF-"),
    ],
)
def test_export_produces_a_real_file(client, admin, fmt, media, magic):
    """
    Exercises the streaming path against a real database. Applying the
    server-side cursor to the whole connection once wrapped the session guards
    in a DECLARE CURSOR, which is a syntax error -- so every export failed and
    nothing caught it until this test existed.
    """
    response = client.post(
        "/api/v1/reports/export",
        headers=admin,
        json={"definition": SIMPLE_REPORT, "format": fmt, "report_name": "Customer List"},
    )
    assert response.status_code == 200, response.text
    assert media in response.headers["content-type"]
    assert response.content.startswith(magic)
    assert "attachment;" in response.headers["content-disposition"]
    assert "Customer_List" in response.headers["content-disposition"]


def test_export_requires_the_export_permission(client):
    headers = auth(client, VIEWER)
    response = client.post(
        "/api/v1/reports/export",
        headers=headers,
        json={"definition": SIMPLE_REPORT, "format": "csv"},
    )
    assert response.status_code == 403


def test_export_filename_cannot_escape_the_download(client, admin):
    """A report name is user input and ends up in a Content-Disposition header."""
    response = client.post(
        "/api/v1/reports/export",
        headers=admin,
        json={
            "definition": SIMPLE_REPORT,
            "format": "csv",
            "report_name": '../../etc/passwd"; drop',
        },
    )
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert ".." not in disposition
    assert "/" not in disposition.split("filename=")[1]


# ---------------------------------------------------------------------------
# Uploaded datasets
# ---------------------------------------------------------------------------
UPLOAD_CSV = (
    b"Customer ID,Region,Sales Target,Review Date\n"
    b"1,North,15000.50,2026-01-15\n"
    b"2,South,22000,2026-02-20\n"
    b"3,North,9800.75,2026-03-10\n"
)


def _upload(client, headers, content=UPLOAD_CSV, filename="targets.csv"):
    import io

    return client.post(
        "/api/v1/uploads",
        headers=headers,
        files={"file": (filename, io.BytesIO(content), "text/csv")},
        data={"name": "Test Targets"},
    )


def test_upload_infers_column_types(client, admin):
    """
    An amount stored as text cannot be summed and a date stored as text sorts
    alphabetically, so types are inferred rather than assumed.
    """
    response = _upload(client, admin)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["row_count"] == 3
    types = {c["name"]: c["data_type"] for c in body["detected"]}
    assert types["customer_id"] == "integer"
    assert types["region"] == "text"
    assert types["sales_target"] == "decimal"
    assert types["review_date"] == "date"

    # Headings become safe identifiers but keep their original label.
    labels = {c["name"]: c["label"] for c in body["detected"]}
    assert labels["customer_id"] == "Customer ID"

    client.delete(f"/api/v1/uploads/{body['id']}", headers=admin)


def test_uploaded_dataset_is_queryable_like_any_table(client, admin):
    body = _upload(client, admin).json()
    table = body["table_name"]
    try:
        catalog = client.get("/api/v1/schema/tables", headers=admin).json()
        names = {t["name"] for c in catalog["categories"] for t in c["tables"]}
        assert table in names

        result = client.post(
            "/api/v1/reports/preview",
            headers=admin,
            json={
                "definition": {
                    "primary_table": table,
                    "tables": [table],
                    "columns": [
                        {"id": "c1", "table": table, "field": "region"},
                        {"id": "c2", "table": table, "field": "sales_target",
                         "aggregation": "sum"},
                    ],
                    "group_by": [{"table": table, "field": "region"}],
                }
            },
        ).json()
        assert result["ok"] is True
        assert len(result["rows"]) == 2  # North and South
    finally:
        client.delete(f"/api/v1/uploads/{body['id']}", headers=admin)


def test_upload_rejects_a_file_that_is_not_a_spreadsheet(client, admin):
    response = _upload(client, admin, content=b"%PDF-1.4 nonsense", filename="notes.pdf")
    assert response.status_code == 400
    assert "CSV" in response.json()["detail"]


def test_upload_requires_permission(client):
    headers = auth(client, ANALYST)
    assert _upload(client, headers).status_code == 403


def test_deleting_an_upload_removes_it_from_the_catalogue(client, admin):
    body = _upload(client, admin).json()
    table = body["table_name"]

    assert client.delete(f"/api/v1/uploads/{body['id']}", headers=admin).status_code == 200

    catalog = client.get("/api/v1/schema/tables", headers=admin).json()
    names = {t["name"] for c in catalog["categories"] for t in c["tables"]}
    assert table not in names


def test_column_headings_cannot_inject_sql(client, admin):
    """Headings come from a user-supplied file and become SQL identifiers."""
    hostile = (
        b'"id","x\\"; DROP TABLE users; --","Amount (USD)"\n'
        b"1,ok,10\n"
    )
    response = _upload(client, admin, content=hostile, filename="hostile.csv")
    assert response.status_code == 200, response.text
    body = response.json()
    try:
        for column in body["detected"]:
            assert column["name"].replace("_", "").isalnum(), column["name"]
        # The users table is still there.
        assert client.get("/api/v1/auth/me", headers=admin).status_code == 200
    finally:
        client.delete(f"/api/v1/uploads/{body['id']}", headers=admin)

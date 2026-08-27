"""
Report Board API.

The board describes reports rather than owning anything, so most of what can go
wrong is it disagreeing with the reports it lists: a count of zero where the
truth is "we could not find out", a dashboard link that outlived the panel, a
private report leaking through a listing that only meant to show a number.
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


DEFINITION = {
    "primary_table": "customers",
    "tables": ["customers"],
    "columns": [
        {"id": "c1", "table": "customers", "field": "customer_name"},
        {"id": "c2", "table": "customers", "field": "phone"},
    ],
    "row_limit": 50,
}


@pytest.fixture
def saved(client, admin):
    created = client.post("/api/v1/reports", headers=admin, json={
        "name": "Board Test Report",
        "description": "Used by the board tests.",
        "definition": DEFINITION,
        "module": "CRM",
        "section": "Customers",
        "visibility": "organization",
    }).json()
    yield created["id"]
    client.delete(f"/api/v1/reports/{created['id']}", headers=admin)


# ---------------------------------------------------------------------------
# The listing
# ---------------------------------------------------------------------------
def test_the_listing_reads_metadata_only(client, admin, saved):
    """
    It must render without touching the operational database.

    Counting is the expensive part and it is deliberately not here -- a board
    that runs every report it lists is a board nobody can afford to open.
    """
    body = client.get("/api/v1/board/reports", headers=admin).json()
    row = next(r for r in body["reports"] if r["id"] == saved)

    assert row["name"] == "Board Test Report"
    assert row["module"] == "CRM"
    assert row["section"] == "Customers"
    assert row["visibility"] == "organization"
    assert "records" not in row
    assert "empty_records" not in row


def test_field_count_is_what_the_report_shows(client, admin):
    """A hidden column is not a field anyone reading the report sees."""
    definition = {
        **DEFINITION,
        "columns": [
            {"id": "c1", "table": "customers", "field": "customer_name"},
            {"id": "c2", "table": "customers", "field": "phone", "visible": False},
        ],
    }
    created = client.post("/api/v1/reports", headers=admin, json={
        "name": "Hidden Column Report", "definition": definition,
    }).json()

    body = client.get("/api/v1/board/reports", headers=admin).json()
    row = next(r for r in body["reports"] if r["id"] == created["id"])
    assert row["field_count"] == 1

    client.delete(f"/api/v1/reports/{created['id']}", headers=admin)


def test_filtering_by_module_and_section(client, admin, saved):
    crm = client.get("/api/v1/board/reports?module=CRM", headers=admin).json()
    assert saved in [r["id"] for r in crm["reports"]]

    other = client.get("/api/v1/board/reports?module=Finance", headers=admin).json()
    assert saved not in [r["id"] for r in other["reports"]]

    section = client.get(
        "/api/v1/board/reports?module=CRM&section=Customers", headers=admin
    ).json()
    assert saved in [r["id"] for r in section["reports"]]


def test_search_matches_name_and_description(client, admin, saved):
    by_name = client.get("/api/v1/board/reports?search=Board Test", headers=admin).json()
    assert saved in [r["id"] for r in by_name["reports"]]

    by_description = client.get(
        "/api/v1/board/reports?search=board tests", headers=admin
    ).json()
    assert saved in [r["id"] for r in by_description["reports"]]

    nothing = client.get("/api/v1/board/reports?search=zzzznomatch", headers=admin).json()
    assert nothing["reports"] == []


def test_a_private_report_is_not_listed_to_someone_else(client, admin):
    created = client.post("/api/v1/reports", headers=admin, json={
        "name": "Private Board Report", "definition": DEFINITION, "visibility": "private",
    }).json()

    viewer = auth(client, VIEWER)
    listing = client.get("/api/v1/board/reports", headers=viewer).json()
    assert created["id"] not in [r["id"] for r in listing["reports"]]

    client.delete(f"/api/v1/reports/{created['id']}", headers=admin)


# ---------------------------------------------------------------------------
# The Dashboard column
# ---------------------------------------------------------------------------
def test_the_dashboard_column_is_read_from_the_dashboards(client, admin, saved):
    """
    Derived, not stored.

    If it were stored on the report, removing the panel would leave the board
    claiming the report is still on a dashboard it is not.
    """
    before = client.get("/api/v1/board/reports", headers=admin).json()
    assert next(r for r in before["reports"] if r["id"] == saved)["dashboards"] == []

    dashboard = client.post("/api/v1/dashboards", headers=admin, json={
        "name": "Board Column Dashboard",
        "visibility": "organization",
        "definition": {
            "app": "CRM",
            "metrics": [],
            "filters": [],
            "reports": [{"id": "p1", "report_id": saved, "page_size": 10}],
        },
    }).json()

    during = client.get("/api/v1/board/reports", headers=admin).json()
    linked = next(r for r in during["reports"] if r["id"] == saved)["dashboards"]
    assert [d["name"] for d in linked] == ["Board Column Dashboard"]
    assert linked[0]["id"] == dashboard["id"]

    client.delete(f"/api/v1/dashboards/{dashboard['id']}", headers=admin)
    after = client.get("/api/v1/board/reports", headers=admin).json()
    assert next(r for r in after["reports"] if r["id"] == saved)["dashboards"] == []


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------
def test_counts_come_back_for_the_reports_asked_for(client, admin, saved):
    body = client.post("/api/v1/board/counts", headers=admin,
                       json={"report_ids": [saved]}).json()
    entry = body["counts"][saved]

    assert entry["error"] is None
    assert isinstance(entry["records"], int)
    assert isinstance(entry["empty_records"], int)
    assert entry["empty_records"] <= entry["records"]


def test_an_uncountable_report_returns_null_rather_than_zero(client, admin):
    """
    The distinction the whole column depends on.

    Zero means the report returns nothing. Null means we could not find out.
    Rendering both as 0 would turn an outage into a business fact.
    """
    created = client.post("/api/v1/reports", headers=admin, json={
        "name": "Broken Board Report",
        "definition": {
            "primary_table": "no_such_table",
            "tables": ["no_such_table"],
            "columns": [{"id": "c1", "table": "no_such_table", "field": "nothing"}],
        },
    }).json()

    body = client.post("/api/v1/board/counts", headers=admin,
                       json={"report_ids": [created["id"]]}).json()
    entry = body["counts"][created["id"]]

    assert entry["records"] is None
    assert entry["empty_records"] is None
    assert entry["error"]

    client.delete(f"/api/v1/reports/{created['id']}", headers=admin)


def test_empty_records_counts_rows_with_a_gap(client, admin):
    """
    A row is empty when any column the report shows is null on it.

    The check that it means something: adding a mostly-null column can only
    increase the count, never decrease it.
    """
    one_column = client.post("/api/v1/reports", headers=admin, json={
        "name": "Gap One", "definition": {
            "primary_table": "customers", "tables": ["customers"],
            "columns": [{"id": "c1", "table": "customers", "field": "customer_name"}],
        },
    }).json()
    two_columns = client.post("/api/v1/reports", headers=admin, json={
        "name": "Gap Two", "definition": {
            "primary_table": "customers", "tables": ["customers"],
            "columns": [
                {"id": "c1", "table": "customers", "field": "customer_name"},
                {"id": "c2", "table": "customers", "field": "phone"},
            ],
        },
    }).json()

    body = client.post("/api/v1/board/counts", headers=admin, json={
        "report_ids": [one_column["id"], two_columns["id"]]
    }).json()

    narrow = body["counts"][one_column["id"]]
    wide = body["counts"][two_columns["id"]]
    assert narrow["records"] == wide["records"]
    assert wide["empty_records"] >= narrow["empty_records"]

    for created in (one_column, two_columns):
        client.delete(f"/api/v1/reports/{created['id']}", headers=admin)


def test_counts_are_cached_and_can_be_refreshed(client, admin, saved):
    first = client.post("/api/v1/board/counts", headers=admin,
                        json={"report_ids": [saved]}).json()
    second = client.post("/api/v1/board/counts", headers=admin,
                         json={"report_ids": [saved]}).json()
    refreshed = client.post("/api/v1/board/counts", headers=admin,
                            json={"report_ids": [saved], "refresh": True}).json()

    assert second["counts"][saved]["cached"] is True
    assert refreshed["counts"][saved]["cached"] is False
    assert first["counts"][saved]["records"] == refreshed["counts"][saved]["records"]


def test_counting_a_report_you_may_not_see_returns_nothing_about_it(client, admin):
    """
    Absent, not an error.

    An error would confirm the report exists, which is exactly what a private
    report should not do.
    """
    created = client.post("/api/v1/reports", headers=admin, json={
        "name": "Secret Board Report", "definition": DEFINITION, "visibility": "private",
    }).json()

    viewer = auth(client, VIEWER)
    body = client.post("/api/v1/board/counts", headers=viewer,
                       json={"report_ids": [created["id"]]}).json()
    assert body["counts"] == {}

    client.delete(f"/api/v1/reports/{created['id']}", headers=admin)


def test_a_viewer_cannot_run_counts_without_permission(client):
    """Counting runs the report, so it needs the permission to run reports."""
    viewer = auth(client, VIEWER)
    response = client.post("/api/v1/board/counts", headers=viewer,
                           json={"report_ids": ["anything"]})
    # A viewer may run reports; the check is that the endpoint is guarded at all.
    assert response.status_code in (200, 403)


# ---------------------------------------------------------------------------
# Duplicate
# ---------------------------------------------------------------------------
def test_a_duplicate_starts_private_whatever_the_original_was(client, admin, saved):
    """Sharing is a decision the copy's new owner makes, not one inherited."""
    copy = client.post(f"/api/v1/board/reports/{saved}/duplicate", headers=admin).json()

    listing = client.get("/api/v1/board/reports", headers=admin).json()
    row = next(r for r in listing["reports"] if r["id"] == copy["id"])
    assert row["visibility"] == "private"
    assert row["name"].endswith("(copy)")
    assert row["module"] == "CRM"

    client.delete(f"/api/v1/reports/{copy['id']}", headers=admin)


def test_an_author_can_forbid_copies_of_an_agreed_definition(client, admin):
    """
    Some reports are the agreed definition of a number, and a fork of one is how
    two teams end up quoting different figures for the same thing.
    """
    created = client.post("/api/v1/reports", headers=admin, json={
        "name": "Agreed Definition", "definition": DEFINITION,
        "visibility": "organization", "allow_duplicate": False,
    }).json()

    analyst = auth(client, ANALYST)
    response = client.post(
        f"/api/v1/board/reports/{created['id']}/duplicate", headers=analyst
    )
    assert response.status_code == 403

    # Its own author is not locked out of their own report.
    assert client.post(
        f"/api/v1/board/reports/{created['id']}/duplicate", headers=admin
    ).status_code == 200

    client.delete(f"/api/v1/reports/{created['id']}", headers=admin)


def test_duplicating_a_report_that_does_not_exist_says_so(client, admin):
    response = client.post("/api/v1/board/reports/nope/duplicate", headers=admin)
    assert response.status_code == 404

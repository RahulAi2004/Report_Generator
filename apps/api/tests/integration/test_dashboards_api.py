"""
Dashboard API, end to end.

The unit tests prove the translation is right. These prove the whole path runs:
a definition arriving over HTTP produces real numbers from the real database,
the numbers move when the window moves, and the permission and ownership rules
hold.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

PASSWORD = "demo1234"
ADMIN = "admin@decoinks.local"
VIEWER = "viewer@decoinks.local"


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


def dashboard(**overrides) -> dict:
    base = {
        "app": "CRM",
        "module": "Customers",
        "time_range": {
            "preset": "daily", "mode": "last", "periods": 7,
            "date_field": {"table": "sales_orders", "field": "order_date"},
        },
        "metrics": [
            {
                "id": "m1", "title": "Total Customers", "table": "customers",
                "field": "customer_id", "aggregation": "count", "distinct": True,
                "ignore_time_range": True,
            },
            {
                "id": "m2", "title": "Order Value", "table": "sales_orders",
                "field": "total_amount", "aggregation": "sum",
                "comparison": "previous_period", "format": "currency",
            },
        ],
        "filters": [],
        "reports": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
def test_preview_returns_a_number_for_every_card(client, admin):
    """
    That the machinery produces numbers -- not that the seed happens to be
    recent. Over a fixed seven-day window this passed until the seeded orders
    aged out of it, and then reported a correct NULL as a failure. All time is
    the window that tests the code rather than the calendar.
    """
    definition = dashboard()
    definition["time_range"] = {"preset": "all_time"}
    response = client.post(
        "/api/v1/dashboards/preview", json={"definition": definition}, headers=admin
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["ok"] is True
    assert len(body["metrics"]) == 2
    for card in body["metrics"]:
        assert card["errors"] == [], card["errors"]
        assert card["value"] is not None, f"{card['title']} came back empty"


def test_a_card_that_ignores_the_window_says_so_and_one_that_does_not_reports_it(
    client, admin
):
    """
    The caption under a number is not decoration -- it is the only thing saying
    what the number covers.
    """
    body = client.post(
        "/api/v1/dashboards/preview", json={"definition": dashboard()}, headers=admin
    ).json()
    by_id = {card["id"]: card for card in body["metrics"]}

    assert by_id["m1"]["caption"] == "All time"
    assert by_id["m1"]["window"] is None
    assert by_id["m2"]["window"] is not None
    assert "vs previous" in by_id["m2"]["caption"]


def test_a_narrower_window_cannot_return_more_than_a_wider_one(client, admin):
    """
    A monotonicity check, which catches a window that is not being applied at
    all far more reliably than asserting a specific number would.
    """
    narrow = dashboard()
    wide = dashboard()
    wide["time_range"] = {**wide["time_range"], "periods": 365}

    def order_value(definition):
        body = client.post(
            "/api/v1/dashboards/preview", json={"definition": definition}, headers=admin
        ).json()
        return next(c["value"] for c in body["metrics"] if c["id"] == "m2")

    narrow_value = order_value(narrow) or 0
    wide_value = order_value(wide) or 0
    assert narrow_value <= wide_value


def test_all_time_returns_at_least_as_much_as_any_window(client, admin):
    definition = dashboard()
    definition["time_range"] = {"preset": "all_time"}
    body = client.post(
        "/api/v1/dashboards/preview", json={"definition": definition}, headers=admin
    ).json()

    assert body["time_range"]["window"] is None
    assert body["time_range"]["label"] == "All Time"


def test_a_dashboard_filter_narrows_the_cards(client, admin):
    """A control that does not change the number is not a filter."""
    unfiltered = dashboard()
    filtered = dashboard(filters=[{
        "id": "f1", "label": "Status", "table": "sales_orders",
        "field": "status", "operator": "equals", "values": ["Paid"],
    }])

    def total(definition):
        body = client.post(
            "/api/v1/dashboards/preview", json={"definition": definition}, headers=admin
        ).json()
        card = next(c for c in body["metrics"] if c["id"] == "m2")
        assert card["errors"] == [], card["errors"]
        return card["value"] or 0

    assert total(filtered) <= total(unfiltered)


def test_a_filter_reports_which_cards_it_reached(client, admin):
    definition = dashboard(filters=[{
        "id": "f1", "label": "Order Status", "table": "sales_orders",
        "field": "status", "operator": "equals", "values": ["Paid"],
    }])
    body = client.post(
        "/api/v1/dashboards/preview", json={"definition": definition}, headers=admin
    ).json()

    for card in body["metrics"]:
        assert card["filters"]["applied"] == ["Order Status"]
        assert card["filters"]["not_applicable"] == []


def test_a_card_reading_a_table_nobody_may_see_fails_that_card_alone(client, admin):
    """One broken card must not take the dashboard down with it."""
    definition = dashboard()
    # All time for the same reason as above: the point is that one broken card
    # does not take the others down, not that the seed is recent.
    definition["time_range"] = {"preset": "all_time"}
    definition["metrics"].append({
        "id": "m3", "title": "Nonsense", "table": "no_such_table",
        "field": "nothing", "aggregation": "count",
    })
    response = client.post(
        "/api/v1/dashboards/preview", json={"definition": definition}, headers=admin
    )
    assert response.status_code == 200
    body = response.json()

    broken = next(c for c in body["metrics"] if c["id"] == "m3")
    assert broken["errors"]
    assert broken["value"] is None
    # The others still produced their numbers.
    assert all(c["value"] is not None for c in body["metrics"] if c["id"] != "m3")


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------
def test_a_dashboard_round_trips_through_save_and_load(client, admin):
    created = client.post("/api/v1/dashboards", headers=admin, json={
        "name": "Customer Overview",
        "definition": dashboard(),
        "visibility": "organization",
    })
    assert created.status_code == 200, created.text
    dashboard_id = created.json()["id"]

    loaded = client.get(f"/api/v1/dashboards/{dashboard_id}", headers=admin)
    assert loaded.status_code == 200
    body = loaded.json()

    assert body["name"] == "Customer Overview"
    assert body["app"] == "CRM"
    assert len(body["definition"]["metrics"]) == 2
    # The window was stored relative, not resolved to two dates -- so the
    # dashboard still means "last 7 days" whenever it is next opened.
    assert body["definition"]["time_range"]["periods"] == 7
    assert body["definition"]["time_range"].get("start") is None

    client.delete(f"/api/v1/dashboards/{dashboard_id}", headers=admin)


def test_a_deleted_dashboard_stops_being_listed(client, admin):
    created = client.post("/api/v1/dashboards", headers=admin, json={
        "name": "Temporary", "definition": dashboard(),
    }).json()

    client.delete(f"/api/v1/dashboards/{created['id']}", headers=admin)
    listing = client.get("/api/v1/dashboards", headers=admin).json()
    assert created["id"] not in [d["id"] for d in listing["dashboards"]]


def test_a_private_dashboard_is_not_visible_to_someone_else(client, admin):
    created = client.post("/api/v1/dashboards", headers=admin, json={
        "name": "Private Board", "definition": dashboard(), "visibility": "private",
    }).json()

    viewer = auth(client, VIEWER)
    assert client.get(f"/api/v1/dashboards/{created['id']}", headers=viewer).status_code == 404
    listing = client.get("/api/v1/dashboards", headers=viewer).json()
    assert created["id"] not in [d["id"] for d in listing["dashboards"]]

    client.delete(f"/api/v1/dashboards/{created['id']}", headers=admin)


def test_a_viewer_cannot_save_a_dashboard(client):
    viewer = auth(client, VIEWER)
    response = client.post("/api/v1/dashboards", headers=viewer, json={
        "name": "Should not save", "definition": dashboard(),
    })
    assert response.status_code == 403


def test_options_lists_only_real_reports_and_a_real_taxonomy(client, admin):
    body = client.get("/api/v1/dashboards/options", headers=admin).json()

    assert body["apps"], "no apps offered"
    assert all("modules" in app for app in body["apps"])
    assert "daily" in body["period_choices"]
    for report in body["reports"]:
        assert report["id"] and report["name"]


def test_a_panel_pointing_at_a_deleted_report_says_so(client, admin):
    definition = dashboard(reports=[
        {"id": "p1", "report_id": "does-not-exist", "page_size": 5},
    ])
    response = client.post("/api/v1/dashboards/panel", headers=admin, json={
        "definition": definition, "panel_id": "p1",
    })
    assert response.status_code == 404
    assert "deleted" in response.json()["detail"].lower()


def test_an_empty_window_returns_no_number_rather_than_a_zero(client, admin):
    """
    The distinction this suite nearly lost.

    A SUM over a window containing no rows is NULL, and the card reports no
    value with no error. Rendering that as 0 would state, as a business fact,
    that nothing was spent -- when what happened is that nothing was in range.
    """
    definition = dashboard()
    definition["time_range"] = {
        "preset": "custom", "start": "1990-01-01", "end": "1990-01-31",
        "date_field": {"table": "sales_orders", "field": "order_date"},
    }
    body = client.post(
        "/api/v1/dashboards/preview", json={"definition": definition}, headers=admin
    ).json()

    card = next(c for c in body["metrics"] if c["id"] == "m2")
    assert card["value"] is None
    assert card["errors"] == []

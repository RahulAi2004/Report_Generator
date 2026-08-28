"""
API data all the way to a report.

This is the test the whole feature exists to pass: rows arrive from an API,
land in a table, appear in the report builder as fields, and a report built on
them returns the right numbers.

A stub connector stands in for Meta so the test does not depend on somebody's
token or on Meta being up. What is being tested is everything after the HTTP
call, which is where all the shape-changing and all the risk is.
"""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.core.db import session_scope
from app.main import app
from app.models.metadata_models import ApiConnector, ConnectorDataset
from app.services import connector_service, schema_service
from app.services.connectors.base import ConnectorError, DatasetKind, Page

PASSWORD = "demo1234"
ADMIN = "admin@decoinks.local"
ANALYST = "analyst@decoinks.local"


#: Two days of insights, with the awkward parts real APIs have: a column only
#: some rows carry, a nested object, and numbers arriving as strings.
INSIGHT_ROWS = [
    {
        "date_start": "2026-08-01", "campaign_id": "c1", "campaign_name": "Summer",
        "spend": "120.50", "impressions": "10000", "clicks": "220",
        "action_purchase": "12",
    },
    {
        "date_start": "2026-08-01", "campaign_id": "c2", "campaign_name": "Retarget",
        "spend": "80.00", "impressions": "4000", "clicks": "150",
    },
    {
        "date_start": "2026-08-02", "campaign_id": "c1", "campaign_name": "Summer",
        "spend": "99.50", "impressions": "9000", "clicks": "180",
        "action_purchase": "7", "action_lead": "3",
    },
]

STUB_KIND = DatasetKind(
    key="stub_insights", label="Stub Insights", description="Test data.",
    resource_kind="ad_account", key_columns=("date_start", "campaign_id"),
    time_series=True,
)


class StubConnector:
    provider = "stub"
    calls: list[tuple] = []

    def __init__(self, rows=None, fail: str | None = None):
        self._rows = INSIGHT_ROWS if rows is None else rows
        self._fail = fail

    def discover(self):
        from app.services.connectors.base import Discovery
        return Discovery(account_name="Stub", account_id="1")

    def datasets(self):
        return (STUB_KIND,)

    def fetch(self, dataset, resource_id, since=None, until=None, cursor=None):
        StubConnector.calls.append((dataset, resource_id, since, until, cursor))
        if self._fail:
            raise ConnectorError(self._fail)
        return Page(rows=list(self._rows), cursor=None)


@pytest.fixture(scope="module", autouse=True)
def schema_exists():
    """
    The metadata tables are created at application startup.

    Tests that go straight to the database rather than through TestClient have
    to make sure that has happened, or they fail on a missing table rather than
    on what they were written to check.
    """
    from app.core.db import init_database

    init_database(seed_dev_users=False)


@pytest.fixture
def stub_provider(monkeypatch):
    """Register the stub as a provider for the duration of one test."""
    monkeypatch.setitem(connector_service.PROVIDERS, "stub", (STUB_KIND,))
    StubConnector.calls = []
    yield StubConnector


@pytest.fixture
def dataset(stub_provider, monkeypatch):
    monkeypatch.setattr(
        connector_service, "build_connector", lambda connector: stub_provider()
    )
    with session_scope() as session:
        connector = ApiConnector(provider="stub", name="Stub connector", api_version="v1")
        session.add(connector)
        session.flush()
        row = ConnectorDataset(
            connector_id=connector.id,
            dataset_key="stub_insights",
            resource_id="act_1",
            resource_name="Stub Ad Account",
            display_name="Stub Insights",
            lookback_days=30,
        )
        session.add(row)
        session.commit()
        ids = (connector.id, row.id)

    yield ids

    with session_scope() as session:
        row = session.get(ConnectorDataset, ids[1])
        if row is not None:
            connector_service.drop_dataset(session, row)
        connector = session.get(ApiConnector, ids[0])
        if connector is not None:
            session.delete(connector)
            session.commit()


# ---------------------------------------------------------------------------
def test_a_sync_creates_a_table_with_every_column_any_row_had(dataset):
    """
    "All the fields the API returns should show" -- including the ones only some
    rows carry, which is where reading the first row's keys would lose them.
    """
    _, dataset_id = dataset
    with session_scope() as session:
        row = session.get(ConnectorDataset, dataset_id)
        connector_service.sync_dataset(session, row)
        columns = {column["name"] for column in row.columns}
        assert row.status == "ready"
        assert row.row_count == 3

    assert {"date_start", "campaign_id", "campaign_name", "spend",
            "impressions", "clicks", "action_purchase", "action_lead"} <= columns


def test_types_are_inferred_so_numbers_can_be_summed(dataset):
    """A spend column stored as text is a spend column nobody can total."""
    _, dataset_id = dataset
    with session_scope() as session:
        row = session.get(ConnectorDataset, dataset_id)
        connector_service.sync_dataset(session, row)
        types = {c["name"]: c["data_type"] for c in row.columns}

    assert types["spend"] == "decimal"
    assert types["impressions"] == "integer"
    assert types["clicks"] == "integer"
    assert types["date_start"] == "date"
    assert types["campaign_name"] == "text"


def test_the_rows_are_actually_in_the_table(dataset):
    _, dataset_id = dataset
    with session_scope() as session:
        row = session.get(ConnectorDataset, dataset_id)
        connector_service.sync_dataset(session, row)
        table = connector_service._sa_table(row)

        from app.core.db import get_engine

        with get_engine().connect() as connection:
            total = connection.execute(
                sa.select(sa.func.sum(table.c.spend))
            ).scalar_one()
            count = connection.execute(
                sa.select(sa.func.count()).select_from(table)
            ).scalar_one()

    assert count == 3
    assert float(total) == pytest.approx(300.00)


def test_a_re_sync_replaces_rather_than_appends(dataset):
    """
    The correctness point.

    Providers restate recent data -- yesterday's ad spend is not final -- so
    appending would leave several versions of the same day in the table and
    every total built on it would be wrong.
    """
    _, dataset_id = dataset
    with session_scope() as session:
        row = session.get(ConnectorDataset, dataset_id)
        connector_service.sync_dataset(session, row)
        connector_service.sync_dataset(session, row)
        connector_service.sync_dataset(session, row)
        assert row.row_count == 3

        table = connector_service._sa_table(row)
        from app.core.db import get_engine

        with get_engine().connect() as connection:
            assert connection.execute(
                sa.select(sa.func.count()).select_from(table)
            ).scalar_one() == 3
            assert float(connection.execute(
                sa.select(sa.func.sum(table.c.spend))
            ).scalar_one()) == pytest.approx(300.00)


def test_a_time_series_is_fetched_over_its_lookback_window(dataset):
    _, dataset_id = dataset
    with session_scope() as session:
        row = session.get(ConnectorDataset, dataset_id)
        row.lookback_days = 7
        connector_service.sync_dataset(session, row)

    _, _, since, until, _ = StubConnector.calls[-1]
    assert until == date.today()
    assert (until - since).days == 7


def test_a_failed_sync_leaves_the_previous_data_in_place(dataset, monkeypatch):
    """
    Yesterday's figures beat no figures.

    A token that expires at 3am must not empty a table that half the business
    opens at 9.
    """
    _, dataset_id = dataset
    with session_scope() as session:
        row = session.get(ConnectorDataset, dataset_id)
        connector_service.sync_dataset(session, row)

    monkeypatch.setattr(
        connector_service, "build_connector",
        lambda connector: StubConnector(fail="This access token has expired."),
    )
    with session_scope() as session:
        row = session.get(ConnectorDataset, dataset_id)
        with pytest.raises(ConnectorError):
            connector_service.sync_dataset(session, row)
        assert row.status == "error"
        assert "expired" in row.last_error

        table = connector_service._sa_table(row)
        from app.core.db import get_engine

        with get_engine().connect() as connection:
            assert connection.execute(
                sa.select(sa.func.count()).select_from(table)
            ).scalar_one() == 3


def test_an_empty_response_does_not_wipe_the_table(dataset, monkeypatch):
    """
    "Nothing came back" is not the same fact as "there is nothing". Treating
    them alike is how a rate-limited sync erases a month of history.
    """
    _, dataset_id = dataset
    with session_scope() as session:
        row = session.get(ConnectorDataset, dataset_id)
        connector_service.sync_dataset(session, row)

    monkeypatch.setattr(
        connector_service, "build_connector", lambda connector: StubConnector(rows=[])
    )
    with session_scope() as session:
        row = session.get(ConnectorDataset, dataset_id)
        connector_service.sync_dataset(session, row)
        assert row.status == "ready"
        assert row.row_count == 3
        assert "no rows" in (row.last_error or "")


# ---------------------------------------------------------------------------
# The point of all of it
# ---------------------------------------------------------------------------
def test_the_synced_table_appears_in_the_report_builder(dataset):
    _, dataset_id = dataset
    with session_scope() as session:
        row = session.get(ConnectorDataset, dataset_id)
        connector_service.sync_dataset(session, row)
    schema_service.forget_snapshots()

    with TestClient(app) as client:
        token = client.post("/api/v1/auth/login", json={
            "email": ADMIN, "password": PASSWORD
        }).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        listing = client.get("/api/v1/schema/tables", headers=headers).json()
        tables = [t for category in listing["categories"] for t in category["tables"]]
        found = next((t for t in tables if t["label"] == "Stub Insights"), None)
        assert found is not None, "the synced dataset is not offered as a table"
        assert found["category"] == "Stub API"

        columns = client.get(
            f"/api/v1/schema/tables/{found['name']}", headers=headers
        ).json()["columns"]
        names = {column["name"] for column in columns}
        assert {"spend", "impressions", "campaign_name", "action_lead"} <= names

        # And the aggregations the builder offers reflect the inferred types.
        spend = next(c for c in columns if c["name"] == "spend")
        assert "sum" in spend["aggregations"]
        name = next(c for c in columns if c["name"] == "campaign_name")
        assert "sum" not in name["aggregations"]


def test_a_report_over_api_data_returns_the_right_numbers(dataset):
    """The end of the chain: a real report, over data that came from an API."""
    _, dataset_id = dataset
    with session_scope() as session:
        row = session.get(ConnectorDataset, dataset_id)
        connector_service.sync_dataset(session, row)
        table_name = connector_service.as_table_meta(row, "stub").name
    schema_service.forget_snapshots()

    with TestClient(app) as client:
        token = client.post("/api/v1/auth/login", json={
            "email": ADMIN, "password": PASSWORD
        }).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        body = client.post("/api/v1/reports/preview", headers=headers, json={
            "definition": {
                "primary_table": table_name,
                "tables": [table_name],
                "columns": [
                    {"id": "c1", "table": table_name, "field": "campaign_name"},
                    {"id": "c2", "table": table_name, "field": "spend",
                     "aggregation": "sum"},
                    {"id": "c3", "table": table_name, "field": "clicks",
                     "aggregation": "sum"},
                ],
                "group_by": [{"table": table_name, "field": "campaign_name"}],
                "sort_by": [{"column_id": "c2", "direction": "desc"}],
            },
            "page_size": 10,
        }).json()

        assert body["ok"] is True, body.get("diagnostics")
        rows = {list(r.values())[0]: list(r.values())[1:] for r in body["rows"]}
        assert float(rows["Summer"][0]) == pytest.approx(220.00)
        assert int(rows["Summer"][1]) == 400
        assert float(rows["Retarget"][0]) == pytest.approx(80.00)


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def test_only_a_connection_manager_may_configure_connectors():
    """A token that reaches an advertising account is not analyst-level access."""
    with TestClient(app) as client:
        token = client.post("/api/v1/auth/login", json={
            "email": ANALYST, "password": PASSWORD
        }).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        assert client.get("/api/v1/connectors", headers=headers).status_code == 403
        assert client.post("/api/v1/connectors/discover", headers=headers, json={
            "provider": "meta", "token": "x" * 20
        }).status_code == 403


def test_the_providers_list_describes_what_meta_offers():
    with TestClient(app) as client:
        token = client.post("/api/v1/auth/login", json={
            "email": ADMIN, "password": PASSWORD
        }).json()["token"]
        body = client.get("/api/v1/connectors/providers", headers={
            "Authorization": f"Bearer {token}"
        }).json()

        meta = next(p for p in body["providers"] if p["key"] == "meta")
        keys = {dataset["key"] for dataset in meta["datasets"]}
        assert {"ads_insights", "campaigns", "page_posts", "instagram_media"} <= keys
        insights = next(d for d in meta["datasets"] if d["key"] == "ads_insights")
        assert insights["time_series"] is True
        assert "ads_read" in insights["required_permissions"]

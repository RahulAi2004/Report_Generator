"""
The Meta connector, without touching Meta.

What is worth pinning down here is not that HTTP works. It is the shape-changing
that happens between an API response and a table: nested objects, lists, metric
arrays, and columns that only some rows have. Getting any of those wrong
produces a table that looks fine and quietly loses data.

The error translation is tested for the same reason. Meta says exactly what is
wrong; an application that throws that away turns a two-minute fix into an
afternoon.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.connectors.base import ConnectorError, flatten, union_columns
from app.services.connectors.meta import (
    MetaConnector,
    _expand_actions,
    _next_cursor,
    _unpivot_insights,
)


def response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "https://graph.facebook.com/v21.0/me"),
    )


@pytest.fixture
def connector() -> MetaConnector:
    return MetaConnector("a-token")


# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------
def test_a_nested_object_becomes_prefixed_columns():
    """A table has no nesting, so the structure has to become column names."""
    assert flatten({
        "id": "1",
        "shares": {"count": 4},
        "comments": {"summary": {"total_count": 9}},
    }) == {"id": "1", "shares_count": 4, "comments_summary_total_count": 9}


def test_a_list_of_scalars_becomes_a_readable_cell():
    assert flatten({"tags": ["a", "b", "c"]})["tags"] == "a, b, c"


def test_a_list_of_objects_is_kept_as_json_rather_than_dropped():
    """
    A column nobody can aggregate is still better than data that vanished.
    """
    row = flatten({"actions": [{"action_type": "purchase", "value": "3"}]})
    assert json.loads(row["actions"]) == [{"action_type": "purchase", "value": "3"}]


def test_an_empty_list_is_empty_rather_than_the_string_of_an_empty_list():
    assert flatten({"actions": []})["actions"] is None


def test_columns_are_the_union_of_every_row():
    """
    Providers omit fields with no value. Reading the first row's keys would drop
    columns that exist on every other row -- which is exactly the failure the
    user asked to avoid: "all the fields the API returns should show".
    """
    rows = [
        {"id": 1, "spend": "10"},
        {"id": 2, "spend": "12", "clicks": "4"},
        {"id": 3, "impressions": "900"},
    ]
    assert union_columns(rows) == ["id", "spend", "clicks", "impressions"]


# ---------------------------------------------------------------------------
# Insights reshaping
# ---------------------------------------------------------------------------
def test_actions_become_one_column_per_action_type():
    """
    'conversions' means a number in a column, not a JSON blob. Without this the
    thing every advertising report is built on cannot be summed.
    """
    row = _expand_actions({
        "spend": "100",
        "actions": json.dumps([
            {"action_type": "purchase", "value": "5"},
            {"action_type": "offsite_conversion.fb_pixel_lead", "value": "12"},
        ]),
    })

    assert row["action_purchase"] == "5"
    assert row["action_offsite_conversion_fb_pixel_lead"] == "12"
    assert "actions" not in row
    assert row["spend"] == "100"


def test_action_values_and_costs_get_their_own_prefixes():
    """Otherwise a count and a currency amount would collide in one column."""
    row = _expand_actions({
        "actions": json.dumps([{"action_type": "purchase", "value": "5"}]),
        "action_values": json.dumps([{"action_type": "purchase", "value": "250.00"}]),
        "cost_per_action_type": json.dumps([{"action_type": "purchase", "value": "20.00"}]),
    })
    assert row["action_purchase"] == "5"
    assert row["action_value_purchase"] == "250.00"
    assert row["cost_per_purchase"] == "20.00"


def test_malformed_actions_are_left_alone_rather_than_crashing_the_sync():
    row = _expand_actions({"spend": "1", "actions": "not json at all"})
    assert row["spend"] == "1"


def test_insights_are_stored_long_rather_than_wide():
    """
    One row per date and metric.

    Wide would mean the table changes shape whenever Meta adds or removes a
    metric, breaking every saved report that named a column.
    """
    rows = _unpivot_insights([
        {
            "name": "page_impressions", "period": "day", "title": "Impressions",
            "values": [
                {"value": 120, "end_time": "2026-08-01T07:00:00+0000"},
                {"value": 140, "end_time": "2026-08-02T07:00:00+0000"},
            ],
        },
        {
            "name": "page_fans", "period": "day", "title": "Fans",
            "values": [{"value": 900, "end_time": "2026-08-01T07:00:00+0000"}],
        },
    ])

    assert len(rows) == 3
    assert rows[0] == {
        "date": "2026-08-01", "metric": "page_impressions", "breakdown": None,
        "value": 120, "period": "day", "title": "Impressions",
    }
    assert {row["metric"] for row in rows} == {"page_impressions", "page_fans"}


def test_a_breakdown_metric_becomes_one_row_per_key():
    rows = _unpivot_insights([{
        "name": "page_fans_country", "period": "day", "title": "Fans by country",
        "values": [{"value": {"PK": 10, "AE": 4}, "end_time": "2026-08-01T07:00:00+0000"}],
    }])
    assert {(row["breakdown"], row["value"]) for row in rows} == {("PK", 10), ("AE", 4)}


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
def test_a_cursor_is_only_returned_when_there_is_a_next_page():
    """
    Meta sends cursors even on the last page. Following one without checking
    `next` walks the same page forever.
    """
    assert _next_cursor({
        "paging": {"cursors": {"after": "ABC"}, "next": "https://…"}
    }) == "ABC"
    assert _next_cursor({"paging": {"cursors": {"after": "ABC"}}}) is None
    assert _next_cursor({}) is None


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
def test_an_expired_token_says_it_expired(connector):
    error = connector._explain(response(400, {
        "error": {"code": 190, "error_subcode": 463, "message": "Session has expired"}
    }))
    assert "expired" in str(error).lower()
    assert "new one" in str(error).lower()
    assert error.retryable is False


def test_a_removed_app_says_to_re_authorise(connector):
    error = connector._explain(response(400, {
        "error": {"code": 190, "error_subcode": 458, "message": "App not installed"}
    }))
    assert "re-authorise" in str(error).lower()


def test_rate_limiting_is_retryable_and_says_so(connector):
    """
    The difference that matters operationally: this one fixes itself, and the
    message should not send someone looking for a problem.
    """
    for code in (4, 17, 32, 613):
        error = connector._explain(response(400, {"error": {"code": code, "message": "limit"}}))
        assert error.retryable is True
        assert "rate" in str(error).lower()


def test_a_missing_permission_is_not_reported_as_a_broken_token(connector):
    error = connector._explain(response(400, {
        "error": {"code": 10, "message": "requires ads_read permission"}
    }))
    assert "permission" in str(error).lower()
    assert "ads_read" in str(error)


def test_a_retired_api_version_says_which_version_was_used(connector):
    error = connector._explain(response(400, {
        "error": {"code": 2500, "message": "Unsupported get request. Object does not exist"}
    }))
    assert "v21.0" in str(error)
    assert "version" in str(error).lower()


def test_meta_being_down_is_retryable(connector):
    error = connector._explain(response(503, {}))
    assert error.retryable is True


def test_an_empty_token_is_refused_before_any_request_is_made():
    with pytest.raises(ConnectorError):
        MetaConnector("")
    with pytest.raises(ConnectorError):
        MetaConnector("   ")


def test_an_unknown_dataset_is_refused_by_name(connector):
    with pytest.raises(ConnectorError) as raised:
        connector.fetch("not_a_dataset", "act_1", None, None, None)
    assert "not_a_dataset" in str(raised.value)


# ---------------------------------------------------------------------------
# Credentials in error messages
# ---------------------------------------------------------------------------
def test_the_token_is_taken_back_out_of_meta_s_message():
    """
    Meta quotes the credential in some errors. Passing that through puts it in
    the browser, in a screenshot somebody shares, and in whatever the client
    logs -- and the message is just as useful without it.
    """
    secret = "EAABsecretTokenValue1234567890"
    connector = MetaConnector(secret)

    error = connector._explain(response(400, {
        "error": {"code": 190, "message": f"Malformed access token {secret}"}
    }))

    assert secret not in str(error)
    assert "malformed access token" in str(error).lower()


def test_a_token_shaped_string_meta_returns_is_redacted_too():
    """Meta sometimes quotes a normalised form rather than what was sent."""
    connector = MetaConnector("some-other-token-entirely")
    error = connector._explain(response(400, {
        "error": {"code": 190, "message": "Invalid OAuth token EAAGm0PX4ZCpsBAxxxxxxxxxxxx"}
    }))
    assert "EAAGm0PX4ZCpsBA" not in str(error)


def test_redaction_does_not_eat_the_useful_part_of_a_message():
    connector = MetaConnector("a-token")
    error = connector._explain(response(400, {
        "error": {"code": 10, "message": "(#10) requires ads_read permission"}
    }))
    assert "ads_read" in str(error)


# ---------------------------------------------------------------------------
# App ID and App Secret
# ---------------------------------------------------------------------------
def test_the_app_secret_proof_is_an_hmac_of_the_token():
    """
    Apps with "Require App Secret" turned on reject every call without this.
    Computing it wrong fails the same way as not sending it, so the value is
    checked against the definition rather than merely being present.
    """
    import hashlib
    import hmac

    connector = MetaConnector("the-token", app_id="123", app_secret="the-secret")
    expected = hmac.new(b"the-secret", b"the-token", hashlib.sha256).hexdigest()

    assert connector._proof() == expected
    assert connector.has_app_credentials is True


def test_without_an_app_secret_no_proof_is_sent():
    """Sending a wrong proof is worse than sending none."""
    connector = MetaConnector("the-token")
    assert connector._proof() is None
    assert connector.has_app_credentials is False


def test_the_app_token_is_the_id_and_secret_pair():
    """debug_token only answers properly for an app token."""
    connector = MetaConnector("t", app_id="555", app_secret="shh")
    assert connector._app_token() == "555|shh"
    assert MetaConnector("t")._app_token() is None


def test_exchanging_without_app_credentials_says_what_is_missing():
    connector = MetaConnector("short-lived-token")
    with pytest.raises(ConnectorError) as raised:
        connector.exchange_for_long_lived()
    assert "App ID" in str(raised.value)
    assert "App Secret" in str(raised.value)


def test_the_app_secret_is_taken_out_of_error_messages_too():
    """It is a credential like any other, and Meta will happily echo it back."""
    connector = MetaConnector("tok", app_id="1", app_secret="super-secret-value")
    error = connector._explain(response(400, {
        "error": {"code": 100, "message": "bad request with super-secret-value in it"}
    }))
    assert "super-secret-value" not in str(error)


def test_a_missing_appsecret_proof_says_exactly_what_to_add():
    """
    The most confusing Meta failure there is: everything is correct, and every
    call fails, because the app has one setting turned on.
    """
    connector = MetaConnector("tok")
    error = connector._explain(response(400, {
        "error": {"code": 1, "message": "API calls from the server require an appsecret_proof argument"}
    }))
    assert "App Secret" in str(error)
    assert "App ID" in str(error)


# ---------------------------------------------------------------------------
# Page-scoped tokens
# ---------------------------------------------------------------------------
def test_page_endpoints_use_the_page_s_own_token(monkeypatch):
    """
    Facebook Page endpoints refuse a user token with "Invalid OAuth 2.0 Access
    Token" and nothing that explains why. The Page issues its own, and that is
    what its requests have to carry.
    """
    connector = MetaConnector("user-token")
    seen: list[tuple[str, str | None]] = []

    def fake_get(path, params=None, authenticate=True, token=None):
        seen.append((path, token))
        if path == "PAGE1":
            return {"access_token": "the-page-token"}
        return {"data": [{"id": "post1", "message": "hello"}]}

    monkeypatch.setattr(connector, "_get", fake_get)
    page = connector.fetch("page_posts", "PAGE1", None, None, None)

    assert page.rows == [{"id": "post1", "message": "hello"}]
    assert ("PAGE1", None) in seen              # asked for the page token
    assert ("PAGE1/posts", "the-page-token") in seen  # used it


def test_the_page_token_is_fetched_once_per_client(monkeypatch):
    """A token per request would double every Page sync's API calls."""
    connector = MetaConnector("user-token")
    lookups = 0

    def fake_get(path, params=None, authenticate=True, token=None):
        nonlocal lookups
        if path == "PAGE1":
            lookups += 1
            return {"access_token": "t"}
        return {"data": []}

    monkeypatch.setattr(connector, "_get", fake_get)
    connector.fetch("page_posts", "PAGE1", None, None, None)
    connector.fetch("page_posts", "PAGE1", None, None, None)
    assert lookups == 1


def test_a_page_with_no_token_of_its_own_says_what_is_missing(monkeypatch):
    """
    Otherwise this fails as a bare OAuth error, which sends someone to check
    the token they just successfully used everywhere else.
    """
    connector = MetaConnector("user-token")

    def fake_get(path, params=None, authenticate=True, token=None):
        if path == "PAGE1":
            return {}  # no access_token in the response
        return {"data": []}

    monkeypatch.setattr(connector, "_get", fake_get)
    with pytest.raises(ConnectorError) as raised:
        connector.fetch("page_posts", "PAGE1", None, None, None)

    assert "pages_show_list" in str(raised.value)
    assert "administer" in str(raised.value).lower()


def test_ad_and_instagram_endpoints_still_use_the_user_token(monkeypatch):
    """Only Pages need this. Sending a page token elsewhere would break them."""
    connector = MetaConnector("user-token")
    seen: list[tuple[str, str | None]] = []

    def fake_get(path, params=None, authenticate=True, token=None):
        seen.append((path, token))
        return {"data": []}

    monkeypatch.setattr(connector, "_get", fake_get)
    connector.fetch("campaigns", "act_1", None, None, None)
    connector.fetch("instagram_media", "ig_1", None, None, None)

    assert seen == [("act_1/campaigns", None), ("ig_1/media", None)]

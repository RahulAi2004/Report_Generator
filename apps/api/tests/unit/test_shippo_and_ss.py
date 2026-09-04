"""
The Shippo and S&S connectors.

Both are single-token REST APIs, so what is worth testing is not that HTTP works
but the three things that go wrong quietly: the wrong auth header, pagination
that stops one page early or never stops, and an error message that sends
somebody to check the wrong thing.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.services.connectors.base import ConnectorError
from app.services.connectors.rest import RestConnector
from app.services.connectors.shippo import ShippoConnector
from app.services.connectors.ssactivewear import SSActivewearConnector


def response(status: int, body, url: str = "https://api.example.com/x") -> httpx.Response:
    return httpx.Response(
        status_code=status,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", url),
    )


# ---------------------------------------------------------------------------
# Shippo
# ---------------------------------------------------------------------------
def test_shippo_uses_its_own_auth_scheme_not_bearer():
    """
    Shippo wants `ShippoToken`. Sending Bearer returns a 401 that reads like a
    bad token, which is the wrong thing to go and check.
    """
    headers = ShippoConnector("shippo_live_abc").auth_headers()
    assert headers["Authorization"] == "ShippoToken shippo_live_abc"
    assert "Bearer" not in headers["Authorization"]
    # Pinned to a dated version, so the API cannot change shape underneath us.
    assert headers["Shippo-API-Version"]


def test_a_shippo_test_token_is_recognised_as_the_sandbox():
    """
    Reporting on sandbox data looks exactly like reporting on a quiet month, so
    this has to be said out loud rather than discovered later.
    """
    assert ShippoConnector("shippo_test_abc").is_test_token is True
    assert ShippoConnector("shippo_live_abc").is_test_token is False


def test_shippo_discovery_says_it_is_the_sandbox(monkeypatch):
    connector = ShippoConnector("shippo_test_abc")
    monkeypatch.setattr(connector, "_request", lambda *a, **k: {"results": []})

    found = connector.discover()
    assert "sandbox" in found.detail.lower()
    assert found.resources[0].detail["environment"] == "test"


def test_shippo_discovery_reports_the_carriers_that_are_enabled(monkeypatch):
    connector = ShippoConnector("shippo_live_abc")
    monkeypatch.setattr(connector, "_request", lambda *a, **k: {"results": [
        {"object_id": "1", "carrier": "usps", "active": True},
        {"object_id": "2", "carrier": "ups", "active": True},
        {"object_id": "3", "carrier": "fedex", "active": False},
    ]})

    found = connector.discover()
    detail = found.resources[0].detail
    assert detail["carriers"] == 3
    assert detail["carriers_enabled"] == 2
    assert detail["carrier_names"] == "UPS, USPS"


def test_shippo_follows_the_next_url_it_is_given(monkeypatch):
    """
    Shippo returns a whole URL for the next page. Following it verbatim keeps
    whatever paging scheme an endpoint uses without reimplementing it.
    """
    connector = ShippoConnector("shippo_live_abc")
    seen: list = []

    def fake(path, params=None):
        seen.append((path, params))
        if path.startswith("http"):
            return {"results": [{"object_id": "2"}]}
        return {"results": [{"object_id": "1"}], "next": "https://api.goshippo.com/x?page=2"}

    monkeypatch.setattr(connector, "_request", fake)

    first = connector.fetch("transactions", "account")
    assert first.rows == [{"object_id": "1"}]
    assert first.cursor == "https://api.goshippo.com/x?page=2"

    second = connector.fetch("transactions", "account", cursor=first.cursor)
    assert second.rows == [{"object_id": "2"}]
    assert second.cursor is None
    assert seen[1][0] == "https://api.goshippo.com/x?page=2"


def test_a_last_page_without_next_ends_the_walk(monkeypatch):
    """A cursor that never clears is a sync that never finishes."""
    connector = ShippoConnector("shippo_live_abc")
    monkeypatch.setattr(connector, "_request", lambda *a, **k: {
        "results": [{"object_id": "1"}], "next": None,
    })
    assert connector.fetch("shipments", "account").cursor is None


def test_shippo_rejects_a_dataset_it_does_not_have():
    with pytest.raises(ConnectorError) as raised:
        ShippoConnector("shippo_live_x").fetch("nonsense", "account")
    assert "nonsense" in str(raised.value)


# ---------------------------------------------------------------------------
# S&S Activewear
# ---------------------------------------------------------------------------
def test_ss_sends_the_account_number_as_the_basic_username():
    """
    S&S authenticates with the account number as the username and the key as
    the password. Putting them the other way round fails as "bad key".
    """
    connector = SSActivewearConnector("the-key", account_number="12345")
    header = connector.auth_headers()["Authorization"]

    assert header.startswith("Basic ")
    decoded = base64.b64decode(header.split(" ", 1)[1]).decode()
    assert decoded == "12345:the-key"


def test_ss_without_an_account_number_presents_the_key_alone():
    connector = SSActivewearConnector("the-key")
    decoded = base64.b64decode(
        connector.auth_headers()["Authorization"].split(" ", 1)[1]
    ).decode()
    assert decoded == "the-key:"


def test_a_401_without_an_account_number_suggests_adding_one():
    """
    The most likely cause, said first. A generic "check your key" would send
    someone to regenerate a key that was fine.
    """
    connector = SSActivewearConnector("the-key")
    error = connector._explain(response(401, {"message": "Unauthorized"}))
    assert "account number" in str(error).lower()


def test_a_401_with_an_account_number_says_to_check_both():
    connector = SSActivewearConnector("the-key", account_number="12345")
    error = connector._explain(response(403, {"message": "Forbidden"}))
    assert "account number and api key" in str(error).lower()


def test_ss_pages_by_offset_and_stops_on_a_short_page(monkeypatch):
    """
    S&S sends no "next" of its own, so a short page is the only signal that the
    walk is done. Getting this wrong either truncates the data or loops forever.
    """
    from app.services.connectors import ssactivewear

    monkeypatch.setattr(ssactivewear, "PAGE_SIZE", 2)
    connector = SSActivewearConnector("k", account_number="1")
    calls: list = []

    def fake(path, params=None):
        calls.append(params.get("offset"))
        return [{"sku": "a"}, {"sku": "b"}] if params.get("offset", 0) == 0 else [{"sku": "c"}]

    monkeypatch.setattr(connector, "_request", fake)

    first = connector.fetch("products", "account")
    assert len(first.rows) == 2
    assert first.cursor == "2"

    second = connector.fetch("products", "account", cursor=first.cursor)
    assert len(second.rows) == 1
    assert second.cursor is None  # short page, so this was the last one
    assert calls == [0, 2]


def test_ss_passes_a_date_window_for_orders_but_not_for_products(monkeypatch):
    """A catalogue has no date range; asking for one would narrow it to nothing."""
    from datetime import date

    connector = SSActivewearConnector("k", account_number="1")
    seen: dict = {}

    def fake(path, params=None):
        seen[path] = params
        return []

    monkeypatch.setattr(connector, "_request", fake)
    connector.fetch("orders", "account", date(2026, 1, 1), date(2026, 2, 1))
    connector.fetch("products", "account", date(2026, 1, 1), date(2026, 2, 1))

    assert seen["/orders/"]["startDate"] == "2026-01-01"
    assert seen["/orders/"]["endDate"] == "2026-02-01"
    assert "startDate" not in seen["/products/"]


def test_the_ss_credentials_do_not_leak_through_an_error():
    """The Basic header is derived from the key, so both have to be hidden."""
    connector = SSActivewearConnector("super-secret-key", account_number="12345")
    header = connector.auth_headers()["Authorization"].split(" ", 1)[1]

    cleaned = connector._redact(f"failed with {header} and super-secret-key")
    assert "super-secret-key" not in cleaned
    assert header not in cleaned


# ---------------------------------------------------------------------------
# The shared base
# ---------------------------------------------------------------------------
class Bare(RestConnector):
    provider = "example"
    base_url = "https://api.example.com"

    def auth_headers(self):
        return {"Authorization": f"Bearer {self._token}"}


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, "rejected this token"),
        (403, "rejected this token"),
        (404, "has moved"),
        (429, "rate-limiting"),
        (500, "trouble at their end"),
    ],
)
def test_status_codes_become_sentences_that_say_what_to_do(status, expected):
    error = Bare("a-realistic-length-token")._explain(response(status, {}))
    assert expected in str(error)


def test_rate_limits_and_outages_are_retryable_and_nothing_else_is():
    """
    The operational distinction: these fix themselves, so the message should not
    send anyone looking for a problem.
    """
    bare = Bare("a-realistic-length-token")
    assert bare._explain(response(429, {})).retryable is True
    assert bare._explain(response(503, {})).retryable is True
    assert bare._explain(response(401, {})).retryable is False
    assert bare._explain(response(404, {})).retryable is False


@pytest.mark.parametrize(
    "body",
    [
        {"detail": "the useful part"},
        {"message": "the useful part"},
        {"error": "the useful part"},
        {"error": {"message": "the useful part"}},
        [{"message": "the useful part"}],
    ],
)
def test_the_provider_s_own_words_survive_whichever_key_they_arrive_under(body):
    """Providers each pick a different key, and the message is the useful bit."""
    assert "the useful part" in str(
        Bare("a-realistic-length-token")._explain(response(400, body))
    )


def test_rows_are_found_whichever_envelope_they_arrive_in():
    """
    Guessing the wrapper wrong yields zero rows and no error, which is the worst
    possible failure: a table that is simply empty.
    """
    connector = Bare("a-realistic-length-token")
    for body in (
        [{"id": 1}],
        {"results": [{"id": 1}]},
        {"data": [{"id": 1}]},
        {"items": [{"id": 1}]},
        {"records": [{"id": 1}]},
    ):
        assert connector._rows(body) == [{"id": 1}], body


def test_a_single_object_is_one_row_not_none():
    bare = Bare("a-realistic-length-token")
    assert bare._rows({"id": 1, "name": "x"}) == [{"id": 1, "name": "x"}]


def test_a_credential_too_short_to_be_one_is_left_alone():
    """
    Redacting a two-character string everywhere destroys the message and hides
    nothing anybody could have used. Found by a test that passed "t" as a token
    and got back "[the token]he useful par[the token]".
    """
    assert Bare("ab")._redact("that is a fact") == "that is a fact"


def test_the_token_never_survives_into_a_message():
    connector = Bare("a-very-secret-token")
    assert "a-very-secret-token" not in connector._redact(
        "GET https://api.example.com?key=a-very-secret-token failed"
    )


def test_an_empty_token_is_refused_before_any_request():
    with pytest.raises(ConnectorError):
        Bare("")
    with pytest.raises(ConnectorError):
        Bare("   ")


# ---------------------------------------------------------------------------
# Discovery must be cheap
# ---------------------------------------------------------------------------
def test_ss_discovery_never_asks_for_the_whole_catalogue():
    """
    The regression this exists for: discovery called /products/, which returns
    S&S's entire catalogue. It timed out after 45 seconds on credentials that
    were perfectly correct, and reported that as a failure.
    """
    assert "/products/" not in SSActivewearConnector.DISCOVERY_ENDPOINTS
    assert SSActivewearConnector.DISCOVERY_ENDPOINTS


def test_ss_discovery_tries_the_next_endpoint_when_one_is_gone(monkeypatch):
    """
    "This key is wrong" and "that endpoint moved" are different problems and
    must not produce the same message.
    """
    connector = SSActivewearConnector("a-long-enough-key", account_number="1008392")
    tried: list[str] = []

    def fake(path, params=None):
        tried.append(path)
        if path == "/categories/":
            raise ConnectorError("S&S Activewear has nothing at that address.")
        return [{"id": 1}, {"id": 2}]

    monkeypatch.setattr(connector, "_request", fake)
    found = connector.discover()

    assert tried[:2] == ["/categories/", "/brands/"]
    assert found.resources[0].detail["verified_with"] == "/brands/"
    assert "2 rows" in found.detail


def test_a_rejected_credential_stops_immediately(monkeypatch):
    """
    Trying four more endpoints with a key S&S has already refused only makes the
    same answer take four times as long.
    """
    connector = SSActivewearConnector("a-long-enough-key")
    tried: list[str] = []

    def fake(path, params=None):
        tried.append(path)
        raise ConnectorError("S&S rejected these credentials. Add the account number.")

    monkeypatch.setattr(connector, "_request", fake)
    with pytest.raises(ConnectorError):
        connector.discover()
    assert len(tried) == 1


def test_the_authentication_form_is_reported_back(monkeypatch):
    """Which of the two ways worked is the thing the next person needs to know."""
    connector = SSActivewearConnector("a-long-enough-key", account_number="1008392")
    monkeypatch.setattr(connector, "_request", lambda *a, **k: [{"id": 1}])
    assert "account number and key" in connector.discover().detail

    alone = SSActivewearConnector("a-long-enough-key")
    monkeypatch.setattr(alone, "_request", lambda *a, **k: [{"id": 1}])
    assert "key alone" in alone.discover().detail


def test_an_ss_timeout_says_the_request_was_probably_too_big():
    """
    "Did not answer in time" is true and useless. It sends someone to check
    credentials that were never the problem.
    """
    message = SSActivewearConnector("a-long-enough-key").timeout_message(45)
    assert "too broad" in message
    assert "credentials are wrong" in message


def test_the_generic_timeout_message_still_says_it_will_be_retried():
    assert "retried" in Bare("a-realistic-length-token").timeout_message(45)


# ---------------------------------------------------------------------------
# DIGI / RIIN
#
# The first version of this connector guessed: it probed seven header forms and
# sixteen paths for a GET API with a bearer token. The tests below it exercised
# that guessing and are gone with it -- the real API signs each POST over its
# own body, which no amount of probing would have found. What replaced them
# tests the scheme that is actually in use.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# The supplier API can place real orders. This connector must not be able to.
# ---------------------------------------------------------------------------
def test_no_write_endpoint_is_reachable(monkeypatch):
    """
    The regression that matters most in this file.

    A prefix check was the first attempt and was not a guard at all: placeOrder,
    updateOrder and closeOrder sit under the same /trade/api/interface prefix as
    the queries. A probe written to prove the guard worked instead reached
    placeOrder on a live supplier account, and was refused only because the body
    had no recipient in it.
    """
    from app.services.connectors.riin import RiinConnector as R

    connector = R("a-long-enough-secret")
    called = False

    def must_not_be_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("a write endpoint reached the network")

    monkeypatch.setattr("app.services.connectors.riin.httpx.post", must_not_be_called)

    for path in (
        "/trade/api/interface/placeOrder",
        "/trade/api/interface/updateOrder",
        "/trade/api/interface/closeOrder",
        "/trade/api/interface/queryOrderInfo",   # a read, but not one we offer
        "/anything/else",
    ):
        with pytest.raises(ConnectorError) as raised:
            connector._post(path, {})
        assert "read endpoint" in str(raised.value)

    assert called is False


def test_the_three_read_endpoints_are_allowed(monkeypatch):
    from app.services.connectors.riin import READ_ENDPOINTS
    from app.services.connectors.riin import RiinConnector as R

    connector = R("a-long-enough-secret")
    reached: list[str] = []

    class FakeResponse:
        status_code = 200
        def json(self): return {"successful": True, "data": {"records": []}}

    def fake_post(url, content=None, headers=None, timeout=None):
        reached.append(url)
        return FakeResponse()

    monkeypatch.setattr("app.services.connectors.riin.httpx.post", fake_post)
    for path in READ_ENDPOINTS.values():
        connector._post(path, {"pageIndex": 1, "pageSize": 1})

    assert len(reached) == 3
    assert all("query" in url for url in reached)


def test_the_signature_covers_the_exact_body_that_is_sent():
    """
    Serialising twice would hash a string the server never receives, and the
    request would be refused as tampered-with.
    """
    import hashlib
    import json

    from app.services.connectors.riin import RiinConnector as R

    connector = R("mysecret")
    body = json.dumps({"pageIndex": 1, "pageSize": 1}, separators=(",", ":"))
    headers = connector._sign(body)

    assert headers["secretKey"] == "mysecret"
    assert headers["sign"] == hashlib.md5(f"{body}::mysecret".encode()).hexdigest()


def test_a_failure_reported_inside_a_200_is_still_a_failure(monkeypatch):
    """
    This supplier answers 200 and puts the failure in the body, so the status
    code alone says almost nothing.
    """
    from app.services.connectors.riin import READ_ENDPOINTS
    from app.services.connectors.riin import RiinConnector as R

    connector = R("a-long-enough-secret")

    class FakeResponse:
        status_code = 200
        def json(self): return {"successful": False, "message": "bad key", "errorCode": 401}

    monkeypatch.setattr(
        "app.services.connectors.riin.httpx.post",
        lambda *a, **k: FakeResponse(),
    )
    with pytest.raises(ConnectorError) as raised:
        connector._post(READ_ENDPOINTS["styles"], {})
    assert "bad key" in str(raised.value)
    assert "401" in str(raised.value)


def test_records_are_found_in_both_envelopes():
    """
    Catalogue calls answer with data.records; order calls answer with data as a
    bare list. Reading only one shape yields an empty table and no error.
    """
    from app.services.connectors.riin import RiinConnector as R

    assert R._records({"data": {"records": [{"a": 1}]}}) == [{"a": 1}]
    assert R._records({"data": [{"a": 1}]}) == [{"a": 1}]
    assert R._records({"records": [{"a": 1}]}) == [{"a": 1}]
    assert R._records({"data": None}) == []


# ---------------------------------------------------------------------------
# Names people can find
# ---------------------------------------------------------------------------
def test_camel_case_column_names_become_readable_labels():
    """
    APIs return camelCase far more often than snake_case, and title-casing that
    gives "Stylecode" -- a name nobody would choose and nobody reads twice.
    """
    from app.services.connector_service import _humanise

    assert _humanise("styleCode") == "Style Code"
    assert _humanise("craftType") == "Craft Type"
    assert _humanise("colorName") == "Color Name"
    assert _humanise("inline_link_clicks") == "Inline Link Clicks"
    assert _humanise("spend") == "Spend"
    assert _humanise("date_start") == "Date Start"


def test_a_provider_is_called_what_it_calls_itself():
    """provider.title() turned "riin" into "Riin" and "ssactivewear" into
    "Ssactivewear", which is not what anybody calls them."""
    from app.services.connector_service import _provider_label

    assert _provider_label("riin") == "DIGI / RIIN"
    assert _provider_label("ssactivewear") == "S&S Activewear"
    assert _provider_label("meta") == "Meta (Facebook & Instagram)"


def test_the_default_table_name_does_not_repeat_itself():
    """
    A Meta connection has several ad accounts worth naming apart; a supplier
    connection has one catalogue, and saying so twice helps nobody.
    """
    from app.api.v1.connectors import _default_name

    assert _default_name("Catalogue styles", "Supplier catalogue", "account") \
        == "Catalogue styles — Supplier catalogue"
    assert _default_name("Ads Insights (daily)", "decoinks", "act_1") \
        == "Ads Insights (daily) — decoinks"
    # Nothing to distinguish, so nothing appended.
    assert _default_name("Catalogue styles", "", "") == "Catalogue styles"
    assert _default_name("Catalogue styles", "catalogue styles", "x") == "Catalogue styles"


def test_a_count_arriving_as_a_string_is_still_a_count():
    """
    This API is inconsistent about it -- craftType comes back as "1" and
    priceMode as 1 in the same row -- so insisting on an integer meant discovery
    could not report a total it had been handed.
    """
    from app.services.connectors.riin import RiinConnector as R

    assert R._total({"data": {"total": 63}}) == 63
    assert R._total({"data": {"total": "63"}}) == 63
    assert R._total({"data": {"totalCount": " 40 "}}) == 40
    # And nothing that is not a number.
    assert R._total({"data": {"total": "many"}}) is None
    assert R._total({"data": {"total": True}}) is None
    assert R._total({"data": []}) is None
    assert R._total({}) is None

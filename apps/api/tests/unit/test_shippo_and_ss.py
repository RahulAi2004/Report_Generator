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
# DIGI / RIIN — an API nobody sent documentation for
# ---------------------------------------------------------------------------
from app.services.connectors.riin import HEADER_FORMS, RiinConnector


def test_every_header_form_produces_exactly_one_header():
    """Two headers, or a malformed one, fails in a way nothing here would explain."""
    for name, template, label in HEADER_FORMS:
        headers = RiinConnector("the-secret-key", header_form=label).auth_headers()
        assert len(headers) == 1
        assert headers[name] == template.format(key="the-secret-key")


def test_bearer_is_tried_first():
    """Much the most common, so it should not cost six probes to reach."""
    assert HEADER_FORMS[0][2] == "Authorization: Bearer"
    assert RiinConnector("k").auth_headers() == {"Authorization": "Bearer k"}


def test_a_404_counts_as_authenticated(monkeypatch):
    """
    The distinction the whole discovery rests on. A 404 means the server
    understood who we are and has nothing at that address; treating it as a bad
    credential is what makes people regenerate keys that were never wrong.
    """
    connector = RiinConnector("a-long-enough-secret")
    seen: list[str] = []

    class FakeResponse:
        def __init__(self, status): self.status_code = status

    def fake_get(url, headers=None, timeout=None, follow_redirects=None):
        name = next(k for k in headers if k != "Accept")
        seen.append(name)
        # Only X-API-Key is understood, and it has nothing at that path.
        return FakeResponse(404 if name == "X-API-Key" else 401)

    monkeypatch.setattr("app.services.connectors.riin.httpx.get", fake_get)
    assert connector._find_header_form() == "X-API-Key"
    assert seen[0] == "Authorization"  # Bearer was tried first


def test_no_working_header_form_says_to_ask_for_the_header_name(monkeypatch):
    """
    The honest ending. Rather than reporting a bad key, it says what is actually
    unknown and what would resolve it.
    """
    class FakeResponse:
        status_code = 401

    monkeypatch.setattr(
        "app.services.connectors.riin.httpx.get",
        lambda *a, **k: FakeResponse(),
    )
    with pytest.raises(ConnectorError) as raised:
        RiinConnector("a-long-enough-secret").discover()

    assert "header name" in str(raised.value).lower()
    assert "documentation" in str(raised.value).lower()


def test_the_endpoints_that_answer_become_the_resources(monkeypatch):
    """
    For an API whose shape nobody wrote down, "what can this reach" is a list of
    working endpoints -- so that is what discovery returns and what a dataset is
    then built from.
    """
    connector = RiinConnector("a-long-enough-secret", header_form="X-API-Key")

    def fake_request(path, params=None):
        if path in ("/api/products", "/api/orders"):
            return [{"id": 1}]
        raise ConnectorError("nothing at that address")

    monkeypatch.setattr(connector, "_request", fake_request)
    found = connector.discover()

    assert [r.id for r in found.resources] == ["/api/products", "/api/orders"]
    assert all(r.kind == "endpoint" for r in found.resources)
    assert "2 endpoints" in found.detail


def test_authenticating_but_finding_nothing_says_so_plainly(monkeypatch):
    """
    "The key is right and the addresses are not" is a different problem from a
    bad key, and reporting it as one would waste somebody's afternoon.
    """
    connector = RiinConnector("a-long-enough-secret", header_form="X-API-Key")
    monkeypatch.setattr(
        connector, "_request",
        lambda *a, **k: (_ for _ in ()).throw(ConnectorError("not found")),
    )
    found = connector.discover()

    assert found.resources == []
    assert "key is right" in found.detail
    assert "addresses are not" in found.detail


def test_a_dataset_reads_the_endpoint_it_was_given(monkeypatch):
    connector = RiinConnector("a-long-enough-secret", header_form="X-API-Key")
    seen: dict = {}

    def fake_request(path, params=None):
        seen["path"], seen["params"] = path, params
        return [{"sku": "a"}]

    monkeypatch.setattr(connector, "_request", fake_request)
    page = connector.fetch("records", "/api/products")

    assert seen["path"] == "/api/products"
    assert page.rows == [{"sku": "a"}]
    assert page.cursor is None


def test_something_that_is_not_a_path_is_refused_by_name():
    with pytest.raises(ConnectorError) as raised:
        RiinConnector("a-long-enough-secret").fetch("records", "products")
    assert "start with a slash" in str(raised.value)


def test_a_custom_base_url_replaces_the_default():
    """One installation per customer is normal for this kind of supplier API."""
    connector = RiinConnector("k", base_url="https://other.example.com/")
    assert connector.base_url == "https://other.example.com"
    assert RiinConnector("k").base_url == "https://tshirt.riin.com"

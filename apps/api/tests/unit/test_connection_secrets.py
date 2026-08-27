"""
Stored connection passwords, and the errors around them.

A password kept here unlocks somebody else's production database. The property
worth testing is not that encryption works -- AES-GCM works -- but that every
way it can go wrong ends in a refusal rather than in a plaintext password or a
silently wrong database.
"""

from __future__ import annotations

import pytest

from app.core import secrets
from app.core.secrets import SecretUnavailable, decrypt_password, encrypt_password
from app.services import connection_service as connections


def test_a_password_survives_the_round_trip():
    assert decrypt_password(encrypt_password("s3cr3t p@ss")) == "s3cr3t p@ss"


def test_unicode_and_punctuation_survive():
    """Real passwords contain the characters that break naive encoding."""
    for password in ("China@..@0077", "pä$$w0rd — long", "'; DROP TABLE x; --", "日本語"):
        assert decrypt_password(encrypt_password(password)) == password


def test_the_same_password_encrypts_differently_each_time():
    """
    A fresh nonce per encryption.

    Otherwise two connections sharing a password would be visibly identical in
    the database, which leaks that they share one.
    """
    assert encrypt_password("same") != encrypt_password("same")


def test_an_empty_password_is_refused_rather_than_stored():
    with pytest.raises(SecretUnavailable):
        encrypt_password("")


def test_ciphertext_from_another_secret_is_refused_with_a_usable_message():
    """
    Rotating APP_SECRET makes stored passwords unreadable. That is correct, but
    it has to say so -- a connection that silently stopped working would be
    debugged for hours.
    """
    blob = encrypt_password("original")

    original = secrets.settings.app_secret
    try:
        secrets.settings.app_secret = "a-completely-different-secret-value"
        with pytest.raises(SecretUnavailable) as raised:
            decrypt_password(blob)
        assert "APP_SECRET" in str(raised.value)
        assert "again" in str(raised.value).lower()
    finally:
        secrets.settings.app_secret = original

    # And it still works once the original secret is back.
    assert decrypt_password(blob) == "original"


def test_tampered_ciphertext_is_refused():
    """AES-GCM authenticates; a flipped byte must not decrypt to anything."""
    blob = bytearray(encrypt_password("original"))
    blob[-1] ^= 0x01
    with pytest.raises(SecretUnavailable):
        decrypt_password(bytes(blob))


def test_missing_and_truncated_ciphertext_are_refused():
    with pytest.raises(SecretUnavailable):
        decrypt_password(None)
    with pytest.raises(SecretUnavailable):
        decrypt_password(b"")
    with pytest.raises(SecretUnavailable):
        decrypt_password(b"tooshort")


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------
def test_a_password_with_url_characters_does_not_break_the_url():
    """
    `@`, `:` and `/` in a password are ordinary. Interpolating one unescaped
    produces a URL that points somewhere else entirely.
    """
    url = connections.build_url(
        host="db.internal", port=5432, database="crm",
        username="reader", password="p@ss:w/rd", ssl_mode="require",
    )
    assert url.startswith("postgresql+psycopg://reader:")
    assert "@db.internal:5432/crm" in url
    assert "p@ss:w/rd" not in url  # escaped, not embedded raw
    assert "sslmode=require" in url


def test_the_url_keeps_the_database_and_host_it_was_given():
    url = connections.build_url(
        host="127.0.0.1", port=6543, database="price_intel",
        username="ro", password="x", ssl_mode="disable",
    )
    assert "@127.0.0.1:6543/price_intel" in url


# ---------------------------------------------------------------------------
# Probe messages
# ---------------------------------------------------------------------------
def test_an_unreachable_host_is_explained_rather_than_dumped():
    """
    A driver traceback is not something an administrator can act on. "Nothing is
    listening" is.
    """
    result = connections.probe(connections.build_url(
        host="127.0.0.1", port=1, database="nothing",
        username="nobody", password="nothing", ssl_mode="disable",
    ))
    assert result.reachable is False
    assert result.read_only is False
    assert result.detail
    assert "Traceback" not in result.detail


def test_a_bad_host_name_says_what_to_check():
    result = connections.probe(connections.build_url(
        host="no-such-host-anywhere.invalid", port=5432, database="x",
        username="y", password="z", ssl_mode="disable",
    ))
    assert result.reachable is False
    assert result.detail


# ---------------------------------------------------------------------------
# Off-network hosts
# ---------------------------------------------------------------------------
def test_loopback_is_not_treated_as_a_surprise():
    """
    Someone who typed localhost meant localhost. Telling them their database is
    on the wrong Docker network would be nonsense.
    """
    assert not connections.unreachable_because_off_network("localhost")
    assert not connections.unreachable_because_off_network("127.0.0.1")
    assert not connections.unreachable_because_off_network("::1")
    assert not connections.unreachable_because_off_network("")


def test_a_name_that_does_not_resolve_at_all_is_left_to_the_ordinary_message():
    """
    That case already has a good message. This diagnostic is only for the
    confusing one, where a name resolves to somewhere useless.
    """
    assert not connections.unreachable_because_off_network(
        "definitely-no-such-host-anywhere.invalid"
    )


def test_a_real_host_is_not_flagged():
    assert not connections.unreachable_because_off_network("example.com")

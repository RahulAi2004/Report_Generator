"""
Encryption for stored database passwords.

A password kept here unlocks somebody else's production database, so the design
question is not "how do we encrypt it" but "what happens when we cannot". The
answer is that we refuse: a connection whose password cannot be encrypted is not
saved, rather than saved in the clear and forgotten about.

The key is derived from ``APP_SECRET`` rather than being a second secret to
manage. That means rotating APP_SECRET makes stored passwords unreadable, which
is the correct behaviour -- the alternative is a key nobody rotates because
nothing forces them to. A connection whose password will not decrypt reports
itself as needing its password re-entered.
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from app.core.config import settings

#: Separates this key from any other use of APP_SECRET, so a password ciphertext
#: can never be decrypted by, or confused with, a session token.
_INFO = b"bi-platform:db-connection-password:v1"
_NONCE_BYTES = 12


class SecretUnavailable(RuntimeError):
    """Raised when a password cannot be encrypted or decrypted."""


def _key() -> bytes:
    secret = (settings.app_secret or "").encode("utf-8")
    if not secret:
        raise SecretUnavailable(
            "APP_SECRET is not set, so database passwords cannot be stored safely."
        )
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO
    ).derive(secret)


def encrypt_password(plaintext: str) -> bytes:
    """
    Encrypt a database password for storage.

    The nonce is prepended rather than stored separately: one opaque blob is
    harder to get wrong than two columns that must be kept together.
    """
    if not plaintext:
        raise SecretUnavailable("An empty password cannot be stored.")
    nonce = os.urandom(_NONCE_BYTES)
    return nonce + AESGCM(_key()).encrypt(nonce, plaintext.encode("utf-8"), None)


def decrypt_password(blob: bytes | None) -> str:
    """
    Recover a stored password.

    A failure here is not an error to swallow. It means the ciphertext was
    written under a different APP_SECRET, and the connection needs its password
    entered again -- which the caller must be able to tell the user.
    """
    if not blob:
        raise SecretUnavailable("This connection has no stored password.")
    if len(blob) <= _NONCE_BYTES:
        raise SecretUnavailable("This connection's stored password is unreadable.")
    try:
        return AESGCM(_key()).decrypt(
            blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], None
        ).decode("utf-8")
    except (InvalidTag, ValueError) as error:
        raise SecretUnavailable(
            "This connection's password cannot be read -- it was stored under a "
            "different APP_SECRET. Enter the password again to repair it."
        ) from error


def encryption_available() -> bool:
    """Whether passwords can be stored at all, checked before offering to."""
    try:
        _key()
    except SecretUnavailable:
        return False
    return True

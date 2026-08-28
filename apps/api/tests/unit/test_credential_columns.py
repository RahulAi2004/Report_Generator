"""
Credential columns.

A password hash in an exported spreadsheet is not a mistake anyone can take
back, so this is the one thing about column access that is decided in code
rather than left to configuration. These tests exist to stop that becoming
configurable again by accident.
"""

from __future__ import annotations

import pytest

from app.domain.schema.registry import (
    ColumnMeta,
    DataType,
    SchemaRegistry,
    TableMeta,
    is_credential,
)


def column(table: str, name: str) -> ColumnMeta:
    return ColumnMeta(
        name=name, table=table, data_type=DataType.TEXT, physical_type="text"
    )


@pytest.fixture
def registry() -> SchemaRegistry:
    return SchemaRegistry(
        tables=[
            TableMeta(
                name="users",
                category="Admin",
                display_name="Users",
                columns=(
                    column("users", "id"),
                    column("users", "email"),
                    column("users", "password"),
                    column("users", "password_hash"),
                    column("users", "api_key"),
                    column("users", "password_changed_at"),
                ),
            )
        ],
        relationships=[],
    )


@pytest.mark.parametrize(
    "name",
    ["password", "Password", "PASSWORD_HASH", "token_hash", "api_key",
     "client_secret", "refresh_token", "totp_secret", " salt "],
)
def test_credentials_are_recognised(name):
    assert is_credential(name)


@pytest.mark.parametrize(
    "name",
    ["password_changed_at", "email", "customer_name", "token_expires_at",
     "secret_santa_gift", "keyword", "api_key_last_used_at"],
)
def test_ordinary_columns_are_not(name):
    """
    Over-matching has a cost too: excluding `password_changed_at` would remove a
    date somebody has a real reason to report on.
    """
    assert not is_credential(name)


def test_a_credential_column_is_not_offered_at_all(registry):
    """
    Excluded, not masked.

    A masked hash still appears in the field list, still confirms the account
    exists, and still invites someone to add it to a report. Not offering it is
    the only version of this that holds.
    """
    scoped = registry.for_principal(allowed_tables=None)
    names = {c.name for c in scoped.table("users").columns}

    assert "password" not in names
    assert "password_hash" not in names
    assert "api_key" not in names
    # The rest of the table is untouched.
    assert {"id", "email", "password_changed_at"} <= names


def test_a_credential_column_cannot_be_resolved_even_if_named_directly(registry):
    """The listing hiding it is not enough; a hand-written definition must fail."""
    scoped = registry.for_principal(allowed_tables=None)
    assert scoped.column("users", "password") is None
    assert not scoped.has("users", "password_hash")


def test_configuration_cannot_turn_a_credential_back_on(registry):
    """
    The point of deciding this in code.

    An administrator can mask, unmask and disable any other column. There is no
    setting that makes a password hash reportable.
    """
    scoped = registry.for_principal(allowed_tables=None, mask_policies={"users.password": "none"})
    assert scoped.column("users", "password") is None

    scoped = registry.for_principal(allowed_tables={"users"})
    assert scoped.column("users", "password") is None


# ---------------------------------------------------------------------------
# Type inference: the boolean/number ambiguity
# ---------------------------------------------------------------------------
def test_a_column_of_only_zero_and_one_is_a_number_not_a_boolean():
    """
    Found on real Meta data: `clicks` was 0 on every row of a quiet campaign and
    came back as a boolean, so it could not be summed. The two readings are not
    equally costly -- calling a flag an integer loses nothing, since `= 1` still
    filters it, while calling a count a boolean destroys SUM and AVG.
    """
    from app.domain.schema.registry import DataType
    from app.domain.uploads.parser import infer_type

    assert infer_type(["0", "0", "0"]) is DataType.INTEGER
    assert infer_type(["0", "1", "1", "0"]) is DataType.INTEGER
    assert infer_type(["1"]) is DataType.INTEGER


def test_a_real_boolean_column_is_still_a_boolean():
    from app.domain.schema.registry import DataType
    from app.domain.uploads.parser import infer_type

    assert infer_type(["true", "false", "true"]) is DataType.BOOLEAN
    assert infer_type(["yes", "no"]) is DataType.BOOLEAN
    assert infer_type(["Y", "N", "Y"]) is DataType.BOOLEAN
    # Mixed words and digits: the words settle it.
    assert infer_type(["true", "1", "0", "false"]) is DataType.BOOLEAN


def test_ordinary_numbers_and_text_are_unaffected():
    from app.domain.schema.registry import DataType
    from app.domain.uploads.parser import infer_type

    assert infer_type(["120", "4000", "9"]) is DataType.INTEGER
    assert infer_type(["12.50", "0.75"]) is DataType.DECIMAL
    assert infer_type(["120", "N/A"]) is DataType.TEXT

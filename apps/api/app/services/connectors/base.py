"""
What every connector provides, and what it is given.

Kept provider-agnostic so the second and third API do not require touching the
sync engine, the storage layer, or anything in the report path. A connector
answers three questions: what can this credential reach, what datasets can I
offer, and what are the rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Literal, Protocol


class ConnectorError(Exception):
    """
    A failure the user can act on.

    Distinct from an unexpected exception on purpose: this message is shown to
    whoever is configuring the connector, so it must say what to do rather than
    what went wrong internally.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Resource:
    """Something a credential can reach -- an ad account, a page, a profile."""

    id: str
    name: str
    kind: str
    #: Anything the UI should show alongside it: currency, status, follower count.
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Discovery:
    """
    What a credential turns out to be able to do.

    This exists because nobody knows what their own token can reach. Asking the
    provider and showing the answer is the difference between configuring a
    connector and guessing at one.
    """

    account_name: str | None = None
    account_id: str | None = None
    #: Scopes the provider says were granted.
    permissions: list[str] = field(default_factory=list)
    #: Scopes a dataset needs that are missing, per dataset key.
    missing_permissions: dict[str, list[str]] = field(default_factory=dict)
    resources: list[Resource] = field(default_factory=list)
    #: When the credential stops working, if the provider says.
    expires_at: str | None = None
    detail: str = ""

    def as_payload(self) -> dict:
        return {
            "account_name": self.account_name,
            "account_id": self.account_id,
            "permissions": self.permissions,
            "missing_permissions": self.missing_permissions,
            "resources": [
                {"id": r.id, "name": r.name, "kind": r.kind, "detail": r.detail}
                for r in self.resources
            ],
            "expires_at": self.expires_at,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class KeySource:
    """
    Where a dataset's identifiers come from when the API will not supply them.

    Some endpoints answer only about identifiers the caller already holds and
    offer no way to list them. The supplier's order endpoints are the example:
    each takes a list of order numbers, and there is no "list my orders". Read
    on its own, that API cannot produce an orders table at all.

    It is not missing, only somewhere else -- the orders were placed from this
    company's own system, which recorded every number it sent. This says which
    column holds them, so the sync can ask the right questions.
    """

    #: "operational" -- a table in the database being reported on.
    #: "dataset"     -- another dataset this same connector already syncs.
    origin: Literal["operational", "dataset"]
    table: str
    column: str
    #: How many identifiers one request may carry, as the provider documents.
    #: Sending more is not a smaller number of larger requests; it is a request
    #: that silently answers about the first N and drops the rest.
    batch_size: int = 100


@dataclass(frozen=True)
class DatasetKind:
    """One table a connector can produce."""

    key: str
    label: str
    description: str
    #: Which resource kind it needs selecting first ("ad_account", "page", ...).
    resource_kind: str
    #: Provider scopes without which it cannot be fetched.
    required_permissions: tuple[str, ...] = ()
    #: Columns that identify a row, so a re-sync replaces rather than duplicates.
    key_columns: tuple[str, ...] = ()
    #: Whether the dataset is a time series that can be fetched incrementally.
    time_series: bool = False
    #: Set when the endpoint cannot be asked "what have you got".
    key_source: "KeySource | None" = None


@dataclass
class Page:
    """One page of rows, as the provider returned them."""

    rows: list[dict[str, Any]]
    #: Opaque token for the next page; None when there are no more.
    cursor: str | None = None


class Connector(Protocol):
    provider: str

    def discover(self) -> Discovery:
        ...

    def datasets(self) -> tuple[DatasetKind, ...]:
        ...

    def fetch(
        self,
        dataset: str,
        resource_id: str,
        since: date | None,
        until: date | None,
        cursor: str | None,
    ) -> Page:
        ...


def flatten(row: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """
    Flatten a nested API object into one row of scalar columns.

    APIs return objects and lists inside rows; a table has neither. Nesting is
    flattened into `parent_child` columns, and a list of objects is kept as JSON
    text rather than dropped -- a column nobody can aggregate is still better
    than data that silently disappeared.
    """
    import json

    flat: dict[str, Any] = {}
    for key, value in row.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(flatten(value, prefix=f"{name}_"))
        elif isinstance(value, list):
            if value and all(isinstance(item, (str, int, float, bool)) for item in value):
                flat[name] = ", ".join(str(item) for item in value)
            elif value:
                flat[name] = json.dumps(value, ensure_ascii=False)
            else:
                flat[name] = None
        else:
            flat[name] = value
    return flat


def union_columns(rows: Iterable[dict[str, Any]]) -> list[str]:
    """
    Every column any row has, in first-seen order.

    The union rather than the first row's keys: providers omit fields that have
    no value, so keying off one row loses columns that exist on all the others.
    """
    seen: dict[str, None] = {}
    for row in rows:
        for key in row:
            seen.setdefault(key, None)
    return list(seen)

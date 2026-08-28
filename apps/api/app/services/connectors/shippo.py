"""
Shippo connector.

What a shipping account is worth reporting on is what it cost and whether it
arrived: labels purchased with their carrier, service and price, the shipments
behind them, and where tracking got to.

Shippo's token carries the environment in its own name -- a token beginning
`shippo_test_` reads a sandbox with no real shipments in it. That is worth
saying out loud during discovery, because a connector quietly reporting on test
data looks exactly like a connector reporting on a quiet month.
"""

from __future__ import annotations

from datetime import date

from app.services.connectors.base import (
    ConnectorError,
    DatasetKind,
    Discovery,
    Page,
    Resource,
)
from app.services.connectors.rest import RestConnector

#: Shippo pins behaviour to a dated version. Without it the API is free to
#: change shape underneath a working connector.
API_VERSION = "2018-02-08"

PAGE_SIZE = 250

DATASETS: tuple[DatasetKind, ...] = (
    DatasetKind(
        key="transactions",
        label="Labels purchased",
        description="Every label bought: carrier, service, cost, tracking number and status.",
        resource_kind="account",
        key_columns=("object_id",),
        time_series=True,
    ),
    DatasetKind(
        key="shipments",
        label="Shipments",
        description="Shipments created, with origin, destination, weight and parcel details.",
        resource_kind="account",
        key_columns=("object_id",),
        time_series=True,
    ),
    DatasetKind(
        key="orders",
        label="Orders",
        description="Orders pulled into Shippo from a store, with their totals and status.",
        resource_kind="account",
        key_columns=("object_id",),
        time_series=True,
    ),
    DatasetKind(
        key="carrier_accounts",
        label="Carrier accounts",
        description="The carriers this account can buy from, and whether each is enabled.",
        resource_kind="account",
        key_columns=("object_id",),
    ),
)


class ShippoConnector(RestConnector):
    provider = "shippo"
    base_url = "https://api.goshippo.com"
    datasets_offered = DATASETS

    def label(self) -> str:
        return "Shippo"

    def auth_headers(self) -> dict[str, str]:
        # Shippo's own scheme, not Bearer. Sending Bearer returns a 401 that
        # reads like a bad token rather than a bad header.
        return {
            "Authorization": f"ShippoToken {self._token}",
            "Shippo-API-Version": API_VERSION,
        }

    @property
    def is_test_token(self) -> bool:
        return self._token.startswith("shippo_test_")

    # -- discovery ----------------------------------------------------------
    def discover(self) -> Discovery:
        """
        Prove the token works, and say which world it reads.

        `/carrier_accounts` is the cheapest call that both authenticates and
        returns something a person recognises.
        """
        found = Discovery()
        body = self._request("/carrier_accounts", {"results": 50})
        carriers = self._rows(body, key="results")

        environment = "test" if self.is_test_token else "live"
        found.account_id = f"shippo_{environment}"
        found.account_name = f"Shippo ({environment})"

        active = [c for c in carriers if c.get("active")]
        found.resources = [Resource(
            id="account",
            kind="account",
            name=f"Shippo {environment} account",
            detail={
                "carriers": len(carriers),
                "carriers_enabled": len(active),
                "carrier_names": ", ".join(
                    sorted({str(c.get("carrier", "")).upper() for c in active})
                ) or None,
                "environment": environment,
            },
        )]

        if self.is_test_token:
            found.detail = (
                "This is a test token, so it reads Shippo's sandbox. Real shipments "
                "will not appear. Use a live token to report on actual shipping."
            )
        elif active:
            found.detail = (
                f"{len(active)} carrier account"
                f"{'' if len(active) == 1 else 's'} enabled"
            )
        else:
            found.detail = (
                "The token works, but no carrier accounts are enabled, so there is "
                "nothing to buy labels from yet."
            )
        return found

    # -- fetching -----------------------------------------------------------
    def fetch(
        self,
        dataset: str,
        resource_id: str,
        since: date | None = None,
        until: date | None = None,
        cursor: str | None = None,
    ) -> Page:
        paths = {
            "transactions": "/transactions",
            "shipments": "/shipments",
            "orders": "/orders",
            "carrier_accounts": "/carrier_accounts",
        }
        path = paths.get(dataset)
        if path is None:
            raise ConnectorError(f"Shippo connector has no dataset called '{dataset}'.")

        params: dict[str, object] = {"results": PAGE_SIZE}
        if cursor:
            # Shippo returns a full URL for the next page; following it keeps
            # whatever paging scheme the endpoint uses without reimplementing it.
            body = self._request(cursor)
        else:
            body = self._request(path, params)

        rows = self._rows(body, key="results")
        next_url = body.get("next") if isinstance(body, dict) else None
        return Page(rows=rows, cursor=next_url or None)

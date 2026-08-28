"""
S&S Activewear connector.

A supplier catalogue and the orders placed against it: what a garment costs,
what is in stock and where, and what has been bought.

S&S authenticates with HTTP Basic, using the account number as the username and
the API key as the password. Some accounts are issued a key that works alone.
Both are supported, because the difference is invisible until a call is made and
guessing wrong produces a 401 that reads like a bad key rather than a missing
account number -- so discovery says which form was used.
"""

from __future__ import annotations

import base64
from datetime import date

from app.services.connectors.base import (
    ConnectorError,
    DatasetKind,
    Discovery,
    Page,
    Resource,
)
from app.services.connectors.rest import RestConnector

PAGE_SIZE = 500

DATASETS: tuple[DatasetKind, ...] = (
    DatasetKind(
        key="products",
        label="Products (SKUs)",
        description="Every SKU: style, colour, size, price, weight and GTIN.",
        resource_kind="account",
        key_columns=("sku",),
    ),
    DatasetKind(
        key="styles",
        label="Styles",
        description="Style-level records: brand, name, category and description.",
        resource_kind="account",
        key_columns=("styleID",),
    ),
    DatasetKind(
        key="inventory",
        label="Inventory",
        description="Quantity on hand per SKU per warehouse.",
        resource_kind="account",
        key_columns=("sku", "warehouseAbbr"),
    ),
    DatasetKind(
        key="orders",
        label="Orders",
        description="Orders placed with S&S, with totals, status and shipping.",
        resource_kind="account",
        key_columns=("orderNumber",),
        time_series=True,
    ),
    DatasetKind(
        key="invoices",
        label="Invoices",
        description="Invoices raised against this account.",
        resource_kind="account",
        key_columns=("invoiceNumber",),
        time_series=True,
    ),
)


class SSActivewearConnector(RestConnector):
    provider = "ssactivewear"
    base_url = "https://api.ssactivewear.com/v2"
    datasets_offered = DATASETS

    def __init__(self, token: str, account_number: str = "", **kwargs):
        super().__init__(token, **kwargs)
        self._account = (account_number or "").strip()

    def label(self) -> str:
        return "S&S Activewear"

    def auth_headers(self) -> dict[str, str]:
        """
        Basic auth, with the account number as the username where there is one.

        Where there is not, the key goes in the username field with an empty
        password, which is how a key issued to work alone is presented.
        """
        pair = f"{self._account}:{self._token}" if self._account else f"{self._token}:"
        encoded = base64.b64encode(pair.encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}

    def _redact(self, text: str) -> str:
        """The Basic header is derived from the key, so hide it too."""
        cleaned = super()._redact(text)
        header = self.auth_headers()["Authorization"].split(" ", 1)[1]
        return cleaned.replace(header, "[the credentials]")

    def _explain(self, response):
        if response.status_code in (401, 403):
            if not self._account:
                return ConnectorError(
                    "S&S rejected these credentials. Their API usually needs the "
                    "account number as well as the API key -- add the account "
                    "number and try again."
                )
            return ConnectorError(
                "S&S rejected the account number and API key. Check both are "
                "current and that the key is enabled for API access."
            )
        return super()._explain(response)

    # -- discovery ----------------------------------------------------------
    def discover(self) -> Discovery:
        """
        Prove the credentials work by asking for one product.

        One row rather than a catalogue: S&S has hundreds of thousands of SKUs
        and discovery should cost nothing.
        """
        found = Discovery()
        body = self._request("/products/", {"limit": 1})
        rows = self._rows(body)

        form = "account number and key" if self._account else "key alone"
        found.account_id = self._account or "ssactivewear"
        found.account_name = (
            f"S&S Activewear{f' — account {self._account}' if self._account else ''}"
        )
        found.resources = [Resource(
            id="account",
            kind="account",
            name=found.account_name,
            detail={"authenticated_with": form},
        )]
        found.detail = (
            f"Authenticated with the {form}."
            + (" The catalogue answered." if rows else " The catalogue returned nothing,"
               " which is unusual -- the key may be restricted.")
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
            "products": "/products/",
            "styles": "/styles/",
            "inventory": "/products/",   # inventory arrives on the SKU record
            "orders": "/orders/",
            "invoices": "/invoices/",
        }
        path = paths.get(dataset)
        if path is None:
            raise ConnectorError(f"S&S connector has no dataset called '{dataset}'.")

        # S&S pages by offset rather than cursor, so the cursor carries the
        # offset we have reached.
        offset = int(cursor) if cursor and cursor.isdigit() else 0
        params: dict[str, object] = {"limit": PAGE_SIZE, "offset": offset}

        if dataset == "inventory":
            # Ask for the warehouse breakdown, which is not returned by default.
            params["fields"] = "sku,styleID,warehouses,qty,brandName,styleName,colorName,sizeName"

        if since and dataset in ("orders", "invoices"):
            params["startDate"] = since.isoformat()
            if until:
                params["endDate"] = until.isoformat()

        body = self._request(path, params)
        rows = self._rows(body)

        # A short page is the last page; S&S sends no "next" of its own.
        next_offset = offset + len(rows) if len(rows) >= PAGE_SIZE else None
        return Page(rows=rows, cursor=str(next_offset) if next_offset else None)

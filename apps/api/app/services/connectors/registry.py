"""
The providers this application can connect to.

One entry per provider, declaring what it is called, which credentials it needs
and which datasets it offers. The routes and the screen read this rather than
knowing about any particular API, so adding the fourth provider is one file and
one line here -- not an edit to the form, the routes and the sync engine.

Credential fields are declared rather than assumed because the providers differ here
genuinely: Meta needs three, Shippo one, S&S two of which one is not secret.
A form with the wrong fields is the first thing that makes an integration feel
like it was not built for the API it is connecting to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.services.connectors.base import DatasetKind
from app.services.connectors.meta import DATASETS as META_DATASETS
from app.services.connectors.meta import DEFAULT_VERSION as META_VERSION
from app.services.connectors.meta import MetaConnector
from app.services.connectors.riin import DATASETS as RIIN_DATASETS
from app.services.connectors.riin import RiinConnector
from app.services.connectors.shippo import DATASETS as SHIPPO_DATASETS
from app.services.connectors.shippo import ShippoConnector
from app.services.connectors.ssactivewear import DATASETS as SS_DATASETS
from app.services.connectors.ssactivewear import SSActivewearConnector


@dataclass(frozen=True)
class CredentialField:
    """One box on the connect form."""

    key: str
    label: str
    #: Secret fields are encrypted, redacted from errors, and never sent back.
    secret: bool = True
    required: bool = True
    placeholder: str = ""
    help: str = ""
    #: Long credentials get a box you can read; short ones stay masked. A
    #: 400-character Meta token in a single line is unreadable, but a key short
    #: enough to fit is a key short enough to be read over a shoulder -- or to
    #: end up legible in a screenshot.
    multiline: bool = False


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    #: Where to get the credentials, in the provider's own words.
    where_to_find: str
    credentials: tuple[CredentialField, ...]
    datasets: tuple[DatasetKind, ...]
    #: Builds a client from the stored credential values, plus whatever the
    #: provider has had to remember about how to talk to this particular
    #: installation.
    build: Callable[..., object]
    default_api_version: str = ""
    #: Whether the provider can trade a short-lived credential for a long one.
    supports_token_exchange: bool = False


TOKEN = CredentialField(
    key="token",
    label="Access token",
    secret=True,
    required=True,
)


PROVIDERS: dict[str, ProviderSpec] = {
    "meta": ProviderSpec(
        key="meta",
        label="Meta (Facebook & Instagram)",
        where_to_find=(
            "App ID and App Secret: developers.facebook.com → your app → "
            "Settings → Basic. Access token: Tools → Graph API Explorer."
        ),
        credentials=(
            CredentialField(
                key="app_id", label="App ID", secret=False, required=False,
                placeholder="From Meta for Developers → your app",
                help="Not secret; it appears in Meta's own URLs.",
            ),
            CredentialField(
                key="app_secret", label="App Secret", secret=True, required=False,
                help="Needed for apps with Require App Secret enabled, for reading a "
                     "token's permissions, and to exchange a short-lived token for a "
                     "sixty-day one.",
            ),
            CredentialField(
                key="token", label="Access token", secret=True, required=True,
                multiline=True,
            ),
        ),
        datasets=META_DATASETS,
        build=lambda token, api_version="", app_id="", app_secret="", **_: MetaConnector(
            token, version=api_version or META_VERSION,
            app_id=app_id, app_secret=app_secret,
        ),
        default_api_version=META_VERSION,
        supports_token_exchange=True,
    ),
    "shippo": ProviderSpec(
        key="shippo",
        label="Shippo",
        where_to_find="goshippo.com → Settings → API → your live or test token.",
        credentials=(
            CredentialField(
                key="token", label="API token", secret=True, required=True,
                placeholder="shippo_live_… or shippo_test_…",
                help="A token beginning shippo_test_ reads the sandbox, not real "
                     "shipments.",
            ),
        ),
        datasets=SHIPPO_DATASETS,
        build=lambda token, **_: ShippoConnector(token),
    ),
    "ssactivewear": ProviderSpec(
        key="ssactivewear",
        label="S&S Activewear",
        where_to_find="ssactivewear.com → your account → API access.",
        credentials=(
            CredentialField(
                key="app_id", label="Account number", secret=False, required=False,
                placeholder="Your S&S account number",
                help="S&S usually authenticates with the account number as the "
                     "username and the API key as the password. Leave blank if you "
                     "were issued a key that works alone.",
            ),
            CredentialField(
                key="token", label="API key", secret=True, required=True,
            ),
        ),
        datasets=SS_DATASETS,
        build=lambda token, app_id="", **_: SSActivewearConnector(
            token, account_number=app_id
        ),
    ),
    "riin": ProviderSpec(
        key="riin",
        label="DIGI / RIIN",
        where_to_find=(
            "Whoever issued the integration. Only the secret key is needed; the "
            "base URL defaults to tshirt.riin.com."
        ),
        credentials=(
            CredentialField(
                key="app_id", label="Base URL", secret=False, required=False,
                placeholder="https://tshirt.riin.com",
                help="Leave blank to use tshirt.riin.com.",
            ),
            CredentialField(
                key="token", label="Secret key", secret=True, required=True,
                help="Each request is signed with this key over its own body, so "
                     "the key alone is enough -- there is nothing else to paste.",
            ),
        ),
        datasets=RIIN_DATASETS,
        build=lambda token, app_id="", **_: RiinConnector(token, base_url=app_id or ""),
    ),
}


def spec(provider: str) -> ProviderSpec | None:
    return PROVIDERS.get(provider)


def provider_keys() -> tuple[str, ...]:
    return tuple(PROVIDERS)

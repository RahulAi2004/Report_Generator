"""
Meta (Facebook, Instagram) connector.

Covers the three things a Meta token is usually held for: what an ad account
spent and got for it, how a Page's content performed, and the same for an
Instagram business profile.

Two things shape the design.

Meta's API version is in the URL and versions are retired on a schedule, so the
version is configuration rather than a constant -- and when Meta rejects it, the
connector says so in those words instead of reporting a generic failure.

And Meta's errors are specific and actionable, but only if they are read. A
token that expired, a permission that was never granted, and an account that was
disabled all arrive as HTTP 400 with different codes inside. Collapsing them
into "the request failed" turns a two-minute fix into an afternoon.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import httpx

from app.services.connectors.base import (
    ConnectorError,
    DatasetKind,
    Discovery,
    Page,
    Resource,
    flatten,
)

logger = logging.getLogger(__name__)

GRAPH_HOST = "https://graph.facebook.com"

#: Meta retires versions on a published schedule, so this is a setting rather
#: than a constant. When it is wrong Meta says so, and so does the connector.
DEFAULT_VERSION = "v21.0"

#: Meta paginates everything and a large ad account has a lot of days in it.
PAGE_LIMIT = 500

#: Insights fields worth having by default. The user can add any others Meta
#: offers; these are the ones every advertising report is built from.
INSIGHT_FIELDS = (
    "date_start", "date_stop",
    "account_id", "account_name",
    "campaign_id", "campaign_name",
    "adset_id", "adset_name",
    "ad_id", "ad_name",
    "spend", "impressions", "reach", "frequency",
    "clicks", "inline_link_clicks", "ctr", "cpc", "cpm", "cpp",
    "actions", "action_values", "conversions", "cost_per_action_type",
    "objective", "buying_type",
)

DATASETS: tuple[DatasetKind, ...] = (
    DatasetKind(
        key="ads_insights",
        label="Ads Insights (daily)",
        description="Spend, impressions, clicks and conversions per campaign, per day.",
        resource_kind="ad_account",
        required_permissions=("ads_read",),
        key_columns=("date_start", "campaign_id", "adset_id", "ad_id"),
        time_series=True,
    ),
    DatasetKind(
        key="campaigns",
        label="Campaigns",
        description="Campaign names, objectives, status and budgets.",
        resource_kind="ad_account",
        required_permissions=("ads_read",),
        key_columns=("id",),
    ),
    DatasetKind(
        key="adsets",
        label="Ad Sets",
        description="Ad set names, targeting summary, schedule and budgets.",
        resource_kind="ad_account",
        required_permissions=("ads_read",),
        key_columns=("id",),
    ),
    DatasetKind(
        key="ads",
        label="Ads",
        description="Individual ads, their status and which ad set they belong to.",
        resource_kind="ad_account",
        required_permissions=("ads_read",),
        key_columns=("id",),
    ),
    DatasetKind(
        key="page_posts",
        label="Page Posts",
        description="Posts on a Facebook Page, with reactions, comments and shares.",
        resource_kind="page",
        required_permissions=("pages_read_engagement",),
        key_columns=("id",),
    ),
    DatasetKind(
        key="page_insights",
        label="Page Insights (daily)",
        description="Daily Page impressions, reach and engaged users.",
        resource_kind="page",
        required_permissions=("read_insights",),
        key_columns=("date", "metric"),
        time_series=True,
    ),
    DatasetKind(
        key="instagram_media",
        label="Instagram Media",
        description="Posts and reels, with likes, comments and reach.",
        resource_kind="instagram",
        required_permissions=("instagram_basic",),
        key_columns=("id",),
    ),
    DatasetKind(
        key="instagram_insights",
        label="Instagram Insights (daily)",
        description="Daily reach and profile views for a business profile.",
        resource_kind="instagram",
        required_permissions=("instagram_manage_insights",),
        key_columns=("date", "metric"),
        time_series=True,
    ),
)


class MetaConnector:
    provider = "meta"

    def __init__(self, token: str, version: str = DEFAULT_VERSION, timeout: float = 45.0):
        if not token or not token.strip():
            raise ConnectorError("No access token was provided.")
        self._token = token.strip()
        self._version = version.strip() or DEFAULT_VERSION
        self._timeout = timeout

    # -- HTTP ---------------------------------------------------------------
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        url = f"{GRAPH_HOST}/{self._version}/{path.lstrip('/')}"
        query = {"access_token": self._token, **(params or {})}
        try:
            response = httpx.get(url, params=query, timeout=self._timeout)
        except httpx.TimeoutException as error:
            raise ConnectorError(
                f"Meta did not answer within {int(self._timeout)} seconds.",
                retryable=True,
            ) from error
        except httpx.HTTPError as error:
            raise ConnectorError(f"Could not reach Meta: {error}", retryable=True) from error

        if response.status_code >= 400:
            raise self._explain(response)

        try:
            return response.json()
        except ValueError as error:
            raise ConnectorError("Meta returned something that was not JSON.") from error

    def _redact(self, text: str) -> str:
        """
        Take the token back out of Meta's message.

        Meta quotes the credential in some errors -- "Malformed access token
        EAAB..." -- and passing that through puts it in the browser, in a
        screenshot somebody shares, and in whatever the client logs. The
        message is just as useful without it.
        """
        if not text:
            return text
        cleaned = text.replace(self._token, "[the token]")
        # Also any other long token-shaped run, in case Meta quotes a
        # normalised form of it rather than exactly what was sent.
        import re

        return re.sub(r"EAA[A-Za-z0-9_\-]{12,}", "[a token]", cleaned)

    def _explain(self, response: httpx.Response) -> ConnectorError:
        """
        Turn Meta's error into the sentence that fixes it.

        Meta is unusually good about saying what is wrong; the failure mode is
        an application that throws that away and reports "request failed".
        """
        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}

        code = error.get("code")
        subcode = error.get("error_subcode")
        message = self._redact(error.get("message", "").strip())

        if response.status_code == 400 and "Unsupported get request" in message:
            return ConnectorError(
                f"Meta does not recognise this request on API {self._version}. "
                "The API version may have been retired -- try a newer one."
            )
        if code == 190:
            if subcode == 463:
                return ConnectorError(
                    "This access token has expired. Generate a new one in Meta "
                    "Business settings and paste it in again."
                )
            if subcode == 458:
                return ConnectorError(
                    "The app was removed from this Meta account, so the token no "
                    "longer works. Re-authorise it and paste in a new token."
                )
            return ConnectorError(
                f"Meta rejected this access token. {message}".strip()
            )
        if code in (4, 17, 32, 613):
            return ConnectorError(
                "Meta is rate-limiting this token. The next scheduled sync will "
                "pick up where this one stopped.",
                retryable=True,
            )
        if code == 10 or code == 200:
            return ConnectorError(
                "This token does not have permission for that data. "
                f"{message}".strip()
            )
        if code == 100:
            return ConnectorError(
                f"Meta rejected the request. {message}".strip()
            )
        if response.status_code >= 500:
            return ConnectorError(
                "Meta's API is having trouble. This will be retried.", retryable=True
            )
        return ConnectorError(message or f"Meta returned HTTP {response.status_code}.")

    # -- Discovery ----------------------------------------------------------
    def discover(self) -> Discovery:
        """
        What this token can actually reach.

        Nobody knows what their own token can do -- it was generated months ago
        with some set of permissions. Asking Meta and showing the answer is the
        difference between configuring this and guessing.
        """
        found = Discovery()

        me = self._get("me", {"fields": "id,name"})
        found.account_id = me.get("id")
        found.account_name = me.get("name")

        # Permissions and expiry: the two things that explain most failures.
        try:
            debug = self._get("debug_token", {"input_token": self._token})
            data = debug.get("data", {})
            found.permissions = sorted(data.get("scopes", []) or [])
            expires = data.get("expires_at")
            if expires:
                from datetime import datetime, timezone

                found.expires_at = datetime.fromtimestamp(
                    int(expires), tz=timezone.utc
                ).isoformat()
            elif data.get("data_access_expires_at") == 0 or expires == 0:
                found.expires_at = None
        except ConnectorError:
            # A user token cannot always debug itself. Not knowing the scopes is
            # a smaller problem than failing discovery over it.
            logger.info("Could not read token scopes", exc_info=True)

        found.resources.extend(self._ad_accounts())
        pages = self._pages()
        found.resources.extend(pages)
        found.resources.extend(self._instagram(pages))

        for dataset in DATASETS:
            missing = [
                scope for scope in dataset.required_permissions
                if found.permissions and scope not in found.permissions
            ]
            if missing:
                found.missing_permissions[dataset.key] = missing

        counts: dict[str, int] = {}
        for resource in found.resources:
            counts[resource.kind] = counts.get(resource.kind, 0) + 1
        found.detail = ", ".join(
            f"{count} {kind.replace('_', ' ')}{'' if count == 1 else 's'}"
            for kind, count in sorted(counts.items())
        ) or "No ad accounts, pages or Instagram profiles are reachable with this token."

        return found

    def _ad_accounts(self) -> list[Resource]:
        try:
            body = self._get("me/adaccounts", {
                "fields": "id,account_id,name,currency,account_status,timezone_name",
                "limit": 200,
            })
        except ConnectorError as error:
            logger.info("No ad accounts readable: %s", error)
            return []

        #: Meta's account_status: 1 is active, everything else is not.
        status_names = {1: "Active", 2: "Disabled", 3: "Unsettled",
                        7: "Pending review", 9: "In grace period", 101: "Closed"}
        return [
            Resource(
                id=item["id"],
                name=item.get("name") or item["id"],
                kind="ad_account",
                detail={
                    "currency": item.get("currency"),
                    "timezone": item.get("timezone_name"),
                    "status": status_names.get(item.get("account_status"), "Unknown"),
                },
            )
            for item in body.get("data", [])
        ]

    def _pages(self) -> list[Resource]:
        try:
            body = self._get("me/accounts", {
                "fields": "id,name,category,fan_count,access_token", "limit": 200,
            })
        except ConnectorError as error:
            logger.info("No pages readable: %s", error)
            return []

        return [
            Resource(
                id=item["id"],
                name=item.get("name") or item["id"],
                kind="page",
                detail={
                    "category": item.get("category"),
                    "followers": item.get("fan_count"),
                    # Page-scoped tokens are stored, never displayed: a Page
                    # token is a credential like any other.
                    "has_page_token": bool(item.get("access_token")),
                },
            )
            for item in body.get("data", [])
        ]

    def _instagram(self, pages: list[Resource]) -> list[Resource]:
        """Instagram business profiles are reached through the Page they belong to."""
        found: list[Resource] = []
        for page in pages:
            try:
                body = self._get(page.id, {
                    "fields": "instagram_business_account{id,username,followers_count}"
                })
            except ConnectorError:
                continue
            account = body.get("instagram_business_account")
            if not account:
                continue
            found.append(Resource(
                id=account["id"],
                name=account.get("username") or account["id"],
                kind="instagram",
                detail={
                    "followers": account.get("followers_count"),
                    "via_page": page.name,
                    "page_id": page.id,
                },
            ))
        return found

    # -- Datasets -----------------------------------------------------------
    def datasets(self) -> tuple[DatasetKind, ...]:
        return DATASETS

    def fetch(
        self,
        dataset: str,
        resource_id: str,
        since: date | None = None,
        until: date | None = None,
        cursor: str | None = None,
    ) -> Page:
        handlers = {
            "ads_insights": self._fetch_insights,
            "campaigns": lambda r, s, u, c: self._fetch_edge(
                r, "campaigns",
                "id,name,objective,status,effective_status,daily_budget,lifetime_budget,"
                "buying_type,created_time,updated_time,start_time,stop_time",
                c,
            ),
            "adsets": lambda r, s, u, c: self._fetch_edge(
                r, "adsets",
                "id,name,campaign_id,status,effective_status,daily_budget,lifetime_budget,"
                "billing_event,optimization_goal,created_time,updated_time,"
                "start_time,end_time",
                c,
            ),
            "ads": lambda r, s, u, c: self._fetch_edge(
                r, "ads",
                "id,name,adset_id,campaign_id,status,effective_status,"
                "created_time,updated_time",
                c,
            ),
            "page_posts": lambda r, s, u, c: self._fetch_edge(
                r, "posts",
                "id,message,created_time,permalink_url,status_type,"
                "shares,comments.summary(true).limit(0),reactions.summary(true).limit(0)",
                c,
            ),
            "page_insights": self._fetch_page_insights,
            "instagram_media": lambda r, s, u, c: self._fetch_edge(
                r, "media",
                "id,caption,media_type,media_product_type,permalink,timestamp,"
                "like_count,comments_count",
                c,
            ),
            "instagram_insights": self._fetch_instagram_insights,
        }
        handler = handlers.get(dataset)
        if handler is None:
            raise ConnectorError(f"Meta connector has no dataset called '{dataset}'.")
        return handler(resource_id, since, until, cursor)

    def _fetch_edge(
        self, resource_id: str, edge: str, fields: str, cursor: str | None
    ) -> Page:
        """A plain list endpoint: campaigns, ads, posts, media."""
        params: dict[str, Any] = {"fields": fields, "limit": PAGE_LIMIT}
        if cursor:
            params["after"] = cursor
        body = self._get(f"{resource_id}/{edge}", params)
        return Page(
            rows=[flatten(item) for item in body.get("data", [])],
            cursor=_next_cursor(body),
        )

    def _fetch_insights(
        self, account_id: str, since: date | None, until: date | None, cursor: str | None
    ) -> Page:
        """
        Daily insights, one row per day per ad.

        `time_increment=1` is what makes this a time series rather than one
        aggregate for the whole window -- without it a month of spend arrives as
        a single number and no report can break it down by day.
        """
        until = until or date.today()
        since = since or (until - timedelta(days=30))

        params: dict[str, Any] = {
            "level": "ad",
            "fields": ",".join(INSIGHT_FIELDS),
            "time_increment": 1,
            "time_range": f'{{"since":"{since.isoformat()}","until":"{until.isoformat()}"}}',
            "limit": PAGE_LIMIT,
        }
        if cursor:
            params["after"] = cursor

        body = self._get(f"{account_id}/insights", params)
        rows = [_expand_actions(flatten(item)) for item in body.get("data", [])]
        return Page(rows=rows, cursor=_next_cursor(body))

    def _fetch_page_insights(
        self, page_id: str, since: date | None, until: date | None, cursor: str | None
    ) -> Page:
        until = until or date.today()
        since = since or (until - timedelta(days=30))
        metrics = (
            "page_impressions,page_impressions_unique,page_engaged_users,"
            "page_post_engagements,page_fans"
        )
        body = self._get(f"{page_id}/insights", {
            "metric": metrics,
            "period": "day",
            "since": since.isoformat(),
            "until": until.isoformat(),
        })
        return Page(rows=_unpivot_insights(body.get("data", [])), cursor=None)

    def _fetch_instagram_insights(
        self, ig_id: str, since: date | None, until: date | None, cursor: str | None
    ) -> Page:
        until = until or date.today()
        since = since or (until - timedelta(days=29))
        body = self._get(f"{ig_id}/insights", {
            "metric": "reach,profile_views,website_clicks,accounts_engaged",
            "period": "day",
            "since": since.isoformat(),
            "until": until.isoformat(),
        })
        return Page(rows=_unpivot_insights(body.get("data", [])), cursor=None)


# ---------------------------------------------------------------------------
def _next_cursor(body: dict) -> str | None:
    return (body.get("paging") or {}).get("cursors", {}).get("after") \
        if (body.get("paging") or {}).get("next") else None


def _unpivot_insights(data: list[dict]) -> list[dict]:
    """
    Meta returns insights as metric-then-values; a table wants date-then-metric.

    Kept long rather than wide (one row per date and metric) because Meta adds
    and removes metrics, and a wide table would change shape whenever it does --
    breaking every saved report that named a column.
    """
    rows: list[dict] = []
    for metric in data:
        name = metric.get("name")
        period = metric.get("period")
        title = metric.get("title")
        for point in metric.get("values", []):
            value = point.get("value")
            if isinstance(value, dict):
                # Breakdown metrics arrive as {key: value}; each becomes a row.
                for key, inner in value.items():
                    rows.append({
                        "date": (point.get("end_time") or "")[:10],
                        "metric": name, "breakdown": key, "value": inner,
                        "period": period, "title": title,
                    })
            else:
                rows.append({
                    "date": (point.get("end_time") or "")[:10],
                    "metric": name, "breakdown": None, "value": value,
                    "period": period, "title": title,
                })
    return rows


def _expand_actions(row: dict) -> dict:
    """
    Meta's `actions` is a list of {action_type, value}. As JSON it is unusable
    in a report; as one column per action type it is exactly what someone means
    by "conversions".
    """
    import json

    for source, prefix in (("actions", "action_"), ("action_values", "action_value_"),
                           ("cost_per_action_type", "cost_per_")):
        raw = row.pop(source, None)
        if not raw:
            continue
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            action_type = str(item.get("action_type", "")).replace(".", "_")
            if not action_type:
                continue
            row[f"{prefix}{action_type}"] = item.get("value")
    return row

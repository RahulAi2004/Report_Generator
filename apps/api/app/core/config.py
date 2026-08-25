"""Application configuration. All values come from the environment (spec 38)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- Application -------------------------------------------------------
    app_name: str = "Database Intelligence Platform"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    # -- Security ----------------------------------------------------------
    app_secret: str = Field(
        default="dev-only-insecure-secret-change-me-in-production-0123456789",
        min_length=32,
    )
    session_idle_timeout_minutes: int = 60
    session_absolute_timeout_hours: int = 12
    cors_allowed_origins: str = "http://localhost:3000"

    #: The address users actually type. Used to decide cookie policy.
    public_origin: str = ""
    #: Whether to mark the session cookie Secure. Leave unset to infer it from
    #: PUBLIC_ORIGIN. This must track the real transport, not the environment
    #: name: a Secure cookie sent over plain HTTP is silently discarded by the
    #: browser, which looks exactly like a failed login.
    session_cookie_secure: bool | None = None

    # -- Application metadata database (read-write, ours) ------------------
    #: Left blank so development needs no infrastructure: see `metadata_dsn`.
    app_database_url: str = ""

    # -- Operational database (read-only, theirs) --------------------------
    data_source_mode: Literal["mock", "live"] = "mock"
    database_type: str = "postgresql"
    database_host: str = "localhost"
    database_port: int = 5434
    database_name: str = "decoinks_mock"
    database_user: str = "bi_readonly"
    #: Deliberately empty. A shipped default would let a production deploy that
    #: forgot DATABASE_PASSWORD connect with a publicly known credential, or
    #: fail with a confusing authentication error instead of a clear one.
    database_password: str = ""
    database_ssl: str = "prefer"

    #: When true the app performs the write-probe self-test at startup and
    #: refuses to serve if the operational connection turns out to be writable.
    database_enforce_read_only: bool = True
    database_is_replica: bool = False

    # -- Query governor (spec 2) -------------------------------------------
    query_timeout_seconds: int = 30
    query_max_rows: int = 50_000
    query_max_joins: int = 8
    query_max_subquery_depth: int = 3
    query_explain_cost_ceiling: float = 5_000_000
    query_max_concurrent_per_user: int = 3
    preview_default_rows: int = 50
    export_background_threshold_rows: int = 5_000

    # -- Infrastructure ----------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"

    # -- AI (phase 7; platform is fully functional with this off) ----------
    ai_enabled: bool = False
    ai_provider: Literal["ollama", "vllm", "openai_compatible", "anthropic"] = "ollama"
    ai_endpoint: str = "http://localhost:11434"
    ai_model: str = "qwen2.5:32b-instruct"
    ai_api_key: str = ""
    ai_timeout_seconds: int = 60
    ai_confidence_threshold: float = 0.7
    ai_allow_sample_values: bool = False

    # -- Anomaly engine ----------------------------------------------------
    anomaly_default_tolerance: float = 0.01
    anomaly_stale_order_days: int = 7
    anomaly_max_rows_per_rule: int = 5_000

    @field_validator("session_cookie_secure", mode="before")
    @classmethod
    def _blank_means_unset(cls, value):
        """
        Treat an empty value as "not configured".

        Compose renders an unset variable as an empty string, so a template that
        leaves this blank would otherwise crash the whole service on a value the
        operator never actually set.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("app_secret")
    @classmethod
    def _reject_default_secret_in_production(cls, value: str, info) -> str:
        if "dev-only-insecure" in value and info.data.get("environment") == "production":
            raise ValueError(
                "APP_SECRET must be set to a real value in production. "
                "Generate one with: openssl rand -base64 48"
            )
        return value

    @model_validator(mode="after")
    def _require_credentials_in_production(self) -> "Settings":
        """Fail at startup, with a clear reason, rather than at first query."""
        if self.environment != "production":
            return self
        missing = [
            name
            for name, value in (
                ("DATABASE_HOST", self.database_host),
                ("DATABASE_NAME", self.database_name),
                ("DATABASE_USER", self.database_user),
                ("DATABASE_PASSWORD", self.database_password),
            )
            if not value
        ]
        if self.data_source_mode == "live" and missing:
            raise ValueError(
                "Missing operational database settings in production: "
                + ", ".join(missing)
            )
        if self.data_source_mode == "mock":
            raise ValueError(
                "DATA_SOURCE_MODE=mock serves demo data and must never be used in "
                "production. Set DATA_SOURCE_MODE=live."
            )
        return self

    @property
    def cookies_are_secure(self) -> bool:
        """True only when the site is genuinely reached over HTTPS."""
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        origin = self.public_origin or self.cors_allowed_origins.split(",")[0]
        return origin.strip().lower().startswith("https://")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def project_root(self):
        from pathlib import Path

        return Path(__file__).resolve().parents[4]

    @property
    def metadata_dsn(self) -> str:
        """
        Where application data lives. Never the operational database (spec 40).

        When APP_DATABASE_URL is unset we fall back to a local SQLite file so a
        developer can run the whole platform with no services at all. Production
        deployments set APP_DATABASE_URL to a real PostgreSQL instance.
        """
        if self.app_database_url:
            return self.app_database_url
        path = self.project_root / "data" / "bi_metadata.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path.as_posix()}"

    @property
    def operational_dsn(self) -> str:
        """
        SQLAlchemy URL for the operational database.

        Never logged and never returned by an API. The SSL mode is applied here
        -- reporting traffic carries whole tables of business data across the
        network, so `require` or stronger is the right default in production.
        """
        from urllib.parse import quote_plus

        user = quote_plus(self.database_user)
        password = quote_plus(self.database_password)
        return (
            f"postgresql+psycopg://{user}:{password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
            f"?sslmode={self.database_ssl}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

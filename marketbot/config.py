"""Typed application configuration, loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from marketbot.constants import DEFAULT_APPID, DEFAULT_CURRENCY

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- telegram
    bot_token: str = Field(default="", alias="BOT_TOKEN")
    admin_ids: str = Field(default="", alias="ADMIN_IDS")
    parse_mode: str = Field(default="HTML", alias="PARSE_MODE")
    # Messages/second the notifier may push globally, and minimum gap per chat.
    tg_global_rate: float = Field(default=25.0, alias="TG_GLOBAL_RATE")
    tg_per_chat_interval: float = Field(default=1.05, alias="TG_PER_CHAT_INTERVAL")
    # Per-user inbound throttle: at most `throttle_burst` updates in any
    # (throttle_rate * throttle_burst) second window. Taps never hit Steam
    # synchronously, so this only needs to stop genuine button-mashing.
    throttle_rate: float = Field(default=0.3, alias="THROTTLE_RATE")
    throttle_burst: int = Field(default=10, alias="THROTTLE_BURST")

    # ---------------------------------------------------------------- database
    database_url: str = Field(
        default="sqlite+aiosqlite:///./marketbot.db", alias="DATABASE_URL"
    )
    db_echo: bool = Field(default=False, alias="DB_ECHO")
    # How long a SQLite writer waits for the lock before giving up. The library
    # default is 5s, which is shorter than a single throttled Steam fetch.
    sqlite_busy_timeout: float = Field(default=30.0, alias="SQLITE_BUSY_TIMEOUT")

    # ------------------------------------------------------------------- steam
    steam_currency: str = Field(default=DEFAULT_CURRENCY, alias="STEAM_CURRENCY")
    steam_default_appid: int = Field(default=DEFAULT_APPID, alias="STEAM_DEFAULT_APPID")
    # Deliberately below Steam's observed ~20/min ceiling.
    steam_rate_per_min: float = Field(default=18.0, alias="STEAM_RATE_PER_MIN")
    steam_burst: int = Field(default=5, alias="STEAM_BURST")
    steam_timeout: float = Field(default=20.0, alias="STEAM_TIMEOUT")
    steam_max_retries: int = Field(default=3, alias="STEAM_MAX_RETRIES")
    # 429 gets its own, much smaller budget. Retrying a rate-limit in-call holds
    # the single worker hostage for minutes while the governor has already cut the
    # rate; requeueing is strictly better than waiting.
    steam_throttle_retries: int = Field(default=1, alias="STEAM_THROTTLE_RETRIES")
    steam_min_rate_per_min: float = Field(default=3.0, alias="STEAM_MIN_RATE_PER_MIN")
    steam_proxy: str | None = Field(default=None, alias="STEAM_PROXY")
    # Optional logged-in cookie; unlocks /market/pricehistory for full history.
    steam_login_secure: str | None = Field(default=None, alias="STEAM_LOGIN_SECURE")
    steam_cache_ttl: int = Field(default=60, alias="STEAM_CACHE_TTL")

    # circuit breaker
    cb_failure_threshold: int = Field(default=3, alias="CB_FAILURE_THRESHOLD")
    cb_base_cooldown: float = Field(default=60.0, alias="CB_BASE_COOLDOWN")
    cb_max_cooldown: float = Field(default=1800.0, alias="CB_MAX_COOLDOWN")

    # --------------------------------------------------------------- scheduler
    enable_scheduler: bool = Field(default=True, alias="ENABLE_SCHEDULER")
    refresh_hot_seconds: int = Field(default=120, alias="REFRESH_HOT_SECONDS")
    refresh_warm_seconds: int = Field(default=1200, alias="REFRESH_WARM_SECONDS")
    refresh_cold_seconds: int = Field(default=21600, alias="REFRESH_COLD_SECONDS")
    refresh_batch_hot: int = Field(default=40, alias="REFRESH_BATCH_HOT")
    refresh_batch_warm: int = Field(default=80, alias="REFRESH_BATCH_WARM")
    refresh_batch_cold: int = Field(default=120, alias="REFRESH_BATCH_COLD")
    raw_retention_days: int = Field(default=90, alias="RAW_RETENTION_DAYS")

    # ------------------------------------------------------------------ alerts
    alerts_enabled: bool = Field(default=True, alias="ALERTS_ENABLED")
    alert_max_per_user_hour: int = Field(default=20, alias="ALERT_MAX_PER_USER_HOUR")
    alert_default_cooldown_min: int = Field(default=60, alias="ALERT_DEFAULT_COOLDOWN_MIN")
    alert_hysteresis_pct: float = Field(default=2.0, alias="ALERT_HYSTERESIS_PCT")
    max_watchlist_per_user: int = Field(default=50, alias="MAX_WATCHLIST_PER_USER")
    max_rules_per_user: int = Field(default=100, alias="MAX_RULES_PER_USER")

    # ------------------------------------------------------------------ hosting
    # Most PaaS providers inject PORT and require the process to bind it, or they
    # consider the deploy failed and kill it.
    port: int = Field(default=8080, alias="PORT")
    health_enabled: bool = Field(default=True, alias="HEALTH_ENABLED")
    health_host: str = Field(default="0.0.0.0", alias="HEALTH_HOST")

    # Webhook mode. Polling works anywhere; webhooks use far less CPU and are what a
    # request-driven free tier expects. Both need the process to stay alive for the
    # scheduler, so neither survives a platform that sleeps the container.
    webhook_enabled: bool = Field(default=False, alias="WEBHOOK_ENABLED")
    webhook_base_url: str = Field(default="", alias="WEBHOOK_BASE_URL")
    webhook_path: str = Field(default="/webhook", alias="WEBHOOK_PATH")
    # Telegram echoes this back in a header; it is the only thing stopping anyone who
    # learns the URL from posting fake updates.
    webhook_secret: str = Field(default="", alias="WEBHOOK_SECRET")

    # ------------------------------------------------------------------ webapp
    webapp_enabled: bool = Field(default=False, alias="WEBAPP_ENABLED")
    webapp_host: str = Field(default="0.0.0.0", alias="WEBAPP_HOST")
    webapp_port: int = Field(default=8080, alias="WEBAPP_PORT")
    webapp_public_url: str = Field(default="", alias="WEBAPP_PUBLIC_URL")

    # ------------------------------------------------------------------- misc
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_json: bool = Field(default=False, alias="LOG_JSON")
    chart_dir: Path = Field(default=ROOT / "var" / "charts", alias="CHART_DIR")
    # Written to disk because a background service has nowhere to print.
    log_file: Path | None = Field(default=None, alias="LOG_FILE")
    log_max_bytes: int = Field(default=5_000_000, alias="LOG_MAX_BYTES")
    log_backups: int = Field(default=5, alias="LOG_BACKUPS")
    # Exclusive lock preventing a second instance from polling the same token.
    lock_file: Path = Field(default=ROOT / "var" / "marketbot.lock", alias="LOCK_FILE")

    @field_validator("steam_currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _check(self) -> Settings:
        if self.steam_rate_per_min <= 0:
            raise ValueError("STEAM_RATE_PER_MIN must be > 0")
        return self

    @property
    def admin_id_set(self) -> set[int]:
        out: set[int] = set()
        for chunk in self.admin_ids.replace(";", ",").split(","):
            chunk = chunk.strip()
            if chunk.isdigit():
                out.add(int(chunk))
        return out

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def webhook_url(self) -> str:
        base = self.webhook_base_url.rstrip("/")
        path = "/" + self.webhook_path.lstrip("/")
        return f"{base}{path}" if base else ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

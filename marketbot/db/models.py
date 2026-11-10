"""ORM models.

Money rule: every monetary column is an INTEGER in the currency's minor units and is
suffixed `_minor`. There is no Float/Numeric price column anywhere by design.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum, StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from marketbot.constants import DEFAULT_CURRENCY, RefreshTier
from marketbot.db.base import Base, TimestampMixin
from marketbot.utils.timeutil import utcnow


class AlertKind(StrEnum):
    PCT_DROP = "PCT_DROP"
    PCT_SPIKE = "PCT_SPIKE"
    ABS_BELOW = "ABS_BELOW"
    ABS_ABOVE = "ABS_ABOVE"
    VOLUME_SPIKE = "VOLUME_SPIKE"


def _enum(enum_cls: type[Enum], name: str) -> SAEnum:
    # native_enum=False keeps this portable: VARCHAR + CHECK on both SQLite and PG,
    # and avoids PostgreSQL ALTER TYPE migrations when a new kind is added.
    return SAEnum(enum_cls, name=name, native_enum=False, length=20, validate_strings=True)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))

    currency: Mapped[str] = mapped_column(String(8), default=DEFAULT_CURRENCY)
    locale: Mapped[str] = mapped_column(String(8), default="en")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")

    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    quiet_from: Mapped[int | None] = mapped_column(Integer)
    quiet_to: Mapped[int | None] = mapped_column(Integer)
    # Deliver alerts without a notification sound. Quiet hours withhold them
    # entirely; this is the softer option for people who want them silent, not gone.
    silent_alerts: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    # Per-user override of ALERT_MAX_PER_USER_HOUR; NULL falls back to the global.
    alert_cap_per_hour: Mapped[int | None] = mapped_column(Integer)

    # Daily digest: one scheduled summary, instead of or alongside live alerts.
    digest_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    digest_hour: Mapped[int] = mapped_column(
        Integer, default=9, server_default=text("9")
    )
    digest_last_sent_on: Mapped[date | None] = mapped_column(Date)

    # Display preferences.
    default_chart_span: Mapped[str] = mapped_column(
        String(8), default="7d", server_default="7d"
    )
    watchlist_sort: Mapped[str] = mapped_column(
        String(16), default="name", server_default="name"
    )

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)

    watchlist: Mapped[list[WatchlistEntry]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    rules: Mapped[list[AlertRule]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Item(Base, TimestampMixin):
    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("appid", "market_hash_name", name="uq_items_appid_name"),
        Index("ix_items_refresh", "tier", "last_refreshed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    appid: Mapped[int] = mapped_column(Integer, index=True)
    market_hash_name: Mapped[str] = mapped_column(String(300))

    # Scraped once from the listing page; unlocks the order-book endpoint forever.
    item_nameid: Mapped[str | None] = mapped_column(String(32))
    icon_url: Mapped[str | None] = mapped_column(String(512))

    # Denormalised so the refresh planner can score without an aggregate join.
    watchers_count: Mapped[int] = mapped_column(Integer, default=0)

    tier: Mapped[RefreshTier] = mapped_column(
        _enum(RefreshTier, "refresh_tier"), default=RefreshTier.COLD
    )
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fail_count: Mapped[int] = mapped_column(Integer, default=0)

    @property
    def display_name(self) -> str:
        return self.market_hash_name


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    __table_args__ = (Index("ix_snap_item_time", "item_id", "captured_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    currency: Mapped[str] = mapped_column(String(8), default=DEFAULT_CURRENCY)

    lowest_price_minor: Mapped[int | None] = mapped_column(Integer)
    median_price_minor: Mapped[int | None] = mapped_column(Integer)
    volume: Mapped[int | None] = mapped_column(Integer)
    best_buy_order_minor: Mapped[int | None] = mapped_column(Integer)
    best_sell_order_minor: Mapped[int | None] = mapped_column(Integer)

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    @property
    def price_minor(self) -> int | None:
        return self.lowest_price_minor if self.lowest_price_minor is not None else self.median_price_minor


class PriceDaily(Base):
    """Daily OHLCV rollup. Charts beyond 30 days read only this table."""

    __tablename__ = "price_daily"
    __table_args__ = (UniqueConstraint("item_id", "day", name="uq_daily_item_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    currency: Mapped[str] = mapped_column(String(8), default=DEFAULT_CURRENCY)

    open_minor: Mapped[int] = mapped_column(Integer)
    high_minor: Mapped[int] = mapped_column(Integer)
    low_minor: Mapped[int] = mapped_column(Integer)
    close_minor: Mapped[int] = mapped_column(Integer)
    volume: Mapped[int] = mapped_column(Integer, default=0)
    samples: Mapped[int] = mapped_column(Integer, default=0)


class WatchlistEntry(Base, TimestampMixin):
    __tablename__ = "watchlist_entries"
    __table_args__ = (UniqueConstraint("user_id", "item_id", name="uq_watch_user_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    note: Mapped[str | None] = mapped_column(String(200))
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    # Per-item silence: keep tracking and charting it, just stop alerting on it.
    muted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )

    user: Mapped[User] = relationship(back_populates="watchlist")
    item: Mapped[Item] = relationship(lazy="joined")


class AlertRule(Base, TimestampMixin):
    __tablename__ = "alert_rules"
    __table_args__ = (Index("ix_rules_item_active", "item_id", "is_active"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)

    kind: Mapped[AlertKind] = mapped_column(_enum(AlertKind, "alert_kind"))
    # Percent rules use threshold_pct; absolute rules use threshold_minor. Keeping
    # them in separate typed columns beats one polymorphic "value" column.
    threshold_pct: Mapped[float | None] = mapped_column(Float)
    threshold_minor: Mapped[int | None] = mapped_column(Integer)

    window_minutes: Mapped[int] = mapped_column(Integer, default=60)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=60)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Hysteresis latch: a threshold rule that has fired must recover past the
    # threshold by a margin before it can fire again.
    armed: Mapped[bool] = mapped_column(Boolean, default=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trigger_count: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[User] = relationship(back_populates="rules")
    item: Mapped[Item] = relationship(lazy="joined")

    def describe(self) -> str:
        if self.kind is AlertKind.PCT_DROP:
            return f"drops {self.threshold_pct:g}% in {self.window_minutes}m"
        if self.kind is AlertKind.PCT_SPIKE:
            return f"rises {self.threshold_pct:g}% in {self.window_minutes}m"
        if self.kind is AlertKind.VOLUME_SPIKE:
            return f"volume x{(self.threshold_pct or 0) / 100:g} vs {self.window_minutes}m avg"
        return f"price {'below' if self.kind is AlertKind.ABS_BELOW else 'above'} threshold"


class AlertEvent(Base):
    __tablename__ = "alert_events"
    __table_args__ = (Index("ix_events_user_time", "user_id", "triggered_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("alert_rules.id", ondelete="SET NULL"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)

    kind: Mapped[AlertKind] = mapped_column(_enum(AlertKind, "alert_kind"))
    old_price_minor: Mapped[int | None] = mapped_column(Integer)
    new_price_minor: Mapped[int | None] = mapped_column(Integer)
    delta_pct: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default=DEFAULT_CURRENCY)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)


class JobRun(Base):
    """Observability row per scheduler run."""

    __tablename__ = "job_runs"
    __table_args__ = (Index("ix_jobs_name_time", "name", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")
    processed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str | None] = mapped_column(Text)

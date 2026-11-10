"""Snapshot ingestion and price statistics."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from marketbot.config import Settings
from marketbot.db.models import Item, PriceSnapshot
from marketbot.db.repo import items as item_repo
from marketbot.db.repo import prices as price_repo
from marketbot.services.alerts import AlertEngine
from marketbot.steam.client import SteamMarketClient
from marketbot.steam.dto import ItemRef, PriceOverview
from marketbot.steam.errors import SteamError, SteamNotFound, SteamUnavailable
from marketbot.utils.money import pct_change
from marketbot.utils.timeutil import utcnow

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FetchedPrice:
    """What one Steam round trip produced, before it touches the database."""

    overview: PriceOverview
    best_buy: int | None = None
    best_sell: int | None = None


@dataclass(frozen=True, slots=True)
class PriceStats:
    currency: str
    current_minor: int | None
    median_minor: int | None
    volume: int | None
    change_1h: float | None
    change_24h: float | None
    change_7d: float | None
    high_7d: int | None
    low_7d: int | None
    avg_volume_24h: float | None
    best_buy_minor: int | None
    best_sell_minor: int | None
    samples: int
    last_update: datetime | None

    @property
    def spread_minor(self) -> int | None:
        if self.best_buy_minor is None or self.best_sell_minor is None:
            return None
        return self.best_sell_minor - self.best_buy_minor

    @property
    def spread_pct(self) -> float | None:
        if self.spread_minor is None or not self.best_sell_minor:
            return None
        return self.spread_minor / self.best_sell_minor * 100.0

    @property
    def volatility_7d(self) -> float | None:
        """High-low range over the low: a cheap volatility proxy that also feeds the
        refresh priority score."""
        if not self.low_7d or self.high_7d is None:
            return None
        return (self.high_7d - self.low_7d) / self.low_7d * 100.0


class PricingService:
    def __init__(
        self,
        client: SteamMarketClient,
        settings: Settings,
        alert_engine: AlertEngine | None = None,
    ) -> None:
        self._client = client
        self._s = settings
        self._alerts = alert_engine

    async def fetch(
        self,
        ref: ItemRef,
        *,
        currency: str | None = None,
        item_nameid: str | None = None,
        with_order_book: bool = False,
    ) -> FetchedPrice | None:
        """Talk to Steam. Deliberately takes no session.

        Holding a database transaction open across this call is what produced
        "database is locked": a throttled fetch can sit in the token bucket for tens of
        seconds, and on SQLite that blocks every other writer for the duration. The
        caller reads what it needs, closes its session, calls this, then opens a short
        write session.

        Returns None when the item has no market data. SteamUnavailable propagates so
        the worker can abandon the batch.
        """
        code = currency or self._s.steam_currency
        try:
            overview = await self._client.price_overview(ref, code)
        except SteamNotFound:
            log.info("no market data item=%s", ref.market_hash_name)
            return None

        best_buy = best_sell = None
        if with_order_book and item_nameid:
            try:
                book = await self._client.order_book(ref, item_nameid, code)
                best_buy, best_sell = book.highest_buy_order, book.lowest_sell_order
            except SteamError as exc:
                # The order book is a bonus signal, never a reason to lose the price.
                log.debug("order book unavailable item=%s: %s", ref.market_hash_name, exc)

        return FetchedPrice(overview=overview, best_buy=best_buy, best_sell=best_sell)

    async def persist(
        self, session: AsyncSession, item: Item, fetched: FetchedPrice | None
    ) -> PriceSnapshot | None:
        """Write a fetch result and evaluate alerts. Short, write-only, no network."""
        if fetched is None:
            await item_repo.mark_refreshed(session, item, success=False)
            return None

        overview = fetched.overview
        snapshot = await price_repo.add_snapshot(
            session,
            item_id=item.id,
            currency=overview.currency,
            lowest_price_minor=overview.lowest_price,
            median_price_minor=overview.median_price,
            volume=overview.volume,
            best_buy_order_minor=fetched.best_buy,
            best_sell_order_minor=fetched.best_sell,
            captured_at=overview.fetched_at or utcnow(),
        )
        await item_repo.mark_refreshed(session, item, success=True)

        if self._alerts:
            await self._alerts.evaluate_item(session, item, snapshot)
        return snapshot

    async def refresh_item(
        self,
        session: AsyncSession,
        item: Item,
        *,
        currency: str | None = None,
        with_order_book: bool = False,
    ) -> PriceSnapshot | None:
        """Fetch and persist in one call.

        Convenient, but it holds the caller's session across the network round trip.
        Use fetch() + persist() anywhere that runs concurrently with other writers --
        the refresh worker does.
        """
        fetched = await self.fetch(
            ItemRef(item.appid, item.market_hash_name),
            currency=currency,
            item_nameid=item.item_nameid,
            with_order_book=with_order_book,
        )
        return await self.persist(session, item, fetched)

    async def ensure_meta(self, session: AsyncSession, item: Item) -> Item:
        """Populate item_nameid and icon_url. Costly, so it happens lazily and once.

        Both come from the same listing-page scrape, but they arrive by different
        routes: search results carry an icon with no nameid. Guarding on nameid alone
        meant an item could never acquire an image, which the photo button needs.
        """
        if item.item_nameid and item.icon_url:
            return item
        try:
            meta = await self._client.item_meta(ItemRef(item.appid, item.market_hash_name))
        except SteamUnavailable:
            # Transient, and the caller needs to know: a sweep should stop rather than
            # march through the rest of the batch, and it must not record the item as
            # processed when nothing was fetched.
            raise
        except SteamError as exc:
            # Permanent for this item (no listing page, malformed name). Nothing to
            # retry, so carry on without metadata.
            log.debug("meta fetch failed item=%s: %s", item.market_hash_name, exc)
            return item
        return await item_repo.set_meta(
            session, item, item_nameid=meta.item_nameid, icon_url=meta.icon_url
        )

    async def compute_stats(
        self, session: AsyncSession, item: Item, *, currency: str
    ) -> PriceStats:
        """Derive the analytics panel from stored snapshots only -- zero Steam calls.

        This is what lets a button tap answer instantly and lets the bot keep working
        while Steam is throttling us.
        """
        now = utcnow()
        latest = await price_repo.latest(session, item.id)
        week = await price_repo.series(session, item.id, since=now - timedelta(days=7))

        async def change_since(delta: timedelta) -> float | None:
            past = await price_repo.price_at_or_before(session, item.id, now - delta)
            if past is None or latest is None:
                return None
            return pct_change(past.price_minor, latest.price_minor)

        prices = [s.price_minor for s in week if s.price_minor is not None]
        avg_vol = await price_repo.average_volume(session, item.id, since=now - timedelta(days=1))

        return PriceStats(
            currency=latest.currency if latest else currency,
            current_minor=latest.price_minor if latest else None,
            median_minor=latest.median_price_minor if latest else None,
            volume=latest.volume if latest else None,
            change_1h=await change_since(timedelta(hours=1)),
            change_24h=await change_since(timedelta(days=1)),
            change_7d=await change_since(timedelta(days=7)),
            high_7d=max(prices) if prices else None,
            low_7d=min(prices) if prices else None,
            avg_volume_24h=float(avg_vol) if avg_vol is not None else None,
            best_buy_minor=latest.best_buy_order_minor if latest else None,
            best_sell_minor=latest.best_sell_order_minor if latest else None,
            samples=len(week),
            last_update=latest.captured_at if latest else None,
        )

    async def backfill_history(self, session: AsyncSession, item: Item, *, currency: str) -> int:
        """Seed history from Steam's own series so a new item charts immediately.

        Optional: needs STEAM_LOGIN_SECURE. Without it the chart simply fills in as our
        own snapshots accumulate.
        """
        if not self._s.steam_login_secure:
            return 0
        try:
            points = await self._client.price_history(
                ItemRef(item.appid, item.market_hash_name), currency
            )
        except SteamError as exc:
            log.debug("history backfill failed item=%s: %s", item.market_hash_name, exc)
            return 0

        existing = await price_repo.latest(session, item.id)
        cutoff = existing.captured_at if existing else None
        added = 0
        for point in points:
            if cutoff and point.ts <= cutoff:
                continue
            await price_repo.add_snapshot(
                session,
                item_id=item.id,
                currency=currency,
                lowest_price_minor=point.price,
                median_price_minor=point.price,
                volume=point.volume,
                captured_at=point.ts,
            )
            added += 1
        return added

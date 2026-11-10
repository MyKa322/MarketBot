"""Shared fixtures: an in-memory database and a Steam client that never leaves the box."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from marketbot.config import Settings
from marketbot.db.repo import items as item_repo
from marketbot.db.repo import users as user_repo
from marketbot.db.session import build_engine, build_session_factory, create_all
from marketbot.steam.dto import ItemRef, OrderBook, PriceOverview, SearchHit


@pytest.fixture
def settings() -> Settings:
    return Settings(
        BOT_TOKEN="test:token",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        STEAM_RATE_PER_MIN=600,
        ALERT_DEFAULT_COOLDOWN_MIN=60,
        ALERT_MAX_PER_USER_HOUR=20,
        # Throttling is covered by its own test; everywhere else it would just
        # swallow updates and make handler tests pass for the wrong reason.
        THROTTLE_BURST=10_000,
    )


@pytest_asyncio.fixture
async def engine(settings):
    # A single shared in-memory connection, so every session sees the same schema.
    eng = build_engine(settings)
    await create_all(eng)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine) -> async_sessionmaker:
    return build_session_factory(engine)


@pytest_asyncio.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s
        await s.rollback()


@pytest_asyncio.fixture
async def user(session):
    u = await user_repo.get_or_create(session, 424242, username="tester", currency="USD")
    await session.commit()
    return u


@pytest_asyncio.fixture
async def item(session):
    it, _ = await item_repo.get_or_create(session, 730, "AK-47 | Redline (Field-Tested)")
    it.item_nameid = "176321160"
    await session.commit()
    return it


class FakeSteam:
    """Stands in for SteamMarketClient. Records calls so tests can assert on traffic."""

    def __init__(self, price: int = 1000, volume: int = 500) -> None:
        self.price = price
        self.volume = volume
        self.calls: list[str] = []
        self.fail_with: Exception | None = None

    async def price_overview(self, ref: ItemRef, code: str = "USD") -> PriceOverview:
        self.calls.append(f"priceoverview:{ref.market_hash_name}")
        if self.fail_with:
            raise self.fail_with
        return PriceOverview(
            item=ref,
            currency=code,
            lowest_price=self.price,
            median_price=self.price + 20,
            volume=self.volume,
            fetched_at=datetime.now(UTC),
        )

    async def order_book(self, ref: ItemRef, nameid: str, code: str = "USD") -> OrderBook:
        self.calls.append(f"orderbook:{ref.market_hash_name}")
        return OrderBook(
            item=ref,
            currency=code,
            highest_buy_order=self.price - 30,
            lowest_sell_order=self.price,
            buy_graph=[(self.price - 30, 12)],
            sell_graph=[(self.price, 8)],
        )

    async def search(self, query: str, *, appid=None, limit=10, code="USD") -> list[SearchHit]:
        self.calls.append(f"search:{query}")
        if self.fail_with:
            raise self.fail_with
        return [
            SearchHit(
                item=ItemRef(appid or 730, f"{query} (Field-Tested)"),
                name=f"{query} (Field-Tested)",
                sell_price=self.price,
                sell_listings=42,
                currency=code,
            )
        ]

    async def item_meta(self, ref: ItemRef):
        from marketbot.steam.dto import ItemMeta

        self.calls.append(f"meta:{ref.market_hash_name}")
        if self.fail_with:
            raise self.fail_with
        return ItemMeta(item=ref, item_nameid="999", icon_url="http://example/icon.png")

    async def price_history(self, ref: ItemRef, code: str = "USD"):
        self.calls.append(f"history:{ref.market_hash_name}")
        return []

    def health(self) -> dict:
        return {
            "requests": len(self.calls),
            "cache_hits": 0,
            "throttled": 0,
            "errors": 0,
            "rate_per_min": 18.0,
            "tokens": 5.0,
            "circuits": {},
        }


@pytest.fixture
def fake_steam() -> FakeSteam:
    return FakeSteam()


@pytest.fixture
def now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
def ago():
    def _ago(**kwargs) -> datetime:
        return datetime.now(UTC) - timedelta(**kwargs)

    return _ago

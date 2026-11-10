"""Cross-layer behaviour: ingestion, resolution, scheduling, rollups, charts."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from marketbot.constants import RefreshTier
from marketbot.db.repo import items as item_repo
from marketbot.db.repo import prices as price_repo
from marketbot.db.repo import watchlist as watch_repo
from marketbot.services.charts import ChartPoint, ChartService
from marketbot.services.items import ItemService
from marketbot.services.pricing import PricingService
from marketbot.services.refresh import RefreshPlanner, RefreshWorker, score_item
from marketbot.steam.client import SteamNotFound, SteamUnavailable
from marketbot.utils.timeutil import utcnow

# ------------------------------------------------------------------- ingestion


async def test_refresh_persists_snapshot(session, item, fake_steam, settings):
    pricing = PricingService(fake_steam, settings)
    snapshot = await pricing.refresh_item(session, item)

    assert snapshot is not None
    assert snapshot.lowest_price_minor == 1000
    assert snapshot.volume == 500
    assert (await price_repo.latest(session, item.id)).id == snapshot.id
    assert item.last_success_at is not None


async def test_order_book_only_fetched_when_worth_it(session, item, fake_steam, settings):
    """A second request per item is spent only on items somebody watches."""
    pricing = PricingService(fake_steam, settings)

    await pricing.refresh_item(session, item, with_order_book=False)
    assert not any(c.startswith("orderbook") for c in fake_steam.calls)

    snapshot = await pricing.refresh_item(session, item, with_order_book=True)
    assert any(c.startswith("orderbook") for c in fake_steam.calls)
    assert snapshot.best_buy_order_minor == 970
    assert snapshot.best_sell_order_minor == 1000


async def test_missing_item_is_marked_failed_not_crashed(session, item, fake_steam, settings):
    fake_steam.fail_with = SteamNotFound("gone")
    pricing = PricingService(fake_steam, settings)

    assert await pricing.refresh_item(session, item) is None
    await session.refresh(item)
    assert item.fail_count == 1
    assert await price_repo.latest(session, item.id) is None


async def test_upstream_throttle_propagates(session, item, fake_steam, settings):
    """The caller must learn Steam is unhappy so it can stop the whole batch."""
    fake_steam.fail_with = SteamUnavailable("429")
    pricing = PricingService(fake_steam, settings)
    with pytest.raises(SteamUnavailable):
        await pricing.refresh_item(session, item)


async def test_compute_stats_derives_changes(session, item, fake_steam, settings):
    for minutes, price in ((1500, 1000), (70, 1200), (0, 900)):
        await price_repo.add_snapshot(
            session,
            item_id=item.id,
            currency="USD",
            lowest_price_minor=price,
            median_price_minor=price,
            volume=400,
            captured_at=utcnow() - timedelta(minutes=minutes),
        )
    await session.commit()

    stats = await PricingService(fake_steam, settings).compute_stats(
        session, item, currency="USD"
    )
    assert stats.current_minor == 900
    assert stats.change_24h == pytest.approx(-10.0)   # vs 1000 a day ago
    assert stats.change_1h == pytest.approx(-25.0)    # vs 1200 an hour ago
    assert stats.high_7d == 1200
    assert stats.low_7d == 900
    assert stats.volatility_7d == pytest.approx(33.333, rel=1e-3)


# ------------------------------------------------------------------ resolution


async def test_url_resolves_without_touching_steam(session, fake_steam, settings):
    service = ItemService(fake_steam, settings)
    result = await service.resolve(
        session,
        "https://steamcommunity.com/market/listings/730/AWP%20%7C%20Asiimov%20%28Field-Tested%29",
    )
    assert result.is_exact
    assert result.item.market_hash_name == "AWP | Asiimov (Field-Tested)"
    assert fake_steam.calls == []


async def test_known_item_resolves_locally(session, item, fake_steam, settings):
    service = ItemService(fake_steam, settings)
    result = await service.resolve(session, item.market_hash_name)
    assert result.is_exact
    assert result.source == "local"
    assert fake_steam.calls == []


async def test_unknown_item_searches_steam(session, fake_steam, settings):
    service = ItemService(fake_steam, settings)
    result = await service.resolve(session, "Butterfly Knife")
    assert any(c.startswith("search") for c in fake_steam.calls)
    assert result.is_exact or result.candidates


async def test_bad_url_gives_actionable_error(session, fake_steam, settings):
    service = ItemService(fake_steam, settings)
    result = await service.resolve(session, "https://steamcommunity.com/profiles/765611")
    assert result.error
    assert not result.is_exact
    assert fake_steam.calls == []


async def test_search_outage_falls_back_to_local(session, item, fake_steam, settings):
    fake_steam.fail_with = SteamUnavailable("429")
    service = ItemService(fake_steam, settings)
    result = await service.resolve(session, "Redline")

    assert result.candidates
    assert result.source == "local"
    assert result.candidates[0].market_hash_name == item.market_hash_name


async def test_search_outage_with_no_local_data_explains_itself(session, fake_steam, settings):
    fake_steam.fail_with = SteamUnavailable("429")
    service = ItemService(fake_steam, settings)
    result = await service.resolve(session, "Totally Unknown Thing")
    assert result.error and "Market URL" in result.error


# ----------------------------------------------------------------- scheduling


def test_planner_deduplicates(monkeypatch):
    """Tapping Refresh five times must not spend five of the minute's requests."""
    planner = RefreshPlanner()
    assert planner.enqueue(1, 10.0) is True
    assert planner.enqueue(1, 99.0) is False
    assert len(planner) == 1

    task = planner.pop()
    assert task.item_id == 1
    assert planner.enqueue(1, 5.0) is True  # allowed again once drained


def test_planner_pops_highest_priority_first():
    planner = RefreshPlanner()
    for item_id, priority in ((1, 5.0), (2, 50.0), (3, 20.0)):
        planner.enqueue(item_id, priority)
    assert [planner.pop().item_id for _ in range(3)] == [2, 3, 1]


def test_planner_respects_maxsize():
    planner = RefreshPlanner(maxsize=2)
    assert planner.enqueue(1, 1.0) is True
    assert planner.enqueue(2, 1.0) is True
    assert planner.enqueue(3, 1.0) is False


def test_score_prefers_watched_items(item):
    from types import SimpleNamespace

    now = utcnow()

    def fake(watchers, *, minutes_old=10, tier=RefreshTier.WARM, fails=0):
        return SimpleNamespace(
            watchers_count=watchers,
            tier=tier,
            last_refreshed_at=now - timedelta(minutes=minutes_old),
            fail_count=fails,
        )

    assert score_item(fake(10), now=now) > score_item(fake(1), now=now)
    assert score_item(fake(5, minutes_old=600), now=now) > score_item(fake(5, minutes_old=1), now=now)
    assert score_item(fake(5), now=now) > score_item(fake(5, fails=3), now=now)


def test_never_refreshed_item_is_urgent(item):
    from types import SimpleNamespace

    now = utcnow()
    never = SimpleNamespace(
        watchers_count=0, tier=RefreshTier.COLD, last_refreshed_at=None, fail_count=0
    )
    fresh = SimpleNamespace(
        watchers_count=0, tier=RefreshTier.COLD, last_refreshed_at=now, fail_count=0
    )
    assert score_item(never, now=now) > score_item(fresh, now=now)


async def test_worker_processes_a_queued_item(session_factory, session, item, fake_steam, settings):
    planner = RefreshPlanner()
    pricing = PricingService(fake_steam, settings)
    worker = RefreshWorker(planner, pricing, session_factory, settings)

    await session.commit()
    assert worker.request_now(item) is True
    task = planner.pop()
    await worker._process(task)

    assert worker.processed == 1
    async with session_factory() as check:
        assert await price_repo.latest(check, item.id) is not None


async def test_enqueue_due_only_picks_stale_items(session, session_factory, item, fake_steam, settings):
    planner = RefreshPlanner()
    worker = RefreshWorker(planner, PricingService(fake_steam, settings), session_factory, settings)

    item.tier = RefreshTier.HOT
    item.watchers_count = 1
    item.last_refreshed_at = utcnow()  # just refreshed
    await session.commit()
    assert await worker.enqueue_due(session, RefreshTier.HOT, limit=10) == 0

    item.last_refreshed_at = utcnow() - timedelta(hours=2)
    await session.commit()
    assert await worker.enqueue_due(session, RefreshTier.HOT, limit=10) == 1


async def test_unwatched_items_never_consume_the_request_budget(
    session, session_factory, item, fake_steam, settings
):
    """Nothing surfaces an unwatched item any more, so refreshing one is pure waste --
    and on a throttled IP the budget is exactly what you cannot afford to waste."""
    planner = RefreshPlanner()
    worker = RefreshWorker(planner, PricingService(fake_steam, settings), session_factory, settings)

    item.tier = RefreshTier.HOT
    item.watchers_count = 0
    item.last_refreshed_at = utcnow() - timedelta(hours=2)
    await session.commit()

    assert await worker.enqueue_due(session, RefreshTier.HOT, limit=10) == 0


async def test_orphan_items_are_pruned(session, user, item):
    """Items accumulate from searches; without cleanup they linger forever."""
    from datetime import UTC, datetime

    from marketbot.db.repo import items as items_repo

    item.created_at = datetime(2020, 1, 1, tzinfo=UTC)
    await session.commit()

    assert await items_repo.delete_orphans(session) == 1
    assert await items_repo.by_id(session, item.id) is None


async def test_watched_items_survive_pruning(session, user, item):
    from datetime import UTC, datetime

    from marketbot.db.repo import items as items_repo

    item.created_at = datetime(2020, 1, 1, tzinfo=UTC)
    await watch_repo.add(session, user.id, item.id)
    await session.commit()

    assert await items_repo.delete_orphans(session) == 0
    assert await items_repo.by_id(session, item.id) is not None


async def test_tier_assignment_follows_watchers(session, user, item):
    await watch_repo.add(session, user.id, item.id)
    await session.commit()

    await item_repo.recompute_tiers(session)
    await session.refresh(item)
    assert item.watchers_count == 1
    assert item.tier is RefreshTier.WARM


# -------------------------------------------------------------------- rollups


async def test_daily_rollup_is_ohlcv_and_idempotent(session, item):
    day = (utcnow() - timedelta(days=1)).date()
    base = datetime(day.year, day.month, day.day, tzinfo=UTC)
    for hour, price in ((1, 1000), (5, 1400), (9, 800), (20, 1100)):
        await price_repo.add_snapshot(
            session,
            item_id=item.id,
            currency="USD",
            lowest_price_minor=price,
            median_price_minor=price,
            volume=100 + hour,
            captured_at=base + timedelta(hours=hour),
        )
    await session.commit()

    daily = await price_repo.rollup_day(session, item.id, day)
    assert (daily.open_minor, daily.high_minor, daily.low_minor, daily.close_minor) == (
        1000, 1400, 800, 1100,
    )
    assert daily.samples == 4

    again = await price_repo.rollup_day(session, item.id, day)
    assert again.id == daily.id  # updated in place, not duplicated


async def test_rollup_with_no_data_returns_none(session, item):
    assert await price_repo.rollup_day(session, item.id, (utcnow() - timedelta(days=5)).date()) is None


async def test_prune_drops_only_old_rows(session, item):
    for days in (200, 1):
        await price_repo.add_snapshot(
            session,
            item_id=item.id,
            currency="USD",
            lowest_price_minor=1000,
            captured_at=utcnow() - timedelta(days=days),
        )
    await session.commit()

    assert await price_repo.prune(session, older_than_days=90) == 1
    remaining = await price_repo.series(session, item.id, since=utcnow() - timedelta(days=365))
    assert len(remaining) == 1


# --------------------------------------------------------------------- charts


async def test_price_chart_renders_png():
    now = utcnow()
    points = [
        ChartPoint(ts=now - timedelta(hours=n), price_minor=1000 + n * 7, volume=100 + n)
        for n in range(48, 0, -1)
    ]
    png = await ChartService().price_chart(
        points, title="Test item", subtitle="Last 48 hours", currency_code="USD"
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 5000


async def test_chart_handles_insufficient_data():
    png = await ChartService().price_chart(
        [], title="Empty", subtitle="", currency_code="USD"
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


async def test_downsampling_preserves_the_latest_point():
    """Dropping the final point would show a stale price beside a fresh label."""
    now = utcnow()
    points = [
        ChartPoint(ts=now - timedelta(minutes=n), price_minor=1000 + n)
        for n in range(1000, 0, -1)
    ]
    service = ChartService(max_points=50)
    thinned = service._downsample(points, 50)
    assert len(thinned) <= 51
    assert thinned[-1] is points[-1]

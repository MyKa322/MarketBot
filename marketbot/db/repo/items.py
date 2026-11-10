"""Item repository, including the refresh-due query that drives the scheduler."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from marketbot.constants import TIER_INTERVAL_SECONDS, RefreshTier
from marketbot.db.models import Item, WatchlistEntry
from marketbot.utils.timeutil import utcnow


async def by_id(session: AsyncSession, item_id: int) -> Item | None:
    return await session.get(Item, item_id)


async def by_ref(session: AsyncSession, appid: int, market_hash_name: str) -> Item | None:
    return await session.scalar(
        select(Item).where(Item.appid == appid, Item.market_hash_name == market_hash_name)
    )


async def get_or_create(
    session: AsyncSession,
    appid: int,
    market_hash_name: str,
    *,
    icon_url: str | None = None,
) -> tuple[Item, bool]:
    item = await by_ref(session, appid, market_hash_name)
    if item:
        if icon_url and not item.icon_url:
            item.icon_url = icon_url
        return item, False
    item = Item(appid=appid, market_hash_name=market_hash_name, icon_url=icon_url)
    session.add(item)
    await session.flush()
    return item, True


async def search_local(
    session: AsyncSession, query: str, *, appid: int | None = None, limit: int = 10
) -> list[Item]:
    """Substring search over already-known items -- answers most lookups with no
    Steam request at all, which is the cheapest possible rate-limit strategy."""
    pattern = f"%{query.strip()}%"
    stmt = select(Item).where(Item.market_hash_name.ilike(pattern))
    if appid:
        stmt = stmt.where(Item.appid == appid)
    stmt = stmt.order_by(Item.watchers_count.desc(), Item.market_hash_name).limit(limit)
    return list(await session.scalars(stmt))


async def set_meta(
    session: AsyncSession,
    item: Item,
    *,
    item_nameid: str | None = None,
    icon_url: str | None = None,
) -> Item:
    if item_nameid:
        item.item_nameid = item_nameid
    if icon_url:
        item.icon_url = icon_url
    await session.flush()
    return item


async def mark_refreshed(session: AsyncSession, item: Item, *, success: bool) -> None:
    """Record the outcome of a refresh.

    Mutates the ORM object rather than emitting a bare UPDATE: a detached statement
    leaves the in-session instance stale, so anything still holding it would read the
    pre-refresh values.
    """
    now = utcnow()
    item.last_refreshed_at = now
    if success:
        item.last_success_at = now
        item.fail_count = 0
    else:
        item.fail_count = (item.fail_count or 0) + 1
    await session.flush()


async def recompute_tiers(session: AsyncSession) -> dict[str, int]:
    """Assign each item a refresh tier from how many users watch it.

    Watched items earn frequent polling; everything else decays to cold. This is what
    keeps a catalogue of thousands of items inside a ~18 req/min budget.
    """
    rows = await session.execute(
        select(WatchlistEntry.item_id, func.count()).group_by(WatchlistEntry.item_id)
    )
    counts = dict(rows.all())
    await session.execute(update(Item).values(watchers_count=0, tier=RefreshTier.COLD))

    stats = {"hot": 0, "warm": 0, "cold": 0}
    for item_id, watchers in counts.items():
        tier = RefreshTier.HOT if watchers >= 3 else RefreshTier.WARM
        stats[tier.value] += 1
        await session.execute(
            update(Item).where(Item.id == item_id).values(watchers_count=watchers, tier=tier)
        )
    stats["cold"] = int(
        await session.scalar(
            select(func.count()).select_from(Item).where(Item.tier == RefreshTier.COLD)
        )
        or 0
    )
    return stats


async def due_for_refresh(
    session: AsyncSession, tier: RefreshTier, *, limit: int = 50
) -> list[Item]:
    """Items in `tier` whose data is older than the tier interval, stalest first.

    Only items somebody watches are eligible. Nothing else surfaces an item any more,
    so refreshing an unwatched one spends the request budget on a price no one will
    ever read -- which matters most on a throttled IP, where the budget is tiny.

    Items that keep failing are pushed back by their failure count, so one permanently
    broken item cannot monopolise what is left.
    """
    cutoff = utcnow() - timedelta(seconds=TIER_INTERVAL_SECONDS[tier])
    stmt = (
        select(Item)
        .where(
            Item.tier == tier,
            Item.watchers_count > 0,
            or_(Item.last_refreshed_at.is_(None), Item.last_refreshed_at < cutoff),
            Item.fail_count < 10,
        )
        .order_by(Item.fail_count.asc(), Item.last_refreshed_at.asc().nulls_first())
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def needing_nameid(session: AsyncSession, *, limit: int = 5) -> list[Item]:
    stmt = (
        select(Item)
        .where(Item.item_nameid.is_(None), Item.watchers_count > 0)
        .order_by(Item.watchers_count.desc())
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def delete_orphans(session: AsyncSession, *, keep_hours: int = 24) -> int:
    """Delete items nobody watches and nobody has an alert on.

    Items arrive from searches and one-off lookups and would otherwise accumulate
    forever. `keep_hours` protects an item somebody is mid-way through adding.
    """
    from marketbot.db.models import AlertRule, WatchlistEntry

    cutoff = utcnow() - timedelta(hours=keep_hours)
    watched = select(WatchlistEntry.item_id)
    alerted = select(AlertRule.item_id)
    result = await session.execute(
        delete(Item).where(
            Item.id.not_in(watched),
            Item.id.not_in(alerted),
            Item.created_at < cutoff,
        )
    )
    return int(result.rowcount or 0)

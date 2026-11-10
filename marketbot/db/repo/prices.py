"""Price snapshot + daily rollup repository."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marketbot.db.models import PriceDaily, PriceSnapshot
from marketbot.utils.timeutil import utcnow


async def add_snapshot(session: AsyncSession, **fields: object) -> PriceSnapshot:
    snap = PriceSnapshot(**fields)
    session.add(snap)
    await session.flush()
    return snap


async def latest(session: AsyncSession, item_id: int) -> PriceSnapshot | None:
    return await session.scalar(
        select(PriceSnapshot)
        .where(PriceSnapshot.item_id == item_id)
        .order_by(PriceSnapshot.captured_at.desc())
        .limit(1)
    )


async def latest_many(session: AsyncSession, item_ids: list[int]) -> dict[int, PriceSnapshot]:
    """One query for a whole watchlist page instead of N round trips."""
    if not item_ids:
        return {}
    newest = (
        select(
            PriceSnapshot.item_id.label("item_id"),
            func.max(PriceSnapshot.captured_at).label("ts"),
        )
        .where(PriceSnapshot.item_id.in_(item_ids))
        .group_by(PriceSnapshot.item_id)
        .subquery()
    )
    stmt = select(PriceSnapshot).join(
        newest,
        (PriceSnapshot.item_id == newest.c.item_id)
        & (PriceSnapshot.captured_at == newest.c.ts),
    )
    return {s.item_id: s for s in await session.scalars(stmt)}


async def series(
    session: AsyncSession, item_id: int, *, since: datetime, limit: int = 2000
) -> list[PriceSnapshot]:
    stmt = (
        select(PriceSnapshot)
        .where(PriceSnapshot.item_id == item_id, PriceSnapshot.captured_at >= since)
        .order_by(PriceSnapshot.captured_at.asc())
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def daily_series(
    session: AsyncSession, item_id: int, *, since: date, limit: int = 400
) -> list[PriceDaily]:
    stmt = (
        select(PriceDaily)
        .where(PriceDaily.item_id == item_id, PriceDaily.day >= since)
        .order_by(PriceDaily.day.asc())
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def price_at_or_before(
    session: AsyncSession, item_id: int, moment: datetime
) -> PriceSnapshot | None:
    """Baseline lookup for window-based alert rules ("price 60 minutes ago")."""
    return await session.scalar(
        select(PriceSnapshot)
        .where(PriceSnapshot.item_id == item_id, PriceSnapshot.captured_at <= moment)
        .order_by(PriceSnapshot.captured_at.desc())
        .limit(1)
    )


async def average_volume(
    session: AsyncSession, item_id: int, *, since: datetime
) -> float | None:
    return await session.scalar(
        select(func.avg(PriceSnapshot.volume)).where(
            PriceSnapshot.item_id == item_id,
            PriceSnapshot.captured_at >= since,
            PriceSnapshot.volume.is_not(None),
        )
    )


async def rollup_day(session: AsyncSession, item_id: int, day: date) -> PriceDaily | None:
    """Idempotent OHLCV upsert for one item-day.

    Re-running for the same day updates in place, so the job is safe to retry and safe
    to run twice after a restart.
    """
    start = datetime(day.year, day.month, day.day, tzinfo=utcnow().tzinfo)
    end = start + timedelta(days=1)
    rows = list(
        await session.scalars(
            select(PriceSnapshot)
            .where(
                PriceSnapshot.item_id == item_id,
                PriceSnapshot.captured_at >= start,
                PriceSnapshot.captured_at < end,
            )
            .order_by(PriceSnapshot.captured_at.asc())
        )
    )
    prices = [(r.price_minor, r) for r in rows if r.price_minor is not None]
    if not prices:
        return None

    values = [p for p, _ in prices]
    volumes = [r.volume for _, r in prices if r.volume is not None]
    existing = await session.scalar(
        select(PriceDaily).where(PriceDaily.item_id == item_id, PriceDaily.day == day)
    )
    payload = {
        "open_minor": values[0],
        "high_minor": max(values),
        "low_minor": min(values),
        "close_minor": values[-1],
        "volume": max(volumes) if volumes else 0,
        "samples": len(values),
        "currency": prices[0][1].currency,
    }
    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)
        await session.flush()
        return existing
    daily = PriceDaily(item_id=item_id, day=day, **payload)
    session.add(daily)
    await session.flush()
    return daily


async def distinct_items_with_snapshots(
    session: AsyncSession, *, since: datetime
) -> list[int]:
    rows = await session.scalars(
        select(PriceSnapshot.item_id)
        .where(PriceSnapshot.captured_at >= since)
        .group_by(PriceSnapshot.item_id)
    )
    return list(rows)


async def prune(session: AsyncSession, *, older_than_days: int) -> int:
    """Drop raw snapshots past retention. Daily rollups keep the long history."""
    cutoff = utcnow() - timedelta(days=older_than_days)
    result = await session.execute(
        delete(PriceSnapshot).where(PriceSnapshot.captured_at < cutoff)
    )
    return int(result.rowcount or 0)


async def price_at_or_before_many(
    session: AsyncSession, item_ids: list[int], moment: datetime
) -> dict[int, PriceSnapshot]:
    """Batched baseline lookup: the newest snapshot at or before `moment`, per item.

    The single-item version in a loop turns a 50-item watchlist into 50 round trips,
    which is what sorting by "biggest movers" would otherwise cost on every page view.
    """
    if not item_ids:
        return {}
    newest = (
        select(
            PriceSnapshot.item_id.label("item_id"),
            func.max(PriceSnapshot.captured_at).label("ts"),
        )
        .where(PriceSnapshot.item_id.in_(item_ids), PriceSnapshot.captured_at <= moment)
        .group_by(PriceSnapshot.item_id)
        .subquery()
    )
    stmt = select(PriceSnapshot).join(
        newest,
        (PriceSnapshot.item_id == newest.c.item_id)
        & (PriceSnapshot.captured_at == newest.c.ts),
    )
    return {s.item_id: s for s in await session.scalars(stmt)}

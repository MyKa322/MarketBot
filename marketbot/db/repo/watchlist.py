"""Watchlist repository."""
from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marketbot.db.models import Item, WatchlistEntry


async def list_for_user(session: AsyncSession, user_id: int) -> list[WatchlistEntry]:
    stmt = (
        select(WatchlistEntry)
        .where(WatchlistEntry.user_id == user_id)
        .join(Item, Item.id == WatchlistEntry.item_id)
        .order_by(WatchlistEntry.pinned.desc(), Item.market_hash_name)
    )
    return list(await session.scalars(stmt))


async def count_for_user(session: AsyncSession, user_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(WatchlistEntry).where(
                WatchlistEntry.user_id == user_id
            )
        )
        or 0
    )


async def get(session: AsyncSession, user_id: int, item_id: int) -> WatchlistEntry | None:
    return await session.scalar(
        select(WatchlistEntry).where(
            WatchlistEntry.user_id == user_id, WatchlistEntry.item_id == item_id
        )
    )


async def add(
    session: AsyncSession, user_id: int, item_id: int, *, note: str | None = None
) -> tuple[WatchlistEntry, bool]:
    """Returns (entry, created). Idempotent -- re-adding is not an error."""
    existing = await get(session, user_id, item_id)
    if existing:
        return existing, False
    entry = WatchlistEntry(user_id=user_id, item_id=item_id, note=note)
    session.add(entry)
    await session.flush()
    return entry, True


async def remove(session: AsyncSession, user_id: int, item_id: int) -> bool:
    result = await session.execute(
        delete(WatchlistEntry).where(
            WatchlistEntry.user_id == user_id, WatchlistEntry.item_id == item_id
        )
    )
    return bool(result.rowcount)


async def toggle_pin(session: AsyncSession, user_id: int, item_id: int) -> bool | None:
    entry = await get(session, user_id, item_id)
    if entry is None:
        return None
    entry.pinned = not entry.pinned
    await session.flush()
    return entry.pinned


async def watcher_counts(session: AsyncSession) -> dict[int, int]:
    """item_id -> number of watchers, for refresh scoring."""
    rows = await session.execute(
        select(WatchlistEntry.item_id, func.count()).group_by(WatchlistEntry.item_id)
    )
    return dict(rows.all())

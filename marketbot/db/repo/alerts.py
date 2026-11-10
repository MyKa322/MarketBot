"""Alert rule and event repository."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from marketbot.db.models import AlertEvent, AlertKind, AlertRule, User, WatchlistEntry
from marketbot.utils.timeutil import utcnow


async def create(
    session: AsyncSession,
    *,
    user_id: int,
    item_id: int,
    kind: AlertKind,
    threshold_pct: float | None = None,
    threshold_minor: int | None = None,
    window_minutes: int = 60,
    cooldown_minutes: int = 60,
) -> AlertRule:
    rule = AlertRule(
        user_id=user_id,
        item_id=item_id,
        kind=kind,
        threshold_pct=threshold_pct,
        threshold_minor=threshold_minor,
        window_minutes=window_minutes,
        cooldown_minutes=cooldown_minutes,
    )
    session.add(rule)
    await session.flush()
    return rule


async def by_id(session: AsyncSession, rule_id: int) -> AlertRule | None:
    return await session.get(AlertRule, rule_id)


async def list_for_user(
    session: AsyncSession, user_id: int, *, item_id: int | None = None
) -> list[AlertRule]:
    stmt = select(AlertRule).where(AlertRule.user_id == user_id)
    if item_id is not None:
        stmt = stmt.where(AlertRule.item_id == item_id)
    return list(await session.scalars(stmt.order_by(AlertRule.created_at.desc())))


async def active_for_item(session: AsyncSession, item_id: int) -> list[AlertRule]:
    """Rules to evaluate right after a snapshot lands.

    Joined to User so the evaluator can honour per-user mute and quiet hours without
    a second query per rule.
    """
    # The LEFT JOIN lets a per-item mute suppress alerts without deleting the rule,
    # while still firing for users who have a rule but no watchlist entry.
    stmt = (
        select(AlertRule)
        .join(User, User.id == AlertRule.user_id)
        .outerjoin(
            WatchlistEntry,
            (WatchlistEntry.user_id == AlertRule.user_id)
            & (WatchlistEntry.item_id == AlertRule.item_id),
        )
        .where(
            AlertRule.item_id == item_id,
            AlertRule.is_active.is_(True),
            User.alerts_enabled.is_(True),
            User.is_blocked.is_(False),
            or_(WatchlistEntry.id.is_(None), WatchlistEntry.muted.is_(False)),
        )
    )
    return list(await session.scalars(stmt))


async def count_for_user(session: AsyncSession, user_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(AlertRule).where(AlertRule.user_id == user_id)
        )
        or 0
    )


async def toggle(session: AsyncSession, rule: AlertRule) -> AlertRule:
    rule.is_active = not rule.is_active
    await session.flush()
    return rule


async def remove(session: AsyncSession, user_id: int, rule_id: int) -> bool:
    result = await session.execute(
        delete(AlertRule).where(AlertRule.id == rule_id, AlertRule.user_id == user_id)
    )
    return bool(result.rowcount)


async def remove_for_item(session: AsyncSession, user_id: int, item_id: int) -> int:
    result = await session.execute(
        delete(AlertRule).where(AlertRule.user_id == user_id, AlertRule.item_id == item_id)
    )
    return int(result.rowcount or 0)


async def record_event(session: AsyncSession, **fields: object) -> AlertEvent:
    event = AlertEvent(**fields)
    session.add(event)
    await session.flush()
    return event


async def recent_event_count(session: AsyncSession, user_id: int, *, hours: int = 1) -> int:
    """Backs the per-user hourly cap that stops a volatile market spamming someone."""
    since = utcnow() - timedelta(hours=hours)
    return int(
        await session.scalar(
            select(func.count())
            .select_from(AlertEvent)
            .where(AlertEvent.user_id == user_id, AlertEvent.triggered_at >= since)
        )
        or 0
    )


async def recent_events(
    session: AsyncSession, user_id: int, *, limit: int = 10
) -> list[AlertEvent]:
    stmt = (
        select(AlertEvent)
        .where(AlertEvent.user_id == user_id)
        .order_by(AlertEvent.triggered_at.desc())
        .limit(limit)
    )
    return list(await session.scalars(stmt))

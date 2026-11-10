"""User repository."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from marketbot.db.models import User
from marketbot.utils.timeutil import ensure_utc, utcnow

# last_seen_at is presence information, not an audit log; a coarse value is fine
# and costs one write per user per interval instead of one per message.
LAST_SEEN_INTERVAL = timedelta(minutes=15)


async def get_by_telegram_id(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def get_or_create(
    session: AsyncSession,
    telegram_id: int,
    *,
    username: str | None = None,
    first_name: str | None = None,
    currency: str = "USD",
    locale: str = "en",
) -> User:
    user = await get_by_telegram_id(session, telegram_id)
    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            currency=currency,
            locale=locale,
            last_seen_at=utcnow(),
        )
        session.add(user)
        await session.flush()
        return user

    # Only write when something actually changed. Touching last_seen_at on every
    # update promoted every single handler session to a write transaction, which is
    # what put the bot in contention with the refresh worker.
    now = utcnow()
    if username and username != user.username:
        user.username = username
    if first_name and first_name != user.first_name:
        user.first_name = first_name
    if user.is_blocked:
        user.is_blocked = False
    if user.last_seen_at is None or (now - ensure_utc(user.last_seen_at)) > LAST_SEEN_INTERVAL:
        user.last_seen_at = now
    return user


async def set_blocked(session: AsyncSession, user_id: int, blocked: bool = True) -> None:
    await session.execute(update(User).where(User.id == user_id).values(is_blocked=blocked))


# Explicit allowlist so a bug or a crafted callback cannot write to arbitrary columns
# (telegram_id, is_blocked) through this path.
EDITABLE_PREFS = frozenset({
    "currency",
    "locale",
    "timezone",
    "alerts_enabled",
    "quiet_from",
    "quiet_to",
    "silent_alerts",
    "alert_cap_per_hour",
    "digest_enabled",
    "digest_hour",
    "digest_last_sent_on",
    "default_chart_span",
    "watchlist_sort",
})


async def update_prefs(session: AsyncSession, user: User, **fields: object) -> User:
    """Apply preference changes.

    Unknown keys raise rather than being skipped: silently ignoring them is how a
    newly added preference ends up appearing to save in the UI while never reaching
    the database.
    """
    unknown = set(fields) - EDITABLE_PREFS
    if unknown:
        raise ValueError(f"not editable preferences: {sorted(unknown)}")
    for key, value in fields.items():
        setattr(user, key, value)
    await session.flush()
    return user


async def digest_candidates(session: AsyncSession) -> list[User]:
    """Users who have the daily digest switched on and can still receive messages."""
    stmt = select(User).where(User.digest_enabled.is_(True), User.is_blocked.is_(False))
    return list(await session.scalars(stmt))

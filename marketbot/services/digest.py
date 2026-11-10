"""Daily digest: one scheduled summary per user.

Live alerts answer "something just moved". A digest answers "what happened while I
wasn't looking", which is what most people actually want from a tracker they check
once a day. Users can run both, or turn live alerts off and keep only this.

Delivery timing is per-user local time, so the hourly job has to ask, for each user,
"is it their digest hour right now, and have they already had today's?"
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from marketbot.db.models import Item, User
from marketbot.db.repo import alerts as alert_repo
from marketbot.db.repo import prices as price_repo
from marketbot.db.repo import watchlist as watch_repo
from marketbot.utils.money import pct_change
from marketbot.utils.timeutil import utcnow

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Mover:
    item: Item
    price_minor: int | None
    change_pct: float | None


@dataclass(slots=True)
class Digest:
    user: User
    currency: str
    gainers: list[Mover] = field(default_factory=list)
    losers: list[Mover] = field(default_factory=list)
    tracked: int = 0
    alerts_fired: int = 0
    @property
    def has_content(self) -> bool:
        return bool(self.gainers or self.losers or self.alerts_fired)


def local_hour(user: User, moment: datetime | None = None) -> int:
    moment = moment or utcnow()
    try:
        return moment.astimezone(ZoneInfo(user.timezone or "UTC")).hour
    except (ZoneInfoNotFoundError, ValueError):
        return moment.hour


def local_date(user: User, moment: datetime | None = None) -> date:
    moment = moment or utcnow()
    try:
        return moment.astimezone(ZoneInfo(user.timezone or "UTC")).date()
    except (ZoneInfoNotFoundError, ValueError):
        return moment.date()


def is_due(user: User, moment: datetime | None = None) -> bool:
    """True when it is this user's digest hour and today's has not gone out.

    Keyed on the user's *local* date so a timezone change cannot cause two digests
    in one day, and an hourly job that runs late still sends exactly one.
    """
    if not user.digest_enabled or user.is_blocked:
        return False
    moment = moment or utcnow()
    if local_hour(user, moment) != user.digest_hour:
        return False
    return user.digest_last_sent_on != local_date(user, moment)


async def build(session: AsyncSession, user: User, *, top_n: int = 5) -> Digest:
    """Assemble one user's digest from stored data only -- no Steam calls."""
    now = utcnow()
    day_ago = now - timedelta(days=1)

    entries = await watch_repo.list_for_user(session, user.id)
    latest = await price_repo.latest_many(session, [e.item_id for e in entries])

    movers: list[Mover] = []
    for entry in entries:
        snapshot = latest.get(entry.item_id)
        past = await price_repo.price_at_or_before(session, entry.item_id, day_ago)
        change = pct_change(
            past.price_minor if past else None,
            snapshot.price_minor if snapshot else None,
        )
        movers.append(
            Mover(
                item=entry.item,
                price_minor=snapshot.price_minor if snapshot else None,
                change_pct=change,
            )
        )

    ranked = [m for m in movers if m.change_pct is not None]
    ranked.sort(key=lambda m: m.change_pct, reverse=True)

    digest = Digest(
        user=user,
        currency=user.currency,
        gainers=[m for m in ranked if m.change_pct > 0][:top_n],
        losers=[m for m in reversed(ranked) if m.change_pct < 0][:top_n],
        tracked=len(entries),
        alerts_fired=await alert_repo.recent_event_count(session, user.id, hours=24),
    )

    return digest

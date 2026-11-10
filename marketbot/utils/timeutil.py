"""Time helpers: everything internal is timezone-aware UTC."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """Attach UTC to a naive datetime (SQLite round-trips lose tzinfo)."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def parse_duration(text: str) -> timedelta | None:
    """Parse '15m', '2h', '7d' into a timedelta. None when unparseable."""
    m = _DURATION_RE.match(text)
    if not m:
        return None
    return timedelta(seconds=int(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()])


def humanize_age(dt: datetime | None, *, now: datetime | None = None) -> str:
    """'just now' / '4m ago' / '3h ago' / '2d ago'."""
    if dt is None:
        return "never"
    now = now or utcnow()
    delta = now - ensure_utc(dt)
    secs = int(delta.total_seconds())
    if secs < 0:
        return "just now"
    if secs < 45:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    if secs < 86400 * 30:
        return f"{secs // 86400}d ago"
    return ensure_utc(dt).strftime("%Y-%m-%d")


def humanize_delta(delta: timedelta) -> str:
    secs = int(abs(delta.total_seconds()))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h {(secs % 3600) // 60}m"
    return f"{secs // 86400}d {(secs % 86400) // 3600}h"


def day_start(dt: datetime) -> datetime:
    dt = ensure_utc(dt)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def in_quiet_hours(now_local_hour: int, start: int | None, end: int | None) -> bool:
    """Quiet-hours check that handles windows wrapping past midnight."""
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= now_local_hour < end
    return now_local_hour >= start or now_local_hour < end

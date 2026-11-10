"""Parsers for Steam's loosely-typed market responses.

Steam returns prices as *localised display strings*: "$1.15", "1,15 EUR", "1.234,56 zl",
"R$ 12,34", "1 234,56 TL". A float() on any of these is wrong in at least one locale,
so price parsing is separator-aware and driven by the currency's minor-unit count.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, unquote, urlparse

from marketbot.constants import ICON_BASE, MARKET_LISTING_URL, currency
from marketbot.utils.money import to_minor

_NUMBER_RE = re.compile(r"[0-9][0-9\s.,  ']*[0-9]|[0-9]")
_NAMEID_RE = re.compile(r"Market_LoadOrderSpread\(\s*(\d+)\s*\)")
_ICON_RE = re.compile(r'"icon_url"\s*:\s*"([^"]+)"')
_SPACES = (" ", " ", " ", " ", "'")
# Steam price-history timestamps: "Jul 12 2020 01: +0"
_HISTORY_TS_RE = re.compile(r"^([A-Za-z]{3})\s+(\d{1,2})\s+(\d{4})\s+(\d{1,2}):")
_MONTHS = {
    m: i
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        start=1,
    )
}


def parse_price(raw: str | float | int | None, code: str) -> int | None:
    """Parse a Steam display price into integer minor units. None when unparseable."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return to_minor(Decimal(str(raw)), code)

    text = str(raw).strip()
    if not text:
        return None
    match = _NUMBER_RE.search(text)
    if not match:
        return None

    num = match.group(0)
    for space in _SPACES:
        num = num.replace(space, "")

    minor_units = currency(code).minor_units
    has_dot, has_comma = "." in num, "," in num

    if has_dot and has_comma:
        # The rightmost separator is the decimal one; the other groups thousands.
        dec_sep = "." if num.rfind(".") > num.rfind(",") else ","
        thou_sep = "," if dec_sep == "." else "."
        num = num.replace(thou_sep, "").replace(dec_sep, ".")
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        tail = num.rsplit(sep, 1)[1]
        # 3 trailing digits with no other separator is a thousands group ("1,234"),
        # unless the currency has no minor units at all, where it always is.
        if num.count(sep) > 1 or len(tail) == 3 or minor_units == 0:
            num = num.replace(sep, "")
        else:
            num = num.replace(sep, ".")

    try:
        return to_minor(Decimal(num), code)
    except (InvalidOperation, ValueError):
        return None


def parse_int(raw: str | int | None) -> int | None:
    """Parse Steam's grouped integers ('1,234' / '1 234') into an int."""
    if raw is None:
        return None
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    digits = re.sub(r"[^\d]", "", str(raw))
    return int(digits) if digits else None


def normalize_hash_name(name: str) -> str:
    """Collapse whitespace and strip wrapping quotes from user-supplied item names."""
    cleaned = re.sub(r"\s+", " ", unquote(str(name)).strip())
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'":
        cleaned = cleaned[1:-1].strip()
    return cleaned


def parse_market_url(url: str) -> tuple[int, str] | None:
    """Extract (appid, market_hash_name) from a market listing URL.

    Accepts the canonical /market/listings/<appid>/<name> form with or without scheme,
    query string or trailing slash. Returns None for anything else.
    """
    text = str(url).strip()
    if not text:
        return None
    if "://" not in text:
        text = "https://" + text.lstrip("/")
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    host = (parsed.netloc or "").lower()
    if "steamcommunity.com" not in host and "steampowered.com" not in host:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 4 or parts[0] != "market" or parts[1] != "listings":
        return None
    if not parts[2].isdigit():
        return None
    name = normalize_hash_name(unquote(parts[3]))
    return (int(parts[2]), name) if name else None


def build_market_url(appid: int, market_hash_name: str) -> str:
    return MARKET_LISTING_URL.format(appid=appid, name=quote(market_hash_name, safe=""))


def extract_item_nameid(html: str) -> str | None:
    """Scrape item_nameid from a listing page; required by the order-book endpoint."""
    match = _NAMEID_RE.search(html or "")
    return match.group(1) if match else None


def extract_icon_url(html: str) -> str | None:
    match = _ICON_RE.search(html or "")
    if not match:
        return None
    return ICON_BASE + match.group(1).replace(r"\/", "/")


def icon_url(icon_hash: str | None, size: str = "62fx62f") -> str | None:
    if not icon_hash:
        return None
    if icon_hash.startswith("http"):
        return icon_hash
    return f"{ICON_BASE}{icon_hash}/{size}"


def parse_history_timestamp(raw: str) -> datetime | None:
    match = _HISTORY_TS_RE.match(str(raw).strip())
    if not match:
        return None
    mon, day, year, hour = match.groups()
    month = _MONTHS.get(mon.title())
    if not month:
        return None
    try:
        return datetime(int(year), month, int(day), int(hour), tzinfo=UTC)
    except ValueError:
        return None

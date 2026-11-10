"""Money helpers.

Prices are integers in minor units (cents) everywhere in this codebase. Floats are
only produced at the very edge, for charting and percentage math.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from marketbot.constants import Currency, currency


def to_minor(amount: str | float | Decimal, code: str) -> int:
    """Convert a human amount ("12.34") to minor units for the given currency."""
    cur = currency(code)
    quant = Decimal(10) ** -cur.minor_units
    dec = Decimal(str(amount)).quantize(quant, rounding=ROUND_HALF_UP)
    return int(dec * (Decimal(10) ** cur.minor_units))


def to_major(minor: int, code: str) -> Decimal:
    cur = currency(code)
    return Decimal(minor) / (Decimal(10) ** cur.minor_units)


def fmt_money(minor: int | None, code: str, *, signed: bool = False) -> str:
    """Render minor units as a display string, e.g. '$12.34' or '-12,34 €'."""
    if minor is None:
        return "—"
    cur: Currency = currency(code)
    neg = minor < 0
    value = to_major(abs(minor), code)
    body = f"{value:,.{cur.minor_units}f}"
    if cur.symbol_first:
        text = f"{cur.symbol}{body}"
    else:
        text = f"{body} {cur.symbol}"
    if neg:
        return f"-{text}"
    if signed and minor > 0:
        return f"+{text}"
    return text


def pct_change(old: int | None, new: int | None) -> float | None:
    """Percentage change from old to new; None when undefined."""
    if not old or new is None:
        return None
    return (new - old) / old * 100.0


def fmt_pct(value: float | None, *, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:+.{digits}f}%"


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default

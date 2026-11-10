"""CSV export of a user's own data.

Written with a UTF-8 BOM: Excel otherwise reads plain UTF-8 CSV as the local ANSI
codepage and mangles every item name containing a non-Latin character -- which is most
of them once stickers and Cyrillic names are involved.
"""
from __future__ import annotations

import csv
import io
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from marketbot.db.models import User
from marketbot.db.repo import prices as price_repo
from marketbot.db.repo import watchlist as watch_repo
from marketbot.utils.money import pct_change, to_major
from marketbot.utils.timeutil import ensure_utc, utcnow

HEADERS = (
    "item",
    "appid",
    "currency",
    "price",
    "median",
    "change_24h_pct",
    "volume_24h",
    "best_buy_order",
    "best_sell_order",
    "updated_utc",
    "muted",
    "pinned",
)


async def export_watchlist(session: AsyncSession, user: User) -> tuple[bytes, int]:
    """Return (csv_bytes, row_count) for everything the user tracks."""
    entries = await watch_repo.list_for_user(session, user.id)
    if not entries:
        return b"", 0

    item_ids = [e.item_id for e in entries]
    latest = await price_repo.latest_many(session, item_ids)
    baseline = await price_repo.price_at_or_before_many(
        session, item_ids, utcnow() - timedelta(days=1)
    )

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADERS)

    def money(minor: int | None, code: str) -> str:
        return f"{to_major(minor, code):.2f}" if minor is not None else ""

    for entry in entries:
        item = entry.item
        snapshot = latest.get(entry.item_id)
        past = baseline.get(entry.item_id)
        code = snapshot.currency if snapshot else user.currency
        change = pct_change(
            past.price_minor if past else None,
            snapshot.price_minor if snapshot else None,
        )
        writer.writerow(
            [
                item.market_hash_name,
                item.appid,
                code,
                money(snapshot.price_minor if snapshot else None, code),
                money(snapshot.median_price_minor if snapshot else None, code),
                f"{change:.2f}" if change is not None else "",
                snapshot.volume if snapshot and snapshot.volume is not None else "",
                money(snapshot.best_buy_order_minor if snapshot else None, code),
                money(snapshot.best_sell_order_minor if snapshot else None, code),
                ensure_utc(snapshot.captured_at).strftime("%Y-%m-%d %H:%M:%S") if snapshot else "",
                "yes" if entry.muted else "no",
                "yes" if entry.pinned else "no",
            ]
        )

    return buffer.getvalue().encode("utf-8-sig"), len(entries)

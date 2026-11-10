"""Watchlist: paged list, compact overview panel, add, bulk refresh."""
from __future__ import annotations

from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from marketbot.bot import keyboards as kb
from marketbot.bot import texts as T
from marketbot.bot.callbacks import WatchCB
from marketbot.bot.states import AddItem
from marketbot.bot.ui import ack, show
from marketbot.db.models import User
from marketbot.db.repo import prices as price_repo
from marketbot.db.repo import watchlist as watch_repo
from marketbot.i18n import t
from marketbot.registry import Services
from marketbot.utils.money import pct_change
from marketbot.utils.timeutil import ensure_utc, utcnow

router = Router(name="watchlist")

PAGE_SIZE = 6


def _sort_key(sort: str):
    """Pinned entries lead; the preference orders everything after that.

    Unpriced items sort last in every price-based order rather than counting as zero,
    which would otherwise park brand-new additions at the top.
    """
    if sort == "change":
        return lambda r: (not r["pinned"], -(r["change_24h"] if r["change_24h"] is not None else -1e9))
    if sort == "price":
        return lambda r: (not r["pinned"], -(r["price_minor"] if r["price_minor"] is not None else -1))
    if sort == "updated":
        return lambda r: (
            not r["pinned"],
            -(ensure_utc(r["updated_at"]).timestamp() if r["updated_at"] else 0),
        )
    return lambda r: (not r["pinned"], r["item"].market_hash_name.casefold())


async def collect_rows(session: AsyncSession, entries: list, currency: str) -> list[dict]:
    """Price and 24h change for the whole watchlist in two queries."""
    item_ids = [e.item_id for e in entries]
    latest = await price_repo.latest_many(session, item_ids)
    baseline = await price_repo.price_at_or_before_many(
        session, item_ids, utcnow() - timedelta(days=1)
    )

    rows: list[dict] = []
    for entry in entries:
        snapshot = latest.get(entry.item_id)
        past = baseline.get(entry.item_id)
        rows.append(
            {
                "item": entry.item,
                "pinned": entry.pinned,
                "muted": entry.muted,
                "price_minor": snapshot.price_minor if snapshot else None,
                "currency": snapshot.currency if snapshot else currency,
                "change_24h": pct_change(
                    past.price_minor if past else None,
                    snapshot.price_minor if snapshot else None,
                ),
                "updated_at": snapshot.captured_at if snapshot else None,
                "spark": "",
            }
        )
    return rows


async def _add_sparklines(session: AsyncSession, rows: list[dict]) -> None:
    """Only for the visible page: the one remaining per-item query."""
    since = utcnow() - timedelta(days=1)
    for row in rows:
        history = await price_repo.series(session, row["item"].id, since=since, limit=64)
        values = [s.price_minor for s in history if s.price_minor is not None]
        row["spark"] = T.sparkline(values)


async def render_watchlist(
    event, session: AsyncSession, user: User, services: Services, *, page: int = 0
) -> None:
    entries = await watch_repo.list_for_user(session, user.id)
    if not entries:
        await show(
            event,
            T.watchlist_screen([], user, page=0, pages=1),
            kb.watchlist_keyboard([], user.locale, page=0, pages=1),
        )
        return

    rows = await collect_rows(session, entries, user.currency)
    rows.sort(key=_sort_key(user.watchlist_sort))

    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    window = rows[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    await _add_sparklines(session, window)

    await show(
        event,
        T.watchlist_screen(window, user, page=page, pages=pages),
        kb.watchlist_keyboard(window, user.locale, page=page, pages=pages),
    )


async def render_overview(event, session: AsyncSession, user: User) -> None:
    """Everything tracked on one screen -- no paging, no scrolling between items."""
    entries = await watch_repo.list_for_user(session, user.id)
    rows = await collect_rows(session, entries, user.currency) if entries else []
    rows.sort(key=_sort_key(user.watchlist_sort))
    await show(event, T.overview_screen(rows, user), kb.overview_keyboard(rows, user.locale))


@router.message(Command("watchlist"))
async def cmd_watchlist(
    message: Message, session: AsyncSession, user: User, services: Services
) -> None:
    await render_watchlist(message, session, user, services)


@router.callback_query(WatchCB.filter(F.action == "list"))
async def cb_list(
    query: CallbackQuery, session: AsyncSession, user: User, services: Services
) -> None:
    await ack(query)
    await render_watchlist(query, session, user, services)


@router.callback_query(WatchCB.filter(F.action == "page"))
async def cb_page(
    query: CallbackQuery,
    callback_data: WatchCB,
    session: AsyncSession,
    user: User,
    services: Services,
) -> None:
    await ack(query)
    await render_watchlist(query, session, user, services, page=callback_data.page)


@router.callback_query(WatchCB.filter(F.action == "overview"))
async def cb_overview(query: CallbackQuery, session: AsyncSession, user: User) -> None:
    await ack(query)
    await render_overview(query, session, user)


@router.message(Command("overview"))
async def cmd_overview(message: Message, session: AsyncSession, user: User) -> None:
    await render_overview(message, session, user)


@router.callback_query(WatchCB.filter(F.action == "add"))
async def cb_add(query: CallbackQuery, user: User, state: FSMContext) -> None:
    await ack(query)
    await state.set_state(AddItem.waiting_for_query)
    await show(query, T.add_item_prompt(user), kb.cancel_keyboard(user.locale))


@router.message(Command("add"))
async def cmd_add(message: Message, user: User, state: FSMContext) -> None:
    await state.set_state(AddItem.waiting_for_query)
    await show(message, T.add_item_prompt(user), kb.cancel_keyboard(user.locale))


@router.callback_query(WatchCB.filter(F.action == "refresh_all"))
async def cb_refresh_all(
    query: CallbackQuery, session: AsyncSession, user: User, services: Services
) -> None:
    """Queue every tracked item at once.

    Queueing, not fetching: the worker still drains at the rate limiter's pace, so
    this is safe even with a full 50-item watchlist.
    """
    entries = await watch_repo.list_for_user(session, user.id)
    queued = sum(1 for entry in entries if services.worker.request_now(entry.item))
    await ack(query, t("item.queued", user.locale, backlog=len(services.planner)) if queued
              else t("item.already_queued", user.locale))
    await render_overview(query, session, user)

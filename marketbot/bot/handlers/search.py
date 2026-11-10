"""Adding items: free-text search, URL paste, candidate disambiguation.

A newly added item is shown with its photo when one is available: an image confirms
you picked the right skin far faster than reading a wear tag does.
"""
from __future__ import annotations

import contextlib
import logging
from dataclasses import asdict

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from marketbot.bot import keyboards as kb
from marketbot.bot import texts as T
from marketbot.bot.callbacks import PickCB
from marketbot.bot.states import AddItem
from marketbot.bot.ui import ack, show
from marketbot.db.models import User
from marketbot.db.repo import watchlist as watch_repo
from marketbot.i18n import t
from marketbot.registry import Services
from marketbot.services.items import Candidate
from marketbot.steam.errors import SteamError
from marketbot.steam.parse import icon_url

log = logging.getLogger(__name__)
router = Router(name="search")


async def _adopt_and_open(
    event, session: AsyncSession, user: User, services: Services, item
) -> None:
    # Scrape the icon *before* any write. ensure_meta is a network call, and doing it
    # after a write would hold this session's transaction open across it -- the same
    # pattern that caused "database is locked" in the refresh worker.
    if not item.icon_url:
        # A photo is a nicety; failing to fetch one must not cost the user the add.
        with contextlib.suppress(SteamError):
            await services.pricing.ensure_meta(session, item)

    limit = services.settings.max_watchlist_per_user
    if await watch_repo.count_for_user(session, user.id) >= limit:
        notice = t("item.watchlist_full", user.locale, limit=limit)
    else:
        _, created = await watch_repo.add(session, user.id, item.id)
        if created:
            item.watchers_count = (item.watchers_count or 0) + 1
        notice = t("item.added" if created else "item.already_added", user.locale)

    # Brand-new item: no price yet, so put it at the front of the refresh queue.
    if item.last_success_at is None:
        services.worker.request_now(item)

    stats = await services.pricing.compute_stats(session, item, currency=user.currency)
    watching = await watch_repo.get(session, user.id, item.id) is not None
    markup = kb.item_keyboard(
        item, user.locale, watching=watching, span=user.default_chart_span
    )

    if item.icon_url:
        # Photo-first confirmation: you can see at a glance it is the right item.
        await show(
            event,
            f"{notice}\n\n{T.photo_caption(item, stats, user)}",
            markup,
            photo_url=icon_url(item.icon_url, "360fx360f"),
        )
        return

    await show(event, f"{notice}\n\n{T.item_card(item, stats, user, watching=watching)}", markup)


async def handle_query(
    message: Message, session: AsyncSession, user: User, services: Services, state: FSMContext
) -> None:
    raw = (message.text or "").strip()
    if not raw:
        return

    result = await services.items.resolve(session, raw, currency=user.currency)

    if result.error:
        await show(message, f"⚠️ {result.error}", kb.cancel_keyboard(user.locale))
        return

    if result.is_exact:
        await state.clear()
        await _adopt_and_open(message, session, user, services, result.item)
        return

    candidates = result.candidates or []
    if not candidates:
        await show(
            message,
            t("add.no_matches", user.locale, query=T.esc(raw)),
            kb.cancel_keyboard(user.locale),
        )
        return

    await state.set_state(AddItem.choosing_candidate)
    await state.update_data(candidates=[asdict(c) for c in candidates])
    await show(
        message,
        t("add.choose", user.locale, count=len(candidates), query=T.esc(raw)),
        kb.candidates_keyboard(candidates, user.locale),
    )


@router.message(AddItem.waiting_for_query, F.text)
async def on_query(
    message: Message, session: AsyncSession, user: User, services: Services, state: FSMContext
) -> None:
    await handle_query(message, session, user, services, state)


@router.message(AddItem.choosing_candidate, F.text)
async def on_requery(
    message: Message, session: AsyncSession, user: User, services: Services, state: FSMContext
) -> None:
    """Typing again while choosing simply starts a new search."""
    await handle_query(message, session, user, services, state)


@router.callback_query(PickCB.filter())
async def on_pick(
    query: CallbackQuery,
    callback_data: PickCB,
    session: AsyncSession,
    user: User,
    services: Services,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    raw_candidates = data.get("candidates") or []
    if callback_data.idx >= len(raw_candidates):
        await ack(query, t("add.expired", user.locale), alert=True)
        await state.clear()
        return

    item = await services.items.adopt(session, Candidate(**raw_candidates[callback_data.idx]))
    await state.clear()
    await ack(query)
    await _adopt_and_open(query, session, user, services, item)


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def on_loose_text(
    message: Message, session: AsyncSession, user: User, services: Services, state: FSMContext
) -> None:
    """Treat stray text as a search: people type the name before finding a button."""
    await handle_query(message, session, user, services, state)

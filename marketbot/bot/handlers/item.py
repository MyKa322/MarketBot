"""Item screens: card, price panel, photo, charts, watch/unwatch, refresh, mute."""
from __future__ import annotations

import contextlib
from datetime import timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from marketbot.bot import keyboards as kb
from marketbot.bot import texts as T
from marketbot.bot.callbacks import ChartCB, ItemCB
from marketbot.bot.ui import ack, show
from marketbot.constants import app_title
from marketbot.db.models import Item, User
from marketbot.db.repo import alerts as alert_repo
from marketbot.db.repo import items as item_repo
from marketbot.db.repo import prices as price_repo
from marketbot.db.repo import watchlist as watch_repo
from marketbot.i18n import t
from marketbot.registry import Services
from marketbot.services.charts import ChartPoint
from marketbot.steam.errors import SteamError
from marketbot.steam.parse import icon_url
from marketbot.utils.timeutil import utcnow

router = Router(name="item")

# Short ranges read raw snapshots; only 90d needs the daily rollup.
SPAN_DELTAS = {
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
SPANS = (*SPAN_DELTAS, "90d")


async def render_item(
    event: CallbackQuery,
    session: AsyncSession,
    user: User,
    services: Services,
    item: Item,
    *,
    span: str | None = None,
    notice: str | None = None,
) -> None:
    span = span or user.default_chart_span or "7d"
    stats = await services.pricing.compute_stats(session, item, currency=user.currency)
    watching = await watch_repo.get(session, user.id, item.id) is not None
    rules = await alert_repo.list_for_user(session, user.id, item_id=item.id)

    text = T.item_card(item, stats, user, watching=watching)
    if notice:
        text = f"{notice}\n\n{text}"
    await show(
        event,
        text,
        kb.item_keyboard(item, user.locale, watching=watching, span=span, has_alerts=bool(rules)),
    )


async def _load(session: AsyncSession, event, user: User, item_id: int) -> Item | None:
    item = await item_repo.by_id(session, item_id)
    if item is None:
        await show(event, t("item.gone", user.locale), kb.back_home(user.locale))
    return item


@router.callback_query(ItemCB.filter(F.action == "open"))
async def cb_open(
    query: CallbackQuery, callback_data: ItemCB, session: AsyncSession, user: User, services: Services
) -> None:
    await ack(query)
    item = await _load(session, query, user, callback_data.item_id)
    if item is None:
        return
    # A newly added item has no price at all; jump the queue so the card fills in.
    if item.last_success_at is None:
        services.worker.request_now(item)
    await render_item(query, session, user, services, item)


@router.callback_query(ItemCB.filter(F.action == "price"))
async def cb_price(
    query: CallbackQuery, callback_data: ItemCB, session: AsyncSession, user: User, services: Services
) -> None:
    """The quick answer: price now, and what you would actually receive after fees."""
    await ack(query)
    item = await _load(session, query, user, callback_data.item_id)
    if item is None:
        return
    if item.last_success_at is None:
        services.worker.request_now(item)
    stats = await services.pricing.compute_stats(session, item, currency=user.currency)
    await show(query, T.price_panel(item, stats, user), kb.price_keyboard(item.id, user.locale))


@router.callback_query(ItemCB.filter(F.action == "photo"))
async def cb_photo(
    query: CallbackQuery, callback_data: ItemCB, session: AsyncSession, user: User, services: Services
) -> None:
    """Send the item's Steam image.

    Telegram fetches the URL itself rather than us downloading it: the image lives on
    Steam's CDN, not the rate-limited market API, so this costs nothing from our
    request budget.
    """
    await ack(query)
    item = await _load(session, query, user, callback_data.item_id)
    if item is None:
        return

    if not item.icon_url:
        # The icon and item_nameid are scraped together; ask for that to happen.
        # If Steam refuses, fall through to the "no image yet" message below.
        with contextlib.suppress(SteamError):
            await services.pricing.ensure_meta(session, item)

    if not item.icon_url:
        await show(
            query,
            f"{t('item.no_photo', user.locale)}\n\n<b>{T.esc(item.market_hash_name)}</b>",
            kb.photo_keyboard(item.id, user.locale),
        )
        return

    stats = await services.pricing.compute_stats(session, item, currency=user.currency)
    await show(
        query,
        T.photo_caption(item, stats, user),
        kb.photo_keyboard(item.id, user.locale),
        photo_url=icon_url(item.icon_url, "360fx360f"),
    )


@router.callback_query(ItemCB.filter(F.action == "watch"))
async def cb_watch(
    query: CallbackQuery, callback_data: ItemCB, session: AsyncSession, user: User, services: Services
) -> None:
    item = await _load(session, query, user, callback_data.item_id)
    if item is None:
        return
    limit = services.settings.max_watchlist_per_user
    if await watch_repo.count_for_user(session, user.id) >= limit:
        await ack(query, t("item.watchlist_full", user.locale, limit=limit), alert=True)
        return

    _, created = await watch_repo.add(session, user.id, item.id)
    item.watchers_count = (item.watchers_count or 0) + int(created)
    if created:
        services.worker.request_now(item)
    await ack(query, t("item.added" if created else "item.already_added", user.locale))
    await render_item(query, session, user, services, item)


@router.callback_query(ItemCB.filter(F.action == "unwatch"))
async def cb_unwatch(
    query: CallbackQuery, callback_data: ItemCB, session: AsyncSession, user: User, services: Services
) -> None:
    item = await _load(session, query, user, callback_data.item_id)
    if item is None:
        return
    if await watch_repo.remove(session, user.id, item.id):
        item.watchers_count = max(0, (item.watchers_count or 1) - 1)
    await ack(query)
    await render_item(query, session, user, services, item)


@router.callback_query(ItemCB.filter(F.action == "refresh"))
async def cb_refresh(
    query: CallbackQuery, callback_data: ItemCB, session: AsyncSession, user: User, services: Services
) -> None:
    """Queue a refresh; never block the UI on a Steam round trip."""
    item = await _load(session, query, user, callback_data.item_id)
    if item is None:
        return
    queued = services.worker.request_now(item)
    notice = (
        t("item.queued", user.locale, backlog=len(services.planner))
        if queued
        else t("item.already_queued", user.locale)
    )
    await ack(query)
    await render_item(query, session, user, services, item, notice=notice)


@router.callback_query(ItemCB.filter(F.action == "mute"))
async def cb_mute(
    query: CallbackQuery, callback_data: ItemCB, session: AsyncSession, user: User
) -> None:
    """Silence one item without deleting the alert rules tuned on it."""
    entry = await watch_repo.get(session, user.id, callback_data.item_id)
    if entry is None:
        await ack(query, t("alerts.mute_needs_watch", user.locale), alert=True)
        return
    entry.muted = not entry.muted
    await session.flush()
    await ack(query, t("alerts.item_muted" if entry.muted else "alerts.item_unmuted", user.locale))

    from marketbot.bot.handlers.alerts import render_item_alerts

    await render_item_alerts(query, session, user, callback_data.item_id)


@router.callback_query(ChartCB.filter())
async def cb_chart(
    query: CallbackQuery, callback_data: ChartCB, session: AsyncSession, user: User, services: Services
) -> None:
    await ack(query)
    item = await _load(session, query, user, callback_data.item_id)
    if item is None:
        return

    span = callback_data.span if callback_data.span in SPANS else (
        user.default_chart_span if user.default_chart_span in SPANS else "7d"
    )
    now = utcnow()

    if span in SPAN_DELTAS:
        rows = await price_repo.series(session, item.id, since=now - SPAN_DELTAS[span])
        points = [
            ChartPoint(ts=r.captured_at, price_minor=r.price_minor, volume=r.volume)
            for r in rows
            if r.price_minor is not None
        ]
    else:
        # Beyond 30 days the daily rollup gives the same picture for a fraction of rows.
        rows = await price_repo.daily_series(
            session, item.id, since=(now - timedelta(days=90)).date()
        )
        points = [
            ChartPoint(
                ts=now.replace(
                    year=r.day.year, month=r.day.month, day=r.day.day,
                    hour=12, minute=0, second=0, microsecond=0,
                ),
                price_minor=r.close_minor,
                volume=r.volume,
            )
            for r in rows
        ]

    stats = await services.pricing.compute_stats(session, item, currency=user.currency)
    watching = await watch_repo.get(session, user.id, item.id) is not None
    rules = await alert_repo.list_for_user(session, user.id, item_id=item.id)

    subtitle = t(
        "chart.points",
        user.locale,
        label=t(f"span.label.{span}", user.locale),
        app=app_title(item.appid),
        count=len(points),
    )
    png = await services.charts.price_chart(
        points,
        title=item.market_hash_name,
        subtitle=subtitle,
        currency_code=user.currency,
        empty_message=t("chart.not_enough", user.locale),
    )
    await show(
        query,
        T.item_card(item, stats, user, watching=watching),
        kb.item_keyboard(item, user.locale, watching=watching, span=span, has_alerts=bool(rules)),
        photo=png,
        filename=f"{item.id}_{span}.png",
    )

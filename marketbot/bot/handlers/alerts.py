"""Alert management: presets, price targets, toggling, history."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from marketbot.bot import keyboards as kb
from marketbot.bot import texts as T
from marketbot.bot.callbacks import AlertCB
from marketbot.bot.states import NewAlert
from marketbot.bot.ui import ack, show
from marketbot.db.models import AlertKind, User
from marketbot.db.repo import alerts as alert_repo
from marketbot.db.repo import items as item_repo
from marketbot.db.repo import watchlist as watch_repo
from marketbot.i18n import t
from marketbot.registry import Services
from marketbot.steam.parse import parse_price
from marketbot.utils.money import fmt_money

router = Router(name="alerts")


async def _names(session: AsyncSession, rows: list) -> dict[int, str]:
    names: dict[int, str] = {}
    for row in rows:
        if row.item_id not in names:
            item = await item_repo.by_id(session, row.item_id)
            names[row.item_id] = item.market_hash_name if item else f"#{row.item_id}"
    return names


async def _render_list(event, session: AsyncSession, user: User) -> None:
    rules = await alert_repo.list_for_user(session, user.id)
    await show(
        event,
        T.alerts_screen(rules, await _names(session, rules), user),
        kb.alerts_list_keyboard(rules, user.locale),
    )


async def render_item_alerts(
    event, session: AsyncSession, user: User, item_id: int, *, notice: str | None = None
) -> None:
    item = await item_repo.by_id(session, item_id)
    if item is None:
        await show(event, t("item.gone", user.locale), kb.back_home(user.locale))
        return
    rules = await alert_repo.list_for_user(session, user.id, item_id=item_id)
    entry = await watch_repo.get(session, user.id, item_id)

    text = T.item_alerts_screen(item, rules, user)
    if notice:
        text = f"{notice}\n\n{text}"
    await show(
        event,
        text,
        kb.alert_item_keyboard(item_id, rules, user.locale, muted=bool(entry and entry.muted)),
    )


@router.message(Command("alerts"))
async def cmd_alerts(message: Message, session: AsyncSession, user: User) -> None:
    await _render_list(message, session, user)


@router.callback_query(AlertCB.filter(F.action == "list"))
async def cb_list(query: CallbackQuery, session: AsyncSession, user: User) -> None:
    await ack(query)
    await _render_list(query, session, user)


@router.callback_query(AlertCB.filter(F.action == "item"))
async def cb_item(
    query: CallbackQuery, callback_data: AlertCB, session: AsyncSession, user: User
) -> None:
    await ack(query)
    await render_item_alerts(query, session, user, callback_data.item_id)


@router.callback_query(AlertCB.filter(F.action == "preset"))
async def cb_preset(
    query: CallbackQuery,
    callback_data: AlertCB,
    session: AsyncSession,
    user: User,
    services: Services,
) -> None:
    try:
        key, kind, pct, window = kb.ALERT_PRESETS[int(callback_data.preset)]
    except (ValueError, IndexError):
        await ack(query, t("error.not_found", user.locale), alert=True)
        return

    limit = services.settings.max_rules_per_user
    if await alert_repo.count_for_user(session, user.id) >= limit:
        await ack(query, t("alerts.limit", user.locale, limit=limit), alert=True)
        return

    await alert_repo.create(
        session,
        user_id=user.id,
        item_id=callback_data.item_id,
        kind=kind,
        threshold_pct=pct,
        window_minutes=window,
        cooldown_minutes=services.settings.alert_default_cooldown_min,
    )
    await ack(query, t("alerts.created", user.locale))
    await render_item_alerts(
        query,
        session,
        user,
        callback_data.item_id,
        notice=t("alerts.added", user.locale, label=t(key, user.locale)),
    )


@router.callback_query(AlertCB.filter(F.action == "kind"))
async def cb_kind(
    query: CallbackQuery,
    callback_data: AlertCB,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    await ack(query)
    lang = user.locale
    if callback_data.kind == "ABS":
        await show(
            query,
            f"{t('alerts.target_title', lang)}\n\n{t('alerts.target_direction', lang)}",
            kb.alert_direction_keyboard(callback_data.item_id, lang),
        )
        return

    item = await item_repo.by_id(session, callback_data.item_id)
    if item is None:
        await ack(query, t("error.not_found", lang), alert=True)
        return

    await state.set_state(NewAlert.waiting_for_threshold)
    await state.update_data(item_id=callback_data.item_id, kind=callback_data.kind)
    direction = t(
        "alerts.dir_below"
        if callback_data.kind == AlertKind.ABS_BELOW.value
        else "alerts.dir_above",
        lang,
    )
    await show(
        query,
        t(
            "alerts.target_prompt",
            lang,
            name=T.esc(item.market_hash_name),
            direction=direction,
            currency=T.esc(user.currency),
        ),
        kb.cancel_keyboard(lang),
    )


@router.message(NewAlert.waiting_for_threshold, F.text)
async def on_threshold(
    message: Message, session: AsyncSession, user: User, services: Services, state: FSMContext
) -> None:
    data = await state.get_data()
    item_id, kind_value = data.get("item_id"), data.get("kind")
    threshold = parse_price(message.text, user.currency)

    if threshold is None or threshold <= 0:
        await show(message, t("alerts.bad_price", user.locale), kb.cancel_keyboard(user.locale))
        return

    await alert_repo.create(
        session,
        user_id=user.id,
        item_id=int(item_id),
        kind=AlertKind(kind_value),
        threshold_minor=threshold,
        cooldown_minutes=services.settings.alert_default_cooldown_min,
    )
    await state.clear()
    await render_item_alerts(
        message,
        session,
        user,
        int(item_id),
        notice=t("alerts.set_at", user.locale, price=fmt_money(threshold, user.currency)),
    )


@router.callback_query(AlertCB.filter(F.action == "toggle"))
async def cb_toggle(
    query: CallbackQuery, callback_data: AlertCB, session: AsyncSession, user: User
) -> None:
    rule = await alert_repo.by_id(session, callback_data.rule_id)
    if rule is None or rule.user_id != user.id:
        await ack(query, t("alerts.not_found", user.locale), alert=True)
        return
    await alert_repo.toggle(session, rule)
    await ack(query, t("alerts.enabled" if rule.is_active else "alerts.muted", user.locale))

    if callback_data.item_id:
        await render_item_alerts(query, session, user, callback_data.item_id)
    else:
        await _render_list(query, session, user)


@router.callback_query(AlertCB.filter(F.action == "delete"))
async def cb_delete(
    query: CallbackQuery, callback_data: AlertCB, session: AsyncSession, user: User
) -> None:
    removed = await alert_repo.remove(session, user.id, callback_data.rule_id)
    await ack(query, t("alerts.deleted" if removed else "alerts.already_gone", user.locale))
    if callback_data.item_id:
        await render_item_alerts(query, session, user, callback_data.item_id)
    else:
        await _render_list(query, session, user)


@router.callback_query(AlertCB.filter(F.action == "history"))
async def cb_history(query: CallbackQuery, session: AsyncSession, user: User) -> None:
    await ack(query)
    events = await alert_repo.recent_events(session, user.id, limit=10)
    await show(
        query,
        T.alert_history_screen(events, await _names(session, events), user),
        kb.back_home(user.locale),
    )

"""Preferences: language, notifications, display, data export."""
from __future__ import annotations

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from marketbot.bot import keyboards as kb
from marketbot.bot import texts as T
from marketbot.bot.callbacks import SettingsCB
from marketbot.bot.states import Prefs
from marketbot.bot.ui import ack, show
from marketbot.constants import CURRENCY_BY_CODE
from marketbot.db.models import User
from marketbot.db.repo import users as user_repo
from marketbot.i18n import LANGUAGES, language_name, t

router = Router(name="settings")

QUIET_RE = re.compile(r"^\s*(\d{1,2})\s*[-:–]\s*(\d{1,2})\s*$")


async def _home(event, user: User) -> None:
    await show(event, T.settings_screen(user), kb.settings_keyboard(user))


async def _notifications(event, user: User) -> None:
    await show(event, T.notification_settings_screen(user), kb.notification_keyboard(user))


async def _display(event, user: User) -> None:
    await show(event, T.display_settings_screen(user), kb.display_keyboard(user))


@router.message(Command("settings"))
async def cmd_settings(message: Message, user: User) -> None:
    await _home(message, user)


@router.message(Command("notifications"))
async def cmd_notifications(message: Message, user: User) -> None:
    await _notifications(message, user)


@router.message(Command("language"))
async def cmd_language(message: Message, user: User) -> None:
    await show(message, T.language_screen(user), kb.language_keyboard(user.locale))


@router.callback_query(SettingsCB.filter(F.action == "home"))
async def cb_home(query: CallbackQuery, user: User) -> None:
    await ack(query)
    await _home(query, user)


@router.callback_query(SettingsCB.filter(F.action == "notif"))
async def cb_notifications(query: CallbackQuery, user: User) -> None:
    await ack(query)
    await _notifications(query, user)


@router.callback_query(SettingsCB.filter(F.action == "display"))
async def cb_display(query: CallbackQuery, user: User) -> None:
    await ack(query)
    await _display(query, user)


# -------------------------------------------------------------------- language


@router.callback_query(SettingsCB.filter(F.action == "language"))
async def cb_language(query: CallbackQuery, user: User) -> None:
    await ack(query)
    await show(query, T.language_screen(user), kb.language_keyboard(user.locale))


@router.callback_query(SettingsCB.filter(F.action == "set_lang"))
async def cb_set_language(
    query: CallbackQuery, callback_data: SettingsCB, session: AsyncSession, user: User
) -> None:
    code = callback_data.value
    if code not in LANGUAGES:
        await ack(query, t("error.not_found", user.locale), alert=True)
        return
    await user_repo.update_prefs(session, user, locale=code)
    # Acknowledge in the language just chosen, not the one being left.
    await ack(query, t("lang.changed", code, language=language_name(code)))
    await _home(query, user)


# --------------------------------------------------------------------- toggles


@router.callback_query(SettingsCB.filter(F.action == "alerts"))
async def cb_toggle_alerts(query: CallbackQuery, session: AsyncSession, user: User) -> None:
    await user_repo.update_prefs(session, user, alerts_enabled=not user.alerts_enabled)
    await ack(query, t("notif.alerts_on" if user.alerts_enabled else "notif.alerts_off", user.locale))
    await _notifications(query, user)


@router.callback_query(SettingsCB.filter(F.action == "sound"))
async def cb_toggle_sound(query: CallbackQuery, session: AsyncSession, user: User) -> None:
    await user_repo.update_prefs(session, user, silent_alerts=not user.silent_alerts)
    await ack(query, t("notif.silent_on" if user.silent_alerts else "notif.silent_off", user.locale))
    await _notifications(query, user)


@router.callback_query(SettingsCB.filter(F.action == "digest"))
async def cb_toggle_digest(query: CallbackQuery, session: AsyncSession, user: User) -> None:
    await user_repo.update_prefs(session, user, digest_enabled=not user.digest_enabled)
    await ack(
        query,
        t("notif.digest_on", user.locale, hour=f"{user.digest_hour:02d}")
        if user.digest_enabled
        else t("notif.digest_off", user.locale),
    )
    await _notifications(query, user)


# --------------------------------------------------------------------- choices


@router.callback_query(SettingsCB.filter(F.action == "digest_hour"))
async def cb_digest_hour(query: CallbackQuery, user: User) -> None:
    await ack(query)
    lang = user.locale
    await show(
        query,
        f"{t('digest.hour_title', lang)}\n\n{t('digest.hour_hint', lang, tz=T.esc(user.timezone))}",
        kb.digest_hour_keyboard(user.digest_hour, lang),
    )


@router.callback_query(SettingsCB.filter(F.action == "set_digest_hour"))
async def cb_set_digest_hour(
    query: CallbackQuery, callback_data: SettingsCB, session: AsyncSession, user: User
) -> None:
    if not callback_data.value.isdigit() or not 0 <= int(callback_data.value) <= 23:
        await ack(query, t("error.not_found", user.locale), alert=True)
        return
    await user_repo.update_prefs(
        session, user, digest_hour=int(callback_data.value), digest_enabled=True
    )
    await ack(query, t("digest.hour_changed", user.locale, hour=f"{user.digest_hour:02d}"))
    await _notifications(query, user)


@router.callback_query(SettingsCB.filter(F.action == "cap"))
async def cb_cap(query: CallbackQuery, user: User) -> None:
    await ack(query)
    lang = user.locale
    await show(
        query,
        f"{t('cap.title', lang)}\n\n{t('cap.hint', lang)}",
        kb.alert_cap_keyboard(user.alert_cap_per_hour, lang),
    )


@router.callback_query(SettingsCB.filter(F.action == "set_cap"))
async def cb_set_cap(
    query: CallbackQuery, callback_data: SettingsCB, session: AsyncSession, user: User
) -> None:
    raw = callback_data.value
    value = int(raw) if raw.isdigit() and int(raw) > 0 else None
    await user_repo.update_prefs(session, user, alert_cap_per_hour=value)
    await ack(
        query,
        t("cap.changed", user.locale, cap=value) if value else t("cap.default", user.locale),
    )
    await _notifications(query, user)


@router.callback_query(SettingsCB.filter(F.action == "tz"))
async def cb_timezone(query: CallbackQuery, user: User, state: FSMContext) -> None:
    await ack(query)
    await state.set_state(Prefs.waiting_for_timezone)
    lang = user.locale
    await show(
        query,
        f"{t('tz.title', lang)}\n\n{t('tz.hint', lang)}",
        kb.timezone_keyboard(user.timezone, lang),
    )


@router.callback_query(SettingsCB.filter(F.action == "set_tz"))
async def cb_set_timezone(
    query: CallbackQuery,
    callback_data: SettingsCB,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    try:
        ZoneInfo(callback_data.value)
    except (ZoneInfoNotFoundError, ValueError):
        await ack(query, t("tz.unknown", user.locale), alert=True)
        return
    await user_repo.update_prefs(session, user, timezone=callback_data.value)
    await state.clear()
    await ack(query, t("tz.changed", user.locale, tz=user.timezone))
    await _notifications(query, user)


@router.message(Prefs.waiting_for_timezone, F.text)
async def on_timezone_text(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    name = (message.text or "").strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        await show(
            message,
            t("tz.invalid", user.locale, name=T.esc(name)),
            kb.timezone_keyboard(user.timezone, user.locale),
        )
        return
    await user_repo.update_prefs(session, user, timezone=name)
    await state.clear()
    await _notifications(message, user)


@router.callback_query(SettingsCB.filter(F.action == "quiet"))
async def cb_quiet(query: CallbackQuery, user: User, state: FSMContext) -> None:
    await ack(query)
    await state.set_state(Prefs.waiting_for_quiet_hours)
    lang = user.locale
    await show(
        query,
        f"{t('quiet.title', lang)}\n\n{t('quiet.prompt', lang, tz=T.esc(user.timezone))}",
        kb.cancel_keyboard(lang),
    )


@router.message(Prefs.waiting_for_quiet_hours, F.text)
async def on_quiet_hours(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    text = (message.text or "").strip().lower()

    if text in {"off", "none", "disable"}:
        await user_repo.update_prefs(session, user, quiet_from=None, quiet_to=None)
        await state.clear()
        await _notifications(message, user)
        return

    match = QUIET_RE.match(text)
    if not match:
        await show(message, t("quiet.invalid", user.locale), kb.cancel_keyboard(user.locale))
        return

    start, end = int(match.group(1)), int(match.group(2))
    if not (0 <= start <= 23 and 0 <= end <= 23):
        await show(message, t("quiet.out_of_range", user.locale), kb.cancel_keyboard(user.locale))
        return

    await user_repo.update_prefs(session, user, quiet_from=start, quiet_to=end)
    await state.clear()
    await _notifications(message, user)


# --------------------------------------------------------------------- display


@router.callback_query(SettingsCB.filter(F.action == "currency"))
async def cb_currency(query: CallbackQuery, user: User) -> None:
    await ack(query)
    lang = user.locale
    await show(
        query,
        f"{t('currency.title', lang)}\n\n{t('currency.hint', lang)}",
        kb.currency_keyboard(user.currency, lang),
    )


@router.callback_query(SettingsCB.filter(F.action == "set_currency"))
async def cb_set_currency(
    query: CallbackQuery, callback_data: SettingsCB, session: AsyncSession, user: User
) -> None:
    code = callback_data.value.upper()
    if code not in CURRENCY_BY_CODE:
        await ack(query, t("error.not_found", user.locale), alert=True)
        return
    await user_repo.update_prefs(session, user, currency=code)
    await ack(query, t("currency.changed", user.locale, code=code))
    await _display(query, user)


@router.callback_query(SettingsCB.filter(F.action == "span"))
async def cb_span(query: CallbackQuery, user: User) -> None:
    await ack(query)
    lang = user.locale
    await show(
        query,
        f"{t('span.title', lang)}\n\n{t('span.hint', lang)}",
        kb.chart_span_keyboard(user.default_chart_span, lang),
    )


@router.callback_query(SettingsCB.filter(F.action == "set_span"))
async def cb_set_span(
    query: CallbackQuery, callback_data: SettingsCB, session: AsyncSession, user: User
) -> None:
    if callback_data.value not in kb.CHART_SPANS:
        await ack(query, t("error.not_found", user.locale), alert=True)
        return
    await user_repo.update_prefs(session, user, default_chart_span=callback_data.value)
    await ack(query, t("span.changed", user.locale, span=callback_data.value))
    await _display(query, user)


@router.callback_query(SettingsCB.filter(F.action == "sort"))
async def cb_sort(query: CallbackQuery, user: User) -> None:
    await ack(query)
    lang = user.locale
    await show(
        query,
        f"{t('sort.title', lang)}\n\n{t('sort.hint', lang)}",
        kb.watchlist_sort_keyboard(user.watchlist_sort, lang),
    )


@router.callback_query(SettingsCB.filter(F.action == "set_sort"))
async def cb_set_sort(
    query: CallbackQuery, callback_data: SettingsCB, session: AsyncSession, user: User
) -> None:
    if callback_data.value not in kb.SORT_CHOICES:
        await ack(query, t("error.not_found", user.locale), alert=True)
        return
    await user_repo.update_prefs(session, user, watchlist_sort=callback_data.value)
    await ack(query, t("sort.changed", user.locale))
    await _display(query, user)


# ---------------------------------------------------------------------- export


@router.callback_query(SettingsCB.filter(F.action == "export"))
async def cb_export(query: CallbackQuery, session: AsyncSession, user: User) -> None:
    """Hand back the user's own watchlist as CSV."""
    from aiogram.types import BufferedInputFile

    from marketbot.services.export import export_watchlist

    await ack(query, t("export.preparing", user.locale))
    csv_bytes, rows = await export_watchlist(session, user)

    if rows == 0:
        await show(query, t("export.empty", user.locale), kb.settings_keyboard(user))
        return

    await query.message.answer_document(
        BufferedInputFile(csv_bytes, filename=f"marketbot_watchlist_{user.telegram_id}.csv"),
        caption=t("export.caption", user.locale, rows=rows),
    )

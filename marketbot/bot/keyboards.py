"""Inline keyboard builders.

Every label goes through t(): a half-translated interface, where the text is localised
but the buttons are not, reads worse than an untranslated one.

Layout rules:
* the primary action for a screen sits on its own full-width row;
* every screen except the root has an explicit way back;
* active state (selected chart range, alerts on/off) is visible in the label itself,
  because Telegram offers no other affordance for it.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from marketbot.bot.callbacks import (
    AlertCB,
    ChartCB,
    ItemCB,
    MenuCB,
    NoopCB,
    PickCB,
    SettingsCB,
    WatchCB,
)
from marketbot.constants import CURRENCIES
from marketbot.db.models import AlertKind, Item, User
from marketbot.i18n import LANGUAGES, t
from marketbot.steam.parse import build_market_url
from marketbot.utils.money import fmt_money

# Short ranges first: "what is it doing right now" is the common question, and 30m/1h
# only became answerable once snapshots were dense enough to support them.
CHART_SPANS = ("30m", "1h", "6h", "24h", "7d", "30d", "90d")


def main_menu(lang: str, *, webapp_url: str | None = None) -> InlineKeyboardMarkup:
    """Four destinations. Adding an item lives in the watchlist, where it belongs."""
    kb = InlineKeyboardBuilder()
    kb.button(text=t("btn.watchlist", lang), callback_data=WatchCB(action="list"))
    kb.button(text=t("btn.alerts", lang), callback_data=AlertCB(action="list"))
    kb.button(text=t("btn.settings", lang), callback_data=SettingsCB(action="home"))
    kb.button(text=t("btn.help", lang), callback_data=MenuCB(action="help"))
    kb.adjust(2, 2)
    if webapp_url:
        kb.row(
            InlineKeyboardButton(
                text=t("btn.dashboard", lang), web_app=WebAppInfo(url=webapp_url)
            )
        )
    return kb.as_markup()


def back_home(lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t("btn.menu", lang), callback_data=MenuCB(action="home"))
    return kb.as_markup()


def cancel_keyboard(lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=t("btn.cancel", lang), callback_data=MenuCB(action="home"))
    return kb.as_markup()


def item_keyboard(
    item: Item, lang: str, *, watching: bool, span: str = "7d", has_alerts: bool = False
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    # Price and photo are the two things people open an item for; they lead.
    kb.row(
        InlineKeyboardButton(
            text=t("btn.price", lang), callback_data=ItemCB(action="price", item_id=item.id).pack()
        ),
        InlineKeyboardButton(
            text=t("btn.photo", lang), callback_data=ItemCB(action="photo", item_id=item.id).pack()
        ),
        InlineKeyboardButton(
            text=t("btn.refresh", lang),
            callback_data=ItemCB(action="refresh", item_id=item.id).pack(),
        ),
    )

    # Seven ranges over two rows: the bracketed one is the current selection.
    for value in CHART_SPANS:
        label = t(f"span.{value}", lang)
        kb.button(
            text=f"[{label}]" if value == span else label,
            callback_data=ChartCB(item_id=item.id, span=value),
        )
    kb.adjust(3, 4, 3)

    kb.row(
        InlineKeyboardButton(
            text=t("btn.unwatch" if watching else "btn.watch", lang),
            callback_data=ItemCB(action="unwatch" if watching else "watch", item_id=item.id).pack(),
        ),
        InlineKeyboardButton(
            text=t("btn.alerts", lang),
            callback_data=AlertCB(action="item", item_id=item.id).pack(),
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text=t("btn.steam", lang),
            url=build_market_url(item.appid, item.market_hash_name),
        )
    )
    kb.row(
        InlineKeyboardButton(text=t("btn.watchlist", lang), callback_data=WatchCB(action="list").pack()),
        InlineKeyboardButton(text=t("btn.menu", lang), callback_data=MenuCB(action="home").pack()),
    )
    return kb.as_markup()


def watchlist_keyboard(
    rows: list[dict], lang: str, *, page: int, pages: int
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for row in rows:
        item: Item = row["item"]
        name = item.market_hash_name
        label = name if len(name) <= 30 else name[:29] + "…"
        price = row.get("price_minor")
        if price is not None:
            label = f"{label} · {fmt_money(price, row.get('currency', 'USD'))}"
        kb.row(
            InlineKeyboardButton(
                text=label, callback_data=ItemCB(action="open", item_id=item.id).pack()
            )
        )

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="◀", callback_data=WatchCB(action="page", page=page - 1).pack()
                )
            )
        nav.append(
            InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=NoopCB(tag="pg").pack())
        )
        if page + 1 < pages:
            nav.append(
                InlineKeyboardButton(
                    text="▶", callback_data=WatchCB(action="page", page=page + 1).pack()
                )
            )
        kb.row(*nav)

    # Adding lives here rather than in the main menu: this is the screen you are on
    # when you notice something is missing.
    kb.row(
        InlineKeyboardButton(text=t("btn.add_item", lang), callback_data=WatchCB(action="add").pack()),
        InlineKeyboardButton(
            text=t("btn.overview", lang), callback_data=WatchCB(action="overview").pack()
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text=t("btn.refresh_all", lang), callback_data=WatchCB(action="refresh_all").pack()
        ),
        InlineKeyboardButton(text=t("btn.menu", lang), callback_data=MenuCB(action="home").pack()),
    )
    return kb.as_markup()


def overview_keyboard(rows: list[dict], lang: str) -> InlineKeyboardMarkup:
    """The compact panel: every tracked item on one screen, two per row."""
    kb = InlineKeyboardBuilder()
    for row in rows[:24]:
        item: Item = row["item"]
        name = item.market_hash_name
        kb.button(
            text=name if len(name) <= 18 else name[:17] + "…",
            callback_data=ItemCB(action="open", item_id=item.id),
        )
    kb.adjust(2)
    kb.row(
        InlineKeyboardButton(
            text=t("btn.refresh_all", lang), callback_data=WatchCB(action="refresh_all").pack()
        ),
        InlineKeyboardButton(
            text=t("btn.watchlist", lang), callback_data=WatchCB(action="list").pack()
        ),
    )
    kb.row(InlineKeyboardButton(text=t("btn.menu", lang), callback_data=MenuCB(action="home").pack()))
    return kb.as_markup()


def price_keyboard(item_id: int, lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=t("btn.chart", lang),
            callback_data=ChartCB(item_id=item_id, span="24h").pack(),
        ),
        InlineKeyboardButton(
            text=t("btn.photo", lang), callback_data=ItemCB(action="photo", item_id=item_id).pack()
        ),
        InlineKeyboardButton(
            text=t("btn.refresh", lang),
            callback_data=ItemCB(action="refresh", item_id=item_id).pack(),
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text=t("btn.item", lang), callback_data=ItemCB(action="open", item_id=item_id).pack()
        ),
        InlineKeyboardButton(text=t("btn.menu", lang), callback_data=MenuCB(action="home").pack()),
    )
    return kb.as_markup()


def photo_keyboard(item_id: int, lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=t("btn.price", lang), callback_data=ItemCB(action="price", item_id=item_id).pack()
        ),
        InlineKeyboardButton(
            text=t("btn.chart", lang), callback_data=ChartCB(item_id=item_id, span="7d").pack()
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text=t("btn.item", lang), callback_data=ItemCB(action="open", item_id=item_id).pack()
        ),
        InlineKeyboardButton(text=t("btn.menu", lang), callback_data=MenuCB(action="home").pack()),
    )
    return kb.as_markup()


def candidates_keyboard(candidates: list, lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for idx, cand in enumerate(candidates[:8]):
        label = cand.display_name
        if len(label) > 34:
            label = label[:33] + "…"
        if cand.price_minor:
            label = f"{label} · {fmt_money(cand.price_minor, 'USD')}"
        kb.row(InlineKeyboardButton(text=label, callback_data=PickCB(idx=idx).pack()))
    kb.row(
        InlineKeyboardButton(
            text=t("btn.search_again", lang), callback_data=WatchCB(action="add").pack()
        ),
        InlineKeyboardButton(text=t("btn.cancel", lang), callback_data=MenuCB(action="home").pack()),
    )
    return kb.as_markup()


# (translation key, kind, threshold_pct, window_minutes)
ALERT_PRESETS = (
    ("preset.drop_5_1h", AlertKind.PCT_DROP, 5.0, 60),
    ("preset.drop_10_24h", AlertKind.PCT_DROP, 10.0, 1440),
    ("preset.spike_5_1h", AlertKind.PCT_SPIKE, 5.0, 60),
    ("preset.spike_10_24h", AlertKind.PCT_SPIKE, 10.0, 1440),
    ("preset.volume_3x", AlertKind.VOLUME_SPIKE, 300.0, 1440),
)


def alert_item_keyboard(
    item_id: int, rules: list, lang: str, *, muted: bool = False
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for idx, preset in enumerate(ALERT_PRESETS):
        kb.button(
            text=t(preset[0], lang),
            callback_data=AlertCB(action="preset", item_id=item_id, preset=str(idx)),
        )
    kb.adjust(2, 2, 1)

    kb.row(
        InlineKeyboardButton(
            text=t("btn.price_target", lang),
            callback_data=AlertCB(action="kind", item_id=item_id, kind="ABS").pack(),
        )
    )
    for rule in rules:
        state = "\U0001F7E2" if rule.is_active else "⚪"
        kb.row(
            InlineKeyboardButton(
                text=f"{state} {t('btn.toggle', lang)}",
                callback_data=AlertCB(action="toggle", rule_id=rule.id, item_id=item_id).pack(),
            ),
            InlineKeyboardButton(
                text=t("btn.delete", lang),
                callback_data=AlertCB(action="delete", rule_id=rule.id, item_id=item_id).pack(),
            ),
        )
    kb.row(
        InlineKeyboardButton(
            text=t("btn.unmute" if muted else "btn.mute", lang),
            callback_data=ItemCB(action="mute", item_id=item_id).pack(),
        )
    )
    kb.row(
        InlineKeyboardButton(
            text=t("btn.item", lang), callback_data=ItemCB(action="open", item_id=item_id).pack()
        ),
        InlineKeyboardButton(text=t("btn.menu", lang), callback_data=MenuCB(action="home").pack()),
    )
    return kb.as_markup()


def alert_direction_keyboard(item_id: int, lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text=t("btn.notify_below", lang),
        callback_data=AlertCB(action="kind", item_id=item_id, kind=AlertKind.ABS_BELOW.value),
    )
    kb.button(
        text=t("btn.notify_above", lang),
        callback_data=AlertCB(action="kind", item_id=item_id, kind=AlertKind.ABS_ABOVE.value),
    )
    kb.adjust(1)
    kb.row(
        InlineKeyboardButton(
            text=t("btn.back", lang),
            callback_data=AlertCB(action="item", item_id=item_id).pack(),
        )
    )
    return kb.as_markup()


def alerts_list_keyboard(rules: list, lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for rule in rules[:10]:
        kb.row(
            InlineKeyboardButton(
                text=f"{'\U0001F7E2' if rule.is_active else '⚪'} #{rule.id}",
                callback_data=AlertCB(action="toggle", rule_id=rule.id).pack(),
            ),
            InlineKeyboardButton(
                text=t("btn.item", lang),
                callback_data=ItemCB(action="open", item_id=rule.item_id).pack(),
            ),
            InlineKeyboardButton(
                text=t("btn.delete", lang),
                callback_data=AlertCB(action="delete", rule_id=rule.id).pack(),
            ),
        )
    kb.row(
        InlineKeyboardButton(text=t("btn.history", lang), callback_data=AlertCB(action="history").pack()),
        InlineKeyboardButton(text=t("btn.menu", lang), callback_data=MenuCB(action="home").pack()),
    )
    return kb.as_markup()


def alert_push_keyboard(item_id: int, rule_id: int, lang: str) -> InlineKeyboardMarkup:
    """Attached to the alert itself: act without hunting through menus."""
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=t("btn.price", lang), callback_data=ItemCB(action="price", item_id=item_id).pack()
        ),
        InlineKeyboardButton(
            text=t("btn.chart", lang), callback_data=ChartCB(item_id=item_id, span="24h").pack()
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text=t("btn.mute", lang),
            callback_data=AlertCB(action="toggle", rule_id=rule_id, item_id=item_id).pack(),
        )
    )
    return kb.as_markup()


def digest_keyboard(lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=t("btn.overview", lang), callback_data=WatchCB(action="overview").pack()
        ),
        InlineKeyboardButton(
            text=t("btn.watchlist", lang), callback_data=WatchCB(action="list").pack()
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text=t("digest.btn_off", lang), callback_data=SettingsCB(action="digest").pack()
        )
    )
    return kb.as_markup()


# Curated rather than exhaustive: 400+ IANA zones in an inline keyboard is unusable,
# and "type your timezone" alone is worse. Anyone outside this list can still send an
# IANA name as text.
COMMON_TIMEZONES = (
    ("UTC", "UTC"),
    ("London", "Europe/London"),
    ("Berlin", "Europe/Berlin"),
    ("Moscow", "Europe/Moscow"),
    ("Kyiv", "Europe/Kyiv"),
    ("Istanbul", "Europe/Istanbul"),
    ("Dubai", "Asia/Dubai"),
    ("Karachi", "Asia/Karachi"),
    ("Delhi", "Asia/Kolkata"),
    ("Shanghai", "Asia/Shanghai"),
    ("Tokyo", "Asia/Tokyo"),
    ("Sydney", "Australia/Sydney"),
    ("São Paulo", "America/Sao_Paulo"),
    ("New York", "America/New_York"),
    ("Chicago", "America/Chicago"),
    ("Denver", "America/Denver"),
    ("Los Angeles", "America/Los_Angeles"),
)

SORT_CHOICES = ("name", "change", "price", "updated")
ALERT_CAP_CHOICES = (5, 10, 20, 50)


def settings_keyboard(user: User) -> InlineKeyboardMarkup:
    """Top level: three sections instead of a flat list of every toggle."""
    lang = user.locale
    kb = InlineKeyboardBuilder()
    kb.button(text=t("btn.notifications", lang), callback_data=SettingsCB(action="notif"))
    kb.button(text=t("btn.display", lang), callback_data=SettingsCB(action="display"))
    kb.button(text=t("btn.language", lang), callback_data=SettingsCB(action="language"))
    kb.button(text=t("btn.export", lang), callback_data=SettingsCB(action="export"))
    kb.adjust(1)
    kb.row(InlineKeyboardButton(text=t("btn.menu", lang), callback_data=MenuCB(action="home").pack()))
    return kb.as_markup()


def notification_keyboard(user: User) -> InlineKeyboardMarkup:
    lang = user.locale
    on, off = t("settings.on", lang), t("settings.off", lang)
    kb = InlineKeyboardBuilder()
    kb.button(
        text=t("notif.btn_alerts", lang, state=on if user.alerts_enabled else off),
        callback_data=SettingsCB(action="alerts"),
    )
    kb.button(
        text=t(
            "notif.btn_sound",
            lang,
            state=t("notif.sound_silent" if user.silent_alerts else "notif.sound_normal", lang),
        ),
        callback_data=SettingsCB(action="sound"),
    )
    kb.button(text=t("notif.btn_quiet", lang), callback_data=SettingsCB(action="quiet"))
    kb.button(
        text=t("notif.btn_digest", lang, state=on if user.digest_enabled else off),
        callback_data=SettingsCB(action="digest"),
    )
    if user.digest_enabled:
        kb.button(
            text=t("notif.btn_digest_hour", lang, hour=f"{user.digest_hour:02d}"),
            callback_data=SettingsCB(action="digest_hour"),
        )
    kb.button(
        text=t("notif.btn_cap", lang, cap=user.alert_cap_per_hour or t("btn.default", lang)),
        callback_data=SettingsCB(action="cap"),
    )
    kb.button(text=t("notif.btn_tz", lang, tz=user.timezone), callback_data=SettingsCB(action="tz"))
    kb.adjust(1)
    kb.row(
        InlineKeyboardButton(
            text=t("btn.settings", lang), callback_data=SettingsCB(action="home").pack()
        )
    )
    return kb.as_markup()


def display_keyboard(user: User) -> InlineKeyboardMarkup:
    from marketbot.i18n import language_name

    lang = user.locale
    kb = InlineKeyboardBuilder()
    kb.button(
        text=t("display.btn_currency", lang, currency=user.currency),
        callback_data=SettingsCB(action="currency"),
    )
    kb.button(
        text=t("display.btn_span", lang, span=user.default_chart_span),
        callback_data=SettingsCB(action="span"),
    )
    kb.button(text=t("display.btn_sort", lang), callback_data=SettingsCB(action="sort"))
    kb.button(
        text=t("display.btn_language", lang, language=language_name(lang)),
        callback_data=SettingsCB(action="language"),
    )
    kb.adjust(1)
    kb.row(
        InlineKeyboardButton(
            text=t("btn.settings", lang), callback_data=SettingsCB(action="home").pack()
        )
    )
    return kb.as_markup()


def _choice_keyboard(
    action: str, choices, current, lang: str, *, columns: int = 3, back: str = "notif"
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for label, value in choices:
        mark = "✓ " if str(value) == str(current) else ""
        kb.button(text=f"{mark}{label}", callback_data=SettingsCB(action=action, value=str(value)))
    kb.adjust(columns)
    kb.row(
        InlineKeyboardButton(text=t("btn.back", lang), callback_data=SettingsCB(action=back).pack())
    )
    return kb.as_markup()


def language_keyboard(current: str) -> InlineKeyboardMarkup:
    choices = [(name, code) for code, name in LANGUAGES.items()]
    return _choice_keyboard("set_lang", choices, current, current, columns=2, back="home")


def timezone_keyboard(current: str, lang: str) -> InlineKeyboardMarkup:
    return _choice_keyboard("set_tz", COMMON_TIMEZONES, current, lang, columns=2)


def digest_hour_keyboard(current: int, lang: str) -> InlineKeyboardMarkup:
    hours = [(f"{h:02d}:00", h) for h in range(0, 24, 2)]
    return _choice_keyboard("set_digest_hour", hours, current, lang, columns=4)


def alert_cap_keyboard(current: int | None, lang: str) -> InlineKeyboardMarkup:
    choices = [(t("btn.default", lang), "0")] + [(str(n), str(n)) for n in ALERT_CAP_CHOICES]
    return _choice_keyboard("set_cap", choices, current or "0", lang, columns=3)


def chart_span_keyboard(current: str, lang: str) -> InlineKeyboardMarkup:
    choices = [(t(f"span.{s}", lang), s) for s in CHART_SPANS]
    return _choice_keyboard("set_span", choices, current, lang, columns=4, back="display")


def watchlist_sort_keyboard(current: str, lang: str) -> InlineKeyboardMarkup:
    choices = [(t(f"sort.{value}", lang), value) for value in SORT_CHOICES]
    return _choice_keyboard("set_sort", choices, current, lang, columns=2, back="display")


def currency_keyboard(current: str, lang: str) -> InlineKeyboardMarkup:
    choices = [(cur.code, cur.code) for cur in CURRENCIES[:12]]
    return _choice_keyboard("set_currency", choices, current, lang, columns=4, back="display")

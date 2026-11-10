"""Message rendering. HTML parse mode, every string via the i18n catalog."""
from __future__ import annotations

from collections.abc import Sequence
from html import escape

from marketbot.constants import app_title
from marketbot.db.models import AlertKind, Item, User
from marketbot.i18n import language_name, t
from marketbot.services.alerts import AlertTrigger
from marketbot.services.pricing import PriceStats
from marketbot.steam.fees import game_fee_percent, net_from_buyer_price
from marketbot.steam.parse import build_market_url
from marketbot.utils.money import fmt_money, fmt_pct
from marketbot.utils.timeutil import ensure_utc, utcnow

SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


def esc(text: object) -> str:
    return escape(str(text), quote=False)


def tinted(pct: float | None) -> str:
    """Percentage with a colour dot -- Telegram has no coloured text."""
    if pct is None:
        return "—"
    dot = "\U0001F7E2" if pct > 0 else ("\U0001F534" if pct < 0 else "⚪")
    return f"{dot} {fmt_pct(pct)}"


def sparkline(values: Sequence[int | None]) -> str:
    """Eight-level unicode sparkline; keeps a watchlist row to one line."""
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return ""
    low, high = min(clean), max(clean)
    if high == low:
        return SPARK_BLOCKS[3] * min(len(clean), 16)
    step = (high - low) / (len(SPARK_BLOCKS) - 1)
    return "".join(
        SPARK_BLOCKS[min(int((v - low) / step), len(SPARK_BLOCKS) - 1)] for v in clean[-16:]
    )



def age(moment, lang: str) -> str:
    """Localised relative time.

    utils.timeutil.humanize_age stays English-only on purpose: it belongs to a layer
    that knows nothing about users or languages. Formatting for a person is this
    layer's job.
    """
    if moment is None:
        return t("age.never", lang)
    seconds = int((utcnow() - ensure_utc(moment)).total_seconds())
    if seconds < 45:
        return t("age.just_now", lang)
    if seconds < 3600:
        return t("age.minutes", lang, n=seconds // 60)
    if seconds < 86400:
        return t("age.hours", lang, n=seconds // 3600)
    if seconds < 86400 * 30:
        return t("age.days", lang, n=seconds // 86400)
    return ensure_utc(moment).strftime("%Y-%m-%d")


def item_link(item: Item) -> str:
    url = build_market_url(item.appid, item.market_hash_name)
    return f'<a href="{url}">{esc(item.market_hash_name)}</a>'


def clip_name(name: str, limit: int = 34) -> str:
    return esc(name if len(name) <= limit else name[: limit - 1] + "…")


# ------------------------------------------------------------------------ menu


def welcome(user: User, *, watch_count: int, rule_count: int) -> str:
    lang = user.locale
    return "\n\n".join(
        [
            t("menu.title", lang),
            t("menu.greeting", lang, name=esc(user.first_name or "")).strip(),
            t("menu.blurb", lang),
            t("menu.stats", lang, watch=watch_count, rules=rule_count, currency=esc(user.currency)),
            t("menu.freshness", lang),
        ]
    )


def help_text(user: User) -> str:
    return t("help.text", user.locale)


def add_item_prompt(user: User) -> str:
    return t("add.prompt", user.locale)


# ------------------------------------------------------------------- watchlist


def watchlist_screen(rows: list[dict], user: User, *, page: int, pages: int) -> str:
    lang = user.locale
    if not rows:
        return t("watchlist.empty", lang)

    lines = [f"{t('watchlist.title', lang)}  {t('watchlist.count', lang, count=len(rows))}", ""]
    for row in rows:
        item: Item = row["item"]
        pin = "\U0001F4CC " if row.get("pinned") else ""
        mute = " \U0001F507" if row.get("muted") else ""
        lines.append(f"{pin}{mute}<b>{clip_name(item.market_hash_name, 42)}</b>")
        detail = f"   {fmt_money(row['price_minor'], row.get('currency', user.currency))}"
        detail += f"  {tinted(row.get('change_24h'))}"
        if row.get("spark"):
            detail += f"  <code>{row['spark']}</code>"
        lines.append(detail)
        lines.append(f"   <i>{age(row.get('updated_at'), lang)}</i>")
        lines.append("")

    if pages > 1:
        lines.append(t("watchlist.page", lang, page=page + 1, pages=pages))
    return "\n".join(lines).strip()


def overview_screen(rows: list[dict], user: User) -> str:
    """One compact panel showing everything tracked, newest prices included."""
    lang = user.locale
    if not rows:
        return f"{t('overview.title', lang)}\n\n{t('overview.empty', lang)}"

    lines = [t("overview.title", lang), ""]
    priced = 0
    total = 0
    newest = None

    for row in rows:
        item: Item = row["item"]
        price = row.get("price_minor")
        if price is not None:
            priced += 1
            total += price
        updated = row.get("updated_at")
        if updated and (newest is None or updated > newest):
            newest = updated

        mute = "\U0001F507" if row.get("muted") else ""
        change = row.get("change_24h")
        marker = "\U0001F7E2" if (change or 0) > 0 else ("\U0001F534" if (change or 0) < 0 else "⚪")
        price_text = fmt_money(price, row.get("currency", user.currency))
        lines.append(
            f"{marker} <b>{clip_name(item.market_hash_name, 30)}</b>{mute}\n"
            f"    {price_text}  ·  {fmt_pct(change) if change is not None else '—'}"
        )

    lines.append("")
    if total:
        lines.append(t("overview.totals", lang, total=fmt_money(total, user.currency)))
    lines.append(
        t(
            "overview.footer",
            lang,
            count=len(rows),
            priced=priced,
            updated=age(newest, lang),
        )
    )
    lines.append(t("overview.hint", lang))
    return "\n".join(lines)


# ------------------------------------------------------------------------ item


def item_card(item: Item, stats: PriceStats, user: User, *, watching: bool) -> str:
    lang = user.locale
    code = stats.currency or user.currency
    lines = [f"<b>{esc(item.market_hash_name)}</b>", f"<i>{esc(app_title(item.appid))}</i>", ""]

    if stats.current_minor is None:
        lines.append(t("item.no_data", lang))
        lines.append("")
        lines.append(t("item.no_data_hint", lang))
        return "\n".join(lines)

    lines.append(t("item.lowest", lang, price=fmt_money(stats.current_minor, code)))
    if stats.median_minor is not None:
        lines.append(t("item.median", lang, price=fmt_money(stats.median_minor, code)))

    lines.append("")
    lines.append(f"1h   {tinted(stats.change_1h)}")
    lines.append(f"24h  {tinted(stats.change_24h)}")
    lines.append(f"7d   {tinted(stats.change_7d)}")

    if stats.high_7d is not None and stats.low_7d is not None:
        lines.append("")
        lines.append(
            t(
                "item.range_7d",
                lang,
                low=fmt_money(stats.low_7d, code),
                high=fmt_money(stats.high_7d, code),
            )
        )
        if stats.volatility_7d is not None:
            lines.append(t("item.volatility", lang, value=f"{stats.volatility_7d:.1f}"))

    if stats.volume is not None:
        line = t("item.volume", lang, volume=f"{stats.volume:,}")
        if stats.avg_volume_24h:
            ratio = stats.volume / stats.avg_volume_24h
            if ratio >= 1.5:
                line += t("item.volume_ratio", lang, ratio=f"{ratio:.1f}")
        lines.append(line)

    if stats.best_buy_minor is not None and stats.best_sell_minor is not None:
        lines.append("")
        lines.append(
            t(
                "item.orderbook",
                lang,
                buy=fmt_money(stats.best_buy_minor, code),
                sell=fmt_money(stats.best_sell_minor, code),
            )
        )
        if stats.spread_pct is not None:
            lines.append(
                t(
                    "item.spread",
                    lang,
                    spread=fmt_money(stats.spread_minor, code),
                    pct=f"{stats.spread_pct:.1f}",
                )
            )

    lines.append("")
    lines.append(
        t("item.updated", lang, age=age(stats.last_update, lang), samples=stats.samples)
    )
    lines.append(t("item.watching" if watching else "item.not_watching", lang))
    return "\n".join(lines)


def price_panel(item: Item, stats: PriceStats, user: User) -> str:
    """The quick-answer screen: what is it worth, and what would I actually get?

    The net-of-fee figure is the one people get wrong -- Steam takes ~15% from the
    seller, so the listing price is never what lands in your wallet.
    """
    lang = user.locale
    code = stats.currency or user.currency
    lines = [t("price.title", lang), "", f"<b>{esc(item.market_hash_name)}</b>", ""]

    if stats.current_minor is None:
        lines.append(t("price.none", lang))
        return "\n".join(lines)

    lines.append(t("price.lowest", lang, price=fmt_money(stats.current_minor, code)))
    if stats.median_minor is not None:
        lines.append(t("price.median", lang, price=fmt_money(stats.median_minor, code)))

    breakdown = net_from_buyer_price(
        stats.current_minor, game_pct=game_fee_percent(item.appid)
    )
    lines.append("")
    lines.append(t("price.net", lang, net=fmt_money(breakdown.seller_receives, code)))
    lines.append(t("price.fee", lang, fee=fmt_money(breakdown.total_fee, code)))

    if stats.best_buy_minor is not None:
        lines.append("")
        lines.append(t("price.buy_order", lang, price=fmt_money(stats.best_buy_minor, code)))
    if stats.best_sell_minor is not None:
        lines.append(t("price.sell_order", lang, price=fmt_money(stats.best_sell_minor, code)))

    if stats.volume is not None:
        lines.append("")
        lines.append(t("price.volume", lang, volume=f"{stats.volume:,}"))

    lines.append("")
    lines.append(f"24h  {tinted(stats.change_24h)}")
    lines.append(t("price.age", lang, age=age(stats.last_update, lang)))
    return "\n".join(lines)


def photo_caption(item: Item, stats: PriceStats, user: User) -> str:
    """Caption under the item image. Kept short: Telegram caps captions at 1024."""
    lang = user.locale
    code = stats.currency or user.currency
    lines = [f"<b>{esc(item.market_hash_name)}</b>", f"<i>{esc(app_title(item.appid))}</i>"]
    if stats.current_minor is not None:
        lines.append("")
        lines.append(t("item.lowest", lang, price=fmt_money(stats.current_minor, code)))
        lines.append(f"24h  {tinted(stats.change_24h)}")
        lines.append("")
        lines.append(t("price.age", lang, age=age(stats.last_update, lang)))
    else:
        lines.append("")
        lines.append(t("item.no_data", lang))
    return "\n".join(lines)


# ---------------------------------------------------------------------- alerts


def describe_rule(rule, user: User) -> str:
    lang = user.locale
    code = user.currency
    if rule.kind is AlertKind.PCT_DROP:
        return t("rule.pct_drop", lang, pct=f"{rule.threshold_pct:g}", window=rule.window_minutes)
    if rule.kind is AlertKind.PCT_SPIKE:
        return t("rule.pct_spike", lang, pct=f"{rule.threshold_pct:g}", window=rule.window_minutes)
    if rule.kind is AlertKind.ABS_BELOW:
        return t("rule.abs_below", lang, price=fmt_money(rule.threshold_minor, code))
    if rule.kind is AlertKind.ABS_ABOVE:
        return t("rule.abs_above", lang, price=fmt_money(rule.threshold_minor, code))
    if rule.kind is AlertKind.VOLUME_SPIKE:
        return t(
            "rule.volume_spike", lang, pct=f"{rule.threshold_pct:g}", window=rule.window_minutes
        )
    return t("rule.custom", lang)


def alerts_screen(rules: list, names: dict[int, str], user: User) -> str:
    lang = user.locale
    if not rules:
        return t("alerts.empty", lang)

    lines = [t("alerts.title", lang), ""]
    for rule in rules:
        state = "\U0001F7E2" if rule.is_active else "⚪"
        latch = "" if rule.armed else " ⏸"
        lines.append(f"{state} <b>{clip_name(names.get(rule.item_id, f'#{rule.item_id}'), 36)}</b>{latch}")
        lines.append(f"   {esc(describe_rule(rule, user))}")
        if rule.trigger_count:
            lines.append(
                "   "
                + t(
                    "alerts.fired",
                    lang,
                    count=rule.trigger_count,
                    age=age(rule.last_triggered_at, lang),
                )
            )
        lines.append("")
    lines.append(t("alerts.latched_hint", lang))
    return "\n".join(lines)


def item_alerts_screen(item: Item, rules: list, user: User) -> str:
    lang = user.locale
    lines = [t("alerts.for_item", lang, name=esc(item.market_hash_name)), ""]
    if rules:
        for rule in rules:
            state = "\U0001F7E2" if rule.is_active else "⚪"
            lines.append(f"{state} {esc(describe_rule(rule, user))}")
            if rule.trigger_count:
                lines.append(
                    "   "
                    + t(
                        "alerts.fired",
                        lang,
                        count=rule.trigger_count,
                        age=age(rule.last_triggered_at, lang),
                    )
                )
        lines.append("")
    else:
        lines.append(t("alerts.none_on_item", lang))
        lines.append("")
    lines.append(t("alerts.pick", lang))
    return "\n".join(lines)


def alert_push(trigger: AlertTrigger) -> str:
    """The message a user actually receives: what, how much, so what?"""
    user = trigger.user
    lang = user.locale
    code = user.currency
    kind = trigger.kind

    if kind is AlertKind.PCT_DROP:
        head = t("push.drop", lang, pct=fmt_pct(trigger.delta_pct))
    elif kind is AlertKind.PCT_SPIKE:
        head = t("push.spike", lang, pct=fmt_pct(trigger.delta_pct))
    elif kind is AlertKind.ABS_BELOW:
        head = t("push.below", lang)
    elif kind is AlertKind.ABS_ABOVE:
        head = t("push.above", lang)
    else:
        head = t("push.volume", lang)

    item = trigger.item
    lines = [head, "", f"<b>{esc(item.market_hash_name)}</b>", f"<i>{esc(app_title(item.appid))}</i>", ""]

    if trigger.new_price_minor is not None:
        lines.append(t("push.now", lang, price=fmt_money(trigger.new_price_minor, code)))
    if trigger.old_price_minor is not None and kind in (AlertKind.PCT_DROP, AlertKind.PCT_SPIKE):
        lines.append(
            t(
                "push.was",
                lang,
                price=fmt_money(trigger.old_price_minor, code),
                window=trigger.rule.window_minutes,
            )
        )
    if kind is AlertKind.VOLUME_SPIKE and trigger.volume is not None:
        lines.append(
            t(
                "push.volume_line",
                lang,
                volume=f"{trigger.volume:,}",
                avg=f"{trigger.baseline_volume or 0:,.0f}",
            )
        )

    lines.append("")
    lines.append(t("push.rule", lang, rule=esc(describe_rule(trigger.rule, user))))
    lines.append(t("push.cooldown", lang, minutes=trigger.rule.cooldown_minutes))
    return "\n".join(lines)


def alert_history_screen(events: list, names: dict[int, str], user: User) -> str:
    lang = user.locale
    if not events:
        return t("alerts.history_empty", lang)
    lines = [t("alerts.history_title", lang), ""]
    for event in events:
        kind = event.kind.value.replace("_", " ").lower()
        lines.append(f"• <b>{clip_name(names.get(event.item_id, f'#{event.item_id}'))}</b> — {kind}")
        lines.append(
            f"   {fmt_money(event.old_price_minor, event.currency)} → "
            f"{fmt_money(event.new_price_minor, event.currency)}  "
            f"<i>{age(event.triggered_at, lang)}</i>"
        )
    return "\n".join(lines)


# -------------------------------------------------------------------- settings


def _quiet_label(user: User) -> str:
    if user.quiet_from is None or user.quiet_to is None:
        return t("settings.off", user.locale)
    return f"{user.quiet_from:02d}:00 – {user.quiet_to:02d}:00"


def settings_screen(user: User) -> str:
    lang = user.locale
    return "\n\n".join(
        [
            t("settings.title", lang),
            t(
                "settings.summary",
                lang,
                language=esc(language_name(lang)),
                currency=esc(user.currency),
                alerts=t("settings.on" if user.alerts_enabled else "settings.off", lang),
                timezone=esc(user.timezone),
            ),
        ]
    )


def notification_settings_screen(user: User) -> str:
    lang = user.locale
    digest = (
        t("notif.digest_at", lang, hour=f"{user.digest_hour:02d}")
        if user.digest_enabled
        else t("settings.off", lang)
    )
    return "\n\n".join(
        [
            t("notif.title", lang),
            t(
                "notif.summary",
                lang,
                alerts=t("settings.on" if user.alerts_enabled else "settings.off", lang),
                sound=t(
                    "notif.sound_silent" if user.silent_alerts else "notif.sound_normal", lang
                ),
                quiet=_quiet_label(user),
                digest=digest,
                cap=user.alert_cap_per_hour or t("btn.default", lang),
                timezone=esc(user.timezone),
            ),
            t("notif.hint", lang),
        ]
    )


def display_settings_screen(user: User) -> str:
    lang = user.locale
    return "\n\n".join(
        [
            t("display.title", lang),
            t(
                "display.summary",
                lang,
                language=esc(language_name(lang)),
                currency=esc(user.currency),
                span=esc(user.default_chart_span),
                sort=t(f"sort.{user.watchlist_sort}", lang),
            ),
            t("display.hint", lang),
        ]
    )


def language_screen(user: User) -> str:
    lang = user.locale
    return f"{t('lang.title', lang)}\n\n{t('lang.prompt', lang)}"


# ---------------------------------------------------------------------- digest


def digest_message(digest) -> str:
    lang = digest.user.locale
    code = digest.currency
    lines = [t("digest.title", lang), ""]

    for key, movers in (("digest.movers_up", digest.gainers), ("digest.movers_down", digest.losers)):
        if not movers:
            continue
        lines.append(t(key, lang))
        for mover in movers:
            lines.append(
                f"  {clip_name(mover.item.market_hash_name)}\n"
                f"    {fmt_money(mover.price_minor, code)}  {tinted(mover.change_pct)}"
            )
        lines.append("")

    if not digest.gainers and not digest.losers:
        lines.append(t("digest.no_moves", lang))
        lines.append("")

    if digest.alerts_fired:
        lines.append(
            t("digest.footer_alerts", lang, count=digest.tracked, alerts=digest.alerts_fired)
        )
    else:
        lines.append(t("digest.footer", lang, count=digest.tracked))
    return "\n".join(lines)


# ----------------------------------------------------------------------- misc


def status_screen(health: dict, user: User) -> str:
    lang = user.locale
    steam = health["steam"]
    lines = [
        t("status.title", lang),
        "",
        t(
            "status.body",
            lang,
            requests=steam["requests"],
            cache=steam["cache_hits"],
            throttled=steam["throttled"],
            errors=steam["errors"],
            rate=steam["rate_per_min"],
            tokens=steam["tokens"],
            queued=health["queue"]["queued"],
            processed=health["worker"]["processed"],
            failed=health["worker"]["failed"],
        ),
    ]
    if steam["circuits"]:
        circuits = ", ".join(f"{k}={v}" for k, v in steam["circuits"].items())
        lines += ["", t("status.circuits", lang, circuits=esc(circuits))]
    return "\n".join(lines)

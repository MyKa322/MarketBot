"""English catalog. The reference: every other language falls back to these keys."""
from __future__ import annotations

MESSAGES: dict[str, str] = {
    # ---------------------------------------------------------------- buttons
    "btn.watchlist": "⭐ Watchlist",
    "btn.alerts": "\U0001F514 Alerts",
    "btn.settings": "⚙️ Settings",
    "btn.help": "ℹ️ Help",
    "btn.menu": "\U0001F3E0 Menu",
    "btn.back": "⬅️ Back",
    "btn.cancel": "❌ Cancel",
    "btn.dashboard": "\U0001F4CA Dashboard",
    "btn.add_item": "➕ Add item",
    "btn.overview": "\U0001F4CB Overview",
    "btn.price": "\U0001F4B0 Price",
    "btn.chart": "\U0001F4C8 Chart",
    "btn.photo": "\U0001F5BC Photo",
    "btn.refresh": "\U0001F504 Refresh",
    "btn.refresh_all": "\U0001F504 Refresh all",
    "btn.watch": "⭐ Watch",
    "btn.unwatch": "\U0001F5D1 Unwatch",
    "btn.steam": "\U0001F517 Steam",
    "btn.mute": "\U0001F507 Mute item",
    "btn.unmute": "\U0001F514 Unmute item",
    "btn.item": "⬅️ Item",
    "btn.notifications": "\U0001F514 Notifications",
    "btn.display": "\U0001F3A8 Display",
    "btn.export": "\U0001F4E4 Export my data",
    "btn.language": "\U0001F310 Language",
    "btn.history": "\U0001F4DC History",
    "btn.delete": "\U0001F5D1 delete",
    "btn.toggle": "toggle",
    "btn.price_target": "\U0001F3AF Price target…",
    "btn.notify_below": "\U0001F53B Notify below…",
    "btn.notify_above": "\U0001F53C Notify above…",
    "btn.search_again": "\U0001F501 Search again",
    "btn.default": "Default",

    # ------------------------------------------------------------------ menu
    "menu.title": "<b>Steam Market Tracker</b>",
    "menu.greeting": "Hi {name} \U0001F44B",
    "menu.blurb": (
        "I track Steam Community Market prices and alert you when they move."
    ),
    "menu.stats": (
        "• Watchlist: <b>{watch}</b> item(s)\n"
        "• Active alerts: <b>{rules}</b>\n"
        "• Currency: <b>{currency}</b>"
    ),
    "menu.freshness": (
        "<i>Prices are polled on a schedule that respects Steam's rate limits, so "
        "figures may be a few minutes old — every screen tells you how old.</i>"
    ),

    # ------------------------------------------------------------- watchlist
    "watchlist.title": "<b>⭐ Watchlist</b>",
    "watchlist.count": "<i>({count} shown)</i>",
    "watchlist.empty": (
        "<b>⭐ Your watchlist is empty</b>\n\n"
        "Add an item by name (<code>AWP | Asiimov (Field-Tested)</code>) or paste a "
        "Steam Market link."
    ),
    "watchlist.page": "<i>Page {page} of {pages}</i>",
    "overview.title": "<b>\U0001F4CB Tracking overview</b>",
    "overview.empty": "Nothing tracked yet.",
    "overview.footer": (
        "<i>{count} item(s) · {priced} priced · updated {updated}</i>"
    ),
    "overview.totals": "Total listed value: <b>{total}</b>",
    "overview.hint": "<i>Tap an item below to open it.</i>",

    # ------------------------------------------------------------------ item
    "item.no_data": "No price data yet — queued for refresh.",
    "item.no_data_hint": "Tap <b>Refresh</b> and check back in a minute.",
    "item.lowest": "\U0001F4B0 <b>{price}</b>  (lowest listing)",
    "item.median": "⚖️ Median: {price}",
    "item.range_7d": "\U0001F4CA 7d range: {low} – {high}",
    "item.volatility": "〰️ Volatility: {value}%",
    "item.volume": "\U0001F501 Volume (24h): <b>{volume}</b> sold",
    "item.volume_ratio": "  — {ratio}x average",
    "item.orderbook": "\U0001F4D6 Order book: buy {buy} / sell {sell}",
    "item.spread": "   Spread: {spread} ({pct}%)",
    "item.updated": "<i>Updated {age} · {samples} samples this week</i>",
    "item.watching": "✅ On your watchlist",
    "item.not_watching": "☆ Not watched",
    "item.gone": "That item is no longer tracked.",
    "item.queued": "\U0001F504 Queued for refresh (position ~{backlog} in line).",
    "item.already_queued": "\U0001F504 Already queued — the next update will land shortly.",
    "item.added": "⭐ Added to your watchlist.",
    "item.already_added": "⭐ Already on your watchlist.",
    "item.watchlist_full": "⚠️ Watchlist is full ({limit}) — item opened but not added.",
    "item.no_photo": "No image available for this item yet.",

    # ----------------------------------------------------------------- price
    "price.title": "<b>\U0001F4B0 Current price</b>",
    "price.lowest": "Lowest listing: <b>{price}</b>",
    "price.median": "Median sale: {price}",
    "price.net": "You would receive: <b>{net}</b>",
    "price.fee": "<i>after Steam's fee of {fee}</i>",
    "price.buy_order": "Highest buy order: {price}",
    "price.sell_order": "Lowest sell order: {price}",
    "price.volume": "Sold in 24h: <b>{volume}</b>",
    "price.age": "<i>Updated {age}</i>",
    "price.none": "No price recorded yet. A refresh has been queued.",

    # ----------------------------------------------------------------- spans
    "span.30m": "30m",
    "span.1h": "1h",
    "span.6h": "6h",
    "span.24h": "24h",
    "span.7d": "7d",
    "span.30d": "30d",
    "span.90d": "90d",
    "span.label.30m": "Last 30 minutes",
    "span.label.1h": "Last hour",
    "span.label.6h": "Last 6 hours",
    "span.label.24h": "Last 24 hours",
    "span.label.7d": "Last 7 days",
    "span.label.30d": "Last 30 days",
    "span.label.90d": "Last 90 days",
    "chart.points": "{label}  ·  {app}  ·  {count} points",
    "chart.not_enough": "Not enough price history yet.\nCheck back after the next refresh.",

    # ---------------------------------------------------------------- adding
    "add.prompt": (
        "<b>➕ Add an item</b>\n\n"
        "Send either:\n"
        "• an item name — <code>AWP | Asiimov (Field-Tested)</code>\n"
        "• a Market link — <code>steamcommunity.com/market/listings/730/...</code>\n\n"
        "<i>A link is exact and needs no search. A name may return several matches to "
        "pick from.</i>"
    ),
    "add.send_something": "Send an item name or a Steam Market link.",
    "add.bad_link": (
        "That link is not a Steam Market listing URL.\n"
        "Expected: steamcommunity.com/market/listings/&lt;appid&gt;/&lt;item&gt;"
    ),
    "add.not_found": "No market items found for \"{query}\".",
    "add.no_matches": (
        "No matches for <b>{query}</b>.\n\n"
        "Item names must match Steam exactly, including wear — e.g. "
        "<code>AK-47 | Redline (Field-Tested)</code>."
    ),
    "add.search_unavailable": (
        "Steam search is unavailable right now. Try again shortly, or paste the item's "
        "Market URL — that works without a search."
    ),
    "add.choose": "Found <b>{count}</b> match(es) for <b>{query}</b>.\nPick one:",
    "add.expired": "That list expired — search again.",

    # ---------------------------------------------------------------- alerts
    "alerts.title": "<b>\U0001F514 Alerts</b>",
    "alerts.empty": (
        "<b>\U0001F514 Alerts</b>\n\nNo alert rules yet.\n\n"
        "Open any item and tap <b>Alerts</b> to be notified on a price drop, spike, or "
        "when it crosses a price you choose."
    ),
    "alerts.latched_hint": (
        "<i>⏸ = latched: waiting for the price to recover before it can fire again.</i>"
    ),
    "alerts.fired": "<i>fired {count}x · last {age}</i>",
    "alerts.for_item": "<b>\U0001F514 Alerts for</b>\n{name}",
    "alerts.none_on_item": "No alerts on this item yet.",
    "alerts.pick": "<i>Pick a preset, or set an exact price target.</i>",
    "alerts.created": "Alert created \U0001F514",
    "alerts.added": "✅ Added: {label}",
    "alerts.deleted": "Alert deleted",
    "alerts.already_gone": "Already gone",
    "alerts.enabled": "Alert enabled",
    "alerts.muted": "Alert muted",
    "alerts.not_found": "Alert not found",
    "alerts.limit": "Alert limit reached ({limit}).",
    "alerts.target_title": "<b>\U0001F3AF Price target</b>",
    "alerts.target_direction": "Notify me when the price goes…",
    "alerts.target_prompt": (
        "<b>\U0001F3AF Price target</b>\n\n{name}\n\n"
        "Send the price that should trigger the alert when it {direction} it.\n\n"
        "Example: <code>12.50</code>  (in {currency})"
    ),
    "alerts.dir_below": "falls to or below",
    "alerts.dir_above": "reaches or exceeds",
    "alerts.bad_price": (
        "⚠️ I couldn't read that as a price. Send a number like <code>12.50</code>."
    ),
    "alerts.set_at": "✅ Alert set at {price}",
    "alerts.history_title": "<b>\U0001F4DC Alert history</b>",
    "alerts.history_empty": "<b>\U0001F4DC Alert history</b>\n\nNothing fired yet.",
    "alerts.item_muted": "Item muted",
    "alerts.item_unmuted": "Item unmuted",
    "alerts.mute_needs_watch": "Add it to your watchlist first to mute it.",

    # rule descriptions
    "rule.pct_drop": "drops {pct}% within {window}m",
    "rule.pct_spike": "rises {pct}% within {window}m",
    "rule.abs_below": "price falls to {price} or below",
    "rule.abs_above": "price reaches {price} or above",
    "rule.volume_spike": "volume hits {pct}% of its {window}m average",
    "rule.custom": "custom rule",

    # presets
    "preset.drop_5_1h": "\U0001F4C9 -5% in 1h",
    "preset.drop_10_24h": "\U0001F4C9 -10% in 24h",
    "preset.spike_5_1h": "\U0001F4C8 +5% in 1h",
    "preset.spike_10_24h": "\U0001F4C8 +10% in 24h",
    "preset.volume_3x": "\U0001F525 Volume 3x",

    # push notifications
    "push.drop": "\U0001F4C9 <b>Price drop</b>  {pct}",
    "push.spike": "\U0001F4C8 <b>Price spike</b>  {pct}",
    "push.below": "\U0001F53B <b>Dropped below your target</b>",
    "push.above": "\U0001F53C <b>Reached your target</b>",
    "push.volume": "\U0001F525 <b>Volume surge</b>",
    "push.now": "Now: <b>{price}</b>",
    "push.was": "Was: {price} <i>({window}m ago)</i>",
    "push.volume_line": "Volume: <b>{volume}</b> vs {avg} average",
    "push.rule": "<i>Rule: {rule}</i>",
    "push.cooldown": "<i>Muted for {minutes}m so you don't get spammed.</i>",

    # -------------------------------------------------------------- settings
    "settings.title": "<b>⚙️ Settings</b>",
    "settings.summary": (
        "Language: <b>{language}</b>\n"
        "Currency: <b>{currency}</b>\n"
        "Alerts: <b>{alerts}</b>\n"
        "Timezone: <b>{timezone}</b>"
    ),
    "settings.on": "on",
    "settings.off": "off",
    "notif.title": "<b>\U0001F514 Notifications</b>",
    "notif.summary": (
        "Live alerts: <b>{alerts}</b>\n"
        "Sound: <b>{sound}</b>\n"
        "Quiet hours: <b>{quiet}</b>\n"
        "Daily digest: <b>{digest}</b>\n"
        "Max alerts/hour: <b>{cap}</b>\n"
        "Timezone: <b>{timezone}</b>"
    ),
    "notif.hint": (
        "<i>Quiet hours withhold alerts entirely. Silent mode still delivers them, "
        "just without a notification sound. Both use your timezone — set it first "
        "or they will follow UTC.</i>"
    ),
    "notif.sound_silent": "silent",
    "notif.sound_normal": "normal",
    "notif.digest_at": "daily at {hour}:00",
    "notif.btn_alerts": "\U0001F514 Live alerts: {state}",
    "notif.btn_sound": "\U0001F507 Sound: {state}",
    "notif.btn_quiet": "\U0001F319 Quiet hours",
    "notif.btn_digest": "☀️ Daily digest: {state}",
    "notif.btn_digest_hour": "\U0001F550 Digest time: {hour}:00",
    "notif.btn_cap": "\U0001F4CA Max alerts/hour: {cap}",
    "notif.btn_tz": "\U0001F30D Timezone: {tz}",
    "notif.alerts_on": "Live alerts on",
    "notif.alerts_off": "Live alerts muted",
    "notif.silent_on": "Silent delivery",
    "notif.silent_off": "Normal notifications",
    "notif.digest_on": "Daily digest on, {hour}:00",
    "notif.digest_off": "Daily digest off",
    "display.title": "<b>\U0001F3A8 Display</b>",
    "display.summary": (
        "Language: <b>{language}</b>\n"
        "Currency: <b>{currency}</b>\n"
        "Default chart range: <b>{span}</b>\n"
        "Watchlist order: <b>{sort}</b>"
    ),
    "display.hint": (
        "<i>Changing currency affects new snapshots. History keeps the currency it was "
        "recorded in.</i>"
    ),
    "display.btn_currency": "\U0001F4B1 Currency: {currency}",
    "display.btn_span": "\U0001F4C8 Default chart: {span}",
    "display.btn_sort": "↕️ Watchlist order",
    "display.btn_language": "\U0001F310 Language: {language}",
    "lang.title": "<b>\U0001F310 Language</b>",
    "lang.prompt": "Pick the language for the interface.",
    "lang.changed": "Language set to {language}",
    "currency.title": "<b>\U0001F4B1 Display currency</b>",
    "currency.hint": (
        "New price snapshots will be fetched in this currency.\n\n"
        "<i>History already recorded keeps the currency it was captured in, so a chart "
        "may briefly mix both after a change.</i>"
    ),
    "currency.changed": "Currency set to {code}",
    "span.title": "<b>\U0001F4C8 Default chart range</b>",
    "span.hint": "Which range an item's chart opens on. You can still switch per item.",
    "span.changed": "Charts open on {span}",
    "sort.title": "<b>↕️ Watchlist order</b>",
    "sort.hint": "How your watchlist is sorted.",
    "sort.changed": "Watchlist order updated",
    "sort.name": "Name A–Z",
    "sort.change": "Biggest movers",
    "sort.price": "Highest price",
    "sort.updated": "Recently updated",
    "cap.title": "<b>\U0001F4CA Alert limit</b>",
    "cap.hint": (
        "The most alerts you want in any one hour. Extra triggers are dropped, not "
        "queued — during a crash that is the difference between a heads-up and "
        "fifty buzzes."
    ),
    "cap.changed": "Limit: {cap}/hour",
    "cap.default": "Using the default limit",
    "tz.title": "<b>\U0001F30D Timezone</b>",
    "tz.hint": (
        "Pick one, or send any IANA name (<code>Europe/Lisbon</code>).\n\n"
        "<i>Quiet hours and the daily digest both depend on this — with it left at "
        "UTC they will fire at the wrong local time.</i>"
    ),
    "tz.changed": "Timezone: {tz}",
    "tz.unknown": "Unknown timezone",
    "tz.invalid": (
        "⚠️ <code>{name}</code> is not a recognised timezone.\n\n"
        "Use an IANA name such as <code>Europe/Lisbon</code> or "
        "<code>America/Bogota</code>, or pick one from the list."
    ),
    "quiet.title": "<b>\U0001F319 Quiet hours</b>",
    "quiet.prompt": (
        "Send a range of hours in your local time (<b>{tz}</b>) when alerts should be "
        "withheld.\n\nExample: <code>23-8</code> — silent from 23:00 to 08:00.\n"
        "Send <code>off</code> to disable."
    ),
    "quiet.invalid": "⚠️ Send a range like <code>23-8</code>, or <code>off</code>.",
    "quiet.out_of_range": "⚠️ Hours must be between 0 and 23.",
    "digest.hour_title": "<b>\U0001F550 Digest time</b>",
    "digest.hour_hint": (
        "Pick the hour, in your local time (<b>{tz}</b>), when the daily summary should "
        "arrive."
    ),
    "digest.hour_changed": "Digest at {hour}:00",
    "export.empty": (
        "<b>\U0001F4E4 Export</b>\n\nNothing to export yet — your watchlist is empty."
    ),
    "export.caption": (
        "\U0001F4E4 <b>{rows} row(s) exported</b>\n\nYour watchlist with the latest "
        "price, 24h change and volume — opens directly in Excel or Google Sheets."
    ),
    "export.preparing": "Preparing export…",

    # ---------------------------------------------------------------- digest
    "digest.title": "☀️ <b>Daily digest</b>",
    "digest.movers_up": "<b>\U0001F4C8 Top movers up</b>",
    "digest.movers_down": "<b>\U0001F4C9 Top movers down</b>",
    "digest.no_moves": "<i>No measurable moves across your watchlist in the last 24h.</i>",
    "digest.footer": "<i>Tracking {count} item(s)</i>",
    "digest.footer_alerts": (
        "<i>Tracking {count} item(s) · {alerts} alert(s) fired today</i>"
    ),
    "digest.btn_off": "\U0001F507 Turn digest off",

    # ---------------------------------------------------------- errors & misc
    "error.generic": (
        "⚠️ Something went wrong on my side.\n\n"
        "Nothing was lost — try that again, or /start to go back to the menu."
    ),
    "error.upstream": (
        "⏳ Steam is rate-limiting me right now.\n\n"
        "I'm backing off automatically. Prices shown are the last known values; fresh "
        "data will arrive shortly."
    ),
    "error.callback_generic": "Something went wrong",
    "error.callback_upstream": "Steam is throttling, retrying soon",
    "error.slow_down": "Slow down a moment ⏳",
    "error.not_found": "Item not found",
    "status.title": "<b>\U0001F9EA Status</b>",
    "status.body": (
        "Steam requests: <b>{requests}</b>\n"
        "Cache hits: <b>{cache}</b>\n"
        "Throttled (429): <b>{throttled}</b>\n"
        "Errors: <b>{errors}</b>\n"
        "Current rate: <b>{rate}/min</b>\n"
        "Tokens available: <b>{tokens}</b>\n\n"
        "Refresh queue: <b>{queued}</b>\n"
        "Processed: <b>{processed}</b> · failed: <b>{failed}</b>"
    ),
    "status.circuits": "Circuits: {circuits}",
    "help.text": (
        "<b>ℹ️ How this works</b>\n\n"
        "<b>Tracking.</b> Prices come from the Steam Community Market. Steam rate-limits "
        "hard, so the bot polls on a priority schedule: items many people watch refresh "
        "every few minutes, quiet ones less often. Every screen shows data age.\n\n"
        "<b>Alerts.</b> Rules compare the current price to the price N minutes ago, so a "
        "slow decline still triggers. After firing, a rule waits for its cooldown and "
        "for the price to recover before it can fire again.\n\n"
        "<b>Notifications.</b> Choose live alerts, a once-a-day digest, or both. Quiet "
        "hours withhold alerts entirely; silent mode delivers them without a sound. "
        "Both follow your timezone, so set that first.\n\n"
        "<b>Commands</b>\n"
        "/start – main menu\n"
        "/watchlist – your items\n"
        "/alerts – manage alerts\n"
        "/notifications – alerts, digest, quiet hours\n"
        "/settings – language, currency, display\n"
        "/status – bot &amp; rate-limit health"
    ),

    # ------------------------------------------------------------------- ages
    "age.never": "never",
    "age.just_now": "just now",
    "age.minutes": "{n}m ago",
    "age.hours": "{n}h ago",
    "age.days": "{n}d ago",
}

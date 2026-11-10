# MarketBot

A Telegram bot that tracks **Steam Community Market** items: watchlists, price-history
charts, volume analytics, drop/spike alerts, and fee-accurate profit/loss reporting.

Built around one hard constraint — Steam rate-limits aggressively (~20 requests/minute
per IP) — so every design decision here follows from *not* getting throttled.

---

## Features

**Watchlist & tracking**
* Add items by **name** (`AWP | Asiimov (Field-Tested)`) or by pasting a **Market URL**
* Newly added items are confirmed **with their image**, so you can see you picked the
  right wear without reading the tag
* Per-item price, 24h volume, live buy/sell order book and spread

**Quick views**
* **Price panel** — current price plus *what you would actually receive* after Steam's
  ~15% seller fee, the number the listing price never tells you
* **Photo** — the item's Steam image on demand
* **Overview panel** — every tracked item on one screen with price and 24h change, no
  paging between them

**Analytics**
* Charts over **30m / 1h / 6h / 24h / 7d / 30d / 90d**, rendered server-side as PNG
* 1h / 24h / 7d change, 7-day high-low range and a volatility figure
* Volume bars beneath the price line; inline sparklines in the watchlist

**Alerts**
* Presets (−5% in 1h, −10% in 24h, +5%, +10%, 3× volume) plus exact price targets
* Compares against the price *N minutes ago*, so a slow decline still fires
* Anti-spam: per-rule cooldown, hysteresis latch, hourly cap, quiet hours

**Notifications, your way**
* Live alerts, a once-a-day **digest** at an hour you pick, or both
* **Quiet hours** (withhold) vs **silent mode** (deliver without a sound)
* Per-user **hourly alert cap**, and **mute a single item** without losing its rules
* Proper **timezone** support, so quiet hours and the digest land at your local time

**Languages**
* Full interface in **English, Українська** — prose *and* buttons
* Defaults to your Telegram language on first contact; switchable in Settings
* Adding a fourth is one catalog file (see [Adding a language](#adding-a-language))

**Display preferences**
* Currency, default chart range, and watchlist ordering (name, movers, price, recency)
* **CSV export** of your watchlist with prices, written so Excel doesn't mangle names

**Two interfaces**
* Inline-keyboard bot UI (works everywhere)
* Optional Telegram **Mini App** — a native-themed dashboard with SVG charts

---

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate on Linux/macOS
pip install -r requirements.txt
copy .env.example .env          # cp on Linux/macOS
```

Put your token from [@BotFather](https://t.me/BotFather) into `.env`:

```
BOT_TOKEN=123456789:AA...
```

Then run it:

```bash
python -m marketbot
```

> **Windows tip.** `python` in a fresh PowerShell is usually the *system* Python, which
> does not have the dependencies — that produces
> `ModuleNotFoundError: No module named 'aiogram'`. Either activate the venv first, or
> skip the problem entirely with the included launcher, which always uses `.venv` and
> creates it on first run:
>
> ```powershell
> .
un.ps1            # start the bot
> .
un.ps1 --check    # pre-flight checks
> ```
>
> `./run.sh` does the same on Linux and macOS.

On first start it creates the schema, starts the refresh worker and registers ten
scheduled jobs. Send `/start` to your bot.

Something not working? Ask it:

```bash
python -m marketbot --check
```

```
[ OK ]  config: BOT_TOKEN   set
[ OK ]  database: connect   reachable
[FAIL]  database: schema    OperationalError -- run: alembic upgrade head
[FAIL]  telegram: token     rejected -- check the value against @BotFather
[WARN]  steam: market api   throttled (HTTP 429) from this IP...
```

It checks config, database, schema, Telegram token, webhook state and live Steam
reachability, then exits non-zero if anything is fatal — so it works as a deploy gate
in CI too.

> **Use Alembic in production.** The automatic `create_all` on boot is a development
> convenience. See [Migrations](#migrations).

---

## How the rate limiting works

This is the part worth understanding before changing anything.

Steam publishes no rate-limit contract. Observed behaviour is roughly **20 requests per
minute** and **200 per five minutes** per IP; exceed it and you get HTTP 429, and keep
pushing and the address is temporarily blackholed. So:

| Mechanism | What it does |
|---|---|
| **Token bucket** | One process-wide gate at `STEAM_RATE_PER_MIN` (default 18 — deliberately under the ceiling). Small burst allowance, because bursts trip the limit even when the average looks safe. |
| **Circuit breaker** | Per endpoint. N consecutive 429/5xx opens it for a cooldown that doubles each trip (60s → 120s → … → 30 min), then a single probe decides whether to close. |
| **AIMD governor** | A 429 halves the rate immediately; a sustained clean window recovers it by +1/min. Same control law as TCP, for the same reason: the safe rate is unknown and moves. |
| **Priority queue** | Button taps *enqueue* a refresh, never perform one. A single worker drains the queue at whatever rate the bucket allows. |
| **Response cache** | Short-TTL, with a per-key lock, so ten users opening the same item cause **one** request. |

The practical consequence: **the UI never waits on Steam.** Every screen reads the
database and tells you how old the data is. When Steam throttles us, the bot keeps
working with last-known prices instead of showing errors.

Watch it live with `/status`:

```
Steam requests: 1284      Current rate: 18/min
Cache hits: 402           Tokens available: 3.7
Throttled (429): 0        Refresh queue: 12
```

---

## Running it 24/7

**Your friends can already use it.** A Telegram bot is multi-user from the moment it
exists — everyone who opens it gets their own watchlist, alerts and settings. Send them
the bot's `@username` and have them press Start. Hosting only changes *uptime*.

For the bot to be up whether or not your own machine is, it has to live somewhere that
is always on. Two options are configured and ready — **[DEPLOYMENT.md](DEPLOYMENT.md)**
has the exact commands:

| | |
|---|---|
| **Fly.io** | `fly.toml` — persistent volume, health check, auto-stop disabled, one machine |
| **Any always-on Linux box** | `marketbot.service` — systemd unit with migrations, restart back-off, clean shutdown |

Both run `alembic upgrade head` on every start, so a fresh database is built and an
existing one upgraded without you doing anything.

Two things that keep a long-running deployment healthy:

* **Single-instance lock.** Two copies polling one token make Telegram reject both. The
  bot now refuses to start if another is running, using an OS file lock that the kernel
  releases automatically on a crash — no stale lock to clean up. It guards one host, so
  still keep the machine count at one.
* **Rotating logs.** Set `LOG_FILE` when running as a background service, which has
  nowhere to print. Leave it unset in a container, where stdout is captured.

One thing to know before choosing a host: **Steam rate-limits by IP and is harsher on
datacenter ranges** — which is what every cloud provider gives you. DEPLOYMENT.md opens
with how to tell within five minutes whether your host's IP is usable, and what to do if
it isn't.

---


## Configuration

Everything lives in `.env` — see [`.env.example`](.env.example) for the annotated list.
The settings that matter most:

| Variable | Default | Notes |
|---|---|---|
| `BOT_TOKEN` | — | **Required.** From @BotFather. |
| `DATABASE_URL` | `sqlite+aiosqlite:///./marketbot.db` | Swap for `postgresql+asyncpg://…` in production (uncomment `asyncpg` in `requirements.txt`). |
| `STEAM_RATE_PER_MIN` | `18` | Raising this **will** get you 429s. |
| `STEAM_CURRENCY` | `USD` | Currency snapshots are fetched in. |
| `RAW_RETENTION_DAYS` | `90` | Raw snapshots are pruned after this; daily OHLCV rollups are kept. |
| `ALERT_MAX_PER_USER_HOUR` | `20` | Hard cap on alerts per user per hour. |
| `STEAM_LOGIN_SECURE` | unset | Optional cookie; only unlocks history backfill. Treat it like a password. |
| `WEBAPP_ENABLED` | `false` | Mini App; needs a public HTTPS URL. |

---

## Commands

| Command | Does |
|---|---|
| `/start` | Main menu |
| `/watchlist` | Your tracked items, with sparklines |
| `/overview` | Every tracked item on one screen |
| `/add` | Track a new item by name or URL |
| `/alerts` | Manage alert rules |
| `/notifications` | Alerts, digest, quiet hours, timezone |
| `/language` | Interface language |
| `/settings` | Language, currency, display, export |
| `/status` | Rate-limit and queue health |
| `/help` | How the tracking and alerts actually work |

Any plain text you send is treated as an item search.

---

## Why the fee maths is its own module

Steam takes 5% plus a 10% publisher fee from the **seller**, each floored at one cent.
It does not compute `buyer_price × 0.15`; it solves the inverse problem — find the
largest seller amount whose fees fit inside the buyer's price. The difference is small
per trade and decisive when you are deciding whether to sell:

```
Listed at $14.28  →  you actually receive $12.42
```

`marketbot/steam/fees.py` implements the exact integer algorithm, and the **Price panel**
puts that figure next to the listing price, because the sticker price is never what
lands in your wallet.

---

## Project layout

```
marketbot/
├── config.py            typed settings (pydantic-settings)
├── constants.py         apps, currencies, fee rates, refresh tiers
├── container.py         application assembly + lifecycle
├── diagnostics.py       `--check` pre-flight report
├── registry.py          the Services container handed to handlers
├── webserver.py         /healthz, /ping and the optional webhook
├── i18n/                en · uk catalogs + lookup with English fallback
├── steam/               THE ONLY CODE THAT TALKS TO STEAM
│   ├── rate_limit.py      token bucket · circuit breaker · AIMD governor · TTL cache
│   ├── client.py          httpx client, retries, endpoint methods
│   ├── parse.py           locale-aware price parsing, URL handling
│   └── fees.py            exact Steam fee arithmetic
├── db/
│   ├── models.py          8 tables; every price is an integer in minor units
│   └── repo/              query layer, one module per aggregate
├── services/
│   ├── pricing.py         snapshot ingestion + statistics
│   ├── alerts.py          rule evaluation, hysteresis, quiet hours
│   ├── digest.py          daily summary assembly
│   ├── refresh.py         priority queue + the single worker
│   ├── charts.py          matplotlib rendering (off the event loop)
│   ├── items.py           name/URL → tracked item
│   ├── export.py          watchlist CSV
│   └── notify.py          outbound Telegram queue with pacing
├── bot/                 aiogram routers, keyboards, message rendering
├── scheduler/jobs.py    10 background jobs, each writing a job_runs row
└── webapp/              optional Mini App (FastAPI + single-page SPA)
```

Dependency rule: **presentation → services → (steam | db)**, never upward. Services
take a session and their collaborators by injection, so all of them are testable with
no bot token and no network.

Full design notes: [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Migrations

`create_all()` runs on boot for convenience, but Alembic owns the schema in production.

```bash
alembic upgrade head                              # apply
alembic revision --autogenerate -m "add x"        # after editing models.py
alembic check                                     # CI: fail if models drift from migrations
alembic downgrade -1                              # roll back one
```

The migration environment reads `DATABASE_URL` from your settings, so one migration
history covers SQLite and PostgreSQL. Batch mode is enabled on SQLite, which cannot
`ALTER` most columns.

---

## Testing

```bash
pip install -r requirements-dev.txt
pytest                      # 219 tests, ~7 seconds, no network
ruff check marketbot tests
```

The suite deliberately covers the things that are expensive to get wrong:

* **Fees** — every case checked against Steam's own arithmetic, including the 1-cent
  floors and the "looks like profit, is actually a loss" case.
* **i18n** — every catalog has the English key set, every placeholder survives
  translation, and every HTML tag balances (Telegram rejects a message otherwise).
* **Rate limiting** — token bucket, breaker escalation and half-open probes, AIMD
  recovery, all on a virtual clock so nothing actually sleeps.
* **Alerts** — each rule kind, hysteresis latch/re-arm lifecycle, cooldown, hourly cap,
  quiet hours across timezones and midnight, and that a delivery failure still records
  the event.
* **Handlers** — real `Update` objects fed through the real `Dispatcher` with a fake
  transport, covering add-by-URL → item card → chart → trade entry, plus a check that
  no `callback_data` exceeds Telegram's 64-byte limit.
* **Parsing** — nine currency/locale formats, because `float("1.234,56")` is wrong
  somewhere in the world.

---

## Mini App (optional)

The bot works fully without this. To enable the dashboard:

1. Serve the app over **public HTTPS** (Telegram will not load it otherwise).
2. Set `WEBAPP_ENABLED=true` and `WEBAPP_PUBLIC_URL=https://your.domain`.
3. Register the same URL with @BotFather → *Bot Settings → Menu Button*.

Requests authenticate from Telegram's signed `initData` — HMAC-verified on every call,
with a constant-time comparison and an expiry check. There are no cookies or sessions.

---

## Deployment

```bash
docker compose up -d        # bot + PostgreSQL
```

**Run exactly one instance.** A second replica doubles the request rate against the
same per-IP budget and duplicates every alert. The compose file pins `replicas: 1` for
that reason.

Operational notes:

* Every scheduled run writes a `job_runs` row — `SELECT * FROM job_runs ORDER BY id DESC`
  answers "is it actually working?" without reading logs.
* `/status` (and `GET /healthz` when the Mini App is on) exposes live limiter state.
* Persist `./var` if you want rendered charts to survive restarts.
* If you see sustained `throttled` counts in `/status`, lower `STEAM_RATE_PER_MIN`
  rather than raising retries.

---

## Adding a language

Catalogs are plain Python dicts — no gettext, no compilation step.

1. Copy `marketbot/i18n/en.py` to `marketbot/i18n/<code>.py` and translate the values.
2. Import it in `marketbot/i18n/catalogs.py` and add it to `LANGUAGES` (with the
   language's own name, so people see "Español", not "Spanish").
3. Run `pytest tests/test_i18n.py` — it checks the key set matches English, that every
   `{placeholder}` survived translation, that HTML tags balance, and that button labels
   stay short enough for a phone.

Missing keys fall back to English automatically, so a partial catalog is safe to ship.
A complete one is strongly preferred: a half-translated interface reads worse than an
untranslated one.

---

## Limitations

* **Unofficial API.** The Steam Market endpoints used here are undocumented and can
  change or start rejecting traffic without notice. The circuit breaker and governor
  are there to fail gracefully when that happens, not to prevent it.
* **Price history before you started tracking** is only available if you supply
  `STEAM_LOGIN_SECURE`; otherwise charts fill in from the moment an item is added.
* **Items must match Steam exactly.** Adding by name searches the market; adding by
  Market URL is exact and needs no search at all, which is also the only path that
  works while Steam is throttling you.
* **Currency changes** apply to new snapshots; existing history keeps the currency it
  was recorded in, so a chart can briefly mix both.
* **Not financial advice.** It reports what the market did; what you do about it is
  your call.

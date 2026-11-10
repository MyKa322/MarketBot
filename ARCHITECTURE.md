# MarketBot — Architecture

A Telegram bot that tracks Steam Community Market items: watchlists, price history and
volume analytics, drop/spike alerts, and a daily digest — in three languages.

---

## 1. Design constraints

Every decision below follows from three hard constraints:

| Constraint | Consequence |
|---|---|
| **Steam rate-limits aggressively** (~20 req/min, ~200 per 5 min per IP, then 429 and eventually a temporary IP block) | A *single* global egress gate. No handler ever calls Steam directly. |
| **Telegram rate-limits outbound messages** (~30 msg/s overall, ~1 msg/s per chat) | All alerts go through a queued dispatcher with per-chat pacing. |
| **Charts are requested on every button tap** | Raw snapshots plus pre-aggregated daily OHLCV; long-range charts read only the rollup. |

## 2. Layer map

```
┌──────────────────────────────────────────────────────────────────┐
│  PRESENTATION                                                     │
│  bot/handlers/*      inline-keyboard routers (aiogram 3)          │
│  bot/keyboards.py    keyboard builders + CallbackData factories   │
│  bot/texts.py        message rendering (HTML)                     │
│  i18n/               en · ru · uk catalogs, English fallback      │
│  webapp/             optional Mini App (FastAPI + SPA)            │
└───────────────┬──────────────────────────────────────────────────┘
                │ talks only to services, never to Steam or the ORM
┌───────────────▼──────────────────────────────────────────────────┐
│  SERVICES (use-cases, transaction boundaries)                     │
│  items · pricing · refresh · alerts · digest · charts · notify     │
└───────┬────────────────────────────────────┬─────────────────────┘
        │                                    │
┌───────▼─────────────────┐      ┌───────────▼──────────────────────┐
│  STEAM EGRESS           │      │  PERSISTENCE                      │
│  rate_limit.py          │      │  db/models.py  (SQLAlchemy 2.0)   │
│    TokenBucket          │      │  db/repo/*     (repositories)     │
│    CircuitBreaker       │      │  migrations/   (Alembic)          │
│    AdaptiveGovernor     │      └───────────────────────────────────┘
│    TTLCache             │
│  client.py · parse.py   │      ┌───────────────────────────────────┐
│  fees.py                │      │  SCHEDULER (APScheduler)          │
└─────────────────────────┘      │  refresh tiers · rollup · prune   │
                                 │  digest · orphan pruning          │
                                 └───────────────────────────────────┘
```

**Dependency rule:** presentation → services → (steam | db). Nothing points back up.
`registry.Services` is a plain dataclass of collaborators, and `container.py` is the
only module that imports every layer. Services receive an `AsyncSession` by injection,
so all of them are testable without a bot token or a network.

## 3. Steam egress

The naive approach — call `priceoverview` when the user taps a button — gets the host
IP throttled within an hour. Instead:

**a) One process-wide `TokenBucket`.** Capacity and refill come from config
(`STEAM_RATE_PER_MIN`, default 18, deliberately under the observed ceiling). Every
request awaits a token, so demand spikes queue instead of failing. The burst allowance
is small on purpose: bursts trip the per-minute limit even when the average looks safe.

**b) A `CircuitBreaker` per endpoint.** N consecutive 429/5xx opens the circuit for a
cooldown that doubles each trip (60s → 120s → … → 30 min cap). After the cooldown a
single HALF_OPEN probe decides whether to close or re-open longer. A 4xx client error
explicitly does *not* count — it says nothing about upstream health.

**c) An `AdaptiveGovernor`.** A 429 multiplicatively decreases the rate at once; a
sustained clean window recovers it additively. This is TCP's congestion-control law,
used for the same reason: the safe rate is unknown and changes underneath you.

**d) Nothing is fetched synchronously for the UI.** Button taps read the database. A
tap can *request* a refresh, which enqueues the item — it never blocks the interface.

**e) Priority-scored refresh queue.** `score_item()` combines watcher count, staleness
relative to the item's tier interval, alert pressure, and a penalty for repeated
failures. One `RefreshWorker` drains the queue at exactly the rate the bucket
allows. Concurrency here would defeat the limiter, so there is deliberately one worker.

**f) Response cache.** Short-TTL, keyed by (endpoint, params), with a per-key lock and
double-checked read, so N concurrent misses become one upstream request rather than N.

## 4. Data model

```
users ──┬── watchlist_entries ──┐
        └── alert_rules ────────┼──► items ──┬── price_snapshots   (raw, pruned)
                                │            └── price_daily       (OHLCV rollup)
                  alert_events ─┘
                  job_runs                   (observability)
```

* **`items`** is the deduplication key for the whole system: `(appid, market_hash_name)`
  is unique, so two users watching the same skin cause **one** Steam request. Its
  `item_nameid` is scraped once from the listing page and cached forever — it unlocks
  the order-book endpoint and never changes.
* **`price_snapshots`** is append-only and pruned after `RAW_RETENTION_DAYS`.
* **`price_daily`** is an idempotent OHLCV upsert, so the rollup job is safe to retry.
* **Money is always an integer in minor units**, column-suffixed `_minor`. There is no
  float or numeric price column anywhere by design.
* **Items are only kept while somebody watches them.** Nothing else surfaces an item
  now that the popular catalogue is gone, so unwatched items are excluded from refresh
  and pruned nightly. On a throttled IP this is the difference between spending the
  budget on prices someone will read and prices nobody will.
* **`job_runs`** records every scheduled run, which makes "is the bot working?" a SQL
  query rather than log archaeology.

## 5. Localisation

Catalogs are plain Python dicts, one module per language, with English as the reference
key set. No gettext: no .po/.mo toolchain, no extraction pass, no compilation step in
the deploy.

Design points that matter in practice:

* **Buttons are translated too.** A screen whose prose is localised but whose buttons
  say "Watchlist" reads worse than one left entirely in English, so every label goes
  through `t()`.
* **Fallback is per key, not per catalog.** A missing key resolves to English rather
  than showing a raw `btn.watchlist` to a user, so a partial catalog is safe to ship.
* **Relative times are localised in the presentation layer.** `utils.timeutil` stays
  English-only on purpose — it knows nothing about users — and `texts.age()` does the
  formatting for a person.
* **The catalogs are tested, not trusted.** `tests/test_i18n.py` asserts key-set parity,
  that every `{placeholder}` survives translation, that HTML tags balance (Telegram
  rejects the whole message otherwise), and that button labels stay short.

First contact defaults to the user's Telegram `language_code`, so the bot is readable
before anyone opens Settings.

## 6. Alert engine

Rules are evaluated in-process immediately after a snapshot is ingested, so an alert
fires on the same pass that discovers the move. There is no second polling loop and no
extra Steam traffic.

Kinds: `PCT_DROP`, `PCT_SPIKE`, `ABS_BELOW`, `ABS_ABOVE`, `VOLUME_SPIKE`.

Two ideas do the heavy lifting:

* **Baselines, not ticks.** A rule compares the current price against the price
  `window_minutes` ago. Comparing against the previous tick would miss a bleed that
  loses 20% over six hours in 1% steps. Baselines are cached per window length, so ten
  rules sharing a 60-minute window cost one query.
* **Hysteresis.** A threshold rule latches after firing and re-arms only once the price
  recovers past the threshold by a margin. Without it, a price sitting on the line
  fires on every refresh.

Layered on top: per-rule cooldown, a per-user hourly cap, quiet hours in the user's
timezone, and a global per-user mute. Fired events persist to `alert_events` for the
history view. Delivery is decoupled — a dispatch failure is logged but never rolls back
the record that the rule fired.

## 7. Scheduler

| Job | Cadence | Purpose |
|---|---|---|
| `refresh_hot` | 2 min | enqueue top-priority watched items |
| `refresh_warm` | 20 min | mid-tier items |
| `refresh_cold` | 6 h | long tail of watched items |
| `recompute_tiers` | 15 min | reassign tiers from watcher counts |
| `ensure_nameids` | 10 min | scrape `item_nameid` + icon for a few watched items |
| `rollup_daily` | 00:07 UTC | snapshots → `price_daily` OHLCV |
| `prune_orphan_items` | 03:50 UTC | drop items nobody watches or alerts on |
| `prune_snapshots` | 03:30 UTC | drop raw rows past retention |
| `send_digests` | hourly | deliver to users whose local digest hour has arrived |
| `heartbeat` | 5 min | liveness row + limiter stats in the log |

Jobs register with `max_instances=1` and `coalesce=True`, so a slow run can never stack.
Jobs only *enqueue* work; the single worker performs it, which keeps all Steam traffic
behind one rate limiter regardless of how many jobs fire at once.

## 8. Failure posture

* **Steam throttling or down** → the circuit opens, the worker stands down for the
  breaker's cooldown and re-queues the item. The bot serves last known prices, labelled
  with their age. Stale data shown honestly beats a spinner.
* **One bad item** → ingestion is per-item transactional and `SteamNotFound` increments
  a failure counter that pushes the item down the priority score. It cannot monopolise
  the request budget or poison the batch.
* **Telegram flood control** → the notifier honours `retry_after` and re-queues, up to
  three attempts. A user who blocked the bot is marked blocked rather than retried
  forever.
* **Unhandled handler error** → `handlers/errors.py` distinguishes upstream throttling
  from real bugs, always answers the callback query so the spinner stops, and shows a
  message that says what to do next.
* **Invalid `BOT_TOKEN`** → caught at startup with a specific message, not a traceback.

## 9. Testing strategy

| Layer | Approach |
|---|---|
| Fee arithmetic | Property-style checks against Steam's own algorithm, incl. 1-cent floors |
| Localisation | Key parity, placeholder survival, HTML balance, label length |
| Rate limiting | Injectable clock + sleeper; virtual time, nothing actually sleeps |
| Alert engine | Real database, full latch → cooldown → re-arm lifecycle |
| Handlers | Real `Update` objects through the real `Dispatcher` with a fake transport |
| Parsing | Nine currency/locale formats and malformed input |

The handler tests are the ones that catch what unit tests cannot: a handler asking for a
dependency nobody injects, a callback payload no filter matches, or keyboard
`callback_data` exceeding Telegram's 64-byte limit.

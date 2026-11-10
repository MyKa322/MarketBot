# Running MarketBot 24/7

You want it up whether your PC is on or off. That rules out your desktop, so it has to
live on a machine that is always on and isn't yours to switch off — a small cloud VM.

Everything in this repo is ready for that. What I cannot do is sign you up: creating the
account and entering card/verification details has to be you. After that it's a handful
of commands, listed below verbatim.

---

## Read this first — it decides whether any of this is worth doing

While setting this up, Steam returned this for **your home IP**, repeatedly, over
roughly an hour:

```
GET https://steamcommunity.com/market/priceoverview/   →  429   body: null
GET https://steamcommunity.com/                        →  200
```

The domain is fine. The market API is refusing this address. That is why your database
has `snapshots: 0` — the bot has never successfully read a price.

**Moving to a cloud host does not obviously fix this, and may make it worse.** Steam
rate-limits per IP and is harsher on datacenter ranges, which is what every cloud
provider gives you. So the honest expectation is:

| | Likely outcome |
|---|---|
| Fresh cloud IP, low request rate | Often works — the IP has no recent history with Steam |
| Cloud IP, default rate | Throttled quickly |
| Your home IP today | Throttled now; these do lift, but not on a schedule you control |

Both configs here therefore ship with `STEAM_RATE_PER_MIN=10` and a 5-minute hot
refresh, which is deliberately slower than the defaults.

**You will know within five minutes of deploying.** Send `/status` to the bot:

```
Steam requests: 12      Throttled (429): 0     ← working
Steam requests: 12      Throttled (429): 12    ← this IP is refused too
```

If it is refused, the fix is not a different host — it is routing Steam traffic through
a residential IP:

```
STEAM_PROXY=http://user:pass@residential-proxy:port
```

Nothing else in the bot changes; only Steam calls go through it.

---

## Option A — Fly.io, step by step

Fly builds the image on their servers, so you do **not** need Docker running locally
(yours currently isn't).

### 1. Install flyctl

`flyctl` is not on this machine yet. In PowerShell:

```powershell
iwr https://fly.io/install.ps1 -useb | iex
```

Close and reopen PowerShell, then confirm:

```powershell
fly version
```

### 2. Create the account and log in

```powershell
fly auth signup      # or: fly auth login
```

This opens a browser. Fly requires a payment method even on the smallest plan — that
part has to be you, and it is worth checking what a `shared-cpu-1x` / 512 MB machine
plus a 1 GB volume costs **today** before you confirm.

### 3. Initialise the app

```powershell
cd C:\Projects\Telegram\MarketBot
fly launch --no-deploy
```

Three things to watch for in the prompts:

* **App name.** `marketbot` in `fly.toml` is a placeholder — Fly app names are global
  and that one is certainly taken. Pick something unique, e.g. `marketbot-mk-7412`.
* **"An existing fly.toml was found — use it?"** Answer **yes**. It already has the
  settings that matter (see below); letting Fly regenerate it loses them.
* **Region.** Use `fra` (Frankfurt) unless you have a reason not to. Fly's region
  list changes, so check what actually exists rather than guessing:
  `fly platform regions`. Remember which you chose — the volume must match it.
  Latency barely matters here; the bot talks to Telegram and Steam, not to you.
* If it offers a Postgres or Redis database, decline both. This bot uses SQLite on a
  volume.

### 4. Create the volume

Without this the database lives on the container's temporary disk and is destroyed on
every deploy, taking your watchlist with it.

```powershell
fly volumes create marketbot_data --size 1 --region fra
```

The region **must match `primary_region` in `fly.toml`**, or the machine cannot
attach the volume. Check it with:

```powershell
fly config show | Select-String primary_region
fly platform regions          # what is actually available
```

### 5. Set the bot token as a secret

```powershell
fly secrets set BOT_TOKEN=123456789:AA...
```

Secrets are encrypted and injected as environment variables at runtime. Never put the
token in `fly.toml` — that file is meant to be committed.

`.dockerignore` already keeps `.env`, `marketbot.db`, its WAL sidecars and `var/` out of
the image, so nothing sensitive is baked in.

### 6. Deploy

```powershell
fly deploy
```

Fly uploads the build context, builds remotely, and starts one machine. The container
entrypoint runs `alembic upgrade head` first, so the database on the fresh volume is
created and migrated before the bot starts.

### 7. Check it worked

```powershell
fly status
fly logs
```

You are looking for:

```
[entrypoint] applying migrations...
[entrypoint] starting bot
scheduler started with 10 jobs
http server listening on 0.0.0.0:8080
starting polling as @MarketBotDota2_bot
```

Then from anywhere:

```powershell
curl https://<your-app-name>.fly.dev/healthz
```

### 8. Stop the bot on your PC

**Do this before testing in Telegram.** Two instances polling one token make Telegram
reject both — press Ctrl+C in the terminal running `run.ps1`. The instance lock does not
help here: it guards one machine, and these are two.

### 9. Does this IP work with Steam?

Send `/status` to the bot and look at the numbers:

```
Steam requests: 12      Throttled (429): 0     ← this IP works, prices will appear
Steam requests: 12      Throttled (429): 12    ← refused here too, see the section above
```

This is the whole question. If it is refused, the fix is `STEAM_PROXY`, not a different
provider.

### Moving your existing watchlist across

The cloud database starts empty — your five tracked items stay on your PC. Simplest is
to re-add them in Telegram. If you would rather copy the file:

```powershell
# stop the local bot first so the database is not mid-write
fly ssh console -C "pkill -f marketbot"      # stop the remote bot briefly
fly sftp shell
put marketbot.db /app/var/marketbot.db
```

Then `fly apps restart <your-app-name>`. Copy `marketbot.db` only — not the `-wal` or
`-shm` files, which belong to a database session that no longer exists.

### What `fly.toml` already sets, and why

| Setting | Why it matters |
|---|---|
| `auto_stop_machines = false` | The scheduler and refresh worker run in this process. A stopped machine means no price updates and no alerts. |
| `auto_start_machines = false` | Same reason: it must never have been stopped in the first place. |
| `min_machines_running = 1` | Exactly one. Never `fly scale count 2` — two machines would poll the same token and fight. |
| `[mounts]` → `/app/var` | Where the SQLite database lives, so it survives deploys. |
| `STEAM_RATE_PER_MIN = "10"` | Below the default 18, because datacenter IPs are throttled harder. |
| `[[http_service.checks]]` | Fly restarts the machine if `/healthz` stops answering. |

### Redeploying later

```powershell
fly deploy          # after any code change
fly logs            # follow output
fly status          # machine state
fly secrets list    # names only, never values
```

## Option B — Oracle Cloud Always Free (genuinely free, more setup)

Oracle's Always Free tier includes ARM VMs that stay on indefinitely. A card is used for
identity verification. Capacity for free ARM instances is often exhausted in popular
regions, so this can take a few attempts.

Once you have a VM with Ubuntu:

```bash
# on the VM
sudo apt update && sudo apt install -y python3-venv git
sudo useradd --system --create-home --home-dir /opt/marketbot marketbot
sudo -u marketbot git clone <your-repo-url> /opt/marketbot
cd /opt/marketbot
sudo -u marketbot python3 -m venv .venv
sudo -u marketbot .venv/bin/pip install -r requirements.txt

sudo -u marketbot cp .env.example .env
sudo -u marketbot nano .env          # add BOT_TOKEN, set STEAM_RATE_PER_MIN=10

sudo cp marketbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now marketbot
journalctl -u marketbot -f
```

`marketbot.service` runs `alembic upgrade head` before each start, restarts on crash
with a back-off, and stops cleanly on SIGINT so the instance lock is released.

The same unit works on any VPS, or on a Raspberry Pi if you have one — and a Pi at home
keeps your residential IP, which is the one thing a cloud host cannot give you.

---

## One instance only

Two copies polling the same token make Telegram reject both with
`Conflict: terminated by other getUpdates request` — you have already seen this — and
they double the Steam requests against a per-IP budget that is the binding constraint.

The bot now refuses to start if another copy is already running:

```
another MarketBot instance is already running (PID 25668). Stop it first,
or delete C:\Projects\Telegram\MarketBot\var\marketbot.lock if you are sure it is gone.
```

That lock is an OS file lock, so it is released automatically if the process crashes —
no stale lock to clean up.

**It only guards one host.** Two Fly machines, or a cloud instance plus the copy on your
desktop, each hold their own lock on their own filesystem and will still fight. So:

* keep `min_machines_running = 1` and never `fly scale count 2`;
* **stop the bot on your PC** once the cloud one is live. Right now you have one running
  locally (that is what was causing the conflict errors in your log).

---

## Database

| | SQLite (default) | PostgreSQL |
|---|---|---|
| Setup | none | a connection string |
| Good for | one host with a persistent volume | ephemeral hosts, or several people |
| Survives redeploys | only on a mounted volume | yes |

SQLite on a Fly volume is fine for this bot — it is a single writer with a background
worker, and it now runs in WAL mode with a 30-second busy timeout.

If your host has **no persistent disk** (Render free, Koyeb free), SQLite will lose
everything on each deploy. Use a managed Postgres instead — [Neon](https://neon.tech)
needs no card:

```bash
# uncomment asyncpg in requirements.txt first
DATABASE_URL=postgresql+asyncpg://user:pass@host/dbname
```

The container runs `alembic upgrade head` on every start, so a new database is built and
an existing one is upgraded without you doing anything.

---

## Checking it is actually up

```bash
curl https://your-app.fly.dev/healthz
```

```json
{
  "status": "ok",
  "steam": {"requests": 412, "throttled": 3, "rate_per_min": 10.0, "circuits": {}},
  "queue": {"queued": 7},
  "worker": {"processed": 405, "failed": 0}
}
```

In Telegram: `/status` shows the same numbers, and `/watchlist` should show prices
within a few minutes of the first successful fetch.

For continuous monitoring, point a free uptime service (UptimeRobot, Better Stack) at
`/ping` every 5 minutes. It costs nothing and tells you the bot died before your friend
does.

| Symptom | Cause |
|---|---|
| `/start` silent | wrong `BOT_TOKEN`, or a second instance is polling |
| `Conflict: terminated by other getUpdates` | two instances — stop the one on your PC |
| Prices never arrive, `throttled` climbing | this IP is refused by Steam; lower the rate or use `STEAM_PROXY` |
| Watchlist empty after a deploy | no persistent volume — see Database |
| `circuits` shows `open` | Steam is refusing us; the breaker is doing its job |

Run the built-in check any time, locally or on the server:

```bash
python -m marketbot --check
```

---

## What this costs

No paid APIs, no per-message charges — Steam's market data is free and Telegram's Bot
API is free. The only cost is the host, and on both options above that is intended to be
zero. Verify current pricing yourself before entering a card; free tiers change, and I
can't see your account.

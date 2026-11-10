"""`python -m marketbot --check` -- pre-flight checks.

Answers "why isn't it working?" without reading logs, which matters most right after a
deploy to a host you cannot easily shell into. Every check is read-only and each one
reports independently, so a failure early on does not hide the rest.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from marketbot.config import Settings, get_settings

OK, WARN, FAIL = "OK", "WARN", "FAIL"
MARK = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}


@dataclass(slots=True)
class Result:
    name: str
    status: str
    detail: str


async def check_config(settings: Settings) -> list[Result]:
    out: list[Result] = []
    if settings.bot_token:
        out.append(Result("config: BOT_TOKEN", OK, "set"))
    else:
        out.append(Result("config: BOT_TOKEN", FAIL, "missing -- copy .env.example to .env"))

    out.append(Result("config: database", OK, settings.database_url.split("://")[0]))

    if settings.is_sqlite and settings.webhook_enabled:
        out.append(
            Result(
                "config: storage",
                WARN,
                "SQLite on a webhook host is usually an ephemeral disk; trades will be "
                "lost on redeploy. Use PostgreSQL or a mounted volume.",
            )
        )

    if settings.webhook_enabled and not settings.webhook_url:
        out.append(Result("config: webhook", FAIL, "WEBHOOK_ENABLED set but WEBHOOK_BASE_URL empty"))
    elif settings.webhook_enabled and not settings.webhook_secret:
        out.append(
            Result(
                "config: webhook",
                WARN,
                "no WEBHOOK_SECRET -- anyone who learns the URL can post fake updates",
            )
        )

    if settings.steam_rate_per_min > 20:
        out.append(
            Result(
                "config: steam rate",
                WARN,
                f"{settings.steam_rate_per_min}/min is above Steam's observed ceiling (~20)",
            )
        )
    else:
        out.append(Result("config: steam rate", OK, f"{settings.steam_rate_per_min}/min"))

    return out


async def check_database(settings: Settings) -> list[Result]:
    from sqlalchemy import func, select, text

    from marketbot.db.models import Item, User
    from marketbot.db.session import build_engine, build_session_factory

    try:
        engine = build_engine(settings)
    except ModuleNotFoundError as exc:
        # The driver is imported lazily when the engine is built, so a missing one
        # surfaces here rather than at connect time. This is the exact failure of
        # pointing DATABASE_URL at PostgreSQL without uncommenting asyncpg.
        missing = getattr(exc, "name", "the database driver")
        return [
            Result(
                "database: driver",
                FAIL,
                f"{missing} is not installed -- uncomment it in requirements.txt and "
                f"reinstall (pip install -r requirements.txt)",
            )
        ]
    except Exception as exc:
        return [Result("database: url", FAIL, f"{type(exc).__name__}: {exc}")]

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        await engine.dispose()
        return [Result("database: connect", FAIL, f"{type(exc).__name__}: {exc}")]

    results = [Result("database: connect", OK, "reachable")]
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            users = await session.scalar(select(func.count()).select_from(User))
            items = await session.scalar(select(func.count()).select_from(Item))
        results.append(Result("database: schema", OK, f"{users} user(s), {items} item(s)"))
    except Exception as exc:
        # Almost always "no such table" -- the schema was never migrated.
        results.append(
            Result(
                "database: schema",
                FAIL,
                f"{type(exc).__name__} -- run: alembic upgrade head",
            )
        )
    finally:
        await engine.dispose()
    return results


async def check_telegram(settings: Settings) -> list[Result]:
    if not settings.bot_token:
        return [Result("telegram: token", FAIL, "skipped, BOT_TOKEN missing")]

    from aiogram import Bot
    from aiogram.exceptions import TelegramUnauthorizedError

    bot = Bot(token=settings.bot_token)
    try:
        me = await bot.get_me()
        results = [Result("telegram: token", OK, f"@{me.username}")]
        info = await bot.get_webhook_info()
        if info.url and not settings.webhook_enabled:
            results.append(
                Result(
                    "telegram: webhook",
                    WARN,
                    f"a webhook is registered ({info.url}) but WEBHOOK_ENABLED is off; "
                    "polling will clear it on start",
                )
            )
        elif settings.webhook_enabled:
            match = info.url == settings.webhook_url
            results.append(
                Result(
                    "telegram: webhook",
                    OK if match else WARN,
                    info.url or "not registered yet (set on start)",
                )
            )
        if info.pending_update_count:
            results.append(
                Result("telegram: backlog", WARN, f"{info.pending_update_count} pending update(s)")
            )
        return results
    except TelegramUnauthorizedError:
        return [Result("telegram: token", FAIL, "rejected -- check the value against @BotFather")]
    except Exception as exc:
        return [Result("telegram: token", FAIL, f"{type(exc).__name__}: {exc}")]
    finally:
        await bot.session.close()


async def check_steam(settings: Settings) -> list[Result]:
    """One real request. Being throttled here is information, not a failure."""
    from marketbot.steam.client import SteamMarketClient, SteamNotFound, SteamUnavailable
    from marketbot.steam.dto import ItemRef
    from marketbot.utils.money import fmt_money

    async with SteamMarketClient(settings) as client:
        ref = ItemRef(settings.steam_default_appid, "AK-47 | Redline (Field-Tested)")
        try:
            overview = await client.price_overview(ref, settings.steam_currency)
            price = fmt_money(overview.best_price, settings.steam_currency)
            return [Result("steam: market api", OK, f"reachable, sample price {price}")]
        except SteamUnavailable:
            return [
                Result(
                    "steam: market api",
                    WARN,
                    "throttled (HTTP 429) from this IP. The bot handles this -- it backs "
                    "off and serves cached prices -- but refreshes will be slower. "
                    "Datacenter IPs are throttled harder; consider a lower "
                    "STEAM_RATE_PER_MIN or STEAM_PROXY.",
                )
            ]
        except SteamNotFound:
            return [Result("steam: market api", OK, "reachable (sample item has no listings)")]
        except Exception as exc:
            return [Result("steam: market api", FAIL, f"{type(exc).__name__}: {exc}")]


async def run_checks(settings: Settings | None = None) -> int:
    """Run every check and print a report. Returns a process exit code."""
    settings = settings or get_settings()
    results: list[Result] = []
    results += await check_config(settings)
    results += await check_database(settings)
    results += await check_telegram(settings)
    results += await check_steam(settings)

    width = max(len(r.name) for r in results)
    print()
    print("MarketBot pre-flight check")
    print("=" * (width + 46))
    for r in results:
        print(f"{MARK[r.status]}  {r.name.ljust(width)}  {r.detail}")
    print("=" * (width + 46))

    failures = sum(r.status == FAIL for r in results)
    warnings = sum(r.status == WARN for r in results)
    if failures:
        print(f"\n{failures} failure(s), {warnings} warning(s). The bot will not start.")
        return 1
    if warnings:
        print(f"\nNo failures, {warnings} warning(s). Safe to start.")
        return 0
    print("\nAll checks passed.")
    return 0


def main() -> int:
    return asyncio.run(run_checks())

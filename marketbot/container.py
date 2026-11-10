"""Application assembly and lifecycle.

This is the only module that knows about every layer at once. Everything else depends
downward, which is why the wiring order below reads like a dependency list.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramUnauthorizedError

from marketbot.config import Settings, get_settings
from marketbot.db.session import build_engine, build_session_factory, create_all, session_scope
from marketbot.logging_setup import setup_logging
from marketbot.registry import Services
from marketbot.services.alerts import AlertEngine, AlertTrigger
from marketbot.services.charts import ChartService
from marketbot.services.items import ItemService
from marketbot.services.notify import Notifier
from marketbot.services.pricing import PricingService
from marketbot.services.refresh import RefreshPlanner, RefreshWorker
from marketbot.single_instance import AlreadyRunningError, SingleInstance
from marketbot.steam.client import SteamMarketClient

log = logging.getLogger(__name__)


async def build_services(settings: Settings | None = None) -> Services:
    """Construct every service. No Telegram objects yet -- see attach_bot()."""
    settings = settings or get_settings()

    engine = build_engine(settings)
    session_factory = build_session_factory(engine)

    steam = SteamMarketClient(settings)
    alerts = AlertEngine(settings)
    pricing = PricingService(steam, settings, alert_engine=alerts)
    items = ItemService(steam, settings)
    charts = ChartService()
    planner = RefreshPlanner()
    worker = RefreshWorker(planner, pricing, session_factory, settings)

    return Services(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        steam=steam,
        pricing=pricing,
        items=items,
        charts=charts,
        alerts=alerts,
        planner=planner,
        worker=worker,
    )


def attach_bot(services: Services, bot: Bot) -> Notifier:
    """Give the services a Bot, and close the alert -> notification loop.

    The AlertEngine cannot be constructed with a dispatcher because the dispatcher
    needs a Bot, the Bot needs a Dispatcher, and the Dispatcher needs the services.
    Wiring it here keeps the engine importable without aiogram.
    """
    # Imported here so the service layer never depends on the presentation layer.
    from marketbot.bot import keyboards as kb
    from marketbot.bot import texts as T
    from marketbot.db.repo import users as user_repo

    async def on_blocked(chat_id: int) -> None:
        async with session_scope(services.session_factory) as session:
            user = await user_repo.get_by_telegram_id(session, chat_id)
            if user:
                await user_repo.set_blocked(session, user.id, True)

    notifier = Notifier(bot, services.settings, on_blocked=on_blocked)

    async def dispatch(trigger: AlertTrigger) -> None:
        await notifier.enqueue(
            trigger.user.telegram_id,
            T.alert_push(trigger),
            reply_markup=kb.alert_push_keyboard(
                trigger.item.id, trigger.rule.id, trigger.user.locale
            ),
            silent=trigger.user.silent_alerts,
        )

    async def send_digest(digest) -> None:
        await notifier.enqueue(
            digest.user.telegram_id,
            T.digest_message(digest),
            reply_markup=kb.digest_keyboard(digest.user.locale),
            silent=digest.user.silent_alerts,
        )

    services.alerts.set_dispatch(dispatch)
    services.digest_dispatch = send_digest
    services.notifier = notifier
    services.bot = bot
    return notifier


async def bootstrap(services: Services) -> None:
    """Create tables. A development convenience; Alembic owns the schema in production."""
    await create_all(services.engine)


async def shutdown(services: Services) -> None:
    log.info("shutting down")
    await services.worker.stop()
    if services.notifier:
        await services.notifier.stop()
    await services.steam.aclose()
    await services.engine.dispose()


async def run() -> None:
    """Entry point: start the bot, the refresh worker and the scheduler together."""
    settings = get_settings()
    setup_logging(
        settings.log_level,
        log_file=settings.log_file,
        max_bytes=settings.log_max_bytes,
        backups=settings.log_backups,
    )

    # Before anything else. Two instances polling one token make Telegram reject both,
    # and double the Steam requests against a per-IP budget that is already the
    # binding constraint.
    try:
        instance_lock = SingleInstance(settings.lock_file)
        instance_lock.acquire()
    except AlreadyRunningError as exc:
        raise SystemExit(str(exc)) from None

    if not settings.bot_token:
        raise SystemExit(
            "BOT_TOKEN is not set. Copy .env.example to .env and add the token from "
            "@BotFather."
        )

    from marketbot.bot.app import build_bot, build_dispatcher, set_commands
    from marketbot.scheduler.jobs import build_scheduler

    services = await build_services(settings)
    await bootstrap(services)

    bot = build_bot(settings.bot_token)
    notifier = attach_bot(services, bot)
    dp = build_dispatcher(services)

    services.worker.start()
    notifier.start()

    scheduler = build_scheduler(services) if settings.enable_scheduler else None
    if scheduler:
        scheduler.start()
        log.info("scheduler started with %s jobs", len(scheduler.get_jobs()))

    webapp_task: asyncio.Task | None = None
    if settings.webapp_enabled:
        from marketbot.webapp.server import serve

        webapp_task = asyncio.create_task(serve(services), name="webapp")
        log.info("mini app listening on %s:%s", settings.webapp_host, settings.webapp_port)

    # One aiohttp app carries both the health endpoint and, when enabled, the webhook.
    http_runner = None
    if settings.health_enabled or settings.webhook_enabled:
        from marketbot import webserver

        http_app = webserver.build_app(services)
        if settings.webhook_enabled:
            webserver.attach_webhook(http_app, services, dp, bot)
        try:
            http_runner = await webserver.serve(http_app, settings.health_host, settings.port)
        except webserver.PortInUseError as exc:
            if settings.webhook_enabled:
                # No listener means Telegram cannot deliver anything. Fatal.
                await shutdown(services)
                await bot.session.close()
                raise SystemExit(f"Cannot start webhook server: {exc}") from None
            # Polling mode: the bot works fine without a health endpoint, so losing it
            # is a warning, not a reason to refuse to run.
            log.warning("%s -- continuing without the health endpoint", exc)

    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError:
        instance_lock.release()
        if http_runner is not None:
            await http_runner.cleanup()
        await shutdown(services)
        await bot.session.close()
        raise SystemExit(
            "Telegram rejected BOT_TOKEN. Check the value in .env against @BotFather."
        ) from None

    await set_commands(bot)
    log.info("starting polling as @%s", me.username)

    try:
        if settings.webhook_enabled:
            if not settings.webhook_url:
                raise SystemExit(
                    "WEBHOOK_ENABLED is on but WEBHOOK_BASE_URL is empty. Set it to the "
                    "public https:// URL of this service."
                )
            await bot.set_webhook(
                settings.webhook_url,
                secret_token=settings.webhook_secret or None,
                allowed_updates=dp.resolve_used_update_types(),
                drop_pending_updates=True,
            )
            log.info("webhook registered at %s", settings.webhook_url)
            # The aiohttp server owns the updates now; just stay alive for the
            # scheduler and the refresh worker.
            await asyncio.Event().wait()
        else:
            # Polling and a previously-set webhook are mutually exclusive; Telegram
            # rejects getUpdates while one is registered.
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(
                bot, services=services, allowed_updates=dp.resolve_used_update_types()
            )
    except asyncio.CancelledError:
        pass
    finally:
        instance_lock.release()
        if webapp_task:
            webapp_task.cancel()
        if http_runner is not None:
            await http_runner.cleanup()
        if scheduler:
            scheduler.shutdown(wait=False)
        await shutdown(services)
        await bot.session.close()

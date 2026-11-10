"""Dispatcher assembly: middlewares, routers, command menu."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from marketbot.bot.handlers import alerts as alerts_handlers
from marketbot.bot.handlers import errors as errors_handlers
from marketbot.bot.handlers import item as item_handlers
from marketbot.bot.handlers import search as search_handlers
from marketbot.bot.handlers import settings as settings_handlers
from marketbot.bot.handlers import start as start_handlers
from marketbot.bot.handlers import watchlist as watchlist_handlers
from marketbot.bot.middlewares.db import DbSessionMiddleware
from marketbot.bot.middlewares.throttle import ThrottleMiddleware
from marketbot.bot.middlewares.user import UserMiddleware
from marketbot.registry import Services

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Main menu"),
    BotCommand(command="watchlist", description="Your tracked items"),
    BotCommand(command="overview", description="All tracked items at a glance"),
    BotCommand(command="add", description="Track a new item"),
    BotCommand(command="alerts", description="Manage price alerts"),
    BotCommand(command="notifications", description="Alerts, digest, quiet hours"),
    BotCommand(command="language", description="Interface language"),
    BotCommand(command="settings", description="Language, currency, display"),
    BotCommand(command="status", description="Bot and rate-limit health"),
    BotCommand(command="help", description="How this works"),
]


def build_bot(token: str) -> Bot:
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dispatcher(services: Services) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp["services"] = services

    # Registered on the event observers rather than on dp.update so that aiogram's own
    # UserContextMiddleware has already populated event_from_user.
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(DbSessionMiddleware(services.session_factory))
        observer.outer_middleware(UserMiddleware(services.settings))
        observer.middleware(
            ThrottleMiddleware(
                rate=services.settings.throttle_rate,
                burst=services.settings.throttle_burst,
            )
        )

    dp.include_router(errors_handlers.router)
    dp.include_router(start_handlers.router)
    dp.include_router(watchlist_handlers.router)
    dp.include_router(item_handlers.router)
    dp.include_router(alerts_handlers.router)
    dp.include_router(settings_handlers.router)
    # Last: it owns the catch-all free-text handler, which must not shadow the
    # state-scoped handlers in the routers above.
    dp.include_router(search_handlers.router)
    return dp


async def set_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(COMMANDS)
    except Exception:
        log.warning("could not set command menu", exc_info=True)

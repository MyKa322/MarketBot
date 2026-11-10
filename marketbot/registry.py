"""The service container passed to handlers.

Lives in its own module, importing nothing from the bot layer, so handlers can depend
on it without creating a cycle with the assembly code in container.py.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from marketbot.config import Settings
from marketbot.services.alerts import AlertEngine
from marketbot.services.charts import ChartService
from marketbot.services.items import ItemService
from marketbot.services.pricing import PricingService
from marketbot.services.refresh import RefreshPlanner, RefreshWorker
from marketbot.steam.client import SteamMarketClient

if TYPE_CHECKING:
    from aiogram import Bot

    from marketbot.services.notify import Notifier


@dataclass(slots=True)
class Services:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    steam: SteamMarketClient
    pricing: PricingService
    items: ItemService
    charts: ChartService
    alerts: AlertEngine
    planner: RefreshPlanner
    worker: RefreshWorker
    notifier: Notifier | None = None
    bot: Bot | None = None
    # Set by container.attach_bot(). Keeps digest rendering (presentation) out of
    # the scheduler, exactly as AlertEngine.set_dispatch does for alerts.
    digest_dispatch: Callable[[object], Awaitable[None]] | None = None

    def health(self) -> dict:
        data = {
            "steam": self.steam.health(),
            "queue": self.planner.stats(),
            "worker": {"processed": self.worker.processed, "failed": self.worker.failed},
        }
        if self.notifier:
            data["notifier"] = self.notifier.stats()
        return data

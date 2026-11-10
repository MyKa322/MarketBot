"""Per-user throttle.

Protects the bot (and Steam) from a user holding down a button. Callback queries get
a silent answer rather than an error, because a visible failure for tapping too fast
reads as a broken bot.
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from marketbot.i18n import normalize_lang, t


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, rate: float = 0.4, burst: int = 5) -> None:
        self._rate = rate
        self._burst = burst
        self._hits: dict[int, list[float]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        now = time.monotonic()
        window = self._hits.setdefault(tg_user.id, [])
        window[:] = [t for t in window if now - t < self._rate * self._burst]

        if len(window) >= self._burst:
            lang = normalize_lang(getattr(tg_user, "language_code", None))
            notice = t("error.slow_down", lang)
            if isinstance(event, CallbackQuery):
                await event.answer(notice, show_alert=False)
            elif isinstance(event, Message):
                await event.answer(notice)
            return None

        window.append(now)
        return await handler(event, data)

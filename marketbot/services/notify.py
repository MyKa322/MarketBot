"""Outbound Telegram delivery with pacing, retries and block detection.

Telegram allows roughly 30 messages/second overall and about one per second to a given
chat. An alert storm during a market crash would blow straight through both, so every
outbound message goes through this queue. It also means a blocked user or a transient
Telegram error can never take down the refresh loop that produced the alert.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from marketbot.config import Settings
from marketbot.steam.rate_limit import TokenBucket
from marketbot.utils.timeutil import utcnow

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Outbound:
    chat_id: int
    text: str
    reply_markup: InlineKeyboardMarkup | None = None
    photo: bytes | None = None
    photo_name: str = "chart.png"
    silent: bool = False
    attempts: int = 0


class Notifier:
    def __init__(
        self,
        bot: Bot,
        settings: Settings,
        *,
        on_blocked: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        self._bot = bot
        self._s = settings
        self._on_blocked = on_blocked
        self._queue: asyncio.Queue[Outbound] = asyncio.Queue(maxsize=10_000)
        self._bucket = TokenBucket(settings.tg_global_rate, capacity=max(1.0, settings.tg_global_rate))
        self._last_sent: dict[int, float] = {}
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self.sent = 0
        self.dropped = 0

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._run(), name="notifier")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            # CancelledError does not inherit from Exception, so both are named.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def enqueue(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
        photo: bytes | None = None,
        silent: bool = False,
    ) -> bool:
        try:
            self._queue.put_nowait(
                Outbound(
                    chat_id=chat_id, text=text, reply_markup=reply_markup,
                    photo=photo, silent=silent,
                )
            )
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            log.error("notifier queue full, dropped message to %s", chat_id)
            return False

    def stats(self) -> dict[str, int]:
        return {"sent": self.sent, "dropped": self.dropped, "queued": self._queue.qsize()}

    async def _run(self) -> None:
        log.info("notifier started")
        while not self._stopping.is_set():
            try:
                message = await asyncio.wait_for(self._queue.get(), timeout=5.0)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            try:
                await self._deliver(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("notifier delivery error chat_id=%s", message.chat_id)
            finally:
                self._queue.task_done()
        log.info("notifier stopped")

    async def _pace(self, chat_id: int) -> None:
        """Global bucket plus a per-chat minimum gap."""
        await self._bucket.acquire(1.0)
        last = self._last_sent.get(chat_id)
        now = utcnow().timestamp()
        if last is not None:
            gap = self._s.tg_per_chat_interval - (now - last)
            if gap > 0:
                await asyncio.sleep(gap)
        self._last_sent[chat_id] = utcnow().timestamp()

    async def _deliver(self, message: Outbound) -> None:
        await self._pace(message.chat_id)
        try:
            if message.photo:
                await self._bot.send_photo(
                    message.chat_id,
                    BufferedInputFile(message.photo, filename=message.photo_name),
                    caption=message.text,
                    reply_markup=message.reply_markup,
                    disable_notification=message.silent,
                )
            else:
                await self._bot.send_message(
                    message.chat_id,
                    message.text,
                    reply_markup=message.reply_markup,
                    disable_web_page_preview=True,
                    disable_notification=message.silent,
                )
            self.sent += 1

        except TelegramRetryAfter as exc:
            # Telegram told us exactly how long to wait; requeue rather than drop.
            message.attempts += 1
            if message.attempts > 3:
                self.dropped += 1
                log.warning("giving up after %s retries chat_id=%s", message.attempts, message.chat_id)
                return
            log.warning("telegram flood control: sleeping %ss", exc.retry_after)
            await asyncio.sleep(exc.retry_after + 0.5)
            await self._queue.put(message)

        except TelegramForbiddenError:
            # The user blocked the bot. Stop targeting them instead of retrying forever.
            self.dropped += 1
            log.info("user blocked the bot chat_id=%s", message.chat_id)
            if self._on_blocked:
                await self._on_blocked(message.chat_id)

        except TelegramBadRequest as exc:
            self.dropped += 1
            log.warning("bad request chat_id=%s: %s", message.chat_id, exc)

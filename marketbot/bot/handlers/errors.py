"""Global error handler.

An exception inside a callback handler leaves Telegram's spinner going forever unless
the callback query is answered, so the first job here is to close that loop; the second
is to log enough context to reproduce the failure.
"""
from __future__ import annotations

import logging
from contextlib import suppress

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ErrorEvent

from marketbot.i18n import DEFAULT_LANG, normalize_lang, t
from marketbot.steam.client import SteamUnavailable
from marketbot.steam.rate_limit import CircuitOpenError

log = logging.getLogger(__name__)
router = Router(name="errors")


def _lang(update) -> str:
    """Best-effort language: the User row may be exactly what failed to load."""
    for attr in ("callback_query", "message"):
        event = getattr(update, attr, None)
        sender = getattr(event, "from_user", None)
        if sender is not None:
            return normalize_lang(getattr(sender, "language_code", None))
    return DEFAULT_LANG


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    exception = event.exception
    update = event.update
    lang = _lang(update)

    upstream = isinstance(exception, (SteamUnavailable, CircuitOpenError))
    if upstream:
        log.warning("upstream unavailable: %s", exception)
    else:
        log.exception("handler failed: %s", exception, exc_info=exception)

    message_text = t("error.upstream" if upstream else "error.generic", lang)

    callback = getattr(update, "callback_query", None)
    if callback is not None:
        with suppress(TelegramBadRequest):
            await callback.answer(
                t("error.callback_upstream" if upstream else "error.callback_generic", lang),
                show_alert=not upstream,
            )
        if callback.message is not None:
            with suppress(TelegramBadRequest):
                await callback.message.answer(message_text)
        return True

    message = getattr(update, "message", None)
    if message is not None:
        with suppress(TelegramBadRequest):
            await message.answer(message_text)
    return True

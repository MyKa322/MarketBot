"""Rendering helpers shared by handlers.

Telegram will not let you turn a photo message into a text message (or vice versa) by
editing, and it rejects an edit whose content is identical. Both facts leak into every
handler unless they are handled once, here.
"""
from __future__ import annotations

import logging
from contextlib import suppress

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    URLInputFile,
)

log = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n\n<i>…truncated</i>"


async def show(
    event: Message | CallbackQuery,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
    *,
    photo: bytes | None = None,
    photo_url: str | None = None,
    filename: str = "chart.png",
) -> None:
    """Render a screen, editing in place where Telegram allows it.

    `photo` uploads bytes we rendered; `photo_url` hands Telegram a URL to fetch
    itself, which is right for Steam's CDN images -- no download, no memory, and it
    never touches our Steam request budget.
    """
    is_callback = isinstance(event, CallbackQuery)
    message: Message | None = event.message if is_callback else event
    if message is None:
        return

    if photo is not None or photo_url is not None:
        file = URLInputFile(photo_url) if photo_url else BufferedInputFile(photo, filename=filename)
        caption = clip(text, CAPTION_LIMIT)
        if is_callback and message.photo:
            with suppress(TelegramBadRequest):
                await message.edit_media(
                    InputMediaPhoto(media=file, caption=caption, parse_mode="HTML"),
                    reply_markup=markup,
                )
            return
        await message.answer_photo(file, caption=caption, reply_markup=markup)
        if is_callback:
            # Swapping a text screen for a chart: drop the old message so the chat
            # does not accumulate stale duplicates of the same card.
            with suppress(TelegramBadRequest):
                await message.delete()
        return

    body = clip(text, TEXT_LIMIT)
    if is_callback:
        if message.photo:
            await message.answer(body, reply_markup=markup, disable_web_page_preview=True)
            with suppress(TelegramBadRequest):
                await message.delete()
            return
        try:
            await message.edit_text(body, reply_markup=markup, disable_web_page_preview=True)
        except TelegramBadRequest as exc:
            # "message is not modified" is normal: the user tapped the screen they are
            # already looking at. Anything else is worth a new message.
            if "not modified" not in str(exc).lower():
                await message.answer(body, reply_markup=markup, disable_web_page_preview=True)
        return

    await message.answer(body, reply_markup=markup, disable_web_page_preview=True)


async def ack(event: CallbackQuery, text: str | None = None, *, alert: bool = False) -> None:
    with suppress(TelegramBadRequest):
        await event.answer(text or "", show_alert=alert)

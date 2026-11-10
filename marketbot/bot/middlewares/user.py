"""Resolves (or creates) the User row for every incoming update."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.types import User as TgUser

from marketbot.config import Settings
from marketbot.db.repo import users as user_repo
from marketbot.i18n import normalize_lang


class UserMiddleware(BaseMiddleware):
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        session = data.get("session")
        if tg_user is None or session is None or tg_user.is_bot:
            return await handler(event, data)

        user = await user_repo.get_or_create(
            session,
            tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            currency=self._s.steam_currency,
            # First contact: default to their Telegram language rather than
            # English, so the bot is readable before anyone opens Settings.
            locale=normalize_lang(tg_user.language_code),
        )
        data["user"] = user
        data["is_admin"] = tg_user.id in self._s.admin_id_set
        return await handler(event, data)

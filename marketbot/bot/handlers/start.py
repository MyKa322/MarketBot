"""Root menu, help and status."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from marketbot.bot import keyboards as kb
from marketbot.bot import texts as T
from marketbot.bot.callbacks import MenuCB, NoopCB
from marketbot.bot.ui import ack, show
from marketbot.db.models import User
from marketbot.db.repo import alerts as alert_repo
from marketbot.db.repo import watchlist as watch_repo
from marketbot.registry import Services

router = Router(name="start")


def _webapp_url(services: Services) -> str | None:
    s = services.settings
    if s.webapp_enabled and s.webapp_public_url.startswith("https://"):
        return s.webapp_public_url
    return None


async def render_menu(event, session: AsyncSession, user: User, services: Services) -> None:
    watch_count = await watch_repo.count_for_user(session, user.id)
    rule_count = await alert_repo.count_for_user(session, user.id)
    await show(
        event,
        T.welcome(user, watch_count=watch_count, rule_count=rule_count),
        kb.main_menu(user.locale, webapp_url=_webapp_url(services)),
    )


@router.message(CommandStart())
async def cmd_start(
    message: Message, session: AsyncSession, user: User, services: Services, state: FSMContext
) -> None:
    await state.clear()
    await render_menu(message, session, user, services)


@router.callback_query(MenuCB.filter(F.action == "home"))
async def cb_home(
    query: CallbackQuery, session: AsyncSession, user: User, services: Services, state: FSMContext
) -> None:
    await state.clear()
    await ack(query)
    await render_menu(query, session, user, services)


@router.message(Command("help"))
async def cmd_help(message: Message, user: User) -> None:
    await show(message, T.help_text(user), kb.back_home(user.locale))


@router.callback_query(MenuCB.filter(F.action == "help"))
async def cb_help(query: CallbackQuery, user: User) -> None:
    await ack(query)
    await show(query, T.help_text(user), kb.back_home(user.locale))


@router.message(Command("status"))
async def cmd_status(message: Message, user: User, services: Services) -> None:
    """Rate-limit and queue health. Essential when debugging 429s."""
    await show(message, T.status_screen(services.health(), user), kb.back_home(user.locale))


@router.callback_query(NoopCB.filter())
async def cb_noop(query: CallbackQuery) -> None:
    await ack(query)

"""Handler wiring: real Updates through the real Dispatcher, with a fake transport.

Unit tests cannot catch a handler that asks for a dependency nobody injects, a
callback payload no filter matches, or a keyboard whose callback_data exceeds
Telegram's 64-byte limit. Feeding genuine Update objects through the assembled
Dispatcher does.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser

from marketbot.bot.app import build_dispatcher
from marketbot.bot.callbacks import AlertCB, ChartCB, ItemCB, MenuCB, SettingsCB, WatchCB
from marketbot.bot.handlers import alerts as alerts_handlers
from marketbot.bot.handlers import errors as errors_handlers
from marketbot.bot.handlers import item as item_handlers
from marketbot.bot.handlers import search as search_handlers
from marketbot.bot.handlers import settings as settings_handlers
from marketbot.bot.handlers import start as start_handlers
from marketbot.bot.handlers import watchlist as watchlist_handlers
from marketbot.db.repo import users as user_repo
from marketbot.db.repo import watchlist as watch_repo
from marketbot.registry import Services
from marketbot.services.alerts import AlertEngine
from marketbot.services.charts import ChartService
from marketbot.services.items import ItemService
from marketbot.services.pricing import PricingService
from marketbot.services.refresh import RefreshPlanner, RefreshWorker

TG_ID = 424242
CHAT = Chat(id=TG_ID, type="private")
TG_USER = TgUser(id=TG_ID, is_bot=False, first_name="Test", username="tester", language_code="en")


class FakeSession(BaseSession):
    """Records outgoing API calls instead of performing them."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod] = []

    async def close(self) -> None:
        pass

    async def stream_content(self, *args: Any, **kwargs: Any):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        if name in {"AnswerCallbackQuery", "DeleteMessage", "SetMyCommands"}:
            return True
        if name == "GetMe":
            return TgUser(id=1, is_bot=True, first_name="Bot", username="testbot")
        return Message(
            message_id=len(self.calls) + 100,
            date=datetime.now(UTC),
            chat=CHAT,
            text=getattr(method, "text", None) or getattr(method, "caption", None) or "ok",
        )

    def texts(self) -> list[str]:
        out = []
        for call in self.calls:
            body = getattr(call, "text", None) or getattr(call, "caption", None)
            if body:
                out.append(body)
        return out

    def last_text(self) -> str:
        texts = self.texts()
        assert texts, f"no text sent; calls were {self.method_names()}"
        return texts[-1]

    def method_names(self) -> list[str]:
        return [type(c).__name__ for c in self.calls]

    def clear(self) -> None:
        self.calls.clear()


@pytest.fixture
def session_obj() -> FakeSession:
    return FakeSession()


@pytest.fixture
def bot(session_obj) -> Bot:
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    return Bot(
        token="42:TEST",
        session=session_obj,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


@pytest_asyncio.fixture
async def services(settings, engine, session_factory, fake_steam) -> Services:
    alerts = AlertEngine(settings)
    pricing = PricingService(fake_steam, settings, alert_engine=alerts)
    planner = RefreshPlanner()
    return Services(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        steam=fake_steam,
        pricing=pricing,
        items=ItemService(fake_steam, settings),
        charts=ChartService(),
        alerts=alerts,
        planner=planner,
        worker=RefreshWorker(planner, pricing, session_factory, settings),
    )


# aiogram routers are module-level singletons registered by decorators at import time,
# so a Router remembers the first Dispatcher it was attached to. Production builds one
# Dispatcher and never rebuilds; tests build one per test, so detach them first.
HANDLER_MODULES = (
    alerts_handlers, errors_handlers, item_handlers,
    search_handlers, settings_handlers, start_handlers, watchlist_handlers,
)


@pytest.fixture
def dp(services):
    for module in HANDLER_MODULES:
        module.router._parent_router = None
    return build_dispatcher(services)


_counter = {"n": 0}


def _next_id() -> int:
    _counter["n"] += 1
    return _counter["n"]


def message_update(text: str) -> Update:
    return Update(
        update_id=_next_id(),
        message=Message(
            message_id=_next_id(), date=datetime.now(UTC), chat=CHAT, from_user=TG_USER, text=text
        ),
    )


def callback_update(data: str) -> Update:
    return Update(
        update_id=_next_id(),
        callback_query=CallbackQuery(
            id=str(_next_id()),
            from_user=TG_USER,
            chat_instance="ci",
            data=data,
            message=Message(
                message_id=_next_id(), date=datetime.now(UTC), chat=CHAT, text="previous screen"
            ),
        ),
    )


async def feed(dp, bot, update: Update, services: Services) -> None:
    await dp.feed_update(bot, update, services=services)


async def current_user(session_factory):
    async with session_factory() as s:
        return await user_repo.get_by_telegram_id(s, TG_ID)


# ------------------------------------------------------------------ navigation


async def test_start_renders_main_menu(dp, bot, services, session_obj):
    await feed(dp, bot, message_update("/start"), services)
    assert "Steam Market Tracker" in session_obj.last_text()


def test_main_menu_has_only_the_four_destinations():
    """Add item moved to the watchlist; Portfolio and Popular are gone."""
    from marketbot.bot import keyboards as kb

    labels = [b.text for row in kb.main_menu("en").inline_keyboard for b in row]
    assert len(labels) == 4
    joined = " ".join(labels)
    for absent in ("Portfolio", "Popular", "Add item"):
        assert absent not in joined
    for present in ("Watchlist", "Alerts", "Settings", "Help"):
        assert present in joined


def test_add_item_lives_on_the_watchlist_screen():
    from marketbot.bot import keyboards as kb

    labels = [b.text for row in kb.watchlist_keyboard([], "en", page=0, pages=1).inline_keyboard for b in row]
    assert any("Add item" in label for label in labels)
    assert any("Overview" in label for label in labels)


async def test_menu_callbacks_all_resolve(dp, bot, services, session_obj):
    payloads = [
        WatchCB(action="list").pack(),
        WatchCB(action="overview").pack(),
        WatchCB(action="add").pack(),
        AlertCB(action="list").pack(),
        SettingsCB(action="home").pack(),
        MenuCB(action="help").pack(),
        MenuCB(action="home").pack(),
    ]
    for payload in payloads:
        session_obj.clear()
        await feed(dp, bot, callback_update(payload), services)
        methods = session_obj.method_names()
        assert any(m in methods for m in ("SendMessage", "EditMessageText")), payload


def test_all_callback_payloads_fit_telegram_limit():
    """callback_data is capped at 64 bytes; large ids must not overflow it."""
    payloads = [
        ItemCB(action="unwatch", item_id=999_999_999, page=99).pack(),
        ChartCB(item_id=999_999_999, span="30m").pack(),
        AlertCB(action="preset", item_id=999_999_999, rule_id=999_999_999,
                kind="ABS_BELOW", preset="4").pack(),
        SettingsCB(action="set_tz", value="America/Argentina/Buenos_Aires").pack(),
        SettingsCB(action="set_lang", value="uk").pack(),
    ]
    for payload in payloads:
        assert len(payload.encode()) <= 64, f"{payload} is {len(payload.encode())} bytes"


# ------------------------------------------------------------------ watchlist


async def test_empty_watchlist_screen(dp, bot, services, session_obj):
    await feed(dp, bot, message_update("/watchlist"), services)
    assert "watchlist is empty" in session_obj.last_text()


async def test_overview_panel_lists_everything_tracked(
    dp, bot, services, session_obj, session_factory, item
):
    async with session_factory() as s:
        await services.pricing.refresh_item(s, await s.merge(item))
        await s.commit()
    await feed(dp, bot, callback_update(ItemCB(action="watch", item_id=item.id).pack()), services)

    session_obj.clear()
    await feed(dp, bot, callback_update(WatchCB(action="overview").pack()), services)
    text = session_obj.last_text()
    assert "Tracking overview" in text
    assert "AK-47 | Redline" in text
    assert "$10.00" in text        # FakeSteam's price
    assert "Total listed value" in text


async def test_refresh_all_queues_every_item(
    dp, bot, services, session_factory, item
):
    await feed(dp, bot, callback_update(ItemCB(action="watch", item_id=item.id).pack()), services)
    services.planner.pop()  # drain what watching queued
    await feed(dp, bot, callback_update(WatchCB(action="refresh_all").pack()), services)
    assert len(services.planner) == 1


# ----------------------------------------------------------------------- item


async def test_item_card_price_panel_and_photo(
    dp, bot, services, session_obj, session_factory, item
):
    async with session_factory() as s:
        await services.pricing.refresh_item(s, await s.merge(item))
        await s.commit()

    session_obj.clear()
    await feed(dp, bot, callback_update(ItemCB(action="open", item_id=item.id).pack()), services)
    assert "$10.00" in session_obj.last_text()

    # Price panel: shows the net-of-fee figure, which the card does not.
    session_obj.clear()
    await feed(dp, bot, callback_update(ItemCB(action="price", item_id=item.id).pack()), services)
    panel = session_obj.last_text()
    assert "Current price" in panel
    assert "You would receive" in panel
    assert "$8.70" in panel  # $10.00 listed -> $8.70 after Steam fee

    # Photo: FakeSteam supplies an icon via ensure_meta.
    session_obj.clear()
    await feed(dp, bot, callback_update(ItemCB(action="photo", item_id=item.id).pack()), services)
    assert "SendPhoto" in session_obj.method_names()


@pytest.mark.parametrize("span", ["30m", "1h", "6h", "24h", "7d", "30d", "90d"])
async def test_every_chart_span_renders(dp, bot, services, session_obj, item, span):
    session_obj.clear()
    await feed(dp, bot, callback_update(ChartCB(item_id=item.id, span=span).pack()), services)
    assert "SendPhoto" in session_obj.method_names(), span


def test_item_keyboard_exposes_the_new_buttons():
    from types import SimpleNamespace

    from marketbot.bot import keyboards as kb

    fake = SimpleNamespace(id=1, appid=730, market_hash_name="Test Item")
    labels = [
        b.text for row in kb.item_keyboard(fake, "en", watching=False).inline_keyboard for b in row
    ]
    joined = " ".join(labels)
    assert "Price" in joined and "Photo" in joined
    assert "Add trade" not in joined
    for span in ("30m", "1h", "6h", "24h", "7d", "30d", "90d"):
        assert any(span in label for label in labels), span


# ------------------------------------------------------------------ add flow


async def test_adding_by_url_confirms_with_a_photo(
    dp, bot, services, session_obj, session_factory
):
    """An image confirms the right skin faster than reading a wear tag."""
    url = "https://steamcommunity.com/market/listings/730/AWP%20%7C%20Asiimov%20%28Field-Tested%29"
    await feed(dp, bot, message_update(url), services)

    # FakeSteam supplies an icon through ensure_meta, so this arrives as a photo.
    assert "SendPhoto" in session_obj.method_names()
    caption = session_obj.last_text()
    assert "AWP | Asiimov (Field-Tested)" in caption
    assert "watchlist" in caption.lower()

    user = await current_user(session_factory)
    async with session_factory() as s:
        assert len(await watch_repo.list_for_user(s, user.id)) == 1


async def test_free_text_is_treated_as_a_search(dp, bot, services, session_obj, fake_steam):
    await feed(dp, bot, message_update("Butterfly Knife"), services)
    assert any(c.startswith("search") for c in fake_steam.calls)
    assert session_obj.texts()


async def test_watch_then_unwatch(dp, bot, services, session_factory, item):
    await feed(dp, bot, callback_update(ItemCB(action="watch", item_id=item.id).pack()), services)
    user = await current_user(session_factory)
    async with session_factory() as s:
        assert await watch_repo.get(s, user.id, item.id) is not None

    await feed(dp, bot, callback_update(ItemCB(action="unwatch", item_id=item.id).pack()), services)
    async with session_factory() as s:
        assert await watch_repo.get(s, user.id, item.id) is None


async def test_missing_item_is_handled_gracefully(dp, bot, services, session_obj):
    await feed(dp, bot, callback_update(ItemCB(action="open", item_id=987654).pack()), services)
    assert "no longer tracked" in session_obj.last_text()


# ------------------------------------------------------------------- settings


async def test_settings_sections_navigate(dp, bot, services, session_obj):
    for action, expect in (
        ("home", "Settings"),
        ("notif", "Notifications"),
        ("display", "Display"),
        ("language", "Language"),
        ("tz", "Timezone"),
        ("cap", "Alert limit"),
        ("span", "Default chart range"),
        ("sort", "Watchlist order"),
        ("currency", "Display currency"),
    ):
        session_obj.clear()
        await feed(dp, bot, callback_update(SettingsCB(action=action).pack()), services)
        assert expect in session_obj.last_text(), action


async def test_language_switches_the_whole_interface(
    dp, bot, services, session_obj, session_factory
):
    """Switching language must move the buttons too, not just the prose."""
    await feed(dp, bot, callback_update(SettingsCB(action="set_lang", value="uk").pack()), services)
    user = await current_user(session_factory)
    assert user.locale == "uk"

    session_obj.clear()
    await feed(dp, bot, message_update("/start"), services)
    text = session_obj.last_text()
    assert "Трекер" in text

    markup = session_obj.calls[-1].reply_markup
    labels = " ".join(b.text for row in markup.inline_keyboard for b in row)
    assert "Список" in labels
    assert "Watchlist" not in labels

    session_obj.clear()
    await feed(dp, bot, message_update("/watchlist"), services)
    assert "відстеження" in session_obj.last_text()


async def test_unknown_language_is_rejected(dp, bot, services, session_factory):
    await feed(dp, bot, message_update("/start"), services)
    await feed(dp, bot, callback_update(SettingsCB(action="set_lang", value="xx").pack()), services)
    assert (await current_user(session_factory)).locale != "xx"


async def test_toggles_and_choices_persist(dp, bot, services, session_factory):
    await feed(dp, bot, message_update("/settings"), services)
    before = await current_user(session_factory)
    was_alerts, was_silent = before.alerts_enabled, before.silent_alerts

    for action in ("alerts", "sound", "digest"):
        await feed(dp, bot, callback_update(SettingsCB(action=action).pack()), services)
    for action, value in (
        ("set_cap", "5"),
        ("set_span", "30m"),
        ("set_sort", "change"),
        ("set_currency", "EUR"),
        ("set_tz", "Asia/Tokyo"),
        ("set_digest_hour", "7"),
    ):
        await feed(dp, bot, callback_update(SettingsCB(action=action, value=value).pack()), services)

    user = await current_user(session_factory)
    assert user.alerts_enabled is not was_alerts
    assert user.silent_alerts is not was_silent
    assert user.alert_cap_per_hour == 5
    assert user.default_chart_span == "30m"
    assert user.watchlist_sort == "change"
    assert user.currency == "EUR"
    assert user.timezone == "Asia/Tokyo"
    assert user.digest_hour == 7


async def test_export_sends_a_document(dp, bot, services, session_obj, item):
    await feed(dp, bot, callback_update(ItemCB(action="watch", item_id=item.id).pack()), services)
    session_obj.clear()
    await feed(dp, bot, callback_update(SettingsCB(action="export").pack()), services)
    assert "SendDocument" in session_obj.method_names()


async def test_item_mute_requires_watchlist_then_toggles(dp, bot, services, session_factory, item):
    await feed(dp, bot, callback_update(ItemCB(action="mute", item_id=item.id).pack()), services)
    await feed(dp, bot, callback_update(ItemCB(action="watch", item_id=item.id).pack()), services)
    await feed(dp, bot, callback_update(ItemCB(action="mute", item_id=item.id).pack()), services)

    user = await current_user(session_factory)
    async with session_factory() as s:
        assert (await watch_repo.get(s, user.id, item.id)).muted is True

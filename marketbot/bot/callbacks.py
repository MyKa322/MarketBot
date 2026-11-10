"""Typed callback-data factories.

Telegram caps callback_data at 64 bytes, so prefixes are one or two characters and
nothing variable-length (item names, queries) is ever packed in -- those live in FSM
state and are referenced by index.
"""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class MenuCB(CallbackData, prefix="m"):
    action: str  # home | help


class ItemCB(CallbackData, prefix="i"):
    action: str  # open | price | photo | refresh | watch | unwatch | mute
    item_id: int
    page: int = 0


class ChartCB(CallbackData, prefix="c"):
    item_id: int
    span: str  # 30m | 1h | 6h | 24h | 7d | 30d | 90d


class WatchCB(CallbackData, prefix="w"):
    action: str  # list | page | add | overview | refresh_all
    page: int = 0


class AlertCB(CallbackData, prefix="a"):
    action: str  # list | new | kind | toggle | delete | item | preset | history
    item_id: int = 0
    rule_id: int = 0
    kind: str = ""
    preset: str = ""


class PickCB(CallbackData, prefix="k"):
    """Selecting one of the search candidates held in FSM state."""

    idx: int
    purpose: str = "watch"


class SettingsCB(CallbackData, prefix="s"):
    action: str  # home | notif | display | language | set_lang | currency | ...
    value: str = ""


class NoopCB(CallbackData, prefix="n"):
    """Inert button (page counters, separators)."""

    tag: str = "x"

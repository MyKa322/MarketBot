"""FSM states for the multi-step flows."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AddItem(StatesGroup):
    waiting_for_query = State()
    choosing_candidate = State()


class NewAlert(StatesGroup):
    choosing_kind = State()
    waiting_for_threshold = State()


class NewTrade(StatesGroup):
    choosing_item = State()
    choosing_side = State()
    waiting_for_details = State()


class Prefs(StatesGroup):
    waiting_for_quiet_hours = State()
    waiting_for_timezone = State()

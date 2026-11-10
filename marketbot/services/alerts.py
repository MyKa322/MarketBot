"""Alert rule evaluation.

Rules are evaluated inline right after a snapshot is ingested, so an alert fires on
the same pass that discovers the price move -- there is no second polling loop and no
extra Steam traffic.

Two ideas do the heavy lifting:

* **Baselines, not ticks.** A rule compares the current price against the price
  `window_minutes` ago. Comparing against the previous tick would miss a slow bleed
  that loses 20% over six hours in 1% steps.
* **Hysteresis.** A threshold rule latches after firing and only re-arms once the
  price has recovered past the threshold by a margin. Without it, a price sitting on
  the line fires on every single refresh.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from marketbot.config import Settings
from marketbot.db.models import AlertKind, AlertRule, Item, PriceSnapshot, User
from marketbot.db.repo import alerts as alert_repo
from marketbot.db.repo import prices as price_repo
from marketbot.utils.money import pct_change
from marketbot.utils.timeutil import ensure_utc, in_quiet_hours, utcnow

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AlertTrigger:
    rule: AlertRule
    item: Item
    user: User
    old_price_minor: int | None
    new_price_minor: int | None
    delta_pct: float | None
    volume: int | None = None
    baseline_volume: float | None = None

    @property
    def kind(self) -> AlertKind:
        return self.rule.kind


def evaluate_rule(
    rule: AlertRule,
    *,
    current_price: int | None,
    baseline_price: int | None,
    current_volume: int | None = None,
    baseline_volume: float | None = None,
) -> tuple[bool, float | None]:
    """Pure predicate: should this rule fire right now? Returns (fire, delta_pct).

    Ignores cooldown, quiet hours and arming -- those are policy, applied by the
    caller, and keeping them out of here is what makes this function testable.
    """
    if rule.kind is AlertKind.VOLUME_SPIKE:
        if current_volume is None or not baseline_volume or not rule.threshold_pct:
            return False, None
        ratio = current_volume / baseline_volume * 100.0
        return ratio >= rule.threshold_pct, ratio

    if current_price is None:
        return False, None

    if rule.kind is AlertKind.ABS_BELOW:
        if rule.threshold_minor is None:
            return False, None
        return current_price <= rule.threshold_minor, pct_change(rule.threshold_minor, current_price)

    if rule.kind is AlertKind.ABS_ABOVE:
        if rule.threshold_minor is None:
            return False, None
        return current_price >= rule.threshold_minor, pct_change(rule.threshold_minor, current_price)

    delta = pct_change(baseline_price, current_price)
    if delta is None or rule.threshold_pct is None:
        return False, None

    if rule.kind is AlertKind.PCT_DROP:
        return delta <= -abs(rule.threshold_pct), delta
    if rule.kind is AlertKind.PCT_SPIKE:
        return delta >= abs(rule.threshold_pct), delta
    return False, delta


def should_rearm(rule: AlertRule, current_price: int | None, hysteresis_pct: float) -> bool:
    """Has the price moved far enough back for a latched threshold rule to re-arm?"""
    if rule.kind not in (AlertKind.ABS_BELOW, AlertKind.ABS_ABOVE):
        return True
    if current_price is None or rule.threshold_minor is None:
        return False
    margin = rule.threshold_minor * hysteresis_pct / 100.0
    if rule.kind is AlertKind.ABS_BELOW:
        return current_price > rule.threshold_minor + margin
    return current_price < rule.threshold_minor - margin


def in_cooldown(rule: AlertRule, now: datetime | None = None) -> bool:
    if rule.last_triggered_at is None:
        return False
    now = now or utcnow()
    return ensure_utc(rule.last_triggered_at) + timedelta(minutes=rule.cooldown_minutes) > now


def user_is_quiet(user: User, now: datetime | None = None) -> bool:
    if user.quiet_from is None or user.quiet_to is None:
        return False
    now = now or utcnow()
    try:
        local = now.astimezone(ZoneInfo(user.timezone or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        local = now
    return in_quiet_hours(local.hour, user.quiet_from, user.quiet_to)


class AlertEngine:
    """Evaluates every active rule for an item against a fresh snapshot."""

    def __init__(
        self,
        settings: Settings,
        dispatch: Callable[[AlertTrigger], Awaitable[None]] | None = None,
    ) -> None:
        self._s = settings
        self._dispatch = dispatch

    def set_dispatch(self, dispatch: Callable[[AlertTrigger], Awaitable[None]]) -> None:
        """Wired after construction: delivery needs a Bot, which needs the engine
        to already exist for the pricing service. Breaking that cycle here keeps the
        engine free of any Telegram import."""
        self._dispatch = dispatch

    async def evaluate_item(
        self, session: AsyncSession, item: Item, snapshot: PriceSnapshot
    ) -> list[AlertTrigger]:
        if not self._s.alerts_enabled:
            return []

        rules = await alert_repo.active_for_item(session, item.id)
        if not rules:
            return []

        now = utcnow()
        current = snapshot.price_minor
        triggers: list[AlertTrigger] = []
        # Baselines are per window length, so ten rules sharing a 60m window cost one
        # query, not ten.
        baseline_cache: dict[int, int | None] = {}
        volume_cache: dict[int, float | None] = {}
        caps: dict[int, int] = {}

        for rule in rules:
            user = await session.get(User, rule.user_id)
            if user is None:
                continue

            baseline = await self._baseline(session, item.id, rule.window_minutes, now, baseline_cache)
            avg_volume = None
            if rule.kind is AlertKind.VOLUME_SPIKE:
                avg_volume = await self._avg_volume(
                    session, item.id, rule.window_minutes, now, volume_cache
                )

            fire, delta = evaluate_rule(
                rule,
                current_price=current,
                baseline_price=baseline,
                current_volume=snapshot.volume,
                baseline_volume=avg_volume,
            )

            if not fire:
                # Re-arm a latched threshold rule once the price has recovered.
                if not rule.armed and should_rearm(rule, current, self._s.alert_hysteresis_pct):
                    rule.armed = True
                continue

            if not rule.armed or in_cooldown(rule, now) or user_is_quiet(user, now):
                continue

            if rule.user_id not in caps:
                caps[rule.user_id] = await alert_repo.recent_event_count(session, rule.user_id)
            cap = user.alert_cap_per_hour or self._s.alert_max_per_user_hour
            if caps[rule.user_id] >= cap:
                log.info("alert cap reached user_id=%s", rule.user_id)
                continue
            caps[rule.user_id] += 1

            rule.last_triggered_at = now
            rule.trigger_count += 1
            rule.armed = False

            await alert_repo.record_event(
                session,
                rule_id=rule.id,
                user_id=rule.user_id,
                item_id=item.id,
                kind=rule.kind,
                old_price_minor=baseline,
                new_price_minor=current,
                delta_pct=delta,
                currency=snapshot.currency,
                delivered=False,
            )
            triggers.append(
                AlertTrigger(
                    rule=rule,
                    item=item,
                    user=user,
                    old_price_minor=baseline,
                    new_price_minor=current,
                    delta_pct=delta,
                    volume=snapshot.volume,
                    baseline_volume=avg_volume,
                )
            )

        await session.flush()

        if self._dispatch:
            for trigger in triggers:
                try:
                    await self._dispatch(trigger)
                except Exception:
                    # Delivery failure must not roll back the rule-state bookkeeping;
                    # the event row already records that it fired.
                    log.exception("alert dispatch failed rule_id=%s", trigger.rule.id)

        return triggers

    @staticmethod
    async def _baseline(
        session: AsyncSession,
        item_id: int,
        window_minutes: int,
        now: datetime,
        cache: dict[int, int | None],
    ) -> int | None:
        if window_minutes in cache:
            return cache[window_minutes]
        snap = await price_repo.price_at_or_before(
            session, item_id, now - timedelta(minutes=window_minutes)
        )
        value = snap.price_minor if snap else None
        cache[window_minutes] = value
        return value

    @staticmethod
    async def _avg_volume(
        session: AsyncSession,
        item_id: int,
        window_minutes: int,
        now: datetime,
        cache: dict[int, float | None],
    ) -> float | None:
        if window_minutes in cache:
            return cache[window_minutes]
        value = await price_repo.average_volume(
            session, item_id, since=now - timedelta(minutes=window_minutes)
        )
        cache[window_minutes] = float(value) if value is not None else None
        return cache[window_minutes]

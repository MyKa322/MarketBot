"""Alert predicates, anti-spam policy, and the end-to-end engine."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from marketbot.db.models import AlertKind
from marketbot.db.repo import alerts as alert_repo
from marketbot.db.repo import prices as price_repo
from marketbot.services.alerts import (
    AlertEngine,
    evaluate_rule,
    in_cooldown,
    should_rearm,
    user_is_quiet,
)
from marketbot.utils.timeutil import in_quiet_hours, utcnow


def rule(kind, *, pct=None, minor=None, window=60, cooldown=60, armed=True, last=None):
    return SimpleNamespace(
        kind=kind,
        threshold_pct=pct,
        threshold_minor=minor,
        window_minutes=window,
        cooldown_minutes=cooldown,
        armed=armed,
        last_triggered_at=last,
    )


# ------------------------------------------------------------------- predicates


def test_pct_drop_fires_on_sufficient_decline():
    fire, delta = evaluate_rule(
        rule(AlertKind.PCT_DROP, pct=10), current_price=850, baseline_price=1000
    )
    assert fire is True
    assert delta == pytest.approx(-15.0)


def test_pct_drop_ignores_small_decline():
    fire, _ = evaluate_rule(
        rule(AlertKind.PCT_DROP, pct=10), current_price=950, baseline_price=1000
    )
    assert fire is False


def test_pct_drop_ignores_a_rise():
    fire, _ = evaluate_rule(
        rule(AlertKind.PCT_DROP, pct=10), current_price=1200, baseline_price=1000
    )
    assert fire is False


def test_slow_bleed_fires_against_window_baseline():
    """The whole point of baselines: -12% over the window, never -12% tick to tick."""
    fire, delta = evaluate_rule(
        rule(AlertKind.PCT_DROP, pct=10, window=360), current_price=880, baseline_price=1000
    )
    assert fire is True
    assert delta == pytest.approx(-12.0)


def test_pct_spike():
    fire, delta = evaluate_rule(
        rule(AlertKind.PCT_SPIKE, pct=10), current_price=1150, baseline_price=1000
    )
    assert fire is True
    assert delta == pytest.approx(15.0)


@pytest.mark.parametrize(("price", "expected"), [(900, True), (1000, True), (1001, False)])
def test_abs_below_is_inclusive(price, expected):
    fire, _ = evaluate_rule(
        rule(AlertKind.ABS_BELOW, minor=1000), current_price=price, baseline_price=None
    )
    assert fire is expected


@pytest.mark.parametrize(("price", "expected"), [(1100, True), (1000, True), (999, False)])
def test_abs_above_is_inclusive(price, expected):
    fire, _ = evaluate_rule(
        rule(AlertKind.ABS_ABOVE, minor=1000), current_price=price, baseline_price=None
    )
    assert fire is expected


def test_volume_spike():
    fire, ratio = evaluate_rule(
        rule(AlertKind.VOLUME_SPIKE, pct=300),
        current_price=1000,
        baseline_price=1000,
        current_volume=900,
        baseline_volume=250.0,
    )
    assert fire is True
    assert ratio == pytest.approx(360.0)


def test_missing_data_never_fires():
    assert evaluate_rule(rule(AlertKind.PCT_DROP, pct=10), current_price=None, baseline_price=1000)[0] is False
    assert evaluate_rule(rule(AlertKind.PCT_DROP, pct=10), current_price=900, baseline_price=None)[0] is False
    assert evaluate_rule(rule(AlertKind.PCT_DROP, pct=10), current_price=900, baseline_price=0)[0] is False
    assert evaluate_rule(rule(AlertKind.ABS_BELOW, minor=None), current_price=900, baseline_price=None)[0] is False


# ---------------------------------------------------------------------- policy


def test_hysteresis_requires_real_recovery():
    r = rule(AlertKind.ABS_BELOW, minor=1000)
    assert should_rearm(r, 1005, 2.0) is False  # still hovering on the line
    assert should_rearm(r, 1021, 2.0) is True   # recovered past threshold + 2%


def test_hysteresis_for_above_rules_is_mirrored():
    r = rule(AlertKind.ABS_ABOVE, minor=1000)
    assert should_rearm(r, 995, 2.0) is False
    assert should_rearm(r, 979, 2.0) is True


def test_percent_rules_always_rearm():
    assert should_rearm(rule(AlertKind.PCT_DROP, pct=10), 500, 2.0) is True


def test_cooldown_window():
    now = utcnow()
    assert in_cooldown(rule(AlertKind.PCT_DROP, cooldown=60, last=now - timedelta(minutes=30)), now) is True
    assert in_cooldown(rule(AlertKind.PCT_DROP, cooldown=60, last=now - timedelta(minutes=61)), now) is False
    assert in_cooldown(rule(AlertKind.PCT_DROP, cooldown=60, last=None), now) is False


@pytest.mark.parametrize(
    ("hour", "start", "end", "expected"),
    [
        (2, 23, 8, True),    # inside a window that wraps midnight
        (23, 23, 8, True),
        (12, 23, 8, False),
        (10, 9, 17, True),   # ordinary daytime window
        (18, 9, 17, False),
        (5, None, None, False),
        (5, 9, 9, False),    # zero-width window is "off"
    ],
)
def test_quiet_hours(hour, start, end, expected):
    assert in_quiet_hours(hour, start, end) is expected


def test_user_is_quiet_respects_timezone():
    user = SimpleNamespace(quiet_from=23, quiet_to=8, timezone="Asia/Tokyo")
    # 16:00 UTC is 01:00 in Tokyo -- inside the quiet window.
    assert user_is_quiet(user, datetime(2026, 1, 1, 16, tzinfo=UTC)) is True
    assert user_is_quiet(user, datetime(2026, 1, 1, 3, tzinfo=UTC)) is False


def test_unknown_timezone_falls_back_to_utc():
    user = SimpleNamespace(quiet_from=23, quiet_to=8, timezone="Not/AZone")
    assert user_is_quiet(user, datetime(2026, 1, 1, 2, tzinfo=UTC)) is True


# --------------------------------------------------------------- engine (with DB)


async def _snapshot(session, item, price, *, minutes_ago=0, volume=500):
    return await price_repo.add_snapshot(
        session,
        item_id=item.id,
        currency="USD",
        lowest_price_minor=price,
        median_price_minor=price,
        volume=volume,
        captured_at=utcnow() - timedelta(minutes=minutes_ago),
    )


async def test_engine_fires_and_records_event(session, user, item, settings):
    await _snapshot(session, item, 1000, minutes_ago=90)
    r = await alert_repo.create(
        session,
        user_id=user.id,
        item_id=item.id,
        kind=AlertKind.PCT_DROP,
        threshold_pct=10,
        window_minutes=60,
    )
    await session.commit()

    fired: list = []
    engine = AlertEngine(settings, dispatch=lambda t: fired.append(t) or _noop())
    current = await _snapshot(session, item, 850)
    triggers = await engine.evaluate_item(session, item, current)

    assert len(triggers) == 1
    trigger = triggers[0]
    assert trigger.old_price_minor == 1000
    assert trigger.new_price_minor == 850
    assert trigger.delta_pct == pytest.approx(-15.0)
    assert len(fired) == 1

    events = await alert_repo.recent_events(session, user.id)
    assert len(events) == 1
    assert events[0].delta_pct == pytest.approx(-15.0)

    await session.refresh(r)
    assert r.trigger_count == 1
    assert r.last_triggered_at is not None


async def _noop():
    return None


async def test_engine_respects_cooldown(session, user, item, settings):
    await _snapshot(session, item, 1000, minutes_ago=90)
    await alert_repo.create(
        session,
        user_id=user.id,
        item_id=item.id,
        kind=AlertKind.PCT_DROP,
        threshold_pct=10,
        window_minutes=60,
        cooldown_minutes=60,
    )
    await session.commit()

    engine = AlertEngine(settings)
    first = await engine.evaluate_item(session, item, await _snapshot(session, item, 850))
    second = await engine.evaluate_item(session, item, await _snapshot(session, item, 840))

    assert len(first) == 1
    assert second == []  # still inside the cooldown


async def test_engine_skips_muted_user(session, user, item, settings):
    await _snapshot(session, item, 1000, minutes_ago=90)
    await alert_repo.create(
        session, user_id=user.id, item_id=item.id, kind=AlertKind.PCT_DROP, threshold_pct=10
    )
    user.alerts_enabled = False
    await session.commit()

    engine = AlertEngine(settings)
    assert await engine.evaluate_item(session, item, await _snapshot(session, item, 800)) == []


async def test_engine_enforces_hourly_cap(session, user, item, settings):
    settings.alert_max_per_user_hour = 1
    await _snapshot(session, item, 1000, minutes_ago=90)
    for _ in range(2):
        await alert_repo.create(
            session,
            user_id=user.id,
            item_id=item.id,
            kind=AlertKind.PCT_DROP,
            threshold_pct=5,
            window_minutes=60,
        )
    await session.commit()

    engine = AlertEngine(settings)
    triggers = await engine.evaluate_item(session, item, await _snapshot(session, item, 800))
    assert len(triggers) == 1  # second rule suppressed by the cap


async def test_threshold_rule_latches_then_rearms(session, user, item, settings):
    r = await alert_repo.create(
        session,
        user_id=user.id,
        item_id=item.id,
        kind=AlertKind.ABS_BELOW,
        threshold_minor=1000,
        cooldown_minutes=0,
    )
    await session.commit()
    engine = AlertEngine(settings)

    assert len(await engine.evaluate_item(session, item, await _snapshot(session, item, 950))) == 1
    await session.refresh(r)
    assert r.armed is False

    # Still below: latched, must not fire again.
    assert await engine.evaluate_item(session, item, await _snapshot(session, item, 940)) == []

    # Recovered well above the threshold: re-arms.
    await engine.evaluate_item(session, item, await _snapshot(session, item, 1100))
    await session.refresh(r)
    assert r.armed is True

    assert len(await engine.evaluate_item(session, item, await _snapshot(session, item, 900))) == 1


async def test_dispatch_failure_does_not_lose_the_event(session, user, item, settings):
    """A Telegram outage must not roll back the fact that the rule fired."""
    await _snapshot(session, item, 1000, minutes_ago=90)
    await alert_repo.create(
        session, user_id=user.id, item_id=item.id, kind=AlertKind.PCT_DROP, threshold_pct=10
    )
    await session.commit()

    async def boom(_trigger):
        raise RuntimeError("telegram down")

    engine = AlertEngine(settings, dispatch=boom)
    triggers = await engine.evaluate_item(session, item, await _snapshot(session, item, 800))

    assert len(triggers) == 1
    assert len(await alert_repo.recent_events(session, user.id)) == 1

"""Digest scheduling, CSV export, per-item mute, cap override, watchlist sorting."""
from __future__ import annotations

import codecs
import csv
import io
from datetime import UTC, date, datetime, timedelta

import pytest

from marketbot.db.models import AlertKind
from marketbot.db.repo import alerts as alert_repo
from marketbot.db.repo import prices as price_repo
from marketbot.db.repo import watchlist as watch_repo
from marketbot.services import digest as digest_service
from marketbot.services.alerts import AlertEngine
from marketbot.services.export import export_watchlist
from marketbot.utils.timeutil import utcnow

# ------------------------------------------------------------- digest schedule


def _user(**kwargs):
    from types import SimpleNamespace

    base = {
        "digest_enabled": True,
        "digest_hour": 9,
        "digest_last_sent_on": None,
        "timezone": "UTC",
        "is_blocked": False,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_digest_due_at_configured_hour():
    moment = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
    assert digest_service.is_due(_user(), moment) is True
    assert digest_service.is_due(_user(digest_hour=10), moment) is False


def test_digest_not_due_when_disabled_or_blocked():
    moment = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    assert digest_service.is_due(_user(digest_enabled=False), moment) is False
    assert digest_service.is_due(_user(is_blocked=True), moment) is False


def test_digest_sends_once_per_local_day():
    """An hourly sweep must not send a second digest inside the same hour."""
    moment = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    assert digest_service.is_due(_user(), moment) is True
    already = _user(digest_last_sent_on=date(2026, 5, 1))
    assert digest_service.is_due(already, moment) is False
    # Next day, same hour: due again.
    assert digest_service.is_due(already, moment + timedelta(days=1)) is True


def test_digest_hour_is_local_not_utc():
    """09:00 in Tokyo is 00:00 UTC; the sweep must fire on the user's clock."""
    tokyo = _user(timezone="Asia/Tokyo", digest_hour=9)
    assert digest_service.is_due(tokyo, datetime(2026, 5, 1, 0, 5, tzinfo=UTC)) is True
    assert digest_service.is_due(tokyo, datetime(2026, 5, 1, 9, 5, tzinfo=UTC)) is False


def test_unknown_timezone_falls_back_to_utc():
    broken = _user(timezone="Not/AZone", digest_hour=9)
    assert digest_service.is_due(broken, datetime(2026, 5, 1, 9, 0, tzinfo=UTC)) is True


async def test_digest_content_ranks_movers(session, user, item):
    await watch_repo.add(session, user.id, item.id)
    for minutes, price in ((1500, 1000), (0, 1200)):
        await price_repo.add_snapshot(
            session, item_id=item.id, currency="USD",
            lowest_price_minor=price, median_price_minor=price, volume=100,
            captured_at=utcnow() - timedelta(minutes=minutes),
        )
    await session.commit()

    payload = await digest_service.build(session, user)
    assert payload.tracked == 1
    assert len(payload.gainers) == 1
    assert payload.gainers[0].change_pct == pytest.approx(20.0)
    assert payload.losers == []
    assert payload.has_content is True


async def test_empty_digest_has_no_content(session, user):
    payload = await digest_service.build(session, user)
    assert payload.has_content is False


# --------------------------------------------------------------------- export


async def test_export_is_empty_without_a_watchlist(session, user):
    data, rows = await export_watchlist(session, user)
    assert rows == 0
    assert data == b""


async def test_export_carries_prices_and_opens_in_excel(session, user, item):
    await watch_repo.add(session, user.id, item.id)
    await price_repo.add_snapshot(
        session,
        item_id=item.id,
        currency="USD",
        lowest_price_minor=1428,
        median_price_minor=1450,
        volume=1820,
        captured_at=utcnow(),
    )
    await session.commit()

    data, rows = await export_watchlist(session, user)
    assert rows == 1
    # Excel reads plain UTF-8 CSV as the local ANSI codepage; the BOM prevents that.
    assert data.startswith(codecs.BOM_UTF8)

    row = next(iter(csv.DictReader(io.StringIO(data.decode("utf-8-sig")))))
    assert row["item"] == item.market_hash_name
    assert row["price"] == "14.28"
    assert row["median"] == "14.50"
    assert row["volume_24h"] == "1820"
    assert row["currency"] == "USD"
    assert row["muted"] == "no"


async def test_export_tolerates_an_unpriced_item(session, user, item):
    """A just-added item has no snapshot yet; the row must still export."""
    await watch_repo.add(session, user.id, item.id)
    await session.commit()

    data, rows = await export_watchlist(session, user)
    assert rows == 1
    row = next(iter(csv.DictReader(io.StringIO(data.decode("utf-8-sig")))))
    assert row["price"] == ""
    assert row["item"] == item.market_hash_name


# ----------------------------------------------------------- mute + cap override


async def _baseline_and_rule(session, user, item, **rule_kwargs):
    await price_repo.add_snapshot(
        session, item_id=item.id, currency="USD",
        lowest_price_minor=1000, median_price_minor=1000, volume=500,
        captured_at=utcnow() - timedelta(minutes=90),
    )
    rule = await alert_repo.create(
        session, user_id=user.id, item_id=item.id,
        kind=AlertKind.PCT_DROP, threshold_pct=10, window_minutes=60, **rule_kwargs,
    )
    await session.commit()
    return rule


async def _drop(session, item, price=800):
    return await price_repo.add_snapshot(
        session, item_id=item.id, currency="USD",
        lowest_price_minor=price, median_price_minor=price, volume=500,
    )


async def test_muted_item_suppresses_alerts_without_deleting_rules(
    session, user, item, settings
):
    rule = await _baseline_and_rule(session, user, item)
    entry, _ = await watch_repo.add(session, user.id, item.id)
    await session.commit()

    engine = AlertEngine(settings)
    assert len(await engine.evaluate_item(session, item, await _drop(session, item))) == 1

    entry.muted = True
    rule.last_triggered_at = None
    rule.armed = True
    await session.commit()

    assert await engine.evaluate_item(session, item, await _drop(session, item, 700)) == []
    # The rule survives the mute -- thresholds the user tuned are not thrown away.
    assert await alert_repo.by_id(session, rule.id) is not None


async def test_rule_without_watchlist_entry_still_fires(session, user, item, settings):
    await _baseline_and_rule(session, user, item)
    engine = AlertEngine(settings)
    assert len(await engine.evaluate_item(session, item, await _drop(session, item))) == 1


async def test_user_cap_override_beats_global(session, user, item, settings):
    settings.alert_max_per_user_hour = 50
    user.alert_cap_per_hour = 1
    await _baseline_and_rule(session, user, item)
    await alert_repo.create(
        session, user_id=user.id, item_id=item.id,
        kind=AlertKind.PCT_DROP, threshold_pct=5, window_minutes=60,
    )
    await session.commit()

    engine = AlertEngine(settings)
    triggers = await engine.evaluate_item(session, item, await _drop(session, item))
    assert len(triggers) == 1  # the user's own tighter cap applies

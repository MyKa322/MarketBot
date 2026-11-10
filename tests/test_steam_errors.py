"""The Steam exception hierarchy, and the call sites that depend on it.

CircuitOpenError used to be a bare RuntimeError living outside the SteamError tree, so
every `except SteamError` in the codebase silently missed a tripped breaker. Two places
compensated by naming it explicitly; the rest quietly did the wrong thing. These tests
pin the hierarchy and the behaviour that follows from it.
"""
from __future__ import annotations

import pytest

from marketbot.steam.errors import (
    CircuitOpenError,
    RateLimitError,
    SteamError,
    SteamNotFound,
    SteamUnavailable,
)


def test_circuit_open_is_a_steam_unavailable():
    """The regression: this is what makes every `except SteamError` correct."""
    exc = CircuitOpenError("listing_page", 59)
    assert isinstance(exc, SteamUnavailable)
    assert isinstance(exc, SteamError)


def test_circuit_open_keeps_its_detail():
    exc = CircuitOpenError("listing_page", 59.4)
    assert exc.endpoint == "listing_page"
    assert exc.retry_after == pytest.approx(59.4)
    assert "listing_page" in str(exc) and "59" in str(exc)


def test_rate_limit_error_is_also_unavailable():
    """Our limiter declining is the same outcome for a caller as Steam declining."""
    assert isinstance(RateLimitError("no token"), SteamUnavailable)


def test_not_found_is_not_unavailable():
    """'No listings' is data; it must not be mistaken for a transient outage."""
    exc = SteamNotFound("no listings")
    assert isinstance(exc, SteamError)
    assert not isinstance(exc, SteamUnavailable)


def test_the_re_exports_are_the_same_objects():
    """Older imports still resolve, and to the same classes -- not copies."""
    from marketbot.steam import client, rate_limit

    assert rate_limit.CircuitOpenError is CircuitOpenError
    assert client.SteamError is SteamError
    assert client.SteamUnavailable is SteamUnavailable


# --------------------------------------------------- behaviour at the call sites


async def test_search_falls_back_to_local_when_the_circuit_is_open(
    session, item, fake_steam, settings
):
    """Before the fix this raised instead, so the user lost their local matches."""
    from marketbot.services.items import ItemService

    fake_steam.fail_with = CircuitOpenError("search", 42)
    result = await ItemService(fake_steam, settings).resolve(session, "Redline")

    assert result.candidates, "should fall back to already-known items"
    assert result.source == "local"
    assert result.candidates[0].market_hash_name == item.market_hash_name


async def test_ensure_meta_reports_a_transient_refusal(session, item, fake_steam, settings):
    """It must not swallow this.

    Swallowing made the nameid sweep record `processed=1, status=ok` for an item it
    never fetched -- a job that looked healthy while doing nothing.
    """
    from marketbot.services.pricing import PricingService

    item.item_nameid = None
    item.icon_url = None
    fake_steam.fail_with = CircuitOpenError("listing_page", 59)

    with pytest.raises(SteamUnavailable):
        await PricingService(fake_steam, settings).ensure_meta(session, item)


async def test_ensure_meta_swallows_a_permanent_failure(session, item, fake_steam, settings):
    """Nothing to retry for this item, so the caller should carry on."""
    from marketbot.services.pricing import PricingService

    item.item_nameid = None
    item.icon_url = None
    fake_steam.fail_with = SteamNotFound("no listing page")

    returned = await PricingService(fake_steam, settings).ensure_meta(session, item)
    assert returned is item
    assert item.item_nameid is None


async def test_diagnostics_reports_throttled_not_failed(settings, monkeypatch):
    """`--check` said FAIL on an open circuit, which is the wrong diagnosis."""
    from marketbot import diagnostics

    class Refusing:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def price_overview(self, ref, code="USD"):
            raise CircuitOpenError("priceoverview", 30)

    # check_steam imports the client inside the function, so patch it at its source.
    from marketbot.steam import client as client_module

    monkeypatch.setattr(client_module, "SteamMarketClient", lambda *_a, **_k: Refusing())
    results = await diagnostics.check_steam(settings)

    assert len(results) == 1
    assert results[0].status == diagnostics.WARN
    assert "throttled" in results[0].detail


async def test_a_backed_off_job_is_recorded_as_throttled_not_failed(
    settings, engine, session_factory, fake_steam
):
    """The reported symptom: an open circuit logged a traceback as a job failure.

    Backing off is the breaker working. It should read as such in job_runs, so a real
    error is still visible among the noise.
    """
    from sqlalchemy import select

    from marketbot.db.models import JobRun
    from marketbot.db.session import session_scope
    from marketbot.registry import Services
    from marketbot.scheduler.jobs import JobRunner
    from marketbot.services.alerts import AlertEngine
    from marketbot.services.charts import ChartService
    from marketbot.services.items import ItemService
    from marketbot.services.pricing import PricingService
    from marketbot.services.refresh import RefreshPlanner, RefreshWorker

    alerts = AlertEngine(settings)
    pricing = PricingService(fake_steam, settings, alert_engine=alerts)
    planner = RefreshPlanner()
    services = Services(
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
    runner = JobRunner(services)

    async with runner._track("probe") as stats:
        stats["processed"] = 1
        raise CircuitOpenError("listing_page", 59)

    async with session_scope(session_factory) as session:
        run = (await session.scalars(select(JobRun).where(JobRun.name == "probe"))).one()

    assert run.status == "throttled"
    assert run.status != "error"
    assert "listing_page" in (run.detail or "")
    assert run.processed == 1  # partial progress is still recorded


async def test_a_real_bug_is_still_recorded_as_an_error(
    settings, engine, session_factory, fake_steam
):
    """The throttled path must not swallow genuine failures."""
    from sqlalchemy import select

    from marketbot.db.models import JobRun
    from marketbot.db.session import session_scope
    from marketbot.registry import Services
    from marketbot.scheduler.jobs import JobRunner
    from marketbot.services.alerts import AlertEngine
    from marketbot.services.charts import ChartService
    from marketbot.services.items import ItemService
    from marketbot.services.pricing import PricingService
    from marketbot.services.refresh import RefreshPlanner, RefreshWorker

    alerts = AlertEngine(settings)
    pricing = PricingService(fake_steam, settings, alert_engine=alerts)
    planner = RefreshPlanner()
    services = Services(
        settings=settings, engine=engine, session_factory=session_factory,
        steam=fake_steam, pricing=pricing, items=ItemService(fake_steam, settings),
        charts=ChartService(), alerts=alerts, planner=planner,
        worker=RefreshWorker(planner, pricing, session_factory, settings),
    )

    async with JobRunner(services)._track("boom"):
        raise ValueError("a genuine bug")

    async with session_scope(session_factory) as session:
        run = (await session.scalars(select(JobRun).where(JobRun.name == "boom"))).one()

    assert run.status == "error"
    assert "ValueError" in (run.detail or "")


async def test_a_job_can_declare_throttled_without_raising(
    settings, engine, session_factory, fake_steam
):
    """A job that handles a refusal itself still records why it achieved nothing."""
    from sqlalchemy import select

    from marketbot.db.models import JobRun
    from marketbot.db.session import session_scope
    from marketbot.registry import Services
    from marketbot.scheduler.jobs import JobRunner
    from marketbot.services.alerts import AlertEngine
    from marketbot.services.charts import ChartService
    from marketbot.services.items import ItemService
    from marketbot.services.pricing import PricingService
    from marketbot.services.refresh import RefreshPlanner, RefreshWorker

    alerts = AlertEngine(settings)
    pricing = PricingService(fake_steam, settings, alert_engine=alerts)
    planner = RefreshPlanner()
    services = Services(
        settings=settings, engine=engine, session_factory=session_factory,
        steam=fake_steam, pricing=pricing, items=ItemService(fake_steam, settings),
        charts=ChartService(), alerts=alerts, planner=planner,
        worker=RefreshWorker(planner, pricing, session_factory, settings),
    )

    async with JobRunner(services)._track("handled") as stats:
        stats["failed"] = 1
        stats["status"] = "throttled"
        stats["detail"] = "circuit open for listing_page"

    async with session_scope(session_factory) as session:
        run = (await session.scalars(select(JobRun).where(JobRun.name == "handled"))).one()

    assert run.status == "throttled"
    assert run.failed == 1

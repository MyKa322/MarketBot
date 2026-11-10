"""Health endpoints used by hosting platforms and uptime pingers."""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from marketbot.registry import Services
from marketbot.services.alerts import AlertEngine
from marketbot.services.charts import ChartService
from marketbot.services.items import ItemService
from marketbot.services.pricing import PricingService
from marketbot.services.refresh import RefreshPlanner, RefreshWorker
from marketbot.webserver import build_app


@pytest_asyncio.fixture
async def client(settings, engine, session_factory, fake_steam):
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
    test_client = TestClient(TestServer(build_app(services)))
    await test_client.start_server()
    yield test_client
    await test_client.close()


async def test_ping(client):
    response = await client.get("/ping")
    assert response.status == 200
    assert await response.text() == "pong"


async def test_index_is_plain_and_harmless(client):
    response = await client.get("/")
    assert response.status == 200
    assert "MarketBot" in await response.text()


async def test_healthz_reports_limiter_state(client):
    response = await client.get("/healthz")
    assert response.status == 200
    body = json.loads(await response.text())
    assert body["status"] == "ok"
    assert "steam" in body and "queue" in body and "worker" in body
    assert body["steam"]["rate_per_min"] > 0


async def test_health_is_not_cached(client):
    """An uptime pinger reading a cached 200 would mask a dead process."""
    response = await client.get("/healthz")
    assert response.headers["Cache-Control"] == "no-store"


async def test_unknown_path_404s(client):
    response = await client.get("/not-a-route")
    assert response.status == 404


async def test_busy_port_raises_a_typed_error(settings, engine, session_factory, fake_steam):
    """A port conflict must be distinguishable from a real failure.

    In polling mode the bot runs fine without a health endpoint, so the caller downgrades
    this to a warning; in webhook mode it is fatal. Both need to tell it apart from an
    arbitrary OSError.
    """
    from aiohttp import web

    from marketbot.webserver import PortInUseError, serve

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

    # Bind an ephemeral port first, then ask for the same one again.
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    first = await serve(build_app(services), "127.0.0.1", port)
    try:
        with pytest.raises(PortInUseError, match="already in use"):
            await serve(web.Application(), "127.0.0.1", port)
    finally:
        await first.cleanup()

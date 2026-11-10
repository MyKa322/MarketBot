"""HTTP surface for hosting: health checks and the optional Telegram webhook.

Two jobs, one port:

* **Health.** Most platforms consider a process that never binds $PORT a failed
  deploy and kill it, and free tiers on some providers sleep a service that receives
  no HTTP traffic. Binding a port and answering GET is the price of admission.
* **Webhook.** Polling works anywhere and needs no public URL. Webhooks use far less
  CPU and no outbound long-poll, which matters on a constrained free instance.

Uses aiohttp, which aiogram already depends on, so basic hosting does not drag in
FastAPI and uvicorn.
"""
from __future__ import annotations

import logging

from aiohttp import web

from marketbot.registry import Services

log = logging.getLogger(__name__)


def build_app(services: Services) -> web.Application:
    app = web.Application()

    async def healthz(_request: web.Request) -> web.Response:
        """Liveness plus the limiter state, so an uptime pinger doubles as a monitor."""
        # Always 200 while the process is alive. An open circuit or a Steam 429 is
        # the breaker working as designed, not a reason for the platform to restart a
        # bot that is correctly backing off -- so those are reported in the body, not
        # the status code.
        return web.json_response(
            {"status": "ok", **services.health()},
            headers={"Cache-Control": "no-store"},
        )

    async def ping(_request: web.Request) -> web.Response:
        return web.Response(text="pong", headers={"Cache-Control": "no-store"})

    async def index(_request: web.Request) -> web.Response:
        return web.Response(
            text="MarketBot is running. Talk to the bot in Telegram.",
            content_type="text/plain",
        )

    app.router.add_get("/", index)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/ping", ping)
    return app


def attach_webhook(app: web.Application, services: Services, dispatcher, bot) -> None:
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    settings = services.settings
    handler = SimpleRequestHandler(
        dispatcher=dispatcher,
        bot=bot,
        secret_token=settings.webhook_secret or None,
        services=services,
    )
    handler.register(app, path="/" + settings.webhook_path.lstrip("/"))
    setup_application(app, dispatcher, bot=bot)


class PortInUseError(RuntimeError):
    """The health/webhook port is already bound by another process."""


async def serve(app: web.Application, host: str, port: int) -> web.AppRunner:
    """Start the server and return the runner so the caller can shut it down.

    Raises PortInUseError rather than a bare OSError so the caller can decide: for the
    health endpoint alone a busy port is an inconvenience, but in webhook mode it means
    no updates arrive at all.
    """
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    try:
        await site.start()
    except OSError as exc:
        await runner.cleanup()
        raise PortInUseError(
            f"port {port} is already in use (another copy of the bot?). "
            f"Set PORT to a free port, or stop the other process."
        ) from exc
    log.info("http server listening on %s:%s", host, port)
    return runner

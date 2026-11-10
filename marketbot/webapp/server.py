"""Optional Telegram Mini App backend.

A read-mostly JSON API over the same services the bot uses, plus the static SPA. Every
request is authenticated from the Telegram initData signature -- there are no sessions
and no cookies, so there is nothing to steal.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from marketbot.db.models import User
from marketbot.db.repo import items as item_repo
from marketbot.db.repo import prices as price_repo
from marketbot.db.repo import users as user_repo
from marketbot.db.repo import watchlist as watch_repo
from marketbot.db.session import session_scope
from marketbot.registry import Services
from marketbot.utils.money import pct_change, to_major
from marketbot.utils.timeutil import utcnow
from marketbot.webapp.auth import InitDataError, validate_init_data

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"

SPANS = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}


def _major(minor: int | None, code: str) -> float | None:
    return float(to_major(minor, code)) if minor is not None else None


def create_app(services: Services) -> FastAPI:
    app = FastAPI(title="MarketBot Mini App", docs_url=None, redoc_url=None)

    async def current_user(
        x_telegram_init_data: Annotated[str | None, Header()] = None,
    ) -> dict:
        try:
            web_user = validate_init_data(
                x_telegram_init_data or "", services.settings.bot_token
            )
        except InitDataError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return {"telegram_id": web_user.telegram_id, "username": web_user.username}

    Auth = Annotated[dict, Depends(current_user)]

    async def _load_user(session, identity: dict) -> User:
        return await user_repo.get_or_create(
            session,
            identity["telegram_id"],
            username=identity.get("username"),
            currency=services.settings.steam_currency,
        )

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse(services.health())

    @app.get("/api/overview")
    async def overview(identity: Auth) -> dict:
        async with session_scope(services.session_factory) as session:
            user = await _load_user(session, identity)
            entries = await watch_repo.list_for_user(session, user.id)
            latest = await price_repo.latest_many(session, [e.item_id for e in entries])
            day_ago = utcnow() - timedelta(days=1)

            items = []
            for entry in entries:
                snapshot = latest.get(entry.item_id)
                past = await price_repo.price_at_or_before(session, entry.item_id, day_ago)
                items.append(
                    {
                        "id": entry.item_id,
                        "name": entry.item.market_hash_name,
                        "appid": entry.item.appid,
                        "icon": entry.item.icon_url,
                        "price": _major(snapshot.price_minor if snapshot else None, user.currency),
                        "change24h": pct_change(
                            past.price_minor if past else None,
                            snapshot.price_minor if snapshot else None,
                        ),
                        "volume": snapshot.volume if snapshot else None,
                        "updatedAt": snapshot.captured_at.isoformat() if snapshot else None,
                        "pinned": entry.pinned,
                    }
                )

            return {"currency": user.currency, "items": items}

    @app.get("/api/item/{item_id}")
    async def item_detail(item_id: int, identity: Auth, span: str = "7d") -> dict:
        days = SPANS.get(span, 7)
        async with session_scope(services.session_factory) as session:
            user = await _load_user(session, identity)
            item = await item_repo.by_id(session, item_id)
            if item is None:
                raise HTTPException(status_code=404, detail="item not found")

            stats = await services.pricing.compute_stats(session, item, currency=user.currency)
            rows = await price_repo.series(
                session, item.id, since=utcnow() - timedelta(days=days), limit=1000
            )
            return {
                "id": item.id,
                "name": item.market_hash_name,
                "appid": item.appid,
                "currency": stats.currency,
                "current": _major(stats.current_minor, stats.currency),
                "change1h": stats.change_1h,
                "change24h": stats.change_24h,
                "change7d": stats.change_7d,
                "high7d": _major(stats.high_7d, stats.currency),
                "low7d": _major(stats.low_7d, stats.currency),
                "volume": stats.volume,
                "spreadPct": stats.spread_pct,
                "watching": await watch_repo.get(session, user.id, item.id) is not None,
                "series": [
                    {
                        "t": r.captured_at.isoformat(),
                        "p": float(to_major(r.price_minor, stats.currency)),
                        "v": r.volume or 0,
                    }
                    for r in rows
                    if r.price_minor is not None
                ],
            }

    @app.post("/api/item/{item_id}/watch")
    async def toggle_watch(item_id: int, identity: Auth) -> dict:
        async with session_scope(services.session_factory) as session:
            user = await _load_user(session, identity)
            item = await item_repo.by_id(session, item_id)
            if item is None:
                raise HTTPException(status_code=404, detail="item not found")

            if await watch_repo.get(session, user.id, item_id):
                await watch_repo.remove(session, user.id, item_id)
                item.watchers_count = max(0, (item.watchers_count or 1) - 1)
                return {"watching": False}

            limit = services.settings.max_watchlist_per_user
            if await watch_repo.count_for_user(session, user.id) >= limit:
                raise HTTPException(status_code=409, detail=f"watchlist limit {limit} reached")
            await watch_repo.add(session, user.id, item_id)
            item.watchers_count = (item.watchers_count or 0) + 1
            services.worker.request_now(item)
            return {"watching": True}

    @app.post("/api/item/{item_id}/refresh")
    async def request_refresh(item_id: int, identity: Auth) -> dict:
        async with session_scope(services.session_factory) as session:
            await _load_user(session, identity)
            item = await item_repo.by_id(session, item_id)
            if item is None:
                raise HTTPException(status_code=404, detail="item not found")
            queued = services.worker.request_now(item)
        return {"queued": queued, "backlog": len(services.planner)}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


async def serve(services: Services) -> None:
    """Run the Mini App server alongside the bot in the same event loop."""
    import uvicorn

    config = uvicorn.Config(
        create_app(services),
        host=services.settings.webapp_host,
        port=services.settings.webapp_port,
        log_level=services.settings.log_level.lower(),
        access_log=False,
    )
    await uvicorn.Server(config).serve()

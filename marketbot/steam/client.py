"""The single egress point to Steam.

Nothing else in the codebase may open a connection to steamcommunity.com. Every call
passes through: global token bucket -> per-endpoint circuit breaker -> TTL cache ->
retry with jittered backoff -> AIMD governor feedback.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from marketbot.config import Settings
from marketbot.constants import DEFAULT_CURRENCY, STEAM_COMMUNITY_BASE, currency
from marketbot.steam import parse as P
from marketbot.steam.dto import HistoryPoint, ItemMeta, ItemRef, OrderBook, PriceOverview, SearchHit
from marketbot.steam.errors import (
    SteamError,
    SteamNotFound,
    SteamUnavailable,
)
from marketbot.steam.rate_limit import AdaptiveGovernor, CircuitBreaker, TokenBucket, TTLCache
from marketbot.utils.timeutil import utcnow

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

# Relative cost of each endpoint in bucket tokens. HTML pages are far heavier on
# Steam's side than the JSON endpoints, and they are the ones that get you banned.
ENDPOINT_COST = {
    "priceoverview": 1.0,
    "itemordershistogram": 1.0,
    "search": 1.5,
    "listing_page": 2.0,
    "pricehistory": 2.0,
}


class SteamMarketClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        bucket: TokenBucket | None = None,
    ) -> None:
        self._s = settings
        self._bucket = bucket or TokenBucket(
            settings.steam_rate_per_min / 60.0, settings.steam_burst
        )
        self._governor = AdaptiveGovernor(
            self._bucket,
            base_rate_per_min=settings.steam_rate_per_min,
            min_rate_per_min=settings.steam_min_rate_per_min,
        )
        self._breakers: dict[str, CircuitBreaker] = {}
        self._cache: TTLCache[Any] = TTLCache(settings.steam_cache_ttl)
        self._owns_client = client is None
        self._client = client or self._build_client(settings)
        self.stats = {"requests": 0, "cache_hits": 0, "throttled": 0, "errors": 0}

    @staticmethod
    def _build_client(s: Settings) -> httpx.AsyncClient:
        cookies = {}
        if s.steam_login_secure:
            cookies["steamLoginSecure"] = s.steam_login_secure
        return httpx.AsyncClient(
            base_url=STEAM_COMMUNITY_BASE,
            timeout=httpx.Timeout(s.steam_timeout),
            follow_redirects=True,
            proxy=s.steam_proxy or None,
            cookies=cookies,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> SteamMarketClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _breaker(self, endpoint: str) -> CircuitBreaker:
        br = self._breakers.get(endpoint)
        if br is None:
            br = self._breakers[endpoint] = CircuitBreaker(
                endpoint,
                failure_threshold=self._s.cb_failure_threshold,
                base_cooldown=self._s.cb_base_cooldown,
                max_cooldown=self._s.cb_max_cooldown,
            )
        return br

    def health(self) -> dict[str, Any]:
        return {
            **self.stats,
            "rate_per_min": round(self._governor.rate_per_min, 2),
            "tokens": round(self._bucket.tokens, 2),
            "circuits": {k: v.state.value for k, v in self._breakers.items()},
        }

    # ------------------------------------------------------------------ plumbing

    @staticmethod
    def _backoff(resp: httpx.Response | None, attempt: int) -> float:
        if resp is not None:
            raw = resp.headers.get("Retry-After")
            if raw:
                try:
                    return min(float(raw), 300.0)
                except ValueError:
                    pass
        # Exponential backoff with full jitter: stops concurrent workers from all
        # waking at the same instant and re-triggering the same 429.
        return random.uniform(0.0, min(2.0**attempt, 60.0))

    async def _fetch(
        self, endpoint: str, url: str, params: dict[str, Any] | None, cost: float
    ) -> httpx.Response:
        breaker = self._breaker(endpoint)
        last_error: Exception | None = None
        throttles = 0

        for attempt in range(1, self._s.steam_max_retries + 1):
            breaker.check()
            await self._bucket.acquire(cost)
            self.stats["requests"] += 1
            try:
                resp = await self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                last_error = exc
                self.stats["errors"] += 1
                breaker.on_failure()
                await asyncio.sleep(self._backoff(None, attempt))
                continue

            if resp.status_code == 429:
                # Observed live: Steam answers 429 with a `null` body and no
                # Retry-After on market endpoints, and the throttle can persist for
                # minutes. Burning the full retry budget here would block the worker
                # that whole time for one item.
                self.stats["throttled"] += 1
                throttles += 1
                self._governor.on_throttled()
                breaker.on_failure()
                last_error = SteamUnavailable(f"429 from {endpoint}")
                if throttles > self._s.steam_throttle_retries:
                    log.warning(
                        "steam throttling endpoint=%s, giving up to requeue (rate=%.1f/min)",
                        endpoint, self._governor.rate_per_min,
                    )
                    raise last_error
                delay = self._backoff(resp, attempt)
                log.warning(
                    "steam throttled endpoint=%s attempt=%s backoff=%.1fs rate=%.1f/min",
                    endpoint, attempt, delay, self._governor.rate_per_min,
                )
                await asyncio.sleep(delay)
                continue

            if resp.status_code >= 500:
                self.stats["errors"] += 1
                breaker.on_failure()
                last_error = SteamUnavailable(f"{resp.status_code} from {endpoint}")
                await asyncio.sleep(self._backoff(resp, attempt))
                continue

            if resp.status_code == 404:
                breaker.on_success()
                raise SteamNotFound(f"404 from {endpoint}: {url}")

            if resp.status_code >= 400:
                # A client error says nothing about upstream health, so it must not
                # count toward opening the circuit.
                breaker.on_success()
                raise SteamError(f"{resp.status_code} from {endpoint}: {url}")

            breaker.on_success()
            self._governor.on_success()
            return resp

        raise SteamUnavailable(str(last_error) if last_error else f"{endpoint} failed")

    async def _get(
        self,
        endpoint: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        ttl: float | None = None,
        as_json: bool = True,
    ) -> Any:
        cost = ENDPOINT_COST.get(endpoint, 1.0)
        key = f"{endpoint}|{url}|{sorted((params or {}).items())}"

        cached = self._cache.get(key)
        if cached is not None:
            self.stats["cache_hits"] += 1
            return cached

        # Double-checked locking: concurrent misses on one key collapse into a single
        # upstream request instead of N.
        async with self._cache.lock(key):
            cached = self._cache.get(key)
            if cached is not None:
                self.stats["cache_hits"] += 1
                return cached
            resp = await self._fetch(endpoint, url, params, cost)
            payload = resp.json() if as_json else resp.text
            self._cache.set(key, payload, ttl=ttl)
            return payload

    # ----------------------------------------------------------------- endpoints

    @staticmethod
    def _currency_id(code: str) -> int:
        return currency(code).steam_id

    async def price_overview(self, item: ItemRef, code: str = DEFAULT_CURRENCY) -> PriceOverview:
        """Cheapest endpoint and the backbone of routine polling.

        Returns lowest/median price and 24h volume. Steam answers success=false for
        items that exist but currently have zero listings, which is data, not an error.
        """
        data = await self._get(
            "priceoverview",
            "/market/priceoverview/",
            params={
                "appid": item.appid,
                "currency": self._currency_id(code),
                "market_hash_name": item.market_hash_name,
            },
        )
        if not isinstance(data, dict):
            raise SteamError("unexpected priceoverview payload")
        if not data.get("success"):
            raise SteamNotFound(f"no market data for {item.market_hash_name}")
        return PriceOverview(
            item=item,
            currency=code,
            lowest_price=P.parse_price(data.get("lowest_price"), code),
            median_price=P.parse_price(data.get("median_price"), code),
            volume=P.parse_int(data.get("volume")),
            fetched_at=utcnow(),
        )

    async def item_meta(self, item: ItemRef) -> ItemMeta:
        """Scrape item_nameid and icon from the listing page.

        Expensive (full HTML) but needed exactly once per item: item_nameid is stable
        forever, so it is persisted on first fetch and never looked up again.
        """
        html = await self._get(
            "listing_page",
            f"/market/listings/{item.appid}/{item.market_hash_name}",
            as_json=False,
            ttl=3600,
        )
        return ItemMeta(
            item=item,
            item_nameid=P.extract_item_nameid(html),
            icon_url=P.extract_icon_url(html),
        )

    async def order_book(
        self, item: ItemRef, item_nameid: str, code: str = DEFAULT_CURRENCY
    ) -> OrderBook:
        """Live buy/sell order book -- the real signal for spread and liquidity.

        Note Steam returns highest_buy_order / lowest_sell_order already in integer
        minor units, while the graphs use float major units. Mixing those two up is
        a 100x pricing bug, so they are parsed differently on purpose.
        """
        data = await self._get(
            "itemordershistogram",
            "/market/itemordershistogram",
            params={
                "country": "US",
                "language": "english",
                "currency": self._currency_id(code),
                "item_nameid": item_nameid,
                "two_factor": 0,
            },
        )
        if not isinstance(data, dict) or not data.get("success"):
            raise SteamNotFound(f"no order book for {item.market_hash_name}")

        def graph(rows: Any) -> list[tuple[int, int]]:
            out: list[tuple[int, int]] = []
            for row in rows or []:
                if not isinstance(row, (list, tuple)) or len(row) < 2:
                    continue
                price = P.parse_price(row[0], code)
                qty = P.parse_int(row[1])
                if price is not None and qty is not None:
                    out.append((price, qty))
            return out

        buy_graph = graph(data.get("buy_order_graph"))
        sell_graph = graph(data.get("sell_order_graph"))
        return OrderBook(
            item=item,
            currency=code,
            highest_buy_order=P.parse_int(data.get("highest_buy_order")),
            lowest_sell_order=P.parse_int(data.get("lowest_sell_order")),
            buy_order_count=buy_graph[-1][1] if buy_graph else 0,
            sell_order_count=sell_graph[-1][1] if sell_graph else 0,
            buy_graph=buy_graph,
            sell_graph=sell_graph,
        )

    async def search(
        self,
        query: str,
        *,
        appid: int | None = None,
        limit: int = 10,
        code: str = DEFAULT_CURRENCY,
    ) -> list[SearchHit]:
        """Free-text market search, used to resolve user-typed item names."""
        params: dict[str, Any] = {
            "query": query,
            "start": 0,
            "count": max(1, min(limit, 100)),
            "search_descriptions": 0,
            "sort_column": "popular",
            "sort_dir": "desc",
            "norender": 1,
            "currency": self._currency_id(code),
        }
        if appid:
            params["appid"] = appid

        data = await self._get("search", "/market/search/render/", params=params, ttl=300)
        if not isinstance(data, dict) or not data.get("success"):
            return []

        hits: list[SearchHit] = []
        for row in data.get("results") or []:
            desc = row.get("asset_description") or {}
            hash_name = row.get("hash_name") or desc.get("market_hash_name")
            row_appid = desc.get("appid") or row.get("appid") or appid
            if not hash_name or not row_appid:
                continue
            hits.append(
                SearchHit(
                    item=ItemRef(int(row_appid), str(hash_name)),
                    name=row.get("name") or hash_name,
                    icon_url=P.icon_url(desc.get("icon_url")),
                    sell_price=P.parse_int(row.get("sell_price")),
                    sell_listings=P.parse_int(row.get("sell_listings")) or 0,
                    currency=code,
                )
            )
        return hits

    async def price_history(
        self, item: ItemRef, code: str = DEFAULT_CURRENCY
    ) -> list[HistoryPoint]:
        """Steam's own hourly/daily history. Requires a logged-in cookie.

        This is a bootstrap convenience: it backfills a chart on the day an item is
        first added, so users are not staring at an empty graph. Ongoing history comes
        from our own snapshots, which do not depend on anyone's session staying valid.
        """
        if not self._s.steam_login_secure:
            raise SteamError("price_history requires STEAM_LOGIN_SECURE")

        data = await self._get(
            "pricehistory",
            "/market/pricehistory/",
            params={
                "appid": item.appid,
                "market_hash_name": item.market_hash_name,
                "currency": self._currency_id(code),
            },
            ttl=3600,
        )
        if not isinstance(data, dict) or not data.get("success"):
            raise SteamNotFound(f"no price history for {item.market_hash_name}")

        points: list[HistoryPoint] = []
        for row in data.get("prices") or []:
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                continue
            ts = P.parse_history_timestamp(row[0])
            price = P.parse_price(row[1], code)
            volume = P.parse_int(row[2]) or 0
            if ts and price is not None:
                points.append(HistoryPoint(ts=ts, price=price, volume=volume))
        points.sort(key=lambda p: p.ts)
        return points

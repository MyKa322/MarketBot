"""Priority-scored refresh queue and the single worker that drains it.

Demand for fresh prices is bursty (everyone opens their watchlist after work) but the
supply of Steam requests is a hard constant. A queue in between converts the burst
into a backlog that drains at a safe rate, instead of a wall of 429s.

Only one worker runs per process. That is deliberate: concurrency here would defeat
the rate limiter, and the bottleneck is Steam, not us.
"""
from __future__ import annotations

import asyncio
import contextlib
import heapq
import itertools
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketbot.config import Settings
from marketbot.constants import TIER_INTERVAL_SECONDS, RefreshTier
from marketbot.db.models import Item
from marketbot.db.repo import items as item_repo
from marketbot.db.session import session_scope
from marketbot.services.pricing import PricingService
from marketbot.steam.client import SteamUnavailable
from marketbot.steam.dto import ItemRef
from marketbot.steam.rate_limit import CircuitOpenError
from marketbot.utils.timeutil import ensure_utc, utcnow

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RefreshTask:
    item_id: int
    priority: float
    source: str
    enqueued_at: datetime


def score_item(item: Item, *, now: datetime | None = None, active_rules: int = 0) -> float:
    """How urgently this item needs a new price.

    Watchers dominate (real people are looking at it), staleness ramps it up, alert
    rules add pressure because a stale price means a missed alert, and repeated
    failures push an item down so one broken entry cannot monopolise the budget.
    """
    now = now or utcnow()
    interval = TIER_INTERVAL_SECONDS.get(item.tier, TIER_INTERVAL_SECONDS[RefreshTier.COLD])

    watchers = min(item.watchers_count, 20) * 3.0

    if item.last_refreshed_at is None:
        staleness = 20.0  # never fetched: get a first price quickly
    else:
        age = (now - ensure_utc(item.last_refreshed_at)).total_seconds()
        staleness = min(age / interval, 5.0) * 4.0

    pressure = min(active_rules, 10) * 1.5
    penalty = item.fail_count * 5.0

    return watchers + staleness + pressure - penalty


class RefreshPlanner:
    """Bounded max-heap of pending refreshes, deduplicated by item."""

    def __init__(self, maxsize: int = 5000) -> None:
        self._heap: list[tuple[float, int, RefreshTask]] = []
        self._pending: set[int] = set()
        self._counter = itertools.count()
        self._maxsize = maxsize
        self._wakeup = asyncio.Event()

    def __len__(self) -> int:
        return len(self._heap)

    def is_pending(self, item_id: int) -> bool:
        return item_id in self._pending

    def enqueue(self, item_id: int, priority: float, *, source: str = "scheduler") -> bool:
        """Returns False when the item is already queued or the queue is full.

        Dedup matters: without it, an impatient user tapping Refresh five times would
        spend five of the minute's eighteen requests on the same item.
        """
        if item_id in self._pending or len(self._heap) >= self._maxsize:
            return False
        task = RefreshTask(item_id, priority, source, utcnow())
        # Negated priority because heapq is a min-heap.
        heapq.heappush(self._heap, (-priority, next(self._counter), task))
        self._pending.add(item_id)
        self._wakeup.set()
        return True

    def pop(self) -> RefreshTask | None:
        if not self._heap:
            return None
        _, _, task = heapq.heappop(self._heap)
        self._pending.discard(task.item_id)
        if not self._heap:
            self._wakeup.clear()
        return task

    async def wait_for_work(self, timeout: float = 30.0) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wakeup.wait(), timeout=timeout)

    def stats(self) -> dict[str, int]:
        return {"queued": len(self._heap)}


class RefreshWorker:
    """Drains the planner sequentially; the client's token bucket sets the pace."""

    def __init__(
        self,
        planner: RefreshPlanner,
        pricing: PricingService,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._planner = planner
        self._pricing = pricing
        self._factory = session_factory
        self._s = settings
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self.processed = 0
        self.failed = 0

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._run(), name="refresh-worker")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            # CancelledError does not inherit from Exception, so both are named.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def _run(self) -> None:
        log.info("refresh worker started")
        while not self._stopping.is_set():
            task = self._planner.pop()
            if task is None:
                await self._planner.wait_for_work(timeout=15.0)
                continue
            try:
                await self._process(task)
            except (SteamUnavailable, CircuitOpenError) as exc:
                # Upstream is unhappy. Put the item back and stand down for a while --
                # the circuit breaker already knows how long it wants.
                cooldown = getattr(exc, "retry_after", 30.0) or 30.0
                self._planner.enqueue(task.item_id, task.priority, source="retry")
                log.warning("refresh paused for %.0fs: %s", cooldown, exc)
                await asyncio.sleep(min(cooldown, 120.0))
            except asyncio.CancelledError:
                raise
            except Exception:
                self.failed += 1
                log.exception("refresh failed item_id=%s", task.item_id)
        log.info("refresh worker stopped")

    async def _process(self, task: RefreshTask) -> None:
        """Three phases, and the middle one holds no database session.

        A throttled fetch can wait tens of seconds in the token bucket. Doing that
        inside a transaction locks out every other writer for the duration, which on
        SQLite surfaces as "database is locked" in the bot handlers. So: read what the
        request needs, let the session go, fetch, then reopen briefly to write.
        """
        async with session_scope(self._factory) as session:
            item = await item_repo.by_id(session, task.item_id)
            if item is None:
                return
            ref = ItemRef(item.appid, item.market_hash_name)
            item_nameid = item.item_nameid
            # The order book costs a second request, so spend it only on items people
            # actually watch -- that is where spread and liquidity matter.
            with_book = bool(item_nameid) and item.watchers_count > 0

        fetched = await self._pricing.fetch(
            ref, item_nameid=item_nameid, with_order_book=with_book
        )

        async with session_scope(self._factory) as session:
            item = await item_repo.by_id(session, task.item_id)
            if item is None:
                return  # deleted while we were fetching
            await self._pricing.persist(session, item, fetched)
            self.processed += 1

    async def enqueue_due(
        self, session: AsyncSession, tier: RefreshTier, *, limit: int
    ) -> int:
        """Scheduler entry point: queue everything due in a tier."""
        items = await item_repo.due_for_refresh(session, tier, limit=limit)
        now = utcnow()
        queued = 0
        for item in items:
            if self._planner.enqueue(item.id, score_item(item, now=now), source=tier.value):
                queued += 1
        if queued:
            log.info("queued %s %s items (backlog=%s)", queued, tier.value, len(self._planner))
        return queued

    def request_now(self, item: Item, *, boost: float = 50.0) -> bool:
        """User-initiated refresh: high priority, but still through the queue.

        The UI never waits on this. It returns immediately and the fresh price shows
        up on the next view -- which is what keeps a tap responsive even when there is
        a 200-item backlog.
        """
        return self._planner.enqueue(item.id, score_item(item) + boost, source="user")

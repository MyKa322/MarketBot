"""Egress control for Steam: token bucket, circuit breaker, AIMD governor, TTL cache.

Steam publishes no rate-limit contract. Observed behaviour is roughly 20 requests per
minute and 200 per five minutes per IP, after which it replies 429 and, if you keep
pushing, temporarily blackholes the address. So this module is built to stay *under*
the limit rather than discover it, and to retreat hard the moment a 429 appears.

Every clock is injectable so the behaviour is unit-testable without real sleeping.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from marketbot.steam.errors import CircuitOpenError, RateLimitError

__all__ = [
    "AdaptiveGovernor",
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "RateLimitError",
    "TTLCache",
    "TokenBucket",
]

Clock = Callable[[], float]



class TokenBucket:
    """Classic token bucket with an adjustable refill rate.

    `capacity` is the burst allowance: how many requests may fire back-to-back after
    an idle period. Keep it small (3-5) for Steam -- large bursts are what trip the
    per-minute ceiling even when the average rate looks safe.
    """

    def __init__(
        self,
        rate_per_sec: float,
        capacity: float,
        *,
        clock: Clock = time.monotonic,
        sleeper: Callable[[float], Any] | None = None,
    ) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        self._rate = rate_per_sec
        self._capacity = max(1.0, capacity)
        self._tokens = self._capacity
        self._clock = clock
        self._sleep = sleeper or asyncio.sleep
        self._updated = clock()
        self._lock = asyncio.Lock()

    @property
    def rate_per_sec(self) -> float:
        return self._rate

    @property
    def tokens(self) -> float:
        self._refill()
        return self._tokens

    def set_rate(self, rate_per_sec: float) -> None:
        self._refill()
        self._rate = max(rate_per_sec, 1e-6)

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._updated = now

    def _try_take(self, tokens: float) -> float:
        """Take tokens if available; otherwise return the seconds to wait."""
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return 0.0
        return (tokens - self._tokens) / self._rate

    async def acquire(self, tokens: float = 1.0, *, timeout: float | None = None) -> None:
        """Block until `tokens` are available. Raises RateLimitError on timeout."""
        deadline = None if timeout is None else self._clock() + timeout
        async with self._lock:
            while True:
                wait = self._try_take(tokens)
                if wait <= 0:
                    return
                if deadline is not None and self._clock() + wait > deadline:
                    raise RateLimitError(
                        f"could not acquire {tokens} token(s) within {timeout}s"
                    )
                await self._sleep(wait)


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-endpoint breaker with exponentially escalating cooldowns.

    After `failure_threshold` consecutive failures the circuit opens for
    base_cooldown * 2**(trips-1), capped at max_cooldown. Once the cooldown expires a
    single probe is allowed through (HALF_OPEN); its outcome either closes the circuit
    or re-opens it with a longer cooldown.
    """

    def __init__(
        self,
        name: str = "default",
        *,
        failure_threshold: int = 3,
        base_cooldown: float = 60.0,
        max_cooldown: float = 1800.0,
        clock: Clock = time.monotonic,
    ) -> None:
        self.name = name
        self._threshold = max(1, failure_threshold)
        self._base = base_cooldown
        self._max = max_cooldown
        self._clock = clock
        self._failures = 0
        self._trips = 0
        self._opened_at = 0.0
        self._state = CircuitState.CLOSED
        self._probing = False

    @property
    def state(self) -> CircuitState:
        self._maybe_half_open()
        return self._state

    @property
    def cooldown(self) -> float:
        if self._trips == 0:
            return 0.0
        return min(self._base * (2 ** (self._trips - 1)), self._max)

    @property
    def retry_after(self) -> float:
        if self._state is not CircuitState.OPEN:
            return 0.0
        return max(0.0, self._opened_at + self.cooldown - self._clock())

    def _maybe_half_open(self) -> None:
        if self._state is CircuitState.OPEN and self.retry_after <= 0:
            self._state = CircuitState.HALF_OPEN
            self._probing = False

    def allow(self) -> bool:
        """True when a request may proceed. HALF_OPEN admits exactly one probe."""
        self._maybe_half_open()
        if self._state is CircuitState.CLOSED:
            return True
        if self._state is CircuitState.HALF_OPEN and not self._probing:
            self._probing = True
            return True
        return False

    def check(self) -> None:
        if not self.allow():
            raise CircuitOpenError(self.name, self.retry_after)

    def on_success(self) -> None:
        self._failures = 0
        self._trips = 0
        self._probing = False
        self._state = CircuitState.CLOSED

    def on_failure(self) -> None:
        self._failures += 1
        if self._state is CircuitState.HALF_OPEN or self._failures >= self._threshold:
            self._trips += 1
            self._failures = 0
            self._probing = False
            self._state = CircuitState.OPEN
            self._opened_at = self._clock()


class AdaptiveGovernor:
    """AIMD controller that steers a TokenBucket's rate from live feedback.

    On a 429 the rate is multiplied by `decrease_factor` (fast retreat). After
    `recovery_after` seconds without throttling the rate climbs by `additive_step`
    requests/min (slow recovery), never above the configured base rate. This is the
    same control law TCP uses, and for the same reason: the safe rate is unknown and
    changes underneath you.
    """

    def __init__(
        self,
        bucket: TokenBucket,
        *,
        base_rate_per_min: float,
        min_rate_per_min: float = 3.0,
        recovery_after: float = 120.0,
        additive_step: float = 1.0,
        decrease_factor: float = 0.5,
        clock: Clock = time.monotonic,
    ) -> None:
        self._bucket = bucket
        self._base = base_rate_per_min
        self._min = min_rate_per_min
        self._recovery_after = recovery_after
        self._step = additive_step
        self._factor = decrease_factor
        self._clock = clock
        self._current = base_rate_per_min
        self._last_change = clock()
        self.throttle_events = 0

    @property
    def rate_per_min(self) -> float:
        return self._current

    def _apply(self, rate_per_min: float) -> None:
        self._current = max(self._min, min(self._base, rate_per_min))
        self._bucket.set_rate(self._current / 60.0)
        self._last_change = self._clock()

    def on_throttled(self) -> None:
        """Called on HTTP 429 or an explicit Steam throttle response."""
        self.throttle_events += 1
        self._apply(self._current * self._factor)

    def on_success(self) -> None:
        if self._current >= self._base:
            return
        if self._clock() - self._last_change >= self._recovery_after:
            self._apply(self._current + self._step)


@dataclass(slots=True)
class _Entry[T]:
    value: T
    expires_at: float


class TTLCache[T]:
    """Tiny TTL cache that collapses duplicate concurrent Steam lookups.

    Two users opening the same item within the TTL cause one upstream request. The
    per-key lock matters as much as the cache: without it, N concurrent misses become
    N requests before the first one finishes writing.
    """

    def __init__(self, ttl: float, *, clock: Clock = time.monotonic, maxsize: int = 2048) -> None:
        self._ttl = ttl
        self._clock = clock
        self._maxsize = maxsize
        self._data: dict[str, _Entry[T]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, key: str) -> T | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            self._data.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: T, *, ttl: float | None = None) -> None:
        if len(self._data) >= self._maxsize:
            now = self._clock()
            expired = [k for k, e in self._data.items() if e.expires_at <= now]
            for k in expired:
                self._data.pop(k, None)
            if len(self._data) >= self._maxsize:
                oldest = min(self._data, key=lambda k: self._data[k].expires_at)
                self._data.pop(oldest, None)
        self._data[key] = _Entry(value, self._clock() + (self._ttl if ttl is None else ttl))

    def lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    def invalidate(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

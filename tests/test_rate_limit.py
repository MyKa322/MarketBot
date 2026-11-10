"""Egress control: token bucket, circuit breaker, AIMD governor, TTL cache."""
from __future__ import annotations

import pytest

from marketbot.steam.rate_limit import (
    AdaptiveGovernor,
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    RateLimitError,
    TokenBucket,
    TTLCache,
)


class FakeClock:
    """Virtual time: sleeping advances the clock instead of blocking the test."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


# ------------------------------------------------------------------- token bucket


async def test_burst_then_throttle(clock):
    bucket = TokenBucket(1.0, capacity=3, clock=clock, sleeper=clock.sleep)
    start = clock.t
    for _ in range(3):
        await bucket.acquire()
    assert clock.t == start  # burst is free

    await bucket.acquire()
    assert clock.t == pytest.approx(start + 1.0)  # fourth waits one refill period


async def test_refill_is_rate_proportional(clock):
    bucket = TokenBucket(2.0, capacity=2, clock=clock, sleeper=clock.sleep)
    await bucket.acquire(2)
    clock.advance(0.5)
    assert bucket.tokens == pytest.approx(1.0)


async def test_capacity_is_not_exceeded(clock):
    bucket = TokenBucket(5.0, capacity=2, clock=clock, sleeper=clock.sleep)
    clock.advance(100)
    assert bucket.tokens == pytest.approx(2.0)


async def test_timeout_raises(clock):
    bucket = TokenBucket(1.0, capacity=1, clock=clock, sleeper=clock.sleep)
    await bucket.acquire()
    with pytest.raises(RateLimitError):
        await bucket.acquire(timeout=0.5)


async def test_set_rate_takes_effect(clock):
    bucket = TokenBucket(1.0, capacity=1, clock=clock, sleeper=clock.sleep)
    await bucket.acquire()
    bucket.set_rate(10.0)
    start = clock.t
    await bucket.acquire()
    assert clock.t - start == pytest.approx(0.1)


def test_rejects_non_positive_rate():
    with pytest.raises(ValueError):
        TokenBucket(0, capacity=1)


# --------------------------------------------------------------- circuit breaker


def test_opens_after_threshold(clock):
    cb = CircuitBreaker("t", failure_threshold=3, base_cooldown=60, clock=clock)
    for _ in range(2):
        cb.on_failure()
    assert cb.state is CircuitState.CLOSED
    cb.on_failure()
    assert cb.state is CircuitState.OPEN
    assert cb.allow() is False


def test_check_raises_when_open(clock):
    cb = CircuitBreaker("endpoint", failure_threshold=1, base_cooldown=60, clock=clock)
    cb.on_failure()
    with pytest.raises(CircuitOpenError) as exc:
        cb.check()
    assert exc.value.retry_after == pytest.approx(60)


def test_half_open_admits_one_probe(clock):
    cb = CircuitBreaker("t", failure_threshold=1, base_cooldown=60, clock=clock)
    cb.on_failure()
    clock.advance(61)
    assert cb.state is CircuitState.HALF_OPEN
    assert cb.allow() is True   # the probe
    assert cb.allow() is False  # everything else still blocked


def test_successful_probe_closes(clock):
    cb = CircuitBreaker("t", failure_threshold=1, base_cooldown=60, clock=clock)
    cb.on_failure()
    clock.advance(61)
    cb.allow()
    cb.on_success()
    assert cb.state is CircuitState.CLOSED
    assert cb.allow() is True


def test_cooldown_doubles_per_trip(clock):
    cb = CircuitBreaker("t", failure_threshold=1, base_cooldown=60, max_cooldown=300, clock=clock)
    seen = []
    for _ in range(5):
        cb.on_failure()
        seen.append(cb.cooldown)
        clock.advance(cb.cooldown + 1)
        cb.allow()  # move to HALF_OPEN and consume the probe
    assert seen == [60, 120, 240, 300, 300]  # doubles, then clamps at max


def test_failed_probe_reopens_longer(clock):
    cb = CircuitBreaker("t", failure_threshold=2, base_cooldown=10, clock=clock)
    cb.on_failure()
    cb.on_failure()
    assert cb.cooldown == 10
    clock.advance(11)
    cb.allow()
    cb.on_failure()  # single failure in HALF_OPEN re-opens immediately
    assert cb.state is CircuitState.OPEN
    assert cb.cooldown == 20


# ------------------------------------------------------------- adaptive governor


def test_throttle_halves_rate(clock):
    bucket = TokenBucket(0.3, capacity=5, clock=clock, sleeper=clock.sleep)
    gov = AdaptiveGovernor(bucket, base_rate_per_min=18, clock=clock)
    gov.on_throttled()
    assert gov.rate_per_min == pytest.approx(9)
    assert bucket.rate_per_sec == pytest.approx(9 / 60)
    gov.on_throttled()
    assert gov.rate_per_min == pytest.approx(4.5)


def test_rate_never_goes_below_floor(clock):
    bucket = TokenBucket(0.3, capacity=5, clock=clock, sleeper=clock.sleep)
    gov = AdaptiveGovernor(bucket, base_rate_per_min=18, min_rate_per_min=3, clock=clock)
    for _ in range(20):
        gov.on_throttled()
    assert gov.rate_per_min == pytest.approx(3)


def test_recovery_is_additive_and_delayed(clock):
    bucket = TokenBucket(0.3, capacity=5, clock=clock, sleeper=clock.sleep)
    gov = AdaptiveGovernor(
        bucket, base_rate_per_min=18, recovery_after=120, additive_step=1.0, clock=clock
    )
    gov.on_throttled()
    assert gov.rate_per_min == pytest.approx(9)

    gov.on_success()  # too soon after the throttle
    assert gov.rate_per_min == pytest.approx(9)

    clock.advance(121)
    gov.on_success()
    assert gov.rate_per_min == pytest.approx(10)


def test_recovery_stops_at_base(clock):
    bucket = TokenBucket(0.3, capacity=5, clock=clock, sleeper=clock.sleep)
    gov = AdaptiveGovernor(bucket, base_rate_per_min=18, recovery_after=1, clock=clock)
    gov.on_throttled()
    for _ in range(50):
        clock.advance(2)
        gov.on_success()
    assert gov.rate_per_min == pytest.approx(18)


# ------------------------------------------------------------------- ttl cache


def test_cache_expires(clock):
    cache: TTLCache[str] = TTLCache(ttl=10, clock=clock)
    cache.set("k", "v")
    assert cache.get("k") == "v"
    clock.advance(11)
    assert cache.get("k") is None


def test_cache_eviction_respects_maxsize(clock):
    cache: TTLCache[int] = TTLCache(ttl=100, clock=clock, maxsize=3)
    for i in range(5):
        cache.set(f"k{i}", i)
    assert len(cache._data) <= 3


def test_cache_lock_is_stable_per_key(clock):
    cache: TTLCache[int] = TTLCache(ttl=10, clock=clock)
    assert cache.lock("a") is cache.lock("a")
    assert cache.lock("a") is not cache.lock("b")

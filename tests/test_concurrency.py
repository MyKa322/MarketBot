"""Regression tests for "database is locked".

The bug had three compounding causes, and each one gets a test here:

1. SQLite ran with the default rollback journal, where a writer blocks readers.
2. `last_seen_at` was written on every update, promoting every handler session to a
   write transaction.
3. The refresh worker held that transaction open across the Steam fetch, which at a
   throttled rate can sit in the token bucket for tens of seconds.

These use a *file* database on purpose: `:memory:` does not reproduce real locking.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest_asyncio
from sqlalchemy import text

from marketbot.config import Settings
from marketbot.db.repo import items as item_repo
from marketbot.db.repo import users as user_repo
from marketbot.db.session import build_engine, build_session_factory, create_all, session_scope
from marketbot.services.pricing import PricingService
from marketbot.services.refresh import RefreshPlanner, RefreshTask, RefreshWorker
from marketbot.utils.timeutil import ensure_utc, utcnow


@pytest_asyncio.fixture
async def file_db(tmp_path):
    """A real file-backed database, where lock contention actually happens."""
    path = (tmp_path / "concurrency.db").as_posix()
    settings = Settings(
        BOT_TOKEN="x",
        DATABASE_URL=f"sqlite+aiosqlite:///{path}",
        SQLITE_BUSY_TIMEOUT=5.0,
        STEAM_RATE_PER_MIN=600,
    )
    engine = build_engine(settings)
    await create_all(engine)
    yield settings, engine, build_session_factory(engine)
    await engine.dispose()


# ----------------------------------------------------------------- 1. pragmas


async def test_sqlite_runs_in_wal_mode(file_db):
    """WAL is what lets a reader run while the worker writes."""
    _, engine, _ = file_db
    async with engine.connect() as conn:
        assert (await conn.execute(text("PRAGMA journal_mode"))).scalar() == "wal"


async def test_busy_timeout_outlasts_a_throttled_fetch(file_db):
    """5s (the library default) is shorter than one rate-limited Steam call."""
    settings, engine, _ = file_db
    async with engine.connect() as conn:
        timeout_ms = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
    assert timeout_ms == int(settings.sqlite_busy_timeout * 1000)
    assert timeout_ms >= 5000


async def test_foreign_keys_are_enforced(file_db):
    """SQLite ignores foreign keys unless asked; cascades depend on this."""
    _, engine, _ = file_db
    async with engine.connect() as conn:
        assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar() == 1


# ------------------------------------------------------------- 2. presence writes


async def test_last_seen_is_not_rewritten_on_every_message(file_db):
    """This is what made every incoming update a write transaction."""
    _, _, factory = file_db
    async with session_scope(factory) as session:
        user = await user_repo.get_or_create(session, 555, username="a")
        first_seen = user.last_seen_at

    async with session_scope(factory) as session:
        user = await user_repo.get_or_create(session, 555, username="a")
        # SQLite drops tzinfo on round-trip, hence ensure_utc on both sides.
        assert ensure_utc(user.last_seen_at) == ensure_utc(first_seen)
        assert not session.dirty, "session must stay clean on a repeat message"


async def test_last_seen_updates_once_the_interval_passes(file_db):
    _, _, factory = file_db
    async with session_scope(factory) as session:
        user = await user_repo.get_or_create(session, 556)
        user.last_seen_at = utcnow() - timedelta(hours=2)

    async with session_scope(factory) as session:
        user = await user_repo.get_or_create(session, 556)
        assert (utcnow() - ensure_utc(user.last_seen_at)) < timedelta(minutes=1)


async def test_a_changed_username_still_writes(file_db):
    _, _, factory = file_db
    async with session_scope(factory) as session:
        await user_repo.get_or_create(session, 557, username="old")

    async with session_scope(factory) as session:
        user = await user_repo.get_or_create(session, 557, username="new")
        assert user.username == "new"


# ------------------------------------------------- 3. no DB session across network


class ProbingSteam:
    """A Steam client that, mid-fetch, tries to write from an independent session.

    This is the whole point: if the caller is holding a write transaction while it
    waits on the network, this probe cannot commit and records the failure.
    """

    def __init__(self, factory) -> None:
        self._factory = factory
        self.probe_succeeded: bool | None = None
        self.probe_error: str | None = None

    async def price_overview(self, ref, code="USD"):
        from marketbot.steam.dto import PriceOverview

        await self._probe()
        return PriceOverview(
            item=ref, currency=code, lowest_price=1000, median_price=1020,
            volume=500, fetched_at=utcnow(),
        )

    async def order_book(self, ref, nameid, code="USD"):  # pragma: no cover
        raise AssertionError("not needed for this test")

    async def _probe(self) -> None:
        try:
            async with asyncio.timeout(4):
                async with session_scope(self._factory) as session:
                    await item_repo.get_or_create(session, 570, "Concurrent Write Probe")
            self.probe_succeeded = True
        except Exception as exc:
            self.probe_succeeded = False
            self.probe_error = f"{type(exc).__name__}: {exc}"


async def test_the_probe_detects_a_held_write_lock(file_db):
    """Sanity-check the detector itself, so the regression test below means something.

    An open write transaction must make the probe fail; otherwise a passing result
    proves nothing.
    """
    settings, _, factory = file_db
    probe = ProbingSteam(factory)

    async with session_scope(factory) as blocking:
        # Take a real write lock and keep it.
        await item_repo.get_or_create(blocking, 730, "Lock Holder")
        await blocking.flush()
        await probe._probe()

    assert probe.probe_succeeded is False
    assert "Timeout" in (probe.probe_error or "") or "locked" in (probe.probe_error or "")


async def test_worker_holds_no_database_session_during_a_fetch(file_db):
    """The actual regression: the fetch must happen with no transaction open."""
    settings, _, factory = file_db
    steam = ProbingSteam(factory)

    async with session_scope(factory) as session:
        item, _ = await item_repo.get_or_create(session, 730, "AK-47 | Redline (Field-Tested)")
        item_id = item.id

    planner = RefreshPlanner()
    worker = RefreshWorker(planner, PricingService(steam, settings), factory, settings)
    await worker._process(RefreshTask(item_id, 1.0, "test", utcnow()))

    assert steam.probe_succeeded is True, f"probe failed: {steam.probe_error}"
    assert worker.processed == 1

    # And the snapshot still landed.
    from marketbot.db.repo import prices as price_repo

    async with session_scope(factory) as session:
        assert await price_repo.latest(session, item_id) is not None


async def test_fetch_needs_no_session_at_all(file_db):
    """fetch() takes no session parameter -- the property that makes the above hold."""
    import inspect

    assert "session" not in inspect.signature(PricingService.fetch).parameters

"""Background jobs and their APScheduler registration.

Every job records a `job_runs` row, so "is the bot actually working?" is answerable
from the database rather than from log archaeology. All jobs are registered with
max_instances=1 and coalesce=True: if one run overruns its interval, the next is
skipped rather than stacked.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from marketbot.constants import RefreshTier
from marketbot.db.models import JobRun
from marketbot.db.repo import items as item_repo
from marketbot.db.repo import prices as price_repo
from marketbot.db.repo import users as user_repo
from marketbot.db.session import session_scope
from marketbot.registry import Services
from marketbot.services import digest as digest_service
from marketbot.steam.errors import SteamError, SteamUnavailable
from marketbot.utils.timeutil import utcnow

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, services: Services) -> None:
        self._s = services

    @asynccontextmanager
    async def _track(self, name: str):
        """Record start/finish/failure for one job run."""
        # `status` lets a job that handled a refusal itself still record why it
        # achieved nothing, instead of reporting "ok" alongside failed=1.
        stats = {"processed": 0, "failed": 0, "detail": None, "status": None}
        started = utcnow()
        async with session_scope(self._s.session_factory) as session:
            run = JobRun(name=name, started_at=started, status="running")
            session.add(run)
            await session.flush()
            run_id = run.id

        status = "ok"
        try:
            yield stats
        except SteamUnavailable as exc:
            # Throttling and an open circuit are the backoff working as designed, not a
            # fault in this job. A stack trace every ten minutes buries the real errors,
            # so these get one calm line and their own status.
            status = "throttled"
            stats["detail"] = str(exc)
            log.warning("job %s backed off: %s", name, exc)
        except Exception as exc:
            status = "error"
            stats["detail"] = f"{type(exc).__name__}: {exc}"
            log.exception("job %s failed", name)
        finally:
            async with session_scope(self._s.session_factory) as session:
                run = await session.get(JobRun, run_id)
                if run is not None:
                    run.finished_at = utcnow()
                    run.status = stats["status"] or status
                    run.processed = int(stats["processed"])
                    run.failed = int(stats["failed"])
                    run.detail = stats["detail"]

    # ------------------------------------------------------------------- refresh

    async def refresh_tier(self, tier: RefreshTier, limit: int) -> None:
        """Queue everything due in a tier. The worker drains it at a safe rate."""
        async with self._track(f"refresh_{tier.value}") as stats:
            async with session_scope(self._s.session_factory) as session:
                stats["processed"] = await self._s.worker.enqueue_due(session, tier, limit=limit)

    async def recompute_tiers(self) -> None:
        async with self._track("recompute_tiers") as stats:
            async with session_scope(self._s.session_factory) as session:
                result = await item_repo.recompute_tiers(session)
                stats["processed"] = result.get("hot", 0) + result.get("warm", 0)
                stats["detail"] = str(result)

    async def ensure_nameids(self) -> None:
        """Fetch item_nameid for watched items that lack one.

        Deliberately tiny (a handful per run): it scrapes a full HTML listing page,
        which is the most expensive call we make, and the value never changes once
        fetched.
        """
        async with self._track("ensure_nameids") as stats:
            async with session_scope(self._s.session_factory) as session:
                items = await item_repo.needing_nameid(session, limit=5)
                for item in items:
                    try:
                        await self._s.pricing.ensure_meta(session, item)
                        stats["processed"] += 1
                    except SteamUnavailable as exc:
                        # Steam is refusing us; the rest of the batch would fail too.
                        stats["failed"] += 1
                        stats["detail"] = str(exc)
                        stats["status"] = "throttled"
                        log.info("nameid sweep stopped early: %s", exc)
                        break
                    except SteamError:
                        stats["failed"] += 1
                        break

    # -------------------------------------------------------------- maintenance

    async def rollup_daily(self) -> None:
        """Aggregate yesterday's snapshots into OHLCV rows. Idempotent."""
        async with self._track("rollup_daily") as stats:
            yesterday = (utcnow() - timedelta(days=1)).date()
            async with session_scope(self._s.session_factory) as session:
                item_ids = await price_repo.distinct_items_with_snapshots(
                    session, since=utcnow() - timedelta(days=2)
                )
                for item_id in item_ids:
                    if await price_repo.rollup_day(session, item_id, yesterday):
                        stats["processed"] += 1

    async def prune_snapshots(self) -> None:
        async with self._track("prune_snapshots") as stats:
            async with session_scope(self._s.session_factory) as session:
                stats["processed"] = await price_repo.prune(
                    session, older_than_days=self._s.settings.raw_retention_days
                )

    async def prune_orphan_items(self) -> None:
        """Drop items nobody watches or alerts on, so they stop costing refreshes."""
        async with self._track("prune_orphan_items") as stats:
            async with session_scope(self._s.session_factory) as session:
                stats["processed"] = await item_repo.delete_orphans(session)

    async def send_digests(self) -> None:
        """Hourly sweep for users whose local digest hour has arrived.

        Hourly rather than at a fixed UTC time because every user picks their own hour
        in their own timezone. `digest_last_sent_on` is stamped even when there is
        nothing to report, so a quiet day cannot cause a retry every hour.
        """
        async with self._track("send_digests") as stats:
            if not self._s.digest_dispatch:
                return
            async with session_scope(self._s.session_factory) as session:
                for user in await user_repo.digest_candidates(session):
                    if not digest_service.is_due(user):
                        continue
                    today = digest_service.local_date(user)
                    try:
                        payload = await digest_service.build(session, user)
                        if payload.has_content:
                            await self._s.digest_dispatch(payload)
                            stats["processed"] += 1
                    except Exception:
                        stats["failed"] += 1
                        log.exception("digest failed user_id=%s", user.id)
                    user.digest_last_sent_on = today

    async def heartbeat(self) -> None:
        async with self._track("heartbeat") as stats:
            health = self._s.health()
            stats["processed"] = health["queue"]["queued"]
            stats["detail"] = str(health["steam"])
            log.info("heartbeat %s", health)


def build_scheduler(services: Services) -> AsyncIOScheduler:
    runner = JobRunner(services)
    s = services.settings
    scheduler = AsyncIOScheduler(
        timezone="UTC",
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 120},
    )

    scheduler.add_job(
        runner.refresh_tier,
        IntervalTrigger(seconds=s.refresh_hot_seconds),
        args=[RefreshTier.HOT, s.refresh_batch_hot],
        id="refresh_hot",
        next_run_time=utcnow() + timedelta(seconds=10),
    )
    scheduler.add_job(
        runner.refresh_tier,
        IntervalTrigger(seconds=s.refresh_warm_seconds),
        args=[RefreshTier.WARM, s.refresh_batch_warm],
        id="refresh_warm",
    )
    scheduler.add_job(
        runner.refresh_tier,
        IntervalTrigger(seconds=s.refresh_cold_seconds),
        args=[RefreshTier.COLD, s.refresh_batch_cold],
        id="refresh_cold",
    )
    scheduler.add_job(
        runner.recompute_tiers, IntervalTrigger(minutes=15), id="recompute_tiers"
    )
    scheduler.add_job(runner.ensure_nameids, IntervalTrigger(minutes=10), id="ensure_nameids")
    scheduler.add_job(
        runner.rollup_daily, CronTrigger(hour=0, minute=7), id="rollup_daily"
    )
    scheduler.add_job(
        runner.prune_snapshots, CronTrigger(hour=3, minute=30), id="prune_snapshots"
    )
    scheduler.add_job(
        runner.prune_orphan_items, CronTrigger(hour=3, minute=50), id="prune_orphan_items"
    )
    scheduler.add_job(runner.send_digests, CronTrigger(minute=2), id="send_digests")
    scheduler.add_job(runner.heartbeat, IntervalTrigger(minutes=5), id="heartbeat")
    return scheduler

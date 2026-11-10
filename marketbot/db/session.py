"""Async engine / session factory."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from marketbot.config import Settings

# Importing the models module for its side effect registers every table on
# Base.metadata. Without it create_all() and Alembic autogenerate both see an
# empty schema and silently do nothing.
from marketbot.db import models as _models  # noqa: F401
from marketbot.db.base import Base


def build_engine(settings: Settings) -> AsyncEngine:
    kwargs: dict = {"echo": settings.db_echo, "future": True}
    if not settings.is_sqlite:
        # Pool tuning only makes sense for a real server-side database.
        kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=1800)
    else:
        # sqlite3's own lock wait, in seconds, applied before PRAGMA busy_timeout.
        kwargs["connect_args"] = {"timeout": settings.sqlite_busy_timeout}

    engine = create_async_engine(settings.database_url, **kwargs)
    if settings.is_sqlite:
        _apply_sqlite_pragmas(engine, settings)
    return engine


def _apply_sqlite_pragmas(engine: AsyncEngine, settings: Settings) -> None:
    """Make SQLite survive a bot with a background worker.

    Out of the box SQLite uses a rollback journal: one writer at a time, and that
    writer blocks readers too. This process has a refresh worker, a scheduler and the
    bot handlers all touching the database, so the default produces
    "database is locked" as soon as two of them overlap.

    * WAL lets readers run concurrently with a writer, which removes most of the
      contention outright.
    * busy_timeout makes a blocked writer *wait* rather than fail instantly.
    * synchronous=NORMAL is the recommended pairing with WAL: durable across process
      crashes, only at risk from an OS-level crash, and far less fsync traffic.
    """
    timeout_ms = int(settings.sqlite_busy_timeout * 1000)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection, _record) -> None:  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute(f"PRAGMA busy_timeout={timeout_ms}")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def create_all(engine: AsyncEngine) -> None:
    """Create tables directly. Convenient for tests and first run; Alembic owns prod."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Transaction boundary: commit on success, roll back on any exception."""
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()

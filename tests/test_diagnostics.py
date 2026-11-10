"""Pre-flight checks (`python -m marketbot --check`)."""
from __future__ import annotations

from marketbot.config import Settings
from marketbot.diagnostics import FAIL, OK, WARN, check_config, check_database


def by_name(results, prefix):
    return [r for r in results if r.name.startswith(prefix)]


async def test_missing_token_is_a_failure():
    results = await check_config(Settings(BOT_TOKEN="", DATABASE_URL="sqlite+aiosqlite:///:memory:"))
    token = by_name(results, "config: BOT_TOKEN")[0]
    assert token.status == FAIL
    assert ".env" in token.detail


async def test_present_token_passes(settings):
    assert by_name(await check_config(settings), "config: BOT_TOKEN")[0].status == OK


async def test_excessive_steam_rate_warns():
    results = await check_config(
        Settings(BOT_TOKEN="x", DATABASE_URL="sqlite+aiosqlite:///:memory:", STEAM_RATE_PER_MIN=60)
    )
    rate = by_name(results, "config: steam rate")[0]
    assert rate.status == WARN
    assert "ceiling" in rate.detail


async def test_webhook_without_url_fails():
    results = await check_config(
        Settings(
            BOT_TOKEN="x",
            DATABASE_URL="sqlite+aiosqlite:///:memory:",
            WEBHOOK_ENABLED=True,
            WEBHOOK_BASE_URL="",
        )
    )
    assert by_name(results, "config: webhook")[0].status == FAIL


async def test_webhook_without_secret_warns():
    results = await check_config(
        Settings(
            BOT_TOKEN="x",
            DATABASE_URL="sqlite+aiosqlite:///:memory:",
            WEBHOOK_ENABLED=True,
            WEBHOOK_BASE_URL="https://example.com",
            WEBHOOK_SECRET="",
        )
    )
    webhook = by_name(results, "config: webhook")[0]
    assert webhook.status == WARN
    assert "fake updates" in webhook.detail


async def test_sqlite_on_a_webhook_host_warns_about_data_loss():
    """The failure mode that silently eats someone's trade ledger."""
    results = await check_config(
        Settings(
            BOT_TOKEN="x",
            DATABASE_URL="sqlite+aiosqlite:///./x.db",
            WEBHOOK_ENABLED=True,
            WEBHOOK_BASE_URL="https://example.com",
            WEBHOOK_SECRET="s",
        )
    )
    storage = by_name(results, "config: storage")[0]
    assert storage.status == WARN
    assert "lost on redeploy" in storage.detail


async def test_database_check_reports_missing_schema(tmp_path):
    db = tmp_path / "empty.db"
    results = await check_database(
        Settings(BOT_TOKEN="x", DATABASE_URL=f"sqlite+aiosqlite:///{db.as_posix()}")
    )
    assert by_name(results, "database: connect")[0].status == OK
    schema = by_name(results, "database: schema")[0]
    assert schema.status == FAIL
    assert "alembic upgrade head" in schema.detail


async def test_database_check_passes_on_a_migrated_schema(settings, engine):
    results = await check_database(settings)
    assert by_name(results, "database: connect")[0].status == OK


async def test_missing_driver_is_reported_not_raised():
    """Pointing at PostgreSQL without installing asyncpg must report, not crash.

    This is the exact mistake DEPLOYMENT.md invites by telling people to switch
    DATABASE_URL; the check has to survive it.
    """
    results = await check_database(
        Settings(BOT_TOKEN="x", DATABASE_URL="postgresql+asyncpg://u:p@127.0.0.1:1/none")
    )
    assert len(results) == 1
    assert results[0].status == FAIL
    assert "asyncpg" in results[0].detail
    assert "requirements.txt" in results[0].detail


async def test_unreachable_database_fails():
    """A malformed URL fails cleanly rather than raising out of the check."""
    results = await check_database(
        Settings(BOT_TOKEN="x", DATABASE_URL="sqlite+aiosqlite:////nonexistent-dir/x.db")
    )
    assert results[0].status == FAIL


def test_webhook_url_is_assembled_without_double_slashes():
    s = Settings(
        BOT_TOKEN="x",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        WEBHOOK_BASE_URL="https://example.com/",
        WEBHOOK_PATH="webhook",
    )
    assert s.webhook_url == "https://example.com/webhook"


def test_webhook_url_is_empty_without_a_base():
    s = Settings(BOT_TOKEN="x", DATABASE_URL="sqlite+aiosqlite:///:memory:", WEBHOOK_BASE_URL="")
    assert s.webhook_url == ""

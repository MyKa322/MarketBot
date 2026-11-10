"""The guard that stops two bots polling one token."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from marketbot.single_instance import AlreadyRunningError, SingleInstance


def test_lock_is_acquired_and_records_the_pid(tmp_path):
    lock = SingleInstance(tmp_path / "bot.lock")
    with lock:
        assert lock.path.exists()
        assert lock.path.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_lock_is_released_on_exit(tmp_path):
    path = tmp_path / "bot.lock"
    with SingleInstance(path):
        pass
    # A second acquisition must succeed once the first has gone.
    with SingleInstance(path):
        pass


def test_release_is_idempotent(tmp_path):
    lock = SingleInstance(tmp_path / "bot.lock")
    lock.acquire()
    lock.release()
    lock.release()


def test_a_second_process_is_refused(tmp_path):
    """The real case: a service and a forgotten terminal, in separate processes.

    Same-process re-locking is not a valid test -- most OS locks are per-handle or
    per-process and would wrongly succeed, so this spawns a genuine second process.
    """
    path = tmp_path / "bot.lock"
    holder_script = textwrap.dedent(
        f"""
        import pathlib, sys, time
        sys.path.insert(0, {str(os.getcwd())!r})
        from marketbot.single_instance import SingleInstance
        with SingleInstance(pathlib.Path({str(path)!r})):
            print("locked", flush=True)
            time.sleep(30)
        """
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"

        with pytest.raises(AlreadyRunningError) as exc:
            SingleInstance(path).acquire()

        message = str(exc.value)
        assert "already running" in message
        # A PID is reported, and it is not ours. Not compared against holder.pid:
        # a Windows venv launcher can re-exec, so Popen's PID is not always the
        # Python process that took the lock.
        assert exc.value.pid and exc.value.pid.isdigit()
        assert exc.value.pid != str(os.getpid())
    finally:
        holder.kill()
        holder.wait(timeout=10)


def test_lock_is_free_again_after_the_holder_dies(tmp_path):
    """A crash must not leave a stale lock needing manual cleanup."""
    path = tmp_path / "bot.lock"
    holder_script = textwrap.dedent(
        f"""
        import sys, time, pathlib
        sys.path.insert(0, {str(os.getcwd())!r})
        from marketbot.single_instance import SingleInstance
        lock = SingleInstance(pathlib.Path({str(path)!r}))
        lock.acquire()
        print("locked", flush=True)
        time.sleep(30)
        """
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_script], stdout=subprocess.PIPE, text=True
    )
    assert holder.stdout.readline().strip() == "locked"
    holder.kill()
    holder.wait(timeout=10)

    # The OS drops the lock when the process dies, however it died.
    with SingleInstance(path):
        pass


# ------------------------------------------------------------- service logging


def test_log_file_is_created_and_written(tmp_path):
    """A background service has no console; the file is the only record."""
    import logging

    from marketbot.logging_setup import setup_logging

    log_path = tmp_path / "logs" / "marketbot.log"
    try:
        setup_logging("INFO", log_file=log_path, max_bytes=10_000, backups=2)
        logging.getLogger("marketbot.test").info("hello from the service")
        for handler in logging.getLogger().handlers:
            handler.flush()

        assert log_path.exists()
        assert "hello from the service" in log_path.read_text(encoding="utf-8")
    finally:
        for handler in list(logging.getLogger().handlers):
            handler.close()
        logging.getLogger().handlers.clear()


def test_log_file_rotates_instead_of_growing_forever(tmp_path):
    """A process that never stops would otherwise fill the disk it runs on."""
    import logging

    from marketbot.logging_setup import setup_logging

    log_path = tmp_path / "marketbot.log"
    try:
        setup_logging("INFO", log_file=log_path, max_bytes=2_000, backups=2)
        logger = logging.getLogger("marketbot.test")
        for i in range(400):
            logger.info("a reasonably long line to force rotation quickly %s", i)
        for handler in logging.getLogger().handlers:
            handler.flush()

        rotated = sorted(tmp_path.glob("marketbot.log*"))
        assert len(rotated) > 1, "expected rotation"
        assert len(rotated) <= 3, "backupCount must cap the number of files"
    finally:
        for handler in list(logging.getLogger().handlers):
            handler.close()
        logging.getLogger().handlers.clear()


def test_logging_without_a_file_still_works(tmp_path):
    """The default path: a terminal or a container, where stdout is captured."""
    import logging

    from marketbot.logging_setup import setup_logging

    try:
        setup_logging("INFO")
        assert logging.getLogger().handlers
    finally:
        logging.getLogger().handlers.clear()

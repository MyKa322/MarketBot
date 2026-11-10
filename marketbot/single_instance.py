"""A process-wide lock so only one copy of the bot can run at a time.

Two instances polling the same token make Telegram reject both with
`Conflict: terminated by other getUpdates request`, and they double every Steam
request against a per-IP budget that is already the binding constraint. A 24/7 setup
makes this *more* likely, not less: an auto-restarting service plus a terminal someone
forgot about is the normal way it happens.

The lock is an OS-level exclusive file lock rather than a PID file. A PID file has to
guess whether a recorded PID is still the same process, and it survives a crash as a
stale lock that needs manual cleanup. An OS lock is released by the kernel the instant
the holder dies, however it dies.
"""
from __future__ import annotations

import contextlib
import logging
import os
import sys
from pathlib import Path
from types import TracebackType

log = logging.getLogger(__name__)

# The PID is written at offset 0; the lock is taken on a byte well past it. Windows
# file locks are *mandatory*, so a byte that is both locked and read would stop a
# losing process from reading the PID it needs to report. Locking beyond end-of-file
# is legal on both platforms.
LOCK_OFFSET = 1024

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> None:
        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, LOCK_OFFSET, os.SEEK_SET)

    def _unlock(fd: int) -> None:
        fcntl.lockf(fd, fcntl.LOCK_UN, 1, LOCK_OFFSET, os.SEEK_SET)


class AlreadyRunningError(RuntimeError):
    """Another instance holds the lock."""

    def __init__(self, path: Path, pid: str | None) -> None:
        who = f" (PID {pid})" if pid else ""
        super().__init__(
            f"another MarketBot instance is already running{who}. "
            f"Stop it first, or delete {path} if you are sure it is gone."
        )
        self.path = path
        self.pid = pid


class SingleInstance:
    """Context manager holding an exclusive lock for the process lifetime."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        """Take the lock, or raise AlreadyRunningError naming the current holder.

        Raw file descriptors throughout: Python's buffered text handles keep their own
        position, which does not reliably match the descriptor position the OS lock
        applies to.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            _try_lock(fd)
        except OSError:
            holder = self._read_pid(fd)
            os.close(fd)
            raise AlreadyRunningError(self.path, holder) from None

        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd
        log.debug("instance lock acquired: %s", self.path)

    @staticmethod
    def _read_pid(fd: int) -> str | None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            raw = os.read(fd, 32).decode("utf-8", "replace").strip()
        except OSError:
            return None
        return raw or None

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            with contextlib.suppress(OSError):
                _unlock(fd)
        finally:
            os.close(fd)
            # Best effort: the lock is what matters; a leftover file is harmless.
            with contextlib.suppress(OSError):
                self.path.unlink()

    def __enter__(self) -> SingleInstance:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

"""Logging configuration.

Forces UTF-8 on the stream handler: the default Windows console codepage (cp1251 on
this machine) raises UnicodeEncodeError the first time a log line contains an item
name with a non-Latin character, which would otherwise kill the handler mid-refresh.
"""
from __future__ import annotations

import contextlib
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(name)-28s %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    level: str = "INFO",
    *,
    log_file: Path | None = None,
    max_bytes: int = 5_000_000,
    backups: int = 5,
) -> None:
    """Configure logging, optionally also to a rotating file.

    A 24/7 service has no console: whatever it prints is discarded, so the only record
    of what happened is the file. Rotation is not optional either -- an unrotated log
    from a process that never stops eventually fills the disk it runs on.
    """
    stream = sys.stderr
    with_reconfigure = getattr(stream, "reconfigure", None)
    if callable(with_reconfigure):
        with contextlib.suppress(ValueError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")

    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(FORMAT, datefmt=DATEFMT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(FORMAT, datefmt=DATEFMT))
        root.addHandler(file_handler)

    # These are chatty and rarely what you are debugging.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

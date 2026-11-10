"""python -m marketbot [--check]

Imports of the application live inside main() rather than at module scope. That keeps
--help working, and lets a missing dependency produce an explanation instead of a
traceback -- which matters most for --check, the command people reach for precisely
when something is already wrong.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

USAGE = """MarketBot

  python -m marketbot           start the bot
  python -m marketbot --check   run pre-flight checks and exit
  python -m marketbot --help    this message
"""


def _venv_python() -> Path | None:
    """The project's virtualenv interpreter, if one exists."""
    candidates = (
        ROOT / ".venv" / "Scripts" / "python.exe",   # Windows
        ROOT / ".venv" / "bin" / "python",           # Linux / macOS
    )
    return next((p for p in candidates if p.exists()), None)


def _dependency_help(exc: ModuleNotFoundError) -> str:
    """Explain a missing dependency in terms of what to actually type."""
    missing = exc.name or "a dependency"
    venv = _venv_python()
    running_this_venv = venv is not None and Path(sys.executable).resolve() == venv.resolve()

    lines = [
        f"Missing dependency: {missing}",
        "",
    ]

    if venv is not None and not running_this_venv:
        # By far the most common case: a fresh shell where the venv is not active.
        windows = sys.platform == "win32"
        activate = r".venv\Scripts\Activate.ps1" if windows else "source .venv/bin/activate"
        lines += [
            "This project has a virtualenv, but you are running a different Python:",
            f"    in use : {sys.executable}",
            f"    project: {venv}",
            "",
            "Use the project interpreter directly:",
            f"    {venv} -m marketbot {' '.join(sys.argv[1:])}".rstrip(),
            "",
            "or activate the environment first, then repeat your command:",
            f"    {activate}",
        ]
        if windows:
            lines += [
                "",
                "If PowerShell blocks that script, allow it for this session with:",
                "    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass",
            ]
    elif venv is None:
        lines += [
            "No virtualenv found. Create one and install the dependencies:",
            "    python -m venv .venv",
            (
                r"    .venv\Scripts\python.exe -m pip install -r requirements.txt"
                if sys.platform == "win32"
                else "    .venv/bin/python -m pip install -r requirements.txt"
            ),
        ]
    else:
        lines += [
            "The virtualenv is active but the dependencies are not installed:",
            "    python -m pip install -r requirements.txt",
        ]

    return "\n".join(lines)


def main() -> None:
    args = sys.argv[1:]

    if any(a in {"-h", "--help", "help"} for a in args):
        print(USAGE)
        return

    try:
        from marketbot.container import run
        from marketbot.logging_setup import setup_logging
    except ModuleNotFoundError as exc:
        print(_dependency_help(exc), file=sys.stderr)
        raise SystemExit(2) from None

    # On Windows the default Proactor loop closes noisily under aiohttp/aiogram on
    # Ctrl+C. loop_factory replaces the deprecated set_event_loop_policy(), which is
    # slated for removal in Python 3.16.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None

    if any(a in {"--check", "-c", "check"} for a in args):
        from marketbot.diagnostics import run_checks

        # The report is the output. Client warnings would only duplicate, and
        # visually precede, the findings it already states plainly.
        setup_logging("ERROR")
        raise SystemExit(asyncio.run(run_checks(), loop_factory=loop_factory))

    try:
        asyncio.run(run(), loop_factory=loop_factory)
    except KeyboardInterrupt:
        print()
        print("Stopped.")
    # SystemExit propagates untouched: Python already prints its message to stderr
    # and exits non-zero, so catching it here would only duplicate the output.


if __name__ == "__main__":
    main()

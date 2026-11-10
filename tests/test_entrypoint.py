"""Entry-point ergonomics: --help without dependencies, and useful import errors."""
from __future__ import annotations

import sys
from pathlib import Path

from marketbot.__main__ import USAGE, _dependency_help, _venv_python


def test_usage_lists_every_mode():
    for flag in ("--check", "--help"):
        assert flag in USAGE


def test_venv_python_is_found_in_this_project():
    venv = _venv_python()
    assert venv is not None
    assert venv.exists()


def test_wrong_interpreter_names_both_pythons(monkeypatch):
    """The common case: a fresh shell with the venv not activated.

    The message has to say which Python ran and which one should have, or the reader
    is left guessing why an installed package is 'missing'.
    """
    monkeypatch.setattr(sys, "executable", r"C:\Python314\python.exe")
    monkeypatch.setattr(sys, "argv", ["marketbot", "--check"])

    message = _dependency_help(ModuleNotFoundError(name="aiogram"))

    assert "aiogram" in message
    assert r"C:\Python314\python.exe" in message      # what they ran
    assert ".venv" in message                          # what they should run
    assert "--check" in message                        # their own arguments, repeated back


def test_message_suggests_creating_a_venv_when_there_is_none(monkeypatch):
    monkeypatch.setattr("marketbot.__main__._venv_python", lambda: None)
    message = _dependency_help(ModuleNotFoundError(name="aiogram"))
    assert "python -m venv .venv" in message
    assert "requirements.txt" in message


def test_message_when_venv_is_active_but_empty(monkeypatch):
    fake = Path(sys.executable)
    monkeypatch.setattr("marketbot.__main__._venv_python", lambda: fake)
    message = _dependency_help(ModuleNotFoundError(name="aiogram"))
    assert "pip install -r requirements.txt" in message
    # No point telling someone to switch interpreter when they are already on it.
    assert "activate the environment" not in message


def test_handles_a_nameless_import_error(monkeypatch):
    monkeypatch.setattr(sys, "executable", r"C:\Python314\python.exe")
    message = _dependency_help(ModuleNotFoundError("no name attribute"))
    assert "a dependency" in message

#!/usr/bin/env bash
# Runs MarketBot with the project's virtualenv, whatever shell you are in.
# Picking the wrong Python is the most common way to hit ModuleNotFoundError,
# so this never relies on the venv being activated.
set -euo pipefail
cd "$(dirname "$0")"

VENV_PYTHON=".venv/bin/python"
[ -x "$VENV_PYTHON" ] || VENV_PYTHON=".venv/Scripts/python.exe"

if [ ! -x "$VENV_PYTHON" ]; then
    echo "No virtualenv found. Creating .venv ..."
    python3 -m venv .venv 2>/dev/null || python -m venv .venv
    VENV_PYTHON=".venv/bin/python"
    [ -x "$VENV_PYTHON" ] || VENV_PYTHON=".venv/Scripts/python.exe"
    echo "Installing dependencies ..."
    "$VENV_PYTHON" -m pip install --upgrade pip --quiet
    "$VENV_PYTHON" -m pip install -r requirements.txt
fi

if [ ! -f .env ]; then
    echo "No .env found. Copying .env.example ..."
    cp .env.example .env
    echo "Add your BOT_TOKEN to .env, then run this again."
    exit 1
fi

exec "$VENV_PYTHON" -m marketbot "$@"

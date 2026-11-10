#!/usr/bin/env sh
# Container entrypoint.
#
# Runs migrations before the bot starts. This matters more than it looks: the app
# calls create_all() on boot as a convenience, which builds the tables but leaves
# alembic_version empty. A later `alembic upgrade head` would then fail trying to
# create tables that already exist. Running Alembic first means the schema is always
# migration-managed, on a fresh volume and on an upgrade alike.
set -eu

echo "[entrypoint] applying migrations..."
alembic upgrade head

echo "[entrypoint] starting bot"
exec python -m marketbot

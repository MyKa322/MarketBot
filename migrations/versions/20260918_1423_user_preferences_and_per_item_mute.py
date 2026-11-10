"""user preferences and per-item mute

Revision ID: e4b60cc9b433
Revises: 8890449ff1ee
Create Date: 2026-09-18 14:23:07.158360+00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e4b60cc9b433'
down_revision: str | None = '8890449ff1ee'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default is required on every NOT NULL column here. SQLAlchemy's
    # `default=` is applied in Python at INSERT time, so it does nothing for rows
    # that already exist -- without these, the migration fails on any database with
    # users in it, which is every database except a fresh one.
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("silent_alerts", sa.Boolean(), nullable=False,
                      server_default=sa.false())
        )
        batch_op.add_column(sa.Column("alert_cap_per_hour", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("digest_enabled", sa.Boolean(), nullable=False,
                      server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("digest_hour", sa.Integer(), nullable=False,
                      server_default=sa.text("9"))
        )
        batch_op.add_column(sa.Column("digest_last_sent_on", sa.Date(), nullable=True))
        batch_op.add_column(
            sa.Column("default_chart_span", sa.String(length=8), nullable=False,
                      server_default="7d")
        )
        batch_op.add_column(
            sa.Column("watchlist_sort", sa.String(length=16), nullable=False,
                      server_default="name")
        )

    with op.batch_alter_table("watchlist_entries", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("muted", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table('watchlist_entries', schema=None) as batch_op:
        batch_op.drop_column('muted')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('watchlist_sort')
        batch_op.drop_column('default_chart_span')
        batch_op.drop_column('digest_last_sent_on')
        batch_op.drop_column('digest_hour')
        batch_op.drop_column('digest_enabled')
        batch_op.drop_column('alert_cap_per_hour')
        batch_op.drop_column('silent_alerts')

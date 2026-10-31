"""segments: how long STT took and how long the line waited, for the per-session timing summary

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("segments", sa.Column("stt_ms", sa.Integer()))
    op.add_column("segments", sa.Column("latency_ms", sa.Integer()))


def downgrade() -> None:
    op.drop_column("segments", "latency_ms")
    op.drop_column("segments", "stt_ms")

"""sessions.transcription_pending: audio no STT tier could take, waiting to be transcribed

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("transcription_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_sessions_transcription_pending",
        "sessions",
        ["started_at"],
        postgresql_where=sa.text("transcription_pending"),
    )


def downgrade() -> None:
    op.drop_index("ix_sessions_transcription_pending", table_name="sessions")
    op.drop_column("sessions", "transcription_pending")

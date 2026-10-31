"""speakers: diarized turns, the speakers of a session, and who spoke each segment

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_speakers",
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("speaker_id", sa.String(32), nullable=False),
        sa.Column("channel", sa.String(8), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text()),
        sa.Column("is_owner", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.PrimaryKeyConstraint("session_id", "speaker_id"),
    )
    op.create_table(
        "speaker_turns",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("channel", sa.String(8), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("speaker_id", sa.String(32), nullable=False),
    )
    op.create_index("ix_speaker_turns_session_start", "speaker_turns", ["session_id", "start_ms"])
    op.add_column("segments", sa.Column("speaker_id", sa.String(32)))
    op.add_column(
        "segments", sa.Column("speaker_rev", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "segments",
        sa.Column("mixed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("segments", "mixed")
    op.drop_column("segments", "speaker_rev")
    op.drop_column("segments", "speaker_id")
    op.drop_index("ix_speaker_turns_session_start", table_name="speaker_turns")
    op.drop_table("speaker_turns")
    op.drop_table("session_speakers")

"""initial schema

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "device_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("devices.id"), nullable=False
        ),
        sa.Column("title", sa.Text()),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="live"),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("local_only", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "channels",
            postgresql.ARRAY(sa.String(8)),
            nullable=False,
            server_default=sa.text("'{mic}'"),
        ),
        sa.CheckConstraint(
            "status IN ('live','finalizing','done','failed')", name="ck_sessions_status"
        ),
        sa.CheckConstraint("mode IN ('meeting','solo')", name="ck_sessions_mode"),
    )
    op.create_index(
        "ix_sessions_device_started", "sessions", ["device_id", sa.text("started_at DESC")]
    )
    op.create_table(
        "audio_chunks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(8), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("t_ms", sa.BigInteger(), nullable=False),
        sa.Column("byte_offset", sa.BigInteger(), nullable=False),
        sa.Column("byte_len", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "session_id", "channel", "seq", name="uq_audio_chunks_session_channel_seq"
        ),
        sa.CheckConstraint("channel IN ('mic','system')", name="ck_audio_chunks_channel"),
    )
    op.create_table(
        "segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(8), nullable=False),
        sa.Column("speaker", sa.String(8), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("stt_tier", sa.String(100)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("channel IN ('mic','system')", name="ck_segments_channel"),
        sa.CheckConstraint("speaker IN ('me','them')", name="ck_segments_speaker"),
        sa.CheckConstraint("source IN ('draft','final')", name="ck_segments_source"),
    )
    op.create_index("ix_segments_session_start", "segments", ["session_id", "start_ms"])
    op.create_table(
        "markers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column("t_ms", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
    )
    op.create_index("ix_markers_session_t", "markers", ["session_id", "t_ms"])
    op.create_table(
        "copilot_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_copilot_items_session_created", "copilot_items", ["session_id", "created_at"]
    )


def downgrade() -> None:
    for table in ("copilot_items", "markers", "segments", "audio_chunks", "sessions", "devices"):
        op.drop_table(table)

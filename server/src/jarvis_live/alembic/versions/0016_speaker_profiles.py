"""speaker_profiles: enrolled voices, and the voiceprint of each speaker of a session

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "speaker_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("is_owner", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # Voiceprints are only comparable within one embedding model.
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("embedding", ARRAY(sa.Float()), nullable=False),
        sa.Column("n_samples", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "label", name="uq_speaker_profiles_user_label"),
    )
    op.create_index(
        "uq_speaker_profiles_one_owner",
        "speaker_profiles",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_owner"),
    )
    op.add_column("session_speakers", sa.Column("embedding", ARRAY(sa.Float())))
    op.add_column("session_speakers", sa.Column("model_id", sa.Text()))
    op.add_column(
        "session_speakers",
        sa.Column(
            "profile_id",
            UUID(as_uuid=True),
            sa.ForeignKey("speaker_profiles.id", ondelete="SET NULL"),
        ),
    )


def downgrade() -> None:
    op.drop_column("session_speakers", "profile_id")
    op.drop_column("session_speakers", "model_id")
    op.drop_column("session_speakers", "embedding")
    op.drop_index("uq_speaker_profiles_one_owner", table_name="speaker_profiles")
    op.drop_table("speaker_profiles")

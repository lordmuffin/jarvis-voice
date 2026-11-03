"""ingest_keys: publish-only keys for streams pushed in over SRT or RTMP (OBS, phones)

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingest_keys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(40), nullable=False, unique=True),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("restricted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("mode IN ('meeting','solo')", name="ck_ingest_keys_mode"),
        sa.CheckConstraint("source IN ('obs','mobile')", name="ck_ingest_keys_source"),
    )
    op.create_index("ix_ingest_keys_user_id", "ingest_keys", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_ingest_keys_user_id", table_name="ingest_keys")
    op.drop_table("ingest_keys")

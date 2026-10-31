"""sessions.source and sessions.client_id: tag uploads (watch) and make upload retries idempotent

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("source", sa.String(16), nullable=True))
    op.add_column("sessions", sa.Column("client_id", sa.String(64), nullable=True))
    op.create_index(
        "uq_sessions_user_client_id",
        "sessions",
        ["user_id", "client_id"],
        unique=True,
        postgresql_where=sa.text("client_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_sessions_user_client_id", table_name="sessions")
    op.drop_column("sessions", "client_id")
    op.drop_column("sessions", "source")

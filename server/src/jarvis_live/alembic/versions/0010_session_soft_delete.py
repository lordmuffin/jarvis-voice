"""sessions.deleted_at and sessions.purge_after: reversible deletes, per-session retention

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
    op.add_column("sessions", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sessions", sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_sessions_user_deleted",
        "sessions",
        ["user_id", sa.text("deleted_at DESC")],
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )
    op.create_index(
        "ix_sessions_purge_after",
        "sessions",
        ["purge_after"],
        postgresql_where=sa.text("purge_after IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_sessions_purge_after", table_name="sessions")
    op.drop_index("ix_sessions_user_deleted", table_name="sessions")
    op.drop_column("sessions", "purge_after")
    op.drop_column("sessions", "deleted_at")

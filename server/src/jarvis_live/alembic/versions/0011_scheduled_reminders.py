"""scheduled_reminders: Gotify pushes that approved reminder actions will send

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
STATES = ("pending", "sending", "sent", "failed")


def upgrade() -> None:
    op.create_table(
        "scheduled_reminders",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "proposal_id",
            UUID,
            sa.ForeignKey("action_proposals.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("fire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("body", sa.Text),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempted_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state IN (" + ",".join(f"'{s}'" for s in STATES) + ")",
            name="ck_scheduled_reminders_state",
        ),
    )
    op.create_index(
        "ix_scheduled_reminders_state_fire", "scheduled_reminders", ["state", "fire_at"]
    )


def downgrade() -> None:
    op.drop_table("scheduled_reminders")

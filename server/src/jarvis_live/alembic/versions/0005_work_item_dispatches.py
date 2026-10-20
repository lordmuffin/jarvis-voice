"""work_item_dispatches: runs of approved work items as headless Claude Code

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
STATES = (
    "queued",
    "running",
    "pr_opened",
    "no_changes",
    "failed",
    "rate_limited",
    "timed_out",
    "cancelled",
)


def upgrade() -> None:
    op.create_table(
        "work_item_dispatches",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "work_item_id",
            UUID,
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("repo", sa.Text(), nullable=False),
        sa.Column("branch", sa.Text(), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
        sa.Column("requested_by", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("job_name", sa.Text()),
        sa.Column("pr_url", sa.Text()),
        sa.Column("pr_number", sa.Integer()),
        sa.Column("summary", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("token_refreshed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN (" + ",".join(f"'{s}'" for s in STATES) + ")",
            name="ck_work_item_dispatches_state",
        ),
        sa.UniqueConstraint("work_item_id", "attempt", name="uq_work_item_dispatches_attempt"),
    )
    op.create_index(
        "ix_work_item_dispatches_work_item_id", "work_item_dispatches", ["work_item_id"]
    )
    op.create_index("ix_work_item_dispatches_state", "work_item_dispatches", ["state"])


def downgrade() -> None:
    op.drop_index("ix_work_item_dispatches_state", "work_item_dispatches")
    op.drop_index("ix_work_item_dispatches_work_item_id", "work_item_dispatches")
    op.drop_table("work_item_dispatches")

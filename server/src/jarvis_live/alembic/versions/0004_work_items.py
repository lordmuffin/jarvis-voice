"""work_items and work_item_events: copilot ideas as draft Claude Code prompts

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
STATES = ("draft", "approved", "discarded", "merged")


def _now(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "work_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("ref", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("repo", sa.Text()),
        sa.Column("intent", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "acceptance_criteria", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("done_when", sa.Text()),
        sa.Column("sources", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "locked_fields",
            postgresql.ARRAY(sa.String(32)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("merged_into", UUID, sa.ForeignKey("work_items.id")),
        sa.Column("prompt_md", sa.Text()),
        sa.Column("prompt_sha256", sa.String(64)),
        sa.Column("approved_by", UUID, sa.ForeignKey("users.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint(
            "state IN (" + ",".join(f"'{s}'" for s in STATES) + ")", name="ck_work_items_state"
        ),
        sa.UniqueConstraint("session_id", "ref", name="uq_work_items_session_ref"),
    )
    op.create_index("ix_work_items_session_id", "work_items", ["session_id"])
    op.create_index("ix_work_items_user_state", "work_items", ["user_id", "state"])
    op.create_table(
        "work_item_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "work_item_id",
            UUID,
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _now("at"),
        sa.Column("actor_user_id", UUID, sa.ForeignKey("users.id")),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_work_item_events_item_at", "work_item_events", ["work_item_id", "at"])


def downgrade() -> None:
    op.drop_index("ix_work_item_events_item_at", "work_item_events")
    op.drop_table("work_item_events")
    op.drop_index("ix_work_items_user_state", "work_items")
    op.drop_index("ix_work_items_session_id", "work_items")
    op.drop_table("work_items")

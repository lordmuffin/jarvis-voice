"""action_proposals and action_proposal_events: what the copilot proposes to do, and its audit log

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
STATES = ("proposed", "approved", "executing", "succeeded", "failed", "rejected", "expired")


def _now(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "action_proposals",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id", UUID, sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("ref", sa.String(16), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        sa.Column("evidence", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("state", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("proposed_by", sa.String(64), nullable=False),
        sa.Column("approved_by", UUID, sa.ForeignKey("users.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("result", JSONB),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint(
            "state IN (" + ",".join(f"'{s}'" for s in STATES) + ")",
            name="ck_action_proposals_state",
        ),
        sa.UniqueConstraint("session_id", "ref", name="uq_action_proposals_session_ref"),
        sa.UniqueConstraint("session_id", "dedupe_key", name="uq_action_proposals_session_dedupe"),
    )
    op.create_index("ix_action_proposals_user_state", "action_proposals", ["user_id", "state"])
    op.create_table(
        "action_proposal_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "proposal_id",
            UUID,
            sa.ForeignKey("action_proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _now("at"),
        sa.Column("actor_user_id", UUID, sa.ForeignKey("users.id")),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index(
        "ix_action_proposal_events_item_at", "action_proposal_events", ["proposal_id", "at"]
    )


def downgrade() -> None:
    op.drop_table("action_proposal_events")
    op.drop_table("action_proposals")

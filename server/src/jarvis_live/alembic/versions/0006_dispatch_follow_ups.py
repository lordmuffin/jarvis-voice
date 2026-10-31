"""work_item_dispatches: runs that ask the owner (needs_input) and follow-up runs

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
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
CHECK = "ck_work_item_dispatches_state"


def _check(states: tuple[str, ...]) -> None:
    op.drop_constraint(CHECK, "work_item_dispatches", type_="check")
    op.create_check_constraint(
        CHECK, "work_item_dispatches", "state IN (" + ",".join(f"'{s}'" for s in states) + ")"
    )


def upgrade() -> None:
    _check((*STATES, "needs_input"))
    op.add_column("work_item_dispatches", sa.Column("questions", sa.Text()))
    op.add_column("work_item_dispatches", sa.Column("guidance", sa.Text()))
    op.add_column(
        "work_item_dispatches",
        sa.Column(
            "continues",
            UUID,
            sa.ForeignKey(
                "work_item_dispatches.id",
                ondelete="SET NULL",
                name="fk_work_item_dispatches_continues",
            ),
        ),
    )


def downgrade() -> None:
    op.execute(
        "UPDATE work_item_dispatches SET state = CASE WHEN pr_url IS NULL"
        " THEN 'no_changes' ELSE 'pr_opened' END WHERE state = 'needs_input'"
    )
    op.drop_constraint(
        "fk_work_item_dispatches_continues", "work_item_dispatches", type_="foreignkey"
    )
    op.drop_column("work_item_dispatches", "continues")
    op.drop_column("work_item_dispatches", "guidance")
    op.drop_column("work_item_dispatches", "questions")
    _check(STATES)

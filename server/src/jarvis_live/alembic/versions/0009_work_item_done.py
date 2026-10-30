"""work_items: the done state, when it was reached and the PR that closed it

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

STATES = ("draft", "approved", "discarded", "merged")
CHECK = "ck_work_items_state"


def _check(states: tuple[str, ...]) -> None:
    op.drop_constraint(CHECK, "work_items", type_="check")
    op.create_check_constraint(
        CHECK, "work_items", "state IN (" + ",".join(f"'{s}'" for s in states) + ")"
    )


def upgrade() -> None:
    _check((*STATES, "done"))
    op.add_column("work_items", sa.Column("done_at", sa.DateTime(timezone=True)))
    op.add_column("work_items", sa.Column("merged_pr_url", sa.Text()))


def downgrade() -> None:
    op.execute("UPDATE work_items SET state = 'approved' WHERE state = 'done'")
    op.drop_column("work_items", "merged_pr_url")
    op.drop_column("work_items", "done_at")
    _check(STATES)

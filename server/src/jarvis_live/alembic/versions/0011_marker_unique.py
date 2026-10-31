"""markers: one per (session, time, label), so a client can safely resend them

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM markers a USING markers b WHERE a.id > b.id "
        "AND a.session_id = b.session_id AND a.t_ms = b.t_ms AND a.label = b.label"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_markers_session_t_label ON markers (session_id, t_ms, md5(label))"
    )


def downgrade() -> None:
    op.drop_index("uq_markers_session_t_label", table_name="markers")

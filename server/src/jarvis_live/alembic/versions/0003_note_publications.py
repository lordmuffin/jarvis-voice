"""note_publications: publishing finished notes to the vault repo; users.auto_publish

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
STATES = ("publishing", "open", "merged", "closed", "skipped", "failed")


def _now(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("auto_publish", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_table(
        "note_publications",
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("repo_path", sa.Text()),
        sa.Column("branch", sa.Text()),
        sa.Column("pr_number", sa.Integer()),
        sa.Column("pr_url", sa.Text()),
        sa.Column("head_sha", sa.String(64)),
        sa.Column("content_sha256", sa.String(64)),
        sa.Column("last_error", sa.Text()),
        sa.Column("published_by", UUID, sa.ForeignKey("users.id")),
        _now("created_at"),
        _now("updated_at"),
        sa.Column("merged_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN (" + ",".join(f"'{s}'" for s in STATES) + ")",
            name="ck_note_publications_state",
        ),
    )
    op.create_index("ix_note_publications_state", "note_publications", ["state"])


def downgrade() -> None:
    op.drop_index("ix_note_publications_state", "note_publications")
    op.drop_table("note_publications")
    op.drop_column("users", "auto_publish")

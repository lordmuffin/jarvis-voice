"""full-text search over final transcript segments

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

# The expression and predicate must match jarvis_live.api.session_list for the planner to use it.
_INDEX = (
    "CREATE INDEX ix_segments_text_fts ON segments "
    "USING gin (to_tsvector('simple'::regconfig, text)) WHERE source = 'final'"
)


def upgrade() -> None:
    op.execute(_INDEX)


def downgrade() -> None:
    op.execute("DROP INDEX ix_segments_text_fts")

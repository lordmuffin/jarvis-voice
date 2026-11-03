"""ingest_keys: a name is unique among active keys only, so a revoked name can be reused (rotate)

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ingest_keys_name_key", "ingest_keys", type_="unique")
    op.create_index(
        "uq_ingest_keys_active_name",
        "ingest_keys",
        ["name"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    # Fails if a name has revoked keys beside its active one: delete the revoked rows first.
    op.drop_index("uq_ingest_keys_active_name", table_name="ingest_keys")
    op.create_unique_constraint("ingest_keys_name_key", "ingest_keys", ["name"])

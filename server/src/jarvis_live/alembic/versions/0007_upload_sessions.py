"""sessions.device_id nullable: recordings uploaded from the web dashboard have no device

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("sessions", "device_id", nullable=True)


def downgrade() -> None:
    # Fails while uploaded sessions exist: they are not deleted to make the downgrade pass.
    op.alter_column("sessions", "device_id", nullable=False)

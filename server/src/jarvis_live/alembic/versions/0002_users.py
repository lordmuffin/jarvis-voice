"""users: per-user ownership of devices and sessions, Pocket-ID sign-in

Every existing device and session goes to one "owner" user, who claims it at their first
Pocket-ID sign-in (``JARVIS_LIVE_OWNER_EMAIL``) or with ``jarvis-live claim-owner``.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

OWNER_USER_ID = "00000000-0000-0000-0000-000000000001"
UUID = postgresql.UUID(as_uuid=True)


def _now(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("oidc_sub", sa.Text(), unique=True),
        sa.Column("email", sa.Text()),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("notes_subdir", sa.Text(), unique=True),
        _now("created_at"),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        f"INSERT INTO users (id, display_name, is_admin) VALUES ('{OWNER_USER_ID}', 'Owner', true)"
    )

    for table in ("devices", "sessions"):
        op.add_column(table, sa.Column("user_id", UUID, sa.ForeignKey("users.id")))
        op.execute(f"UPDATE {table} SET user_id = '{OWNER_USER_ID}'")
        op.alter_column(table, "user_id", nullable=False)
    op.create_index("ix_devices_user_id", "devices", ["user_id"])
    op.create_index("ix_sessions_user_started", "sessions", ["user_id", sa.text("started_at DESC")])

    op.create_table(
        "login_sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        _now("created_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_login_sessions_user_id", "login_sessions", ["user_id"])
    op.create_table(
        "oidc_flows",
        sa.Column("state", sa.String(64), primary_key=True),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("pkce_verifier", sa.String(128), nullable=False),
        sa.Column("client", sa.String(16), nullable=False),
        sa.Column("app_challenge", sa.String(128)),
        sa.Column("return_to", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "device_codes",
        sa.Column("code_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("app_challenge", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    for table in ("device_codes", "oidc_flows", "login_sessions"):
        op.drop_table(table)
    op.drop_index("ix_sessions_user_started", "sessions")
    op.drop_index("ix_devices_user_id", "devices")
    for table in ("sessions", "devices"):
        op.drop_column(table, "user_id")
    op.drop_table("users")

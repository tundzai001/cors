"""Add revocable integration API keys."""

from alembic import op
import sqlalchemy as sa

revision = "20261008_api_keys"
down_revision = "20260929_rover_auto_save_sessions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("revoked_at", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("all_stations", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("station_serials", sa.JSON(), nullable=False),
        sa.Column("data_fields", sa.JSON(), nullable=False),
        sa.Column("edit_config", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)


def downgrade():
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_table("api_keys")

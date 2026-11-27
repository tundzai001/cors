"""Store intended group policy in one-time Zalo link codes.

Revision ID: 20260705_zalo_link_intent
Revises: 20260705_zalo_scope
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260705_zalo_link_intent"
down_revision: str | Sequence[str] | None = "20260705_zalo_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent because application startup also repairs these columns. This
    # lets Alembic safely stamp/upgrade a server that received code first.
    op.execute(
        "ALTER TABLE zalo_link_codes ADD COLUMN IF NOT EXISTS "
        "link_type VARCHAR NOT NULL DEFAULT 'private'"
    )
    op.execute(
        "ALTER TABLE zalo_link_codes ADD COLUMN IF NOT EXISTS "
        "station_scope_mode VARCHAR NOT NULL DEFAULT 'all'"
    )
    op.execute(
        "ALTER TABLE zalo_link_codes ADD COLUMN IF NOT EXISTS "
        "allowed_bases JSON NOT NULL DEFAULT '[]'::json"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_zalo_link_codes_link_type "
        "ON zalo_link_codes (link_type)"
    )


def downgrade() -> None:
    op.drop_index("ix_zalo_link_codes_link_type", table_name="zalo_link_codes")
    op.drop_column("zalo_link_codes", "allowed_bases")
    op.drop_column("zalo_link_codes", "station_scope_mode")
    op.drop_column("zalo_link_codes", "link_type")

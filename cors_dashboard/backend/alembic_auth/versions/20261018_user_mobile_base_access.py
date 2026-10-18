"""Add can_view_mobile_bases permission column to users table.

Revision ID: 20261018_user_mobile_base_access
Revises: 20261009_api_key_reveal
"""
from typing import Sequence, Union
from alembic import op

revision: str = "20261018_user_mobile_base_access"
down_revision: Union[str, Sequence[str], None] = "20261009_api_key_reveal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "can_view_mobile_bases BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS can_view_mobile_bases")

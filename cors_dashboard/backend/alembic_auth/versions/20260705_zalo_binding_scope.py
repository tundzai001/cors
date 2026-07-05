"""Add editable station scope to Zalo chat bindings.

Revision ID: 20260705_zalo_scope
Revises: 20260704_zalo_smart
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260705_zalo_scope"
down_revision: Union[str, Sequence[str], None] = "20260704_zalo_smart"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Application startup performs the same compatibility repair, so keep the
    # formal migration safe when it is applied afterwards.
    op.execute(
        "ALTER TABLE zalo_chat_bindings ADD COLUMN IF NOT EXISTS "
        "station_scope_mode VARCHAR NOT NULL DEFAULT 'all'"
    )
    op.execute(
        "ALTER TABLE zalo_chat_bindings ADD COLUMN IF NOT EXISTS "
        "allowed_bases JSON NOT NULL DEFAULT '[]'::json"
    )


def downgrade() -> None:
    op.drop_column("zalo_chat_bindings", "allowed_bases")
    op.drop_column("zalo_chat_bindings", "station_scope_mode")

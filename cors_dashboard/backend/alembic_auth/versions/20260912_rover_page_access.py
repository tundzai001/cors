"""Separate Rover page access from Rover create/edit access.

Revision ID: 20260912_rover_page_access
Revises: 20260705_zalo_link_intent
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260912_rover_page_access"
down_revision: Union[str, Sequence[str], None] = "20260705_zalo_link_intent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Startup performs the same compatibility repair, so keep this migration
    # idempotent when code is deployed before the formal Alembic step.
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "can_view_rovers BOOLEAN NOT NULL DEFAULT FALSE"
    )
    # Preserve access for existing coordinators and users who already had the
    # old Rover capability or an explicit Rover Access list.
    op.execute(
        "UPDATE users SET can_view_rovers = TRUE "
        "WHERE role = 'coordinator' OR can_create_rovers = TRUE "
        "OR (allowed_rovers IS NOT NULL AND allowed_rovers NOT IN ('', '[]'))"
    )
    op.execute("ALTER TABLE users ALTER COLUMN can_view_rovers DROP DEFAULT")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS can_view_rovers")

"""Persist when a device entered the offline state.

Revision ID: 20260927_offline_since
Revises: e7b9c4d1a2f3
Create Date: 2026-09-27

"""
from typing import Sequence, Union

from alembic import op


revision: str = "20260927_offline_since"
down_revision: Union[str, Sequence[str], None] = "e7b9c4d1a2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent because app startup also adds runtime-compatible columns.
    op.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS offline_since BIGINT")


def downgrade() -> None:
    op.execute("ALTER TABLE devices DROP COLUMN IF EXISTS offline_since")

"""Add separate read-only station scope for coordinators."""

from typing import Sequence

from alembic import op


revision: str = "20260924_coordinator_view_stations"
down_revision: str | Sequence[str] | None = "20260924_rover_session_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS view_only_bases VARCHAR")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS view_only_bases")

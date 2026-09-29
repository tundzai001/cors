"""Allow session history capture to be configured per Rover account."""

from typing import Sequence, Union

from alembic import op


revision: str = "20260929_rover_auto_save_sessions"
down_revision: Union[str, Sequence[str], None] = "20260926_rover_auto_off"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE rover_accounts "
        "ADD COLUMN IF NOT EXISTS auto_save_sessions BOOLEAN NOT NULL DEFAULT TRUE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE rover_accounts DROP COLUMN IF EXISTS auto_save_sessions")

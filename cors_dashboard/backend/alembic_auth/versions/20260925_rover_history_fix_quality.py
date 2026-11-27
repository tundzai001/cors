"""Record RTK fix quality with rover history positions."""

from typing import Sequence

from alembic import op


revision: str = "20260925_rover_history_fix_quality"
down_revision: str | Sequence[str] | None = "20260924_coordinator_view_stations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE rover_session_history "
        "ADD COLUMN IF NOT EXISTS position_fix_quality SMALLINT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE rover_session_history "
        "DROP COLUMN IF EXISTS position_fix_quality"
    )

"""Configure automatic RTK point capture per rover account."""

from typing import Sequence, Union

from alembic import op


revision: str = "20260925_rover_auto_points"
down_revision: Union[str, Sequence[str], None] = "20260925_rover_fixed_points"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE rover_accounts "
        "ADD COLUMN IF NOT EXISTS auto_save_fixed_points BOOLEAN NOT NULL DEFAULT TRUE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE rover_accounts DROP COLUMN IF EXISTS auto_save_fixed_points")

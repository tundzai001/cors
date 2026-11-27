"""Default new Rover accounts to manual RTK point saving."""

from typing import Sequence

from alembic import op


revision: str = "20260926_rover_auto_off"
down_revision: str | Sequence[str] | None = "20260925_rover_auto_points"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE rover_accounts "
        "ALTER COLUMN auto_save_fixed_points SET DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE rover_accounts "
        "ALTER COLUMN auto_save_fixed_points SET DEFAULT TRUE"
    )

"""Store named RTK fixed points inside rover sessions."""

from typing import Sequence

from alembic import op


revision: str = "20260925_rover_fixed_points"
down_revision: str | Sequence[str] | None = "20260925_rover_history_fix_quality"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE rover_session_history ADD COLUMN IF NOT EXISTS position_satellite_count INTEGER")
    op.execute("ALTER TABLE rover_session_history ADD COLUMN IF NOT EXISTS latest_fix_quality SMALLINT")
    op.execute("ALTER TABLE rover_session_history ADD COLUMN IF NOT EXISTS satellite_count INTEGER")
    op.execute(
        """CREATE TABLE IF NOT EXISTS rover_fixed_points (
            id SERIAL PRIMARY KEY,
            session_id INTEGER NOT NULL REFERENCES rover_session_history(id) ON DELETE CASCADE,
            label VARCHAR(80) NOT NULL DEFAULT 'Điểm đo',
            latitude DOUBLE PRECISION NOT NULL,
            longitude DOUBLE PRECISION NOT NULL,
            fix_quality SMALLINT NOT NULL DEFAULT 4,
            satellite_count INTEGER NULL,
            recorded_at BIGINT NOT NULL,
            saved_at BIGINT NOT NULL,
            created_by VARCHAR NOT NULL
        )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rover_fixed_points_session_recorded "
        "ON rover_fixed_points (session_id, recorded_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rover_fixed_points")
    op.execute("ALTER TABLE rover_session_history DROP COLUMN IF EXISTS position_satellite_count")
    op.execute("ALTER TABLE rover_session_history DROP COLUMN IF EXISTS latest_fix_quality")
    op.execute("ALTER TABLE rover_session_history DROP COLUMN IF EXISTS satellite_count")

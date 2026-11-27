"""Add mountpoint to rtcm_stream_sessions

Revision ID: d3f4a9b2c7e1
Revises: c1a8e7f9b2d4
Create Date: 2026-03-20 15:10:00.000000

"""
from typing import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd3f4a9b2c7e1'
down_revision: str | Sequence[str] | None = 'c1a8e7f9b2d4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema, tolerating columns/indexes created before Alembic."""
    op.execute(
        "ALTER TABLE rtcm_stream_sessions "
        "ADD COLUMN IF NOT EXISTS mountpoint VARCHAR NOT NULL DEFAULT ''"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rtcm_stream_sessions_mountpoint "
        "ON rtcm_stream_sessions (mountpoint)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rtcm_serial_mountpoint_expires "
        "ON rtcm_stream_sessions (serial, mountpoint, expires_at)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_rtcm_serial_mountpoint_expires', table_name='rtcm_stream_sessions')
    op.drop_index(op.f('ix_rtcm_stream_sessions_mountpoint'), table_name='rtcm_stream_sessions')
    op.drop_column('rtcm_stream_sessions', 'mountpoint')

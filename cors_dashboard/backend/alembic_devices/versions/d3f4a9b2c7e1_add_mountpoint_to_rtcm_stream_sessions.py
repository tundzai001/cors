"""Add mountpoint to rtcm_stream_sessions

Revision ID: d3f4a9b2c7e1
Revises: c1a8e7f9b2d4
Create Date: 2026-03-20 15:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3f4a9b2c7e1'
down_revision: Union[str, Sequence[str], None] = 'c1a8e7f9b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'rtcm_stream_sessions',
        sa.Column('mountpoint', sa.String(), nullable=False, server_default='')
    )
    op.create_index(
        op.f('ix_rtcm_stream_sessions_mountpoint'),
        'rtcm_stream_sessions',
        ['mountpoint'],
        unique=False
    )
    op.create_index(
        'ix_rtcm_serial_mountpoint_expires',
        'rtcm_stream_sessions',
        ['serial', 'mountpoint', 'expires_at'],
        unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_rtcm_serial_mountpoint_expires', table_name='rtcm_stream_sessions')
    op.drop_index(op.f('ix_rtcm_stream_sessions_mountpoint'), table_name='rtcm_stream_sessions')
    op.drop_column('rtcm_stream_sessions', 'mountpoint')

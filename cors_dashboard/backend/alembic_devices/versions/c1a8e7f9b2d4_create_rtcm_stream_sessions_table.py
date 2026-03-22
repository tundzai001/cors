"""Create rtcm_stream_sessions table

Revision ID: c1a8e7f9b2d4
Revises: 765ea0a2347c
Create Date: 2026-03-20 10:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1a8e7f9b2d4'
down_revision: Union[str, Sequence[str], None] = '765ea0a2347c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'rtcm_stream_sessions',
        sa.Column('session_id', sa.String(), nullable=False),
        sa.Column('serial', sa.String(), nullable=False),
        sa.Column('client_id', sa.String(), nullable=False),
        sa.Column('expires_at', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('last_heartbeat_at', sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint('session_id')
    )
    op.create_index(op.f('ix_rtcm_stream_sessions_session_id'), 'rtcm_stream_sessions', ['session_id'], unique=False)
    op.create_index(op.f('ix_rtcm_stream_sessions_serial'), 'rtcm_stream_sessions', ['serial'], unique=False)
    op.create_index(op.f('ix_rtcm_stream_sessions_client_id'), 'rtcm_stream_sessions', ['client_id'], unique=False)
    op.create_index(op.f('ix_rtcm_stream_sessions_expires_at'), 'rtcm_stream_sessions', ['expires_at'], unique=False)
    op.create_index('ix_rtcm_serial_expires', 'rtcm_stream_sessions', ['serial', 'expires_at'], unique=False)
    op.create_index('ix_rtcm_client_serial', 'rtcm_stream_sessions', ['client_id', 'serial'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_rtcm_client_serial', table_name='rtcm_stream_sessions')
    op.drop_index('ix_rtcm_serial_expires', table_name='rtcm_stream_sessions')
    op.drop_index(op.f('ix_rtcm_stream_sessions_expires_at'), table_name='rtcm_stream_sessions')
    op.drop_index(op.f('ix_rtcm_stream_sessions_client_id'), table_name='rtcm_stream_sessions')
    op.drop_index(op.f('ix_rtcm_stream_sessions_serial'), table_name='rtcm_stream_sessions')
    op.drop_index(op.f('ix_rtcm_stream_sessions_session_id'), table_name='rtcm_stream_sessions')
    op.drop_table('rtcm_stream_sessions')

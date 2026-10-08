"""Keep a single RTCM lease for each station/client/mountpoint.

Revision ID: 20261001_unique_rtcm_client_leases
Revises: 20261001_secure_device_tokens
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op


revision: str = "20261001_unique_rtcm_client_leases"
down_revision: Union[str, Sequence[str], None] = "20261001_secure_device_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Keep the newest lease and remove duplicates left by earlier wake requests.
    op.execute(
        "DELETE FROM rtcm_stream_sessions AS old "
        "USING rtcm_stream_sessions AS newer "
        "WHERE old.serial = newer.serial "
        "AND lower(old.mountpoint) = lower(newer.mountpoint) "
        "AND old.client_id = newer.client_id "
        "AND (old.last_heartbeat_at, old.created_at, old.session_id) "
        "< (newer.last_heartbeat_at, newer.created_at, newer.session_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_rtcm_serial_mountpoint_client "
        "ON rtcm_stream_sessions (serial, lower(mountpoint), client_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_rtcm_serial_mountpoint_client")

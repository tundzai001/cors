"""Add device runtime columns

Revision ID: e7b9c4d1a2f3
Revises: d3f4a9b2c7e1
Create Date: 2026-05-31 03:05:00.000000

"""
from typing import Sequence

from alembic import op


revision: str = "e7b9c4d1a2f3"
down_revision: str | Sequence[str] | None = "d3f4a9b2c7e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Runtime compatibility checks may have provisioned these before Alembic.
    op.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS device_token VARCHAR")
    op.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS is_provisioned BOOLEAN DEFAULT FALSE")
    op.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS system_info JSONB")
    op.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS rtk_fix_status VARCHAR DEFAULT 'NO_FIX'")
    op.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS base_mode_active BOOLEAN DEFAULT FALSE")
    op.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS version VARCHAR")
    op.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS is_synced BOOLEAN NOT NULL DEFAULT TRUE")
    op.execute("CREATE INDEX IF NOT EXISTS ix_devices_device_token ON devices (device_token)")


def downgrade() -> None:
    op.drop_index(op.f("ix_devices_device_token"), table_name="devices")
    op.drop_column("devices", "is_synced")
    op.drop_column("devices", "version")
    op.drop_column("devices", "base_mode_active")
    op.drop_column("devices", "rtk_fix_status")
    op.drop_column("devices", "system_info")
    op.drop_column("devices", "is_provisioned")
    op.drop_column("devices", "device_token")

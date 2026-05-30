"""Add device runtime columns

Revision ID: e7b9c4d1a2f3
Revises: d3f4a9b2c7e1
Create Date: 2026-05-31 03:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e7b9c4d1a2f3"
down_revision: Union[str, Sequence[str], None] = "d3f4a9b2c7e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("device_token", sa.String(), nullable=True))
    op.add_column("devices", sa.Column("is_provisioned", sa.Boolean(), nullable=True, server_default=sa.false()))
    op.add_column("devices", sa.Column("system_info", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("devices", sa.Column("rtk_fix_status", sa.String(), nullable=True, server_default="NO_FIX"))
    op.add_column("devices", sa.Column("base_mode_active", sa.Boolean(), nullable=True, server_default=sa.false()))
    op.add_column("devices", sa.Column("version", sa.String(), nullable=True))
    op.add_column("devices", sa.Column("is_synced", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_index(op.f("ix_devices_device_token"), "devices", ["device_token"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_devices_device_token"), table_name="devices")
    op.drop_column("devices", "is_synced")
    op.drop_column("devices", "version")
    op.drop_column("devices", "base_mode_active")
    op.drop_column("devices", "rtk_fix_status")
    op.drop_column("devices", "system_info")
    op.drop_column("devices", "is_provisioned")
    op.drop_column("devices", "device_token")

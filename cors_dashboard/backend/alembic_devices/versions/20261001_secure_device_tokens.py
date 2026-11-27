"""Enforce uniqueness for random per-device bearer tokens.

Revision ID: 20261001_secure_device_tokens
Revises: 20260927_offline_since
Create Date: 2026-10-01
"""
from typing import Sequence

from alembic import op


revision: str = "20261001_secure_device_tokens"
down_revision: str | Sequence[str] | None = "20260927_offline_since"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Legacy deterministic tokens stay untouched for rollback and staged
    # re-deployment. New dt_ tokens are unique at the database boundary.
    op.execute("DROP INDEX IF EXISTS ix_devices_device_token")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_devices_secure_device_token "
        "ON devices (device_token) WHERE left(device_token, 3) = 'dt_'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_devices_secure_device_token")
    op.execute("CREATE INDEX IF NOT EXISTS ix_devices_device_token ON devices (device_token)")

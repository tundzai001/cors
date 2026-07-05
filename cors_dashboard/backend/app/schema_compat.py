import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


DEVICE_SCHEMA_STATEMENTS = (
    "ALTER TABLE devices ADD COLUMN IF NOT EXISTS device_token VARCHAR",
    "ALTER TABLE devices ADD COLUMN IF NOT EXISTS is_provisioned BOOLEAN DEFAULT FALSE",
    "ALTER TABLE devices ADD COLUMN IF NOT EXISTS system_info JSONB",
    "ALTER TABLE devices ADD COLUMN IF NOT EXISTS rtk_fix_status VARCHAR DEFAULT 'NO_FIX'",
    "ALTER TABLE devices ADD COLUMN IF NOT EXISTS base_mode_active BOOLEAN DEFAULT FALSE",
    "ALTER TABLE devices ADD COLUMN IF NOT EXISTS version VARCHAR",
    "ALTER TABLE devices ADD COLUMN IF NOT EXISTS is_synced BOOLEAN NOT NULL DEFAULT TRUE",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_devices_device_token ON devices (device_token)",
)


AUTH_ZALO_SCHEMA_STATEMENTS = (
    "ALTER TABLE zalo_chat_bindings ADD COLUMN IF NOT EXISTS station_scope_mode VARCHAR NOT NULL DEFAULT 'all'",
    "ALTER TABLE zalo_chat_bindings ADD COLUMN IF NOT EXISTS allowed_bases JSON NOT NULL DEFAULT '[]'::json",
    "ALTER TABLE zalo_link_codes ADD COLUMN IF NOT EXISTS link_type VARCHAR NOT NULL DEFAULT 'private'",
    "ALTER TABLE zalo_link_codes ADD COLUMN IF NOT EXISTS station_scope_mode VARCHAR NOT NULL DEFAULT 'all'",
    "ALTER TABLE zalo_link_codes ADD COLUMN IF NOT EXISTS allowed_bases JSON NOT NULL DEFAULT '[]'::json",
    "CREATE INDEX IF NOT EXISTS ix_zalo_link_codes_link_type ON zalo_link_codes (link_type)",
)


async def ensure_device_runtime_columns(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        for statement in DEVICE_SCHEMA_STATEMENTS:
            await conn.execute(text(statement))
    logger.info("Device schema compatibility check completed")


async def ensure_auth_zalo_runtime_columns(engine: AsyncEngine) -> None:
    """Keep Zalo authorization usable when code is deployed before Alembic."""
    async with engine.begin() as conn:
        for statement in AUTH_ZALO_SCHEMA_STATEMENTS:
            await conn.execute(text(statement))
    logger.info("Auth Zalo schema compatibility check completed")

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


async def ensure_device_runtime_columns(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        for statement in DEVICE_SCHEMA_STATEMENTS:
            await conn.execute(text(statement))
    logger.info("Device schema compatibility check completed")

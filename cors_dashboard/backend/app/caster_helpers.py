import asyncio
import logging
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.ext.asyncio import AsyncSession

from .caster_client import CasterClient
from .database import settings

logger = logging.getLogger(__name__)

ENV_FILE_PATH = Path(__file__).resolve().parent.parent / ".env"
global_caster_cache_lock = asyncio.Lock()


def _read_caster_config_from_env() -> tuple[list[str], dict]:
    env_values = dotenv_values(ENV_FILE_PATH)
    raw_host = str(env_values.get("CASTER_HOST", "")).strip()
    raw_port = env_values.get("CASTER_PORT")
    raw_user = env_values.get("CASTER_ADMIN_USER")
    raw_pass = env_values.get("CASTER_PASSWORD")

    hosts = [h.strip() for h in raw_host.split(",") if h.strip()]
    config = {
        "host": hosts[0] if hosts else None,
        "port": None,
        "user": str(raw_user).strip() if raw_user and str(raw_user).strip() else None,
        "pass": str(raw_pass).strip() if raw_pass and str(raw_pass).strip() else None,
    }

    if raw_port and str(raw_port).strip():
        try:
            config["port"] = int(str(raw_port).strip())
        except ValueError:
            logger.warning("Invalid CASTER_PORT in .env: %s", raw_port)

    return hosts, config


async def get_caster_client(db: AsyncSession) -> CasterClient:
    hosts, env_cfg = _read_caster_config_from_env()
    host = str(env_cfg["host"] or settings.CASTER_HOST or "aitogy.com.vn")
    port = int(env_cfg["port"] or settings.CASTER_PORT or 2101)
    user = str(env_cfg["user"] or "admin")
    password = str(env_cfg["pass"] or "12345")
    return CasterClient(host, port, admin_user=user, admin_password=password)

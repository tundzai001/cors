import asyncio
import logging
import os
import re
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
    get_value = lambda key: os.getenv(key) or env_values.get(key)
    raw_host = str(get_value("CASTER_HOST") or "").strip()
    raw_port = get_value("CASTER_PORT")
    raw_user = get_value("CASTER_ADMIN_USER") or get_value("CASTER_MASTER_USER")
    raw_pass = get_value("CASTER_PASSWORD") or get_value("CASTER_MASTER_PASSWORD")

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


def configured_caster_hosts() -> list[str]:
    hosts, _ = _read_caster_config_from_env()
    if hosts:
        return hosts
    return [host.strip() for host in str(settings.CASTER_HOST or "").split(",") if host.strip()]


def validate_caster_hosts(hosts: list[str] | tuple[str, ...]) -> list[str]:
    """Accept only configured caster names; request data must never choose a network target."""
    configured = configured_caster_hosts()
    allowed = {host.rstrip(".").casefold(): host for host in configured}
    if not allowed:
        raise ValueError("No CASTER_HOST is configured")

    validated = []
    seen = set()
    for raw_host in hosts:
        host = str(raw_host or "").strip()
        normalized = host.rstrip(".").casefold()
        if (
            not host
            or len(host) > 253
            or not re.fullmatch(r"[A-Za-z0-9._:-]+", host)
            or normalized not in allowed
        ):
            raise ValueError("Caster host must match a configured CASTER_HOST")
        if normalized not in seen:
            validated.append(allowed[normalized])
            seen.add(normalized)
    if not validated:
        raise ValueError("At least one configured caster host is required")
    return validated


async def get_caster_client(db: AsyncSession) -> CasterClient:
    hosts, env_cfg = _read_caster_config_from_env()
    host = str(env_cfg["host"] or settings.CASTER_HOST or "").strip()
    port = int(env_cfg["port"] or settings.CASTER_PORT or 2101)
    user = str(env_cfg["user"] or "").strip()
    password = str(env_cfg["pass"] or "").strip()
    if not host or not user or not password:
        raise RuntimeError("CASTER_HOST, CASTER_ADMIN_USER, and CASTER_PASSWORD must be configured")
    return CasterClient(host, port, admin_user=user, admin_password=password)

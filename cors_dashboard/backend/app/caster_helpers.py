import asyncio
import logging
import ipaddress
import socket
import time
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


def saved_rover_duration(rover) -> int:
    """Restore a missing account without renewing expired/disabled access."""
    if rover.is_active is False:
        raise ValueError("Rover đang bị khóa; cần mở khóa trước khi khôi phục.")
    if rover.expires_at is None:
        return 315360000
    remaining = int(rover.expires_at - time.time())
    if remaining <= 0:
        raise ValueError("Rover đã hết hạn; chọn thời hạn gia hạn để khôi phục.")
    return remaining


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


_caster_peer_cache = {}


async def is_configured_caster_peer(peer: str) -> bool:
    """Legacy control is restricted to the configured caster's actual peer IP."""
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    hosts = tuple(configured_caster_hosts())
    cached = _caster_peer_cache.get(hosts)
    if not cached or time.monotonic() - cached[0] > 60:
        addresses = set()
        for host in hosts:
            try:
                addresses.add(ipaddress.ip_address(host))
            except ValueError:
                try:
                    results = await asyncio.wait_for(
                        asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM),
                        timeout=3,
                    )
                    addresses.update(ipaddress.ip_address(item[4][0]) for item in results)
                except (OSError, asyncio.TimeoutError, ValueError):
                    continue
        cached = (time.monotonic(), addresses)
        _caster_peer_cache.clear()
        _caster_peer_cache[hosts] = cached
    return peer_ip in cached[1]


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
    _, env_cfg = _read_caster_config_from_env()
    host = str(env_cfg["host"] or settings.CASTER_HOST or "").strip()
    port = int(env_cfg["port"] or settings.CASTER_PORT or 2101)
    user = str(env_cfg["user"] or "").strip()
    password = str(env_cfg["pass"] or "").strip()
    if not host or not user or not password:
        raise RuntimeError("CASTER_HOST, CASTER_ADMIN_USER, and CASTER_PASSWORD must be configured")
    return CasterClient(host, port, admin_user=user, admin_password=password)

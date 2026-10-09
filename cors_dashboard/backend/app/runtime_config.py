"""Shared environment-backed runtime configuration."""

import logging
import os
from pathlib import Path
from dotenv import dotenv_values


logger = logging.getLogger("app.main")


ENV_FILE_PATH = Path(__file__).resolve().parent.parent / ".env"


def _read_caster_config_from_env() -> tuple[list[str], dict]:
    """Reads caster config from .env and supports multiple CSV hosts."""
    env_values = dotenv_values(ENV_FILE_PATH)
    get_value = lambda key: os.getenv(key) or env_values.get(key)
    raw_host = str(get_value("CASTER_HOST") or "").strip()
    raw_port = get_value("CASTER_PORT")
    raw_user = get_value("CASTER_ADMIN_USER") or get_value("CASTER_MASTER_USER")
    raw_pass = get_value("CASTER_PASSWORD") or get_value("CASTER_MASTER_PASSWORD")

    # Split multiple hosts if comma-separated
    hosts = [h.strip() for h in raw_host.split(',') if h.strip()]

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


_ENV_VALUES = dotenv_values(ENV_FILE_PATH)


def _get_env_preferring_dotenv(key: str, default: str) -> str:
    val = _ENV_VALUES.get(key)
    if val is not None and str(val).strip() != "":
        return str(val)
    return str(os.getenv(key, default))


try:
    ROVER_FIXED_POINT_INTERVAL_SECONDS = int(
        _get_env_preferring_dotenv("ROVER_FIXED_POINT_INTERVAL_SECONDS", "120")
    )
    if ROVER_FIXED_POINT_INTERVAL_SECONDS <= 0:
        raise ValueError("must be positive")
except (TypeError, ValueError):
    ROVER_FIXED_POINT_INTERVAL_SECONDS = 120
    logger.warning("Invalid ROVER_FIXED_POINT_INTERVAL_SECONDS; using 120 seconds")


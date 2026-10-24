"""Live polling settings shared by .env edits and the admin switch."""

import logging
import os
from dotenv import dotenv_values, set_key
from .runtime_config import ENV_FILE_PATH

logger = logging.getLogger(__name__)


def get_check_settings():
    values = dotenv_values(ENV_FILE_PATH)

    def value(key, default):
        return str(values.get(key) or os.getenv(key, default)).strip()

    def seconds(key, default):
        try:
            number = int(value(key, str(default)))
            if number > 0:
                return number
        except ValueError:
            pass
        logger.warning("Invalid %s; using %s seconds", key, default)
        return default

    return {
        "map_status_enabled": value("CORS_CUC_MAP_STATUS_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        "rtcm_check_enabled": value("CORS_CUC_RTCM_CHECK_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        "web_check_interval_seconds": seconds("CORS_CUC_WEB_CHECK_INTERVAL_SECONDS", 30),
        "rtcm_check_interval_seconds": seconds("CORS_CUC_RTCM_CHECK_INTERVAL_SECONDS", 900),
    }


def set_rtcm_check_enabled(enabled):
    set_key(str(ENV_FILE_PATH), "CORS_CUC_RTCM_CHECK_ENABLED", "true" if enabled else "false")
    return get_check_settings()

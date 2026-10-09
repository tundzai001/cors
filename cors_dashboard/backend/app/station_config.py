"""Shared station service configuration normalization."""

from typing import Optional

from . import crud, models


def _device_service_config(device: Optional[models.Device]) -> dict:
    return crud.normalize_service_config(getattr(device, "service_config", None) if device else None)


def _preserve_relay_config_metadata(serial: str, previous: dict, incoming: dict) -> dict:
    updated = dict(incoming or {})
    if str(serial or "").strip().upper().startswith("RELAY_") and isinstance(previous, dict):
        source = previous.get("relay_source")
        if isinstance(source, dict):
            updated["relay_source"] = source
        for key in ("agent_station_power_control", "agent_station_enabled"):
            if key in previous and key not in updated:
                updated[key] = previous[key]
    return updated


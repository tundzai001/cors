"""Build small application-level ACKs for agent status probes."""

from __future__ import annotations

import time
from typing import Any, Mapping


def _status_ack_identity(payload: Mapping[str, Any] | None, channel: str) -> tuple[str, str, int] | None:
    if not isinstance(payload, Mapping):
        return None

    boot_id = str(payload.get("status_boot_id") or "").strip()
    try:
        sequence = int(payload.get("status_sequence"))
    except (TypeError, ValueError):
        return None

    normalized_channel = str(channel or "").strip().lower()
    if not boot_id or len(boot_id) > 128 or sequence < 0:
        return None
    if normalized_channel not in {"websocket", "mqtt"}:
        return None
    return normalized_channel, boot_id, sequence


def _auto_base_location_ack(progress: Any) -> dict[str, Any] | None:
    if not isinstance(progress, Mapping) or progress.get("phase") != "resolving_location":
        return None
    request = progress.get("location_request")
    if not isinstance(request, Mapping):
        return None
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not 0 < len(request_id) <= 128:
        return None

    from .auto_base_location import resolve_location

    try:
        if any(isinstance(request.get(key), bool) for key in ("lat", "lon", "alt")):
            raise ValueError("Boolean LLH")
        point = [float(request[key]) for key in ("lat", "lon", "alt")]
        location = resolve_location(*point)
    except (KeyError, TypeError, ValueError, OverflowError):
        location = {"state": "invalid_request"}
    return {"request_id": request_id, "location": location}


def build_control_plane_ack(
    payload: Mapping[str, Any] | None,
    *,
    channel: str,
    serial: str,
    server_timestamp: float | None = None,
) -> dict[str, Any] | None:
    identity = _status_ack_identity(payload, channel)
    if identity is None:
        return None
    normalized_channel, boot_id, sequence = identity

    ack = {
        "type": "control_plane_ack",
        "channel": normalized_channel,
        "serial": str(serial or ""),
        "status_boot_id": boot_id,
        "status_sequence": sequence,
        "server_timestamp": round(
            time.time() if server_timestamp is None else float(server_timestamp),
            3,
        ),
    }

    location = _auto_base_location_ack(payload.get("auto_base_progress"))
    if location is not None:
        ack["auto_base_location"] = location
    return ack

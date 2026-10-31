"""Build small application-level ACKs for agent status probes."""

from __future__ import annotations

import time
from typing import Any, Mapping


def build_control_plane_ack(
    payload: Mapping[str, Any] | None,
    *,
    channel: str,
    serial: str,
    server_timestamp: float | None = None,
) -> dict[str, Any] | None:
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

    progress = payload.get("auto_base_progress")
    if isinstance(progress, Mapping) and progress.get("phase") == "resolving_location":
        request = progress.get("location_request")
        if isinstance(request, Mapping):
            request_id = request.get("request_id")
            if isinstance(request_id, str) and 0 < len(request_id) <= 128:
                from .auto_base_location import resolve_location
                try:
                    if any(isinstance(request.get(key), bool) for key in ("lat", "lon", "alt")):
                        raise ValueError("Boolean LLH")
                    point = [float(request[key]) for key in ("lat", "lon", "alt")]
                    location = resolve_location(*point)
                except (KeyError, TypeError, ValueError, OverflowError):
                    location = {"state": "invalid_request"}
                ack["auto_base_location"] = {"request_id": request_id, "location": location}
    return ack

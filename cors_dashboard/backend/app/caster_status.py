"""Per-server status change detection and caster notifications."""

import asyncio
import logging
import os
from typing import Dict

from . import models

logger = logging.getLogger("app.main")


CASTER_STATUS_NOTIFY_CONCURRENCY = max(
    1,
    int(os.getenv("CASTER_STATUS_NOTIFY_CONCURRENCY", "4")),
)
caster_status_notify_semaphore = asyncio.Semaphore(CASTER_STATUS_NOTIFY_CONCURRENCY)

device_status_cache: Dict[str, dict] = {}
device_status_cache_lock = asyncio.Lock()

def _extract_per_server_status(device: models.Device, payload: dict | None = None) -> dict:
    """
    Trích xuất per-server NTRIP status từ device và payload.
    Dùng để tracking thay đổi status S1/S2 riêng biệt.
    
    Ưu tiên sử dụng ntrip_stream_state từ payload (nếu có) để nhận data chi tiết từ Agent.
    Fallback sang device fields nếu payload không có.
    
    State mapping:
    - streaming: active=true && connected=true (đang push dữ liệu)
    - sleep: active=false && on_demand=true (có thể wake)
    - offline: Mọi trường hợp khác (không thể wake)
    
    Returns: {
        "server1": {"enabled": bool, "state": "streaming|sleep|offline", "mountpoint": str},
        "server2": {...}
    }
    """
    # 🔥 FIX: Robustly handle service_config type (handle list or string from migration)
    raw_cfg = getattr(device, "service_config", None)
    if isinstance(raw_cfg, dict):
        cfg = raw_cfg
    elif isinstance(raw_cfg, str):
        import json
        try: cfg = json.loads(raw_cfg)
        except: cfg = {}
    else:
        cfg = {}

    device_status = str(device.status or "").lower()
    payload = payload or {}

    # Lấy ntrip_stream_state từ payload (nếu Agent gửi)
    stream_state = payload.get("ntrip_stream_state", {})
    has_stream_state = bool(stream_state)

    def _get_server_state(server_id: int) -> str:
        """Determine server state: streaming, sleep, or offline"""
        # Device offline → cả hai server offline
        if device_status == "offline":
            return "offline"

        server_key = f"server{server_id}"

        # Ưu tiên sử dụng rich state từ Agent (nếu có)
        if has_stream_state and server_key in stream_state:
            state_info = stream_state[server_key]
            enabled = bool(state_info.get("enabled", False))
            if not enabled:
                return "offline"

            active = bool(state_info.get("active", False))
            on_demand = bool(state_info.get("on_demand", False))
            connected = bool(state_info.get("connected", False))

            # streaming: active && connected
            if active and connected:
                return "streaming"
            # sleep: not active && on_demand
            elif not active and on_demand:
                return "sleep"
            else:
                return "offline"

        # Fallback: Use basic config fields nếu payload không có stream_state
        enabled = bool(cfg.get(f"server{server_id}_enabled"))
        if not enabled:
            return "offline"

        active = bool(cfg.get(f"server{server_id}_active"))
        stream_on_demand = bool(cfg.get("stream_on_demand"))

        # Không có per-server connected info → use basic logic
        if active and bool(device.ntrip_connected):
            return "streaming"
        elif not active and stream_on_demand:
            return "sleep"
        else:
            return "offline"

    result = {}

    # S1 status
    result["server1"] = {
        "enabled": bool(cfg.get("server1_enabled")),
        "state": _get_server_state(1),
        "mountpoint": cfg.get("mountpoint1") or "",
    }

    # S2 status
    result["server2"] = {
        "enabled": bool(cfg.get("server2_enabled")),
        "state": _get_server_state(2),
        "mountpoint": cfg.get("mountpoint2") or "",
    }

    return result


def _status_changed(old_status: dict, new_status: dict) -> dict:
    """
    So sánh old vs new status, trả về dict chứa thay đổi per-server.
    
    Returns: {
        "server1": {"changed": bool, "old": {...}, "new": {...}},
        "server2": {...},
        "overall_changed": bool
    }
    """
    result = {
        "server1": {"changed": False, "old": old_status.get("server1", {}), "new": new_status.get("server1", {})},
        "server2": {"changed": False, "old": old_status.get("server2", {}), "new": new_status.get("server2", {})},
        "overall_changed": False
    }

    for sid in ("server1", "server2"):
        if result[sid]["old"] != result[sid]["new"]:
            result[sid]["changed"] = True
            result["overall_changed"] = True

    return result


async def _notify_casters_on_status_change(serial: str, device: models.Device, status_change: dict) -> None:
    """
    Detect per-server status change và gửi status to appropriate Caster.
    
    Logic:
    - S1 changed → gửi cho serverhost1
    - S2 changed → gửi cho serverhost2
    - offline → gửi cho cả 2
    """
    try:
        # 🔥 FIX: Robustly handle service_config type
        raw_cfg = getattr(device, "service_config", None)
        if isinstance(raw_cfg, dict):
            cfg = raw_cfg
        elif isinstance(raw_cfg, str):
            import json
            try: cfg = json.loads(raw_cfg)
            except: cfg = {}
        else:
            cfg = {}

        device_status = str(device.status or "").lower()

        # Build common payload
        payload = {
            "mountpoint": "",
            "state": "offline",  # streaming, sleep, offline
            "enabled": False,
        }

        # Offline → notify both casters
        if device_status == "offline":
            s1_host = cfg.get("serverhost1", "").strip() if isinstance(cfg, dict) else ""
            s1_port = cfg.get("port1") if isinstance(cfg, dict) else None
            s1_full = f"{s1_host}:{s1_port}" if s1_host and s1_port and ":" not in s1_host else s1_host

            s2_host = cfg.get("serverhost2", "").strip() if isinstance(cfg, dict) else ""
            s2_port = cfg.get("port2") if isinstance(cfg, dict) else None
            s2_full = f"{s2_host}:{s2_port}" if s2_host and s2_port and ":" not in s2_host else s2_host

            if s1_full and cfg.get("mountpoint1"):
                payload["mountpoint"] = cfg.get("mountpoint1")
                payload["state"] = "offline"
                payload["enabled"] = bool(cfg.get("server1_enabled"))
                asyncio.create_task(_send_status_to_caster(s1_full, payload, "S1", serial))

            if s2_full and cfg.get("mountpoint2"):
                payload["mountpoint"] = cfg.get("mountpoint2")
                payload["state"] = "offline"
                payload["enabled"] = bool(cfg.get("server2_enabled"))
                asyncio.create_task(_send_status_to_caster(s2_full, payload, "S2", serial))

            return

        # Notify S1 if changed
        if status_change["server1"]["changed"] and cfg.get("server1_enabled"):
            s1_host = cfg.get("serverhost1", "").strip()
            s1_port = cfg.get("port1")
            s1_full = f"{s1_host}:{s1_port}" if s1_host and s1_port and ":" not in s1_host else s1_host
            s1_mp = cfg.get("mountpoint1", "").strip()
            if s1_full and s1_mp:
                s1_info = status_change["server1"]["new"]
                payload["mountpoint"] = s1_mp
                payload["state"] = s1_info.get("state", "offline")
                payload["enabled"] = s1_info.get("enabled", False)
                asyncio.create_task(_send_status_to_caster(s1_full, payload, "S1", serial))

        # Notify S2 if changed
        if status_change["server2"]["changed"] and cfg.get("server2_enabled"):
            s2_host = cfg.get("serverhost2", "").strip()
            s2_port = cfg.get("port2")
            s2_full = f"{s2_host}:{s2_port}" if s2_host and s2_port and ":" not in s2_host else s2_host
            s2_mp = cfg.get("mountpoint2", "").strip()
            if s2_full and s2_mp:
                s2_info = status_change["server2"]["new"]
                payload["mountpoint"] = s2_mp
                payload["state"] = s2_info.get("state", "offline")
                payload["enabled"] = s2_info.get("enabled", False)
                asyncio.create_task(_send_status_to_caster(s2_full, payload, "S2", serial))

    except Exception as e:
        logger.error(f"[CASTER SYNC] Error notifying casters for '{serial}': {e}")


async def _send_status_to_caster(caster_host: str, status_payload: dict, server_label: str, serial: str) -> None:
    """
    Gửi HTTP POST status to Caster.
    endpoint: POST http://caster_host/api/station-status
    
    Payload: {
        "mountpoint": "HN01",
        "state": "streaming|sleep|offline",
        "enabled": bool
    }
    """
    try:
        import urllib.request
        import json as json_lib

        # 🔥 FILTER: Chỉ gửi sync status tới aitogy.com.vn hoặc aitogy.click
        target_host_lower = str(caster_host or "").lower()
        is_allowed_domain = "aitogy.com.vn" in target_host_lower or "aitogy.click" in target_host_lower

        if not is_allowed_domain:
            # logger.debug(f"[CASTER SYNC] Skipping unauthorized domain: {caster_host}")
            return

        # Build URL (caster_host có thể là "aitogy.com.vn" hoặc "aitogy.com.vn:2101")
        if "://" not in caster_host:
            url = f"http://{caster_host}/api/station-status"
        else:
            url = f"{caster_host}/api/station-status"

        payload_json = json_lib.dumps(status_payload).encode('utf-8')
        request = urllib.request.Request(
            url,
            data=payload_json,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )

        def _post_status_sync() -> None:
            # urllib is blocking, so this function must never execute on the
            # single Uvicorn event-loop thread.
            with urllib.request.urlopen(request, timeout=3) as response:
                response.read()

        async with caster_status_notify_semaphore:
            await asyncio.wait_for(
                asyncio.to_thread(_post_status_sync),
                timeout=4.0,
            )

        state = status_payload.get('state', 'unknown')
        mp = status_payload.get('mountpoint', '?')
        logger.debug(
            f"[CASTER SYNC] ✓ Sent {server_label} status to {caster_host}: "
            f"{mp} → {state}"
        )
    except Exception as e:
        logger.warning(
            f"[CASTER SYNC] ✗ Failed to notify {caster_host} ({server_label}) "
            f"for '{serial}': {e}"
        )


async def _on_device_status_update(device: models.Device, payload: dict | None = None) -> None:
    """
    Called when Agent sends device status via MQTT.
    Detects per-server changes and notifies Casters.
    
    Args:
        device: Device object from DB (để lấy config)
        payload: Raw MQTT payload từ Agent (chứa ntrip_stream_state)
    """
    serial = device.serial
    if not serial:
        return

    # Extract status with rich data từ payload
    new_status = _extract_per_server_status(device, payload)

    async with device_status_cache_lock:
        old_status = device_status_cache.get(serial, {})

        # Compare if there's actual change
        if old_status != new_status:
            # Detect what changed
            status_change = _status_changed(old_status, new_status)

            # Update cache
            device_status_cache[serial] = new_status

            # Notify casters (non-blocking)
            await _notify_casters_on_status_change(serial, device, status_change)


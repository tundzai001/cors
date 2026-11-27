"""Per-server status change detection and caster notifications."""

import asyncio
import logging
import os
import time
from typing import Dict

from . import models
from .crud import normalize_service_config, normalize_ntrip_status_payload
from .rtcm_stream_state import get_server_stream_state, _as_bool
from .background_tasks import schedule_background

logger = logging.getLogger("app.main")


CASTER_STATUS_NOTIFY_CONCURRENCY = max(
    1,
    int(os.getenv("CASTER_STATUS_NOTIFY_CONCURRENCY", "4")),
)
caster_status_notify_semaphore = asyncio.Semaphore(CASTER_STATUS_NOTIFY_CONCURRENCY)

device_status_cache: Dict[str, dict] = {}
device_status_cache_lock = asyncio.Lock()
_status_last_notify: Dict[str, float] = {}
STATUS_REFRESH_SECONDS = 30.0

def _extract_per_server_status(device: models.Device, payload: dict | None = None) -> dict:
    """
    Trích xuất per-server NTRIP status từ device và payload.
    Dùng để tracking thay đổi status S1/S2 riêng biệt.
    
    Ưu tiên sử dụng ntrip_stream_state từ payload (nếu có) để nhận data chi tiết từ Agent.
    Fallback sang device fields nếu payload không có.
    
    State mapping:
    - streaming: active=true && connected=true (đang push dữ liệu)
    - sleep: agent blocks output (active=false && on_demand=true)
    - starting: wake requested, waiting for the source connection
    - offline: device unavailable, disabled output, or failed Always-On connection
    
    Returns: {
        "server1": {"enabled": bool, "state": "streaming|sleep|offline", "mountpoint": str},
        "server2": {...}
    }
    """
    cfg = normalize_service_config(getattr(device, "service_config", None))
    payload = payload or {}
    device_status = str(payload.get("status") or device.status or "").lower()
    stream_state = payload.get("ntrip_stream_state") or {}
    ntrip = normalize_ntrip_status_payload(payload) or (getattr(device, "ntrip_status", None) or {})
    result = {}
    for sid in (1, 2):
        key = f"server{sid}"
        switch = get_server_stream_state(cfg, sid)
        info = stream_state.get(key)
        state = "offline"
        if switch.enabled and device_status not in {"offline", "locked", "unprovisioned", "awaiting_license"}:
            if isinstance(info, dict):
                enabled = _as_bool(info.get("enabled"), switch.enabled)
                active = _as_bool(info.get("active"), switch.active)
                on_demand = _as_bool(info.get("on_demand"), switch.on_demand)
                connected = _as_bool(info.get("connected"))
                can_push = _as_bool(info.get("effective_can_push", info.get("can_push")), active or not on_demand)
                if enabled and connected and can_push:
                    state = "streaming"
                elif enabled and on_demand:
                    state = "starting" if active else "sleep"
            else:
                server = ntrip.get(key)
                connected = _as_bool(server.get("connected")) if isinstance(server, dict) else (
                    _as_bool(server) if server is not None else _as_bool(payload.get("ntrip_connected"), device.ntrip_connected))
                if switch.can_push and connected:
                    state = "streaming"
                elif switch.on_demand:
                    state = "starting" if switch.active else "sleep"
        result[key] = {"enabled": switch.enabled, "state": state,
                       "mountpoint": cfg.get(f"mountpoint{sid}") or ""}
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
    """Send a separate snapshot to each affected caster."""
    try:
        raw_cfg = getattr(device, "service_config", None)
        if isinstance(raw_cfg, str):
            import json
            try:
                raw_cfg = json.loads(raw_cfg)
            except ValueError:
                raw_cfg = None
        cfg = raw_cfg if isinstance(raw_cfg, dict) else {}

        for server_id in (1, 2):
            change = status_change[f"server{server_id}"]
            if not change["changed"]:
                continue
            current = change["new"]
            host = str(cfg.get(f"serverhost{server_id}") or "").strip()
            port = cfg.get(f"port{server_id}")
            target = f"{host}:{port}" if host and port and ":" not in host else host
            mountpoint = str(cfg.get(f"mountpoint{server_id}") or "").strip()
            if target and mountpoint:
                payload = {
                    "mountpoint": mountpoint,
                    "state": current.get("state", "offline"),
                    "enabled": current.get("enabled", False),
                }
                schedule_background(_send_status_to_caster(target, payload, f"S{server_id}", serial))

    except Exception as e:
        logger.error(f"[CASTER SYNC] Error notifying casters for '{serial}': {e}")


async def _send_status_to_caster(caster_host: str, status_payload: dict, server_label: str, serial: str) -> None:
    """
    Gửi HTTPS POST status to Caster.
    endpoint: POST https://caster_host/api/station-status
    
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
        from urllib.parse import urlsplit

        configured_url = str(caster_host or "").strip()
        parsed = urlsplit(configured_url if "://" in configured_url else f"https://{configured_url}")
        hostname = (parsed.hostname or "").lower()
        is_allowed_domain = hostname == "aitogy.com.vn" or hostname.endswith(".aitogy.com.vn") or hostname == "aitogy.click" or hostname.endswith(".aitogy.click")

        if not is_allowed_domain:
            # logger.debug(f"[CASTER SYNC] Skipping unauthorized domain: {caster_host}")
            return

        if parsed.scheme != "https":
            logger.warning("Skipping caster status sync because HTTPS is required")
            return
        url = parsed._replace(path="/api/station-status", query="", fragment="").geturl()

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
            for attempt in range(3):
                try:
                    await asyncio.wait_for(asyncio.to_thread(_post_status_sync), timeout=4.0)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0.25)


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
    Nhận trạng thái đã được chấp nhận từ MQTT, WebSocket hoặc hết hạn heartbeat.
    Gửi thay đổi theo từng đầu ra; gửi lại định kỳ để caster phục hồi sau lỗi kết nối.
    
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

        # Gửi khi trạng thái đổi hoặc đã đến hạn gửi lại.
        refresh = time.monotonic() - _status_last_notify.get(serial, float("-inf")) >= STATUS_REFRESH_SECONDS
        if old_status != new_status or refresh:
            # Detect what changed
            status_change = _status_changed({} if refresh else old_status, new_status)
            _status_last_notify[serial] = time.monotonic()

            # Update cache
            device_status_cache[serial] = new_status

            # Notify casters (non-blocking)
            await _notify_casters_on_status_change(serial, device, status_change)


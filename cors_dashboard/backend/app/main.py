# ==============================================================================
# == backend/app/main.py                ==
# ==============================================================================

import logging
import uuid
import json
import asyncio
import sys
import traceback
import base64
import io
import os
import time
import csv
import psutil
import secrets
import ctypes
from pathlib import Path
from collections import deque
from contextlib import asynccontextmanager
from typing import Optional, List, Any, Set, Dict
from dotenv import dotenv_values, set_key

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect, Depends, 
    HTTPException, Request, Query, status, Body
)
from fastapi.responses import PlainTextResponse
# === NTRIP WAKE STATION HTTP ENDPOINT ===
@app.post("/api/ntrip/wake-station", response_class=PlainTextResponse)
async def ntrip_wake_station(
    payload: dict = Body(...)
):
    """
    Endpoint cho NTRIP gửi lệnh bật trạm qua HTTP.
    Yêu cầu JSON: {"mountpoint": "...", "client_instance_id": "...", "ttl_seconds": 90}
    """
    from .mqtt import handle_ntrip_mqtt_command
    import json
    # Giả lập message như MQTT gửi vào
    topic = "ntrip/rtcm/start"
    try:
        # Chỉ lấy các trường cần thiết
        data = {
            "mountpoint": payload.get("mountpoint"),
            "client_instance_id": payload.get("client_instance_id"),
            "ttl_seconds": int(payload.get("ttl_seconds", 90)),
        }
        # Chuyển thành bytes như MQTT
        await handle_ntrip_mqtt_command(topic, json.dumps(data).encode("utf-8"))
        return "OK"
    except Exception as e:
        return PlainTextResponse(f"ERROR: {e}", status_code=500)
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select, update, delete, func
from starlette.middleware.base import BaseHTTPMiddleware
from .monitoring import health_monitor, CircuitBreaker, global_rate_limiter
from . import crud, models, schemas, command_builder, auth
from starlette.responses import Response
from .database import (
    engine, auth_engine, get_db, get_auth_db, 
    AuthBase, AsyncAuthSession, AsyncSessionLocal, settings,
    device_session_scope, auth_session_scope
)

from .websocket import manager as ui_manager, visitor_manager
from .pi_websocket import pi_manager
from . import mqtt as mqtt_handler
from .mqtt import mqtt_client
from . import license_manager
from . import nmea_parser
from . import models, auth, crud
from .security import rate_limiter
from .monitoring import health_monitor
from .caster_client import CasterClient
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log', encoding='utf-8'), 
        logging.StreamHandler()
    ]
)


def disable_windows_quick_edit_mode() -> None:
    """Prevent console freeze when selecting text in Windows terminal windows."""
    if sys.platform != "win32":
        return

    try:
        kernel32 = ctypes.windll.kernel32
        STD_INPUT_HANDLE = -10
        ENABLE_QUICK_EDIT_MODE = 0x0040
        ENABLE_EXTENDED_FLAGS = 0x0080

        h_stdin = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        if h_stdin in (0, -1):
            return

        mode = ctypes.c_uint()
        if not kernel32.GetConsoleMode(h_stdin, ctypes.byref(mode)):
            return

        new_mode = (mode.value | ENABLE_EXTENDED_FLAGS) & ~ENABLE_QUICK_EDIT_MODE
        kernel32.SetConsoleMode(h_stdin, new_mode)
    except Exception:
        # Non-fatal: backend should continue even if console mode cannot be changed.
        pass


disable_windows_quick_edit_mode()

BATCH_SIZE = 20  
BATCH_TIMEOUT = 2.0 
UI_BROADCAST_QUEUE_MAXSIZE = int(os.getenv("UI_BROADCAST_QUEUE_MAXSIZE", "10000"))
PI_STATUS_INGEST_QUEUE_MAXSIZE = int(os.getenv("PI_STATUS_INGEST_QUEUE_MAXSIZE", "20000"))
PI_INGEST_WORKER_COUNT = int(os.getenv("PI_INGEST_WORKER_COUNT", "4"))
DEVICE_UPDATE_BUFFER_MAXSIZE = int(os.getenv("DEVICE_UPDATE_BUFFER_MAXSIZE", "50000"))
STATUS_HEAVY_FIELD_MAX_BYTES = int(os.getenv("STATUS_HEAVY_FIELD_MAX_BYTES", "4096"))
STATUS_ALLOW_HEAVY_FIELDS = str(os.getenv("STATUS_ALLOW_HEAVY_FIELDS", "1")).strip().lower() in ("1", "true", "yes", "on")
STATUS_PAYLOAD_COMPACT_ENABLED = str(os.getenv("STATUS_PAYLOAD_COMPACT_ENABLED", "0")).strip().lower() in ("1", "true", "yes", "on")

# Adaptive buffer flush tuning
BUFFER_FLUSH_INTERVAL_MIN = float(os.getenv("BUFFER_FLUSH_INTERVAL_MIN", "0.5"))  # Early flush if buffer warm
BUFFER_FLUSH_INTERVAL_MAX = float(os.getenv("BUFFER_FLUSH_INTERVAL_MAX", "2.0"))  # Normal flush timeout
BUFFER_SIZE_THRESHOLD_HIGH = int(os.getenv("BUFFER_SIZE_THRESHOLD_HIGH", "30000"))  # Flush if buffer > 60% full
BUFFER_SIZE_THRESHOLD_NORMAL = int(os.getenv("BUFFER_SIZE_THRESHOLD_NORMAL", "15000"))  # Switch to adaptive mode

mqtt_circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
db_circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)

logger = logging.getLogger(__name__)
device_update_buffer = deque(maxlen=DEVICE_UPDATE_BUFFER_MAXSIZE)
device_update_buffer_lock = asyncio.Lock()

ENV_FILE_PATH = Path(__file__).resolve().parent.parent / ".env"


def _read_caster_config_from_env() -> tuple[Optional[str], Optional[int]]:
    env_values = dotenv_values(ENV_FILE_PATH)
    raw_host = env_values.get("CASTER_HOST")
    raw_port = env_values.get("CASTER_PORT")

    host = str(raw_host).strip() if raw_host is not None and str(raw_host).strip() else None
    port = None
    if raw_port is not None and str(raw_port).strip():
        try:
            port = int(str(raw_port).strip())
        except ValueError:
            logger.warning("Invalid CASTER_PORT in .env: %s", raw_port)

    return host, port


def _persist_caster_config_to_env(host: str, port: int) -> None:
    set_key(str(ENV_FILE_PATH), "CASTER_HOST", host, quote_mode="never")
    set_key(str(ENV_FILE_PATH), "CASTER_PORT", str(port), quote_mode="never")

    # Keep runtime settings consistent without requiring process restart.
    os.environ["CASTER_HOST"] = host
    os.environ["CASTER_PORT"] = str(port)
    settings.CASTER_HOST = host
    settings.CASTER_PORT = port

# Metrics for buffer performance tracking
buffer_metrics = {
    "flush_count": 0,
    "total_updates_flushed": 0,
    "total_duplicates_deduped": 0,
    "ui_queue_drop_count": 0,
    "pi_ingest_queue_drop_count": 0,
    "status_payload_invalid_count": 0,
    "status_payload_compacted_count": 0,
    "last_flush_time": time.time(),
    "last_buffer_size": 0,
}
ui_broadcast_queue: asyncio.Queue = asyncio.Queue(maxsize=UI_BROADCAST_QUEUE_MAXSIZE)
pi_status_ingest_queue: asyncio.Queue = asyncio.Queue(maxsize=PI_STATUS_INGEST_QUEUE_MAXSIZE)


def _safe_json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str))
    except Exception:
        return len(str(value))


def _compact_status_payload(payload: Any) -> Optional[dict]:
    """
    Keep only fields that DB/UI pipeline consumes and trim oversized heavy fields.
    This reduces RAM pressure when status payloads contain very large nested objects.
    """
    if not isinstance(payload, dict):
        buffer_metrics["status_payload_invalid_count"] += 1
        return None
    if not payload.get("serial"):
        buffer_metrics["status_payload_invalid_count"] += 1
        return None
    if not STATUS_PAYLOAD_COMPACT_ENABLED:
        return payload

    allowed_keys = (
        "serial",
        "name",
        "status",
        "timestamp",
        "bps",
        "ntrip_status",
        "ntrip_connected",
        "detected_chip_type",
        "base_config",
        "base_coords",
        "auto_base_progress",
        "service_config",
        "system_info",
        "is_provisioned",
        "is_synced",
        "ntrip_stats",
    )
    compact = {key: payload[key] for key in allowed_keys if key in payload}

    if not STATUS_ALLOW_HEAVY_FIELDS:
        compact.pop("base_config", None)
        compact.pop("service_config", None)
        compact.pop("system_info", None)
        buffer_metrics["status_payload_compacted_count"] += 1
        return compact

    for heavy_key in ("base_config", "service_config", "system_info"):
        if heavy_key not in compact:
            continue
        if _safe_json_size(compact[heavy_key]) > STATUS_HEAVY_FIELD_MAX_BYTES:
            compact.pop(heavy_key, None)

    buffer_metrics["status_payload_compacted_count"] += 1
    return compact


async def enqueue_ui_broadcast(message: dict) -> None:
    try:
        ui_broadcast_queue.put_nowait(message)
    except asyncio.QueueFull:
        # Backpressure protection: drop oldest queued broadcast and keep latest.
        try:
            _ = ui_broadcast_queue.get_nowait()
            ui_broadcast_queue.task_done()
            buffer_metrics["ui_queue_drop_count"] += 1
        except Exception:
            pass
        try:
            ui_broadcast_queue.put_nowait(message)
        except Exception:
            pass


async def ui_broadcast_worker_task():
    while True:
        message = await ui_broadcast_queue.get()
        try:
            await ui_manager.broadcast(message)
        except Exception as e:
            logger.warning(f"UI broadcast worker error: {e}")
        finally:
            ui_broadcast_queue.task_done()


async def pi_status_ingest_worker_task():
    while True:
        payload = await pi_status_ingest_queue.get()
        try:
            await enqueue_ui_broadcast({"type": "status_update", "data": payload})
            async with device_update_buffer_lock:
                device_update_buffer.append(payload)
        except Exception as e:
            logger.warning(f"PI status ingest worker error: {e}")
        finally:
            pi_status_ingest_queue.task_done()

rtcm_session_lock = asyncio.Lock()
RTCM_MIN_TTL = 30
RTCM_MAX_TTL = 600
RTCM_NTRIP_CLIENTTABLE_CHECK_INTERVAL = float(os.getenv("RTCM_NTRIP_CLIENTTABLE_CHECK_INTERVAL", "5"))
RTCM_KEEPALIVE_MISS_THRESHOLD = max(1, int(os.getenv("RTCM_KEEPALIVE_MISS_THRESHOLD", "2")))
RTCM_KEEPALIVE_FETCH_GRACE_SECONDS = max(15, int(os.getenv("RTCM_KEEPALIVE_FETCH_GRACE_SECONDS", "45")))
RTCM_KEEPALIVE_DEBUG = str(
    os.getenv(
        "RTCM_KEEPALIVE_DEBUG",
        str(dotenv_values(ENV_FILE_PATH).get("RTCM_KEEPALIVE_DEBUG", "0")),
    )
).strip().lower() in ("1", "true", "yes", "on")
rtcm_keepalive_miss_counts: Dict[str, int] = {}


def _sanitize_ttl(ttl_seconds: int) -> int:
    try:
        ttl = int(ttl_seconds)
    except (TypeError, ValueError):
        ttl = 90
    return max(RTCM_MIN_TTL, min(RTCM_MAX_TTL, ttl))


def _extract_station_mountpoints(device: models.Device) -> List[str]:
    cfg = device.service_config or {}
    result: List[str] = []
    if cfg.get("server1_enabled") and cfg.get("mountpoint1"):
        result.append(str(cfg.get("mountpoint1")))
    if cfg.get("server2_enabled") and cfg.get("mountpoint2"):
        result.append(str(cfg.get("mountpoint2")))
    return result


def _normalize_mountpoint(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned.lower() if cleaned else None


def _resolve_requested_mountpoint(available: List[str], requested: Optional[str]) -> str:
    if not available:
        raise HTTPException(status_code=400, detail="Station has no enabled mountpoint in service_config")

    requested_norm = _normalize_mountpoint(requested)
    if requested_norm is None:
        return available[0]

    for mp in available:
        if _normalize_mountpoint(mp) == requested_norm:
            return mp

    raise HTTPException(
        status_code=400,
        detail=f"Requested mountpoint '{requested}' is not enabled for this station",
    )


def _extract_active_mountpoint(device: Optional[models.Device]) -> Optional[str]:
    if not device:
        return None
    cfg = device.service_config or {}
    active_mp = cfg.get("active_mountpoint")
    return str(active_mp) if active_mp else None


def _build_rtcm_client_id(current_user: models.User, client_instance_id: Optional[str]) -> str:
    base_id = f"user:{current_user.id}"
    if not client_instance_id:
        return base_id

    clean = "".join(ch for ch in str(client_instance_id) if ch.isalnum() or ch in "-_.")[:40]
    if not clean:
        return base_id
    return f"{base_id}:{clean}"


def _resolve_stream_status(device: Optional[models.Device], force_starting: bool = False) -> str:
    if force_starting:
        return "starting"
    if not device:
        return "offline"

    if bool(getattr(device, "ntrip_connected", False)):
        return "streaming"

    cfg = (getattr(device, "service_config", None) or {})
    if cfg.get("stream_on_demand") and not cfg.get("stream_active", True):
        return "offline"
    return "starting"


async def _set_station_stream(serial: str, active: bool, mountpoint: Optional[str] = None) -> None:
    payload = {"active": bool(active)}
    if mountpoint:
        payload["mountpoint"] = str(mountpoint)

    await send_command_to_pi(
        serial,
        {
            "command": "SET_RTCM_STREAM_ACTIVE",
            "payload": payload,
        },
    )

    # Keep DB service_config in sync with control-plane intent to avoid stale
    # stream_active/active_mountpoint values until the next agent status heartbeat.
    try:
        async with device_session_scope() as db:
            device = await crud.get_device_by_serial(db, serial)
            if not device:
                return

            cfg = dict(device.service_config or {})
            cfg["stream_on_demand"] = True
            cfg["stream_active"] = bool(active)

            if bool(active):
                if mountpoint:
                    cfg["active_mountpoint"] = str(mountpoint)
            else:
                cfg.pop("active_mountpoint", None)
                # Reflect immediate off state in top-level flags.
                device.ntrip_connected = False
                device.bps = 0

            device.service_config = cfg
            await db.commit()
    except Exception as e:
        logger.warning(f"[RTCM] Failed to persist stream state for '{serial}': {e}")


async def _count_active_station_sessions(db: AsyncSession, serial: str, now_ts: Optional[int] = None) -> int:
    now_ts = now_ts or int(time.time())
    result = await db.execute(
        select(func.count(models.RTCMStreamSession.session_id)).where(
            models.RTCMStreamSession.serial == serial,
            models.RTCMStreamSession.expires_at > now_ts,
        )
    )
    return int(result.scalar() or 0)


async def _count_active_mountpoint_sessions(
    db: AsyncSession,
    serial: str,
    mountpoint: str,
    now_ts: Optional[int] = None,
) -> int:
    now_ts = now_ts or int(time.time())
    mountpoint_norm = _normalize_mountpoint(mountpoint)
    if not mountpoint_norm:
        return 0

    result = await db.execute(
        select(func.count(models.RTCMStreamSession.session_id)).where(
            models.RTCMStreamSession.serial == serial,
            func.lower(models.RTCMStreamSession.mountpoint) == mountpoint_norm,
            models.RTCMStreamSession.expires_at > now_ts,
        )
    )
    return int(result.scalar() or 0)


async def _fetch_active_station_client_ids(db: AsyncSession, serial: str, now_ts: Optional[int] = None) -> List[str]:
    now_ts = now_ts or int(time.time())
    result = await db.execute(
        select(models.RTCMStreamSession.client_id).where(
            models.RTCMStreamSession.serial == serial,
            models.RTCMStreamSession.expires_at > now_ts,
        )
    )
    return sorted(set(str(row[0]) for row in result.all() if row and row[0]))


def _build_ntrip_mqtt_client_id(client_instance_id: Optional[str]) -> str:
    if not client_instance_id:
        return "ntrip-mqtt:anonymous"
    clean = "".join(ch for ch in str(client_instance_id) if ch.isalnum() or ch in "-_.")[:64]
    if not clean:
        clean = "anonymous"
    return f"ntrip-mqtt:{clean}"


def _extract_http10_path_segments(raw_cmd: Optional[str], endpoint: str) -> Optional[List[str]]:
    if not raw_cmd:
        return None
    text = str(raw_cmd).strip().replace("\r", "").replace("\n", "")
    marker = f"GET /{endpoint} HTTP/1.0/"
    idx = text.find(marker)
    if idx < 0:
        return None
    suffix = text[idx + len(marker):].strip().strip("/")
    if not suffix:
        return []
    return [seg.strip() for seg in suffix.split("/")]


def _extract_legacy_mountpoint(data: dict) -> Optional[str]:
    clienttable_cmd = data.get("clienttable_cmd") or data.get("clienttable")
    segs = _extract_http10_path_segments(clienttable_cmd, "clienttable")
    if segs and len(segs) >= 2 and segs[1]:
        return str(segs[1]).strip()

    genuser_cmd = data.get("genuser_cmd") or data.get("genuser")
    segs = _extract_http10_path_segments(genuser_cmd, "genuser")
    if segs and len(segs) >= 4 and segs[3]:
        # genuser mountpoint field can contain comma-separated MPs; use first non-empty.
        first_mp = next((x.strip() for x in str(segs[3]).split(",") if x.strip()), None)
        return first_mp
    return None


def _extract_legacy_client_instance_id(data: dict) -> Optional[str]:
    direct = data.get("client_instance_id") or data.get("receiver_id") or data.get("identifier")
    if direct:
        return str(direct).strip()

    genuser_cmd = data.get("genuser_cmd") or data.get("genuser")
    segs = _extract_http10_path_segments(genuser_cmd, "genuser")
    if segs:
        # HTTP/1.0/<identifier>/<username>/<password>/...
        if len(segs) >= 1 and segs[0]:
            return str(segs[0]).strip()
        if len(segs) >= 2 and segs[1]:
            return str(segs[1]).strip()
    return None


async def _ntrip_mqtt_start(serial: str, mountpoint: str, client_instance_id: str, ttl_seconds: int = 90):
    ttl = _sanitize_ttl(ttl_seconds)
    now = int(time.time())

    async with device_session_scope() as db:
        device = await crud.get_device_by_serial(db, serial)
        if not device:
            raise ValueError(f"Station '{serial}' not found")

        mountpoints = _extract_station_mountpoints(device)
        selected_mountpoint = _resolve_requested_mountpoint(mountpoints, mountpoint)

        session_id = uuid.uuid4().hex
        client_id = _build_ntrip_mqtt_client_id(client_instance_id)
        expires_at = now + ttl

        should_activate = False
        active_mountpoint_norm: Optional[str] = None

        async with rtcm_session_lock:
            await db.execute(
                delete(models.RTCMStreamSession).where(
                    models.RTCMStreamSession.serial == serial,
                    models.RTCMStreamSession.expires_at <= now,
                )
            )

            active_before_same_mp = await _count_active_mountpoint_sessions(db, serial, selected_mountpoint, now)
            should_activate = active_before_same_mp == 0

            active_before_station = await _count_active_station_sessions(db, serial, now)

            if active_before_station > 0:
                # Backend is source-of-truth for stream mountpoint while sessions are active.
                # Do not let subsequent START requests switch mountpoint mid-stream.
                active_mountpoint_norm = _normalize_mountpoint(_extract_active_mountpoint(device))
                requested_norm = _normalize_mountpoint(selected_mountpoint)
                if active_mountpoint_norm and requested_norm and active_mountpoint_norm != requested_norm:
                    raise ValueError(
                        f"Station '{serial}' is already active on mountpoint '{active_mountpoint_norm}', "
                        f"cannot switch to '{requested_norm}' until all active sessions expire"
                    )

            db.add(
                models.RTCMStreamSession(
                    session_id=session_id,
                    serial=serial,
                    mountpoint=str(selected_mountpoint),
                    client_id=client_id,
                    expires_at=expires_at,
                    created_at=now,
                    last_heartbeat_at=now,
                )
            )
            await db.commit()

        # Only first active session triggers an agent command.
        if should_activate or not getattr(device, 'ntrip_connected', False):
            await _set_station_stream(serial, active=True, mountpoint=selected_mountpoint)


def _device_has_enabled_mountpoint(device: models.Device, mountpoint: str) -> bool:
    requested = _normalize_mountpoint(mountpoint)
    if not requested:
        return False

    cfg = device.service_config or {}
    s1_ok = bool(cfg.get("server1_enabled")) and _normalize_mountpoint(cfg.get("mountpoint1")) == requested
    s2_ok = bool(cfg.get("server2_enabled")) and _normalize_mountpoint(cfg.get("mountpoint2")) == requested
    return s1_ok or s2_ok


async def _resolve_station_serial_by_mountpoint(db: AsyncSession, mountpoint: str) -> str:
    requested = _normalize_mountpoint(mountpoint)
    if not requested:
        raise ValueError("mountpoint is required")

    rs = await db.execute(select(models.Device))
    devices = [d for d in rs.scalars().all() if _device_has_enabled_mountpoint(d, mountpoint)]

    if not devices:
        raise ValueError(f"No station found for mountpoint '{mountpoint}'")

    if len(devices) == 1:
        return str(devices[0].serial)

    online = [d for d in devices if str(getattr(d, "status", "")).lower() == "online"]
    if len(online) == 1:
        return str(online[0].serial)

    serials = ",".join(str(d.serial) for d in devices)
    raise ValueError(f"Mountpoint '{mountpoint}' maps to multiple stations: {serials}")


async def _resolve_stop_serial(
    db: AsyncSession,
    mountpoint: Optional[str],
    client_instance_id: str,
) -> str:
    client_id = _build_ntrip_mqtt_client_id(client_instance_id)
    now = int(time.time())
    active_rs = await db.execute(
        select(models.RTCMStreamSession.serial)
        .where(
            models.RTCMStreamSession.client_id == client_id,
            models.RTCMStreamSession.expires_at > now,
        )
        .distinct()
    )
    serials = sorted(set(str(r[0]) for r in active_rs.all() if r and r[0]))
    if len(serials) == 1:
        return serials[0]
    if len(serials) > 1:
        raise ValueError("client_instance_id has active sessions on multiple stations; provide mountpoint")

    if mountpoint:
        return await _resolve_station_serial_by_mountpoint(db, mountpoint)

    raise ValueError("Cannot resolve station for stop; provide mountpoint")


async def _ntrip_mqtt_stop(serial: str, client_instance_id: str):
    now = int(time.time())
    client_id = _build_ntrip_mqtt_client_id(client_instance_id)
    should_deactivate = False
    active_mountpoint_norm: Optional[str] = None

    async with device_session_scope() as db:
        async with rtcm_session_lock:
            await db.execute(
                delete(models.RTCMStreamSession).where(models.RTCMStreamSession.expires_at <= now)
            )

            active_sessions_rs = await db.execute(
                select(models.RTCMStreamSession.session_id, models.RTCMStreamSession.mountpoint).where(
                    models.RTCMStreamSession.serial == serial,
                    models.RTCMStreamSession.client_id == client_id,
                    models.RTCMStreamSession.expires_at > now,
                )
            )
            active_rows = [(str(r[0]), str(r[1])) for r in active_sessions_rs.all() if r and r[0] and r[1]]
            active_session_ids = [row[0] for row in active_rows]

            if active_session_ids:
                await db.execute(
                    delete(models.RTCMStreamSession).where(
                        models.RTCMStreamSession.session_id.in_(active_session_ids)
                    )
                )

            await db.commit()

            device = await crud.get_device_by_serial(db, serial)
            active_mountpoint_norm = _normalize_mountpoint(_extract_active_mountpoint(device))

            if active_mountpoint_norm:
                remaining_for_active_mp = await _count_active_mountpoint_sessions(
                    db,
                    serial,
                    active_mountpoint_norm,
                    now,
                )
                should_deactivate = remaining_for_active_mp == 0

    if should_deactivate:
        await _set_station_stream(serial, active=False)


async def handle_ntrip_mqtt_command(topic: str, payload: bytes):
    try:
        data = json.loads(payload.decode("utf-8"))
    except Exception as e:
        logger.warning(f"[NTRIP MQTT] Invalid JSON on topic '{topic}': {e}")
        return

    if not topic.endswith("/start"):
        logger.warning(f"[NTRIP MQTT] Unsupported topic '{topic}'. Use ntrip/rtcm/start only")
        return

    serial = str(data.get("serial", "")).strip()
    mountpoint = str(data.get("mountpoint", "")).strip()
    if not mountpoint:
        mountpoint = _extract_legacy_mountpoint(data)
    client_instance_id = _extract_legacy_client_instance_id(data) or ""

    if serial:
        logger.warning("[NTRIP MQTT] 'serial' is not allowed. Use mountpoint-only payload")
        return

    if not client_instance_id:
        logger.warning("[NTRIP MQTT] Missing required field: client_instance_id/identifier")
        return

    try:
        if not mountpoint:
            logger.warning("[NTRIP MQTT] Missing 'mountpoint' for start command")
            return

        async with device_session_scope() as db:
            resolved_serial = await _resolve_station_serial_by_mountpoint(db, mountpoint)

        ttl_seconds = int(data.get("ttl_seconds", 90))
        await _ntrip_mqtt_start(
            serial=resolved_serial,
            mountpoint=mountpoint,
            client_instance_id=client_instance_id,
            ttl_seconds=ttl_seconds,
        )
        logger.info(f"[NTRIP MQTT] start accepted serial={resolved_serial} mountpoint={mountpoint} client={client_instance_id}")
    except Exception as e:
        logger.warning(f"[NTRIP MQTT] Start command failed mountpoint={mountpoint}: {e}")


async def rtcm_ntrip_clienttable_keepalive_task():
    global rtcm_keepalive_miss_counts

    while True:
        await asyncio.sleep(RTCM_NTRIP_CLIENTTABLE_CHECK_INTERVAL)
        now = int(time.time())

        try:
            async with rtcm_session_lock:
                async with device_session_scope() as db:
                    rs = await db.execute(
                        select(
                            models.RTCMStreamSession.session_id,
                            models.RTCMStreamSession.client_id,
                            models.RTCMStreamSession.serial,
                            models.RTCMStreamSession.mountpoint,
                        )
                        .where(
                            models.RTCMStreamSession.expires_at > now,
                            models.RTCMStreamSession.client_id.like("ntrip-mqtt:%"),
                        )
                    )
                    active_rows = [
                        (str(r[0]), str(r[1]), str(r[2]), str(r[3]))
                        for r in rs.all()
                        if r and r[0] and r[1] and r[2] and r[3]
                    ]

            if not active_rows:
                rtcm_keepalive_miss_counts.clear()
                continue

            requested_mountpoints = sorted(
                {
                    mp
                    for _, _, _, mp in active_rows
                    if _normalize_mountpoint(mp)
                }
            )
            requested_mountpoints_norm = sorted({_normalize_mountpoint(mp) for mp in requested_mountpoints if _normalize_mountpoint(mp)})
            if RTCM_KEEPALIVE_DEBUG:
                logger.warning(
                    f"[RTCM DEBUG] keepalive requested_mountpoints_raw={requested_mountpoints} "
                    f"requested_mountpoints_norm={requested_mountpoints_norm} active_sessions={len(active_rows)}"
                )

            clienttable: Optional[List[dict]] = None
            try:
                async with auth_session_scope() as auth_db:
                    caster_client = await get_caster_client(auth_db)
                    merged_clienttable: List[dict] = []
                    for requested_mp in requested_mountpoints:
                        rows = await caster_client.get_clienttable(mountpoint=requested_mp)
                        if rows:
                            merged_clienttable.extend(rows)
                    clienttable = merged_clienttable
            except Exception as e:
                logger.warning(f"[RTCM] Failed to fetch clienttable, applying grace keepalive: {e}")

            if clienttable is None:
                grace_expires_at = now + RTCM_KEEPALIVE_FETCH_GRACE_SECONDS
                touched = 0
                active_session_ids = {sid for sid, _, _, _ in active_rows}
                stale_ids = [sid for sid in rtcm_keepalive_miss_counts.keys() if sid not in active_session_ids]
                for sid in stale_ids:
                    rtcm_keepalive_miss_counts.pop(sid, None)

                async with rtcm_session_lock:
                    async with device_session_scope() as db:
                        sessions_rs = await db.execute(
                            select(models.RTCMStreamSession).where(
                                models.RTCMStreamSession.session_id.in_(list(active_session_ids))
                            )
                        )
                        sessions_by_id = {
                            str(s.session_id): s for s in sessions_rs.scalars().all() if s and s.session_id
                        }

                        for session_id, _, _, _ in active_rows:
                            session = sessions_by_id.get(session_id)
                            if not session:
                                continue
                            if int(session.expires_at or 0) < grace_expires_at:
                                session.expires_at = grace_expires_at
                                session.last_heartbeat_at = now
                                touched += 1
                        if touched > 0:
                            await db.commit()
                continue

            # get_clienttable() returns [] on failures too, so guard with one short retry to avoid false misses.
            if len(clienttable) == 0:
                try:
                    await asyncio.sleep(1.0)
                    async with auth_session_scope() as auth_db:
                        caster_client = await get_caster_client(auth_db)
                        retried_merged: List[dict] = []
                        for requested_mp in requested_mountpoints:
                            retried_rows = await caster_client.get_clienttable(mountpoint=requested_mp)
                            if retried_rows:
                                retried_merged.extend(retried_rows)
                        if retried_merged:
                            clienttable = retried_merged
                except Exception:
                    pass

            active_mountpoints: Set[str] = set()
            for item in clienttable:
                mp = _normalize_mountpoint(item.get("mountpoint"))
                if mp:
                    active_mountpoints.add(mp)

            if RTCM_KEEPALIVE_DEBUG:
                logger.warning(
                    f"[RTCM DEBUG] keepalive active_mountpoints={sorted(active_mountpoints)[:40]} "
                    f"clienttable_rows={len(clienttable)}"
                )

            keepalive_to = now + 90
            updated = 0
            expired_on_miss = 0
            stations_to_deactivate = set()
            active_session_ids = {sid for sid, _, _, _ in active_rows}
            stale_ids = [sid for sid in rtcm_keepalive_miss_counts.keys() if sid not in active_session_ids]
            for sid in stale_ids:
                rtcm_keepalive_miss_counts.pop(sid, None)

            active_sessions_by_mountpoint: Dict[tuple[str, str], int] = {}
            for _, _, serial, mountpoint in active_rows:
                mp_norm = _normalize_mountpoint(mountpoint)
                if not mp_norm:
                    continue
                key = (serial, mp_norm)
                active_sessions_by_mountpoint[key] = active_sessions_by_mountpoint.get(key, 0) + 1

            async with rtcm_session_lock:
                async with device_session_scope() as db:
                    sessions_rs = await db.execute(
                        select(models.RTCMStreamSession).where(
                            models.RTCMStreamSession.session_id.in_(list(active_session_ids))
                        )
                    )
                    sessions_by_id = {
                        str(s.session_id): s for s in sessions_rs.scalars().all() if s and s.session_id
                    }

                    for session_id, client_id, serial, mountpoint in active_rows:
                        session = sessions_by_id.get(session_id)
                        if not session:
                            rtcm_keepalive_miss_counts.pop(session_id, None)
                            continue

                        session_mp = _normalize_mountpoint(mountpoint)
                        if not session_mp or session_mp not in active_mountpoints:
                            miss_count = rtcm_keepalive_miss_counts.get(session_id, 0) + 1
                            rtcm_keepalive_miss_counts[session_id] = miss_count

                            if RTCM_KEEPALIVE_DEBUG:
                                logger.warning(
                                    f"[RTCM DEBUG] miss_detail serial={serial} client_id={client_id} "
                                    f"session_mountpoint_raw='{mountpoint}' session_mountpoint_norm='{session_mp}' "
                                    f"present_in_active={bool(session_mp and session_mp in active_mountpoints)}"
                                )

                            if miss_count < RTCM_KEEPALIVE_MISS_THRESHOLD:
                                logger.warning(
                                    f"[RTCM] Keepalive pending miss({miss_count}/{RTCM_KEEPALIVE_MISS_THRESHOLD}): "
                                    f"serial={serial} mountpoint={mountpoint} client_id={client_id} "
                                    f"active_mountpoints={len(active_mountpoints)}"
                                )
                                continue

                            # Missing for N consecutive checks -> expire session.
                            session.expires_at = now - 1
                            session.last_heartbeat_at = now
                            expired_on_miss += 1

                            key = (serial, session_mp)
                            remaining_for_mp = max(0, active_sessions_by_mountpoint.get(key, 0) - 1)
                            active_sessions_by_mountpoint[key] = remaining_for_mp

                            if remaining_for_mp == 0:
                                stations_to_deactivate.add(serial)
                            logger.warning(
                                f"[RTCM] Keepalive miss: serial={serial} mountpoint={mountpoint} "
                                f"client_id={client_id} miss_count={miss_count} "
                                f"active_mountpoints={len(active_mountpoints)}"
                            )
                            continue

                        rtcm_keepalive_miss_counts.pop(session_id, None)
                        session.expires_at = keepalive_to
                        session.last_heartbeat_at = now
                        updated += 1

                    if updated > 0 or expired_on_miss > 0 or stations_to_deactivate:
                        await db.commit()

            for serial in stations_to_deactivate:
                try:
                    await _set_station_stream(serial, active=False)
                    logger.info(f"[RTCM] Auto-deactivated station '{serial}' due to missing receiver in clienttable")
                except Exception as e:
                    logger.warning(f"[RTCM] Failed to auto-deactivate station '{serial}' after clienttable drop: {e}")

        except Exception as e:
            logger.warning(f"[RTCM] NTRIP clienttable keepalive check failed: {e}")


async def rtcm_session_reaper_task():
    while True:
        await asyncio.sleep(5)
        now = int(time.time())
        stations_to_deactivate = set()

        async with rtcm_session_lock:
            async with device_session_scope() as db:
                expired_serials_rs = await db.execute(
                    select(models.RTCMStreamSession.serial, models.RTCMStreamSession.mountpoint)
                    .where(models.RTCMStreamSession.expires_at <= now)
                    .distinct()
                )
                expired_pairs = [
                    (str(r[0]), str(r[1]))
                    for r in expired_serials_rs.all()
                    if r and r[0] and r[1]
                ]

                if expired_pairs:
                    await db.execute(
                        delete(models.RTCMStreamSession).where(models.RTCMStreamSession.expires_at <= now)
                    )
                    await db.commit()

                    for serial, expired_mountpoint in expired_pairs:
                        device = await crud.get_device_by_serial(db, serial)
                        active_mountpoint = _extract_active_mountpoint(device)
                        if _normalize_mountpoint(active_mountpoint) != _normalize_mountpoint(expired_mountpoint):
                            continue

                        active_count = await _count_active_mountpoint_sessions(db, serial, expired_mountpoint, now)
                        if active_count == 0:
                            stations_to_deactivate.add(serial)

                # Safety sweep: if stream is marked active but there are no sessions for active mountpoint,
                # force stream off. This covers manual DB cleanup and stale state scenarios.
                devices_rs = await db.execute(select(models.Device))
                for device in devices_rs.scalars().all():
                    cfg = device.service_config or {}
                    if not cfg.get("stream_on_demand"):
                        continue
                    if not bool(cfg.get("stream_active", False)):
                        continue

                    active_mountpoint = _extract_active_mountpoint(device)
                    if not active_mountpoint:
                        continue

                    remaining_for_active_mp = await _count_active_mountpoint_sessions(
                        db,
                        str(device.serial),
                        active_mountpoint,
                        now,
                    )
                    if remaining_for_active_mp == 0:
                        stations_to_deactivate.add(str(device.serial))

        for serial in stations_to_deactivate:
            try:
                await _set_station_stream(serial, active=False)
                logger.info(f"[RTCM] Auto-deactivated station '{serial}' due to expired sessions")
            except Exception as e:
                logger.warning(f"[RTCM] Failed to auto-deactivate station '{serial}': {e}")

async def log_system_event(
    db: AsyncSession,
    type: str,     # success, error, warning, info
    message: str,
    user_id: int = None,
    serial: str = None,
    ip: str = None
):
    """HÃ m helper để ghi log vÃ o DB từ bất kỳ đâu"""
    icon_map = {
        "success": "âœ…",
        "error": "âŒ",
        "warning": "âš ï¸",
        "info": "â„¹ï¸"
    }
    try:
        log_entry = schemas.ActivityLogCreate(
            timestamp=int(time.time()),
            type=type,
            message=message,
            icon=icon_map.get(type, "ðŸ“"),
            user_id=user_id,
            device_serial=serial,
            ip_address=ip
        )
        await crud.create_activity_log(db, log_entry)
        
        await ui_manager.broadcast({
            "type": "activity_log_event",
            "data": {
                "type": type,
                "message": message,
                "icon": icon_map.get(type, "ðŸ“"),
                "timestamp": int(time.time())
            }
        })
    except Exception as e:
        logger.error(f"Failed to write activity log: {e}")

class IgnoreSocketErrorsFilter(logging.Filter):
    def filter(self, record):
        # Chặn các log warning của asyncio liên quan đến socket.send
        if "socket.send() raised exception" in record.getMessage():
            return False
        return True

# Áp dụng filter cho logger của asyncio
logging.getLogger("asyncio").addFilter(IgnoreSocketErrorsFilter())

class RequestIDFilter(logging.Filter):
    """Filter thêm request_id vÃ o mọi log"""
    def filter(self, record):
        record.request_id = getattr(record, 'request_id', 'N/A')
        return True

# Thêm filter vÃ o root logger
for handler in logging.root.handlers:
    handler.addFilter(RequestIDFilter())

# --- CẢI TIẾN 2: REQUEST ID MIDDLEWARE ---
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        
        # Ghi log mÃ  không cần extra, vì filter sẽ tự thêm vÃ o
        logger.info(f"--> {request.method} {request.url.path}")
        
        start_time = time.time()
        response = await call_next(request)
        process_time = (time.time() - start_time) * 1000
        
        response.headers['X-Request-ID'] = request_id
        logger.info(f"<-- {request.method} {request.url.path} - Completed in {process_time:.2f}ms, Status: {response.status_code}")
        
        return response

# === HEARTBEAT CHECKER (FIXED) ===
async def check_device_heartbeats_with_retry():
    """Heartbeat check với retry vÃ  backoff"""
    HEARTBEAT_TIMEOUT = 300 
    max_retries = 3
    
    while True:
        await asyncio.sleep(60) # Check mỗi 60s lÃ  đủ, không cần 30s
        
        for attempt in range(max_retries):
            try:
                async with device_session_scope() as db:
                    # Sử dụng circuit breaker
                    await db_circuit_breaker.call(
                        _do_heartbeat_check, db, HEARTBEAT_TIMEOUT
                    )
                break  # Success, thoát retry loop
            
            except Exception as e:
                logger.error(f"Heartbeat check failed (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                else:
                    health_monitor.record_error('heartbeat_check', str(e))

async def _do_heartbeat_check(db, timeout_threshold):
    """Logic thực tế của heartbeat check"""
    from sqlalchemy import select
    
    now = int(time.time())
    timeout_threshold = now - timeout_threshold
    
    result = await db.execute(
        select(models.Device).filter(
            models.Device.status == 'online',
            models.Device.timestamp < timeout_threshold
        )
    )
    timed_out_devices = result.scalars().all()
    
    if timed_out_devices:
        for device in timed_out_devices:
            device.status = 'offline'
            device.timestamp = now
            device.bps = 0
            device.ntrip_connected = False
            device.ntrip_status = {
                "server1": {"connected": False, "bps": 0},
                "server2": {"connected": False, "bps": 0}
            }
        
        await db.commit()
        logger.info(f"Set {len(timed_out_devices)} devices to offline")

async def check_device_heartbeats():
    """Heartbeat check với bulk update"""
    HEARTBEAT_TIMEOUT = 180
    
    while True:
        await asyncio.sleep(30)
        
        try:
            async with device_session_scope() as db:
                # Sử dụng single query thay vì loop
                now = int(time.time())
                timeout_threshold = now - HEARTBEAT_TIMEOUT
                
                # Tìm devices cần set offline
                result = await db.execute(
                    select(models.Device)
                    .filter(
                        models.Device.status == 'online',
                        models.Device.timestamp < timeout_threshold
                    )
                )
                timed_out_devices = result.scalars().all()
                
                if not timed_out_devices:
                    continue
                
                # Bulk update
                for device in timed_out_devices:
                    device.status = 'offline'
                    device.timestamp = now
                    device.bps = 0
                    device.ntrip_connected = False
                
                await db.commit()
                
                logging.info(f"Set {len(timed_out_devices)} devices to offline due to timeout")
                
                # Broadcast updates
                for device in timed_out_devices:
                    await ui_manager.broadcast({
                        "type": "status_update",
                        "data": schemas.Device.from_orm(device).model_dump()
                    })
        
        except Exception as e:
            logging.error(f"Error in heartbeat check: {e}", exc_info=True)

async def cleanup_rate_limiter():
    """Dọn dẹp rate limiter định kỳ"""
    while True:
        await asyncio.sleep(300)  # Mỗi 5 phút
        global_rate_limiter.cleanup()
        logger.debug("Rate limiter cleanup completed")

# === LIFESPAN ===
async def periodic_buffer_flush():
    """Adaptive buffer flush: faster when buffer is warm, slower when cold"""
    last_flush = time.monotonic()
    
    while True:
        # Calculate adaptive timeout based on buffer size
        buffer_size = len(device_update_buffer)
        
        if buffer_size > BUFFER_SIZE_THRESHOLD_HIGH:
            # Buffer very full: flush ASAP
            flush_timeout = BUFFER_FLUSH_INTERVAL_MIN
        elif buffer_size > BUFFER_SIZE_THRESHOLD_NORMAL:
            # Buffer warm: intermediate timeout
            flush_timeout = (BUFFER_FLUSH_INTERVAL_MIN + BUFFER_FLUSH_INTERVAL_MAX) / 2
        else:
            # Buffer cool: normal timeout
            flush_timeout = BUFFER_FLUSH_INTERVAL_MAX
        
        # Sleep with adaptive timeout
        await asyncio.sleep(flush_timeout)
        
        # Check if we should flush (either time-based or threshold-based)
        elapsed_since_last_flush = time.monotonic() - last_flush
        should_flush = (
            (elapsed_since_last_flush >= BUFFER_FLUSH_INTERVAL_MAX) or  # Always flush after max timeout
            (device_update_buffer and buffer_size > BUFFER_SIZE_THRESHOLD_NORMAL)  # Or if buffer warm
        )
        
        if should_flush and device_update_buffer:
            try:
                async with device_session_scope() as db:
                    await flush_device_buffer(db)
                    last_flush = time.monotonic()
            except Exception as e:
                logger.error(f"Flush task error: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context với proper error handling, khởi động MQTT vÃ  Rate Limiter.
    """
    logger.info("ðŸš€ Application starting...")
    
    # List để track tất cả background tasks
    tasks = []
    
    try:
        # ===================================================================
        # BƯỚC 1: INITIALIZE DATABASES
        # ===================================================================
        try:
            async with engine.begin() as conn:
                await conn.run_sync(models.Base.metadata.create_all)
            logger.info("âœ“ Device database initialized")
            
            async with auth_engine.begin() as conn:
                await conn.run_sync(AuthBase.metadata.create_all) 
            logger.info("âœ“ Auth database initialized")
        except Exception as e:
            logger.critical(f"Database initialization failed: {e}", exc_info=True)
            raise
        
        # ===================================================================
        # BƯỚC 2: CREATE DEFAULT ADMIN USER
        # ===================================================================
        try:
            async with auth_session_scope() as db_session:
                admin_user = await crud.get_user_by_username(db_session, username="admin")
                if not admin_user:
                    logger.warning("Không tìm thấy người dùng 'admin' mặc định. Đang tiến hÃ nh tạo...")
                    hashed_password = await auth.get_password_hash("Admin@123")
                    await crud.create_user(
                        db=db_session,
                        username="admin",
                        hashed_password=hashed_password,
                        role=auth.Role.ADMIN,
                        full_name="Administrator"
                    )
                    logger.info("âœ“ Đã tạo người dùng 'admin' mặc định thÃ nh công.")
        except Exception as e:
            logger.error(f"Failed to create default admin: {e}")
        
        # ===================================================================
        # BƯỚC 2.5: CLEANUP GHOST DEVICES (optional)
        # ===================================================================
        try:
            async with device_session_scope() as db:
                deleted_count = await crud.cleanup_ghost_devices(db)
                if deleted_count > 0:
                    logger.info(f"ðŸ—‘ï¸ Cleaned up {deleted_count} ghost devices on startup")
        except Exception as e:
            logger.warning(f"Ghost device cleanup failed (non-critical): {e}")
        
        # ===================================================================
        # BƯỚC 3: START MQTT
        # ===================================================================
        mqtt_handler.set_external_command_handler(handle_ntrip_mqtt_command)
        mqtt_handler.start_mqtt_loop()
        
        # ===================================================================
        # BƯỚC 4: START BACKGROUND TASKS
        # ===================================================================
        
        # Task 1: Check device heartbeats
        async def check_device_heartbeats_with_retry():
            """Heartbeat check với retry vÃ  backoff"""
            from .database import AsyncSessionLocal
            from . import schemas
            from .websocket import manager as ui_manager
            
            HEARTBEAT_TIMEOUT = 180
            max_retries = 3
            
            while True:
                await asyncio.sleep(30)
                
                for attempt in range(max_retries):
                    try:
                        async with device_session_scope() as db:
                            now = int(time.time())
                            timeout_threshold = now - HEARTBEAT_TIMEOUT
                            
                            result = await db.execute(
                                select(models.Device).filter(
                                    models.Device.status == 'online',
                                    models.Device.timestamp < timeout_threshold
                                )
                            )
                            timed_out_devices = result.scalars().all()
                            
                            if timed_out_devices:
                                for device in timed_out_devices:
                                    device.status = 'offline'
                                    device.timestamp = now
                                    device.bps = 0
                                    device.ntrip_connected = False
                                
                                await db.commit()
                                logger.info(f"Set {len(timed_out_devices)} devices to offline")
                                
                                # Broadcast updates
                                for device in timed_out_devices:
                                    await ui_manager.broadcast({
                                        "type": "status_update",
                                        "data": schemas.Device.from_orm(device).model_dump()
                                    })
                        
                        break  # Success, thoát retry loop
                    
                    except Exception as e:
                        logger.error(f"Heartbeat check failed (attempt {attempt+1}/{max_retries}): {e}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 ** attempt) 
        
        # Task 2: Global rate limiter cleanup (nếu có)
        async def global_rate_limiter_cleanup_task():
            """Dọn dẹp global rate limiter định kỳ"""
            from .monitoring import global_rate_limiter
            while True:
                await asyncio.sleep(300)  # Mỗi 5 phút
                try:
                    global_rate_limiter.cleanup()
                    logger.debug("Global rate limiter cleanup completed")
                except Exception as e:
                    logger.error(f"Global rate limiter cleanup error: {e}")

        # Task 3: Security rate limiter cleanup - THÊM MỚI
        async def security_rate_limiter_cleanup_task():
            """Dọn dẹp dữ liệu cũ trong security rate limiter"""
            from .security import rate_limiter
            while True:
                await asyncio.sleep(900)  # Mỗi 15 phút
                try:
                    rate_limiter.cleanup_stale_data()
                    logger.debug("Security rate limiter cleanup completed")
                except Exception as e:
                    logger.error(f"Security rate limiter cleanup error: {e}")
        
        # Tạo vÃ  track tất cả tasks
        tasks.append(asyncio.create_task(check_device_heartbeats_with_retry()))
        tasks.append(asyncio.create_task(global_rate_limiter_cleanup_task()))
        tasks.append(asyncio.create_task(security_rate_limiter_cleanup_task()))
        tasks.append(asyncio.create_task(periodic_buffer_flush()))
        tasks.append(asyncio.create_task(rtcm_session_reaper_task()))
        tasks.append(asyncio.create_task(rtcm_ntrip_clienttable_keepalive_task()))
        tasks.append(asyncio.create_task(ui_broadcast_worker_task()))
        for _ in range(max(1, PI_INGEST_WORKER_COUNT)):
            tasks.append(asyncio.create_task(pi_status_ingest_worker_task()))
        logger.info("âœ“ Background tasks started")
        logger.info("=" * 60)
        logger.info("ðŸŽ‰ Application startup complete!")
        logger.info("=" * 60)
        
        yield
        
    finally:
        logger.info("ðŸ›‘ Application shutting down...")
        
        # --- BƯỚC 1: DỪNG MQTT TRƯỚC (Ngắt nguồn dữ liệu vÃ o) ---
        try:
            mqtt_handler.stop_mqtt_loop()
            logger.info("âœ“ MQTT stopped")
            await asyncio.sleep(1.0) 
        except Exception as e:
            logger.error(f"Error stopping MQTT: {e}")
        
        # --- BƯỚC 2: HỦY CÁC BACKGROUND TASKS ---
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info(f"âœ“ Cancelled {len(tasks)} background tasks")
        
        # --- BƯỚC 3: ĐÓNG KẾT NỐI DATABASE (QUAN TRỌNG NHẤT) ---
        try:
            logger.info("Setting all devices to OFFLINE...")
            async with device_session_scope() as db:
                await db.execute(
                    update(models.Device)
                    .where(models.Device.status == 'online')
                    .values(
                        status='offline',
                        ntrip_connected=False,
                        bps=0
                    )
                )
                await db.commit()
            logger.info("âœ“ All devices marked as OFFLINE")
        except Exception as e:
            logger.error(f"Error setting devices offline: {e}")

        # 4. ĐÓNG KẾT NỐI DATABASE (Cuối cùng)
        try:
            logger.info("Closing database connections...")
            await engine.dispose()
            await auth_engine.dispose()
            logger.info("âœ“ Database connections closed gracefully")
        except Exception as e:
            logger.error(f"Error closing databases: {e}")
            
        logger.info("âœ… Shutdown sequence complete")

app = FastAPI(title="CORS Geodetic Backend", lifespan=lifespan, version="0.1.0", docs_url=None, openapi_url=None)
app.add_middleware(RequestIDMiddleware) 

# === COMMAND DISPATCHER ===
async def send_command_to_pi(serial: str, command: dict) -> dict:
    if mqtt_client and mqtt_client.is_connected():
        topic = f"pi/devices/{serial}/command"
        message = json.dumps(command)
        mqtt_handler.publish_message(topic, message)
        return {"status": "command_sent", "channel": "mqtt", "command": command.get('command')}

    logging.warning(f"MQTT down. Trying WebSocket for '{serial}'.")
    success = await pi_manager.send_personal_message(serial, command)
    if success:
        return {"status": "command_sent", "channel": "websocket", "command": command.get('command')}

    raise HTTPException(status_code=503, detail=f"Cannot send command to '{serial}'. Both MQTT and WebSocket are unavailable.")

# --- CASTER CLIENT FACTORY ---
async def get_caster_client(db: AsyncSession) -> CasterClient:
    """Helper để khởi tạo Caster Client từ SystemSettings"""
    system_settings = await crud.get_system_settings(db)

    env_host, env_port = _read_caster_config_from_env()
    # Ưu tiên .env (có thể vừa được cập nhật từ web), fallback runtime settings rồi DB.
    host = str(env_host or settings.CASTER_HOST or system_settings.get('caster_host', 'aitogy.com.vn'))
    port = int(env_port or settings.CASTER_PORT or system_settings.get('caster_port', 2101))
    if RTCM_KEEPALIVE_DEBUG:
        logger.warning(f"[RTCM DEBUG] Using caster host={host} port={port}")
    
    return CasterClient(host, port)

# === ðŸ†• WEBSOCKET THEO DÕI KHÁCH TRUY CẬP (PUBLIC) ===
@app.websocket("/ws/track-visitors")
async def track_visitors_endpoint(websocket: WebSocket):
    client_ip = websocket.client.host
    await visitor_manager.connect(websocket, client_ip)
    
    await ui_manager.broadcast({
        "type": "visitor_update",
        "count": visitor_manager.get_count(),
        "ips": list(visitor_manager.active_ips) # Gửi danh sách IP để Admin soi (tùy chọn)
    })
    
    try:
        while True:
            # Giữ kết nối sống, nhận ping pong
            await websocket.receive_text()
    except:
        # Khi ngắt kết nối
        visitor_manager.disconnect(websocket, client_ip)
        # ðŸ“¢ Báo lại cho Admin khi có người thoát
        await ui_manager.broadcast({
            "type": "visitor_update",
            "count": visitor_manager.get_count(),
            "ips": list(visitor_manager.active_ips)
        })

# === AUTHENTICATION ===
@app.post("/api/auth/login", response_model=schemas.Token)
async def login(
    login_data: schemas.LoginRequest, 
    request: Request,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db)  
):
    user = await crud.get_user_by_username(auth_db, username=login_data.username)
    user_role = user.role if user else None
    
    try:
        await rate_limiter.check_and_record(
            auth_db,
            request, 
            username=login_data.username,
            is_success=False,
            user_role=user_role
        )
    except HTTPException as e:
        raise e
    
    user = await crud.get_user_by_username(auth_db, username=login_data.username)
    
    if not user:
        await auth.verify_password(
            login_data.password, 
            auth.pwd_context.hash("dummy_password_12345")
        )
        logger.warning(
            f"âŒ Login failed: Unknown user '{login_data.username}' "
            f"from {request.client.host}"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    if not await auth.verify_password(login_data.password, user.hashed_password):
        logger.warning(
            f"âŒ Login failed: Wrong password for '{login_data.username}' "
            f"from {request.client.host}"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    if not user.is_active:
        logger.warning(
            f"âŒ Login failed: Inactive account '{login_data.username}'"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled"
        )
    
    logger.info(
        f"âœ… Successful login: '{user.username}' (role: {user.role}) "
        f"from {request.client.host}"
    )
    
    await rate_limiter.check_and_record(
        auth_db,  
        request, 
        username=user.username,
        is_success=True
    )
    
    access_token = auth.create_access_token(
        data={"sub": user.username, "role": user.role}
    )

    await log_system_event(
        db=devices_db,
        type="success",
        message=f"User '{user.username}' logged in",
        user_id=user.id,
        ip=request.client.host
    )

    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/api/public/map-stations")
async def get_public_map_stations(db: AsyncSession = Depends(get_db)):
    try:
        # Lấy tất cả thiết bị
        result = await db.execute(select(models.Device))
        devices = result.scalars().all()
        
        public_data = []
        
        for dev in devices:
            base_config = dev.base_config or {}
            
            # === TRY FORMAT 1: NESTED (Trạm thật) ===
            coords = base_config.get('coords', {})
            lat = coords.get('lat')
            lon = coords.get('lon')
            
            # === TRY FORMAT 2: FLAT (Relay/Virtual) ===
            if not lat or not lon:
                lat = base_config.get('lat')
                lon = base_config.get('lon')
            
            # Chỉ thêm vÃ o danh sách nếu có đủ tọa độ hợp lệ
            if lat and lon:
                try:
                    public_data.append({
                        "name": dev.name,
                        "status": dev.status,
                        "lat": float(lat),
                        "lon": float(lon)
                    })
                except (ValueError, TypeError) as e:
                    logging.warning(f"âš ï¸ Invalid coordinates for {dev.name}: {e}")
                    continue
        
        logging.info(f"âœ“ Public map returned {len(public_data)} stations")
        return public_data
        
    except Exception as e:
        logging.error(f"âŒ Error fetching public map data: {e}")
        return []



@app.get("/api/caster/clienttable")
async def get_caster_clienttable(
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    """Lấy danh sách rover đang kết nối từ NTRIP Caster để hiển thị trên map."""
    try:
        caster_client = await get_caster_client(db)
        clients = await caster_client.get_clienttable()
        
        # Lọc bỏ rover có số lượng vệ tinh (nsv) bằng 0 để không hiển thị trên map
        filtered_clients = [c for c in clients if c.get("nsv") != 0]
        
        return {
            "count": len(filtered_clients),
            "items": filtered_clients,
        }
    except Exception as e:
        logger.error(f"Error fetching clienttable: {e}")
        return {
            "count": 0,
            "items": [],
        }


@app.post("/api/rtcm/stations/{serial}/activate", response_model=schemas.RTCMSessionResponse)
async def activate_rtcm_station(
    serial: str,
    req: schemas.RTCMStationActivateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Station not found")

    mountpoints = _extract_station_mountpoints(device)
    selected_mountpoint = _resolve_requested_mountpoint(mountpoints, req.mountpoint)

    ttl = _sanitize_ttl(req.ttl_seconds)
    now = int(time.time())
    expires_at = now + ttl
    session_id = uuid.uuid4().hex
    client_id = _build_rtcm_client_id(current_user, req.client_instance_id)

    should_activate = False

    async with rtcm_session_lock:
        # Clean expired sessions of this station before counting
        await db.execute(
            delete(models.RTCMStreamSession).where(
                models.RTCMStreamSession.serial == serial,
                models.RTCMStreamSession.expires_at <= now,
            )
        )

        active_before_same_mp = await _count_active_mountpoint_sessions(db, serial, selected_mountpoint, now)
        should_activate = active_before_same_mp == 0

        active_before_station = await _count_active_station_sessions(db, serial, now)
        if active_before_station > 0:
            active_mountpoint_norm = _normalize_mountpoint(_extract_active_mountpoint(device))
            requested_norm = _normalize_mountpoint(selected_mountpoint)
            if active_mountpoint_norm and requested_norm and active_mountpoint_norm != requested_norm:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Station '{serial}' is already active on mountpoint '{active_mountpoint_norm}', "
                        f"cannot switch to '{requested_norm}' until all active sessions expire"
                    ),
                )

        db.add(
            models.RTCMStreamSession(
                session_id=session_id,
                serial=serial,
                mountpoint=str(selected_mountpoint),
                client_id=client_id,
                expires_at=expires_at,
                created_at=now,
                last_heartbeat_at=now,
            )
        )
        await db.commit()

        active_count = await _count_active_station_sessions(db, serial, now)

    if should_activate:
        try:
            await _set_station_stream(serial, active=True, mountpoint=selected_mountpoint)
        except Exception as e:
            async with rtcm_session_lock:
                await db.execute(
                    delete(models.RTCMStreamSession).where(
                        models.RTCMStreamSession.session_id == session_id
                    )
                )
                await db.commit()
            raise HTTPException(status_code=503, detail=f"Failed to activate station stream: {e}")

    return schemas.RTCMSessionResponse(
        session_id=session_id,
        serial=serial,
        client_id=client_id,
        expires_at=expires_at,
        active_sessions_for_station=active_count,
        stream_status=_resolve_stream_status(device, force_starting=should_activate),
        mountpoints=mountpoints,
        selected_mountpoint=selected_mountpoint,
    )


@app.post("/api/rtcm/ntrip/start", response_model=schemas.RTCMSessionResponse)
async def ntrip_start_forward(
    req: schemas.RTCMNtripStartRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    # Wrapper endpoint for NTRIP server integration.
    # Keeps existing activate semantics while allowing NTRIP team to call a single, explicit API.
    return await activate_rtcm_station(
        serial=req.serial,
        req=schemas.RTCMStationActivateRequest(
            client_instance_id=req.client_instance_id,
            ttl_seconds=req.ttl_seconds,
            mountpoint=req.mountpoint,
        ),
        db=db,
        current_user=current_user,
    )


@app.post("/api/rtcm/sessions/{session_id}/heartbeat", response_model=schemas.RTCMSessionResponse)
async def heartbeat_rtcm_session(
    session_id: str,
    req: schemas.RTCMSessionHeartbeatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    ttl = _sanitize_ttl(req.ttl_seconds)
    now = int(time.time())
    expires_at = now + ttl

    async with rtcm_session_lock:
        session = await db.get(models.RTCMStreamSession, session_id)
        if not session or int(session.expires_at) <= now:
            if session and int(session.expires_at) <= now:
                await db.execute(
                    delete(models.RTCMStreamSession).where(models.RTCMStreamSession.session_id == session_id)
                )
                await db.commit()
            raise HTTPException(status_code=404, detail="Session not found or expired")

        expected_prefix = f"user:{current_user.id}"
        if not str(session.client_id).startswith(expected_prefix):
            raise HTTPException(status_code=403, detail="Session does not belong to this user")

        session.expires_at = expires_at
        session.last_heartbeat_at = now
        await db.commit()

        serial = str(session.serial)
        client_id = str(session.client_id)
        active_count = await _count_active_station_sessions(db, serial, now)

    device = await crud.get_device_by_serial(db, serial)
    mountpoints = _extract_station_mountpoints(device) if device else []

    return schemas.RTCMSessionResponse(
        session_id=session_id,
        serial=serial,
        client_id=client_id,
        expires_at=expires_at,
        active_sessions_for_station=active_count,
        stream_status=_resolve_stream_status(device),
        mountpoints=mountpoints,
        selected_mountpoint=_extract_active_mountpoint(device),
    )


@app.delete("/api/rtcm/sessions/{session_id}")
async def release_rtcm_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    serial_to_deactivate: Optional[str] = None
    remaining = 0

    async with rtcm_session_lock:
        session = await db.get(models.RTCMStreamSession, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found or already released")

        expected_prefix = f"user:{current_user.id}"
        if not str(session.client_id).startswith(expected_prefix):
            raise HTTPException(status_code=403, detail="Session does not belong to this user")

        serial = str(session.serial)
        mountpoint = str(getattr(session, "mountpoint", "") or "").strip()

        await db.execute(
            delete(models.RTCMStreamSession).where(models.RTCMStreamSession.session_id == session_id)
        )
        await db.commit()

        remaining = await _count_active_station_sessions(db, serial)
        device = await crud.get_device_by_serial(db, serial)
        active_mountpoint = _extract_active_mountpoint(device)
        if _normalize_mountpoint(active_mountpoint) == _normalize_mountpoint(mountpoint):
            remaining_for_mountpoint = await _count_active_mountpoint_sessions(db, serial, mountpoint)
            if remaining_for_mountpoint == 0:
                serial_to_deactivate = serial

    if serial_to_deactivate:
        try:
            await _set_station_stream(serial_to_deactivate, active=False)
        except Exception as e:
            logger.warning(f"[RTCM] Failed to deactivate station '{serial_to_deactivate}' after release: {e}")

    return {
        "status": "released",
        "session_id": session_id,
        "remaining_sessions_for_station": remaining,
    }


@app.post("/api/rtcm/ntrip/stop")
async def ntrip_stop_forward(
    req: schemas.RTCMNtripStopRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    now = int(time.time())
    client_id = _build_rtcm_client_id(current_user, req.client_instance_id)
    serial_to_deactivate: Optional[str] = None

    async with rtcm_session_lock:
        # Opportunistic cleanup first.
        await db.execute(
            delete(models.RTCMStreamSession).where(models.RTCMStreamSession.expires_at <= now)
        )

        active_sessions_rs = await db.execute(
            select(models.RTCMStreamSession.session_id, models.RTCMStreamSession.mountpoint).where(
                models.RTCMStreamSession.serial == req.serial,
                models.RTCMStreamSession.client_id == client_id,
                models.RTCMStreamSession.expires_at > now,
            )
        )
        active_rows = [(str(r[0]), str(r[1])) for r in active_sessions_rs.all() if r and r[0] and r[1]]
        active_session_ids = [row[0] for row in active_rows]

        if active_session_ids:
            await db.execute(
                delete(models.RTCMStreamSession).where(
                    models.RTCMStreamSession.session_id.in_(active_session_ids)
                )
            )

        await db.commit()

        remaining = await _count_active_station_sessions(db, req.serial, now)

        device = await crud.get_device_by_serial(db, req.serial)
        active_mountpoint = _extract_active_mountpoint(device)
        if active_mountpoint:
            remaining_for_active_mountpoint = await _count_active_mountpoint_sessions(
                db,
                req.serial,
                active_mountpoint,
                now,
            )
            if remaining_for_active_mountpoint == 0:
                serial_to_deactivate = req.serial

    if serial_to_deactivate:
        try:
            await _set_station_stream(serial_to_deactivate, active=False)
        except Exception as e:
            logger.warning(f"[RTCM] Failed to deactivate station '{serial_to_deactivate}' after ntrip stop: {e}")

    return {
        "status": "released",
        "serial": req.serial,
        "client_id": client_id,
        "released_sessions": len(active_session_ids),
        "remaining_sessions_for_station": remaining,
    }


@app.get("/api/rtcm/stations/active", response_model=list[schemas.RTCMActiveStationResponse])
async def get_active_rtcm_stations(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    now = int(time.time())
    async with rtcm_session_lock:
        # Opportunistic cleanup before listing actives
        await db.execute(
            delete(models.RTCMStreamSession).where(models.RTCMStreamSession.expires_at <= now)
        )
        await db.commit()

        active_serials_rs = await db.execute(
            select(models.RTCMStreamSession.serial)
            .where(models.RTCMStreamSession.expires_at > now)
            .distinct()
        )
        serials = sorted(str(r[0]) for r in active_serials_rs.all() if r and r[0])

        items: list[schemas.RTCMActiveStationResponse] = []
        for serial in serials:
            active_count = await _count_active_station_sessions(db, serial, now)
            client_ids = await _fetch_active_station_client_ids(db, serial, now)
            items.append(
                schemas.RTCMActiveStationResponse(
                    serial=serial,
                    active_sessions=active_count,
                    client_ids=client_ids,
                )
            )

        return items


@app.get("/api/auth/me", response_model=schemas.UserResponse)
async def get_current_user_info(current_user: models.User = Depends(auth.get_current_user)):
    permissions = auth.get_user_permissions(current_user)
    user_response = schemas.UserResponse.from_orm(current_user)
    user_response.permissions = permissions
    return user_response

#=== Admin endpoint ===
@app.get("/api/admin/settings", response_model=dict)
async def get_system_settings(
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Lấy trạng thái hiện tại của các cÃ i đặt bảo mật."""
    settings_data = await crud.get_system_settings(db)
    env_host, env_port = _read_caster_config_from_env()
    if env_host:
        settings_data["caster_host"] = env_host
    if env_port:
        settings_data["caster_port"] = env_port
    return settings_data

@app.post("/api/admin/settings")
async def update_system_settings(
    settings_data: dict,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Cập nhật cÃ i đặt bảo mật."""
    caster_host = settings_data.get("caster_host")
    caster_port = settings_data.get("caster_port")
    if caster_host is not None or caster_port is not None:
        env_host, env_port = _read_caster_config_from_env()
        resolved_host = str(caster_host).strip() if caster_host is not None else env_host
        resolved_port = caster_port if caster_port is not None else env_port

        if not resolved_host:
            raise HTTPException(status_code=400, detail="caster_host không hợp lệ")

        try:
            resolved_port_int = int(resolved_port)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="caster_port không hợp lệ")

        _persist_caster_config_to_env(resolved_host, resolved_port_int)
        settings_data["caster_host"] = resolved_host
        settings_data["caster_port"] = resolved_port_int

    await crud.update_system_settings(db, settings_data)
    logger.info(f"Admin '{current_user.username}' updated system settings: {settings_data}")
    return {"status": "ok", "updated_settings": settings_data}

@app.get("/api/admin/security/stats")
async def get_security_stats(
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """
    Enhanced security stats với error handling
    """
    try:
        # 1. Security stats
        security_stats = await rate_limiter.get_stats(db)
        
        # 2. System health
        system_health = health_monitor.get_health_status()
        
        return {
            "status": "ok",
            "timestamp": time.time(),
            "rate_limiter": security_stats,
            "system_health": system_health
        }
    
    except Exception as e:
        logger.error(f"Error in get_security_stats: {e}", exc_info=True)
        
        # Return partial data instead of crashing
        return JSONResponse(
            status_code=status.HTTP_200_OK,  # Still return 200 with error info
            content={
                "status": "error",
                "error": str(e),
                "timestamp": time.time(),
                "rate_limiter": {
                    "error": "Failed to load security stats",
                    "blocked_ips_count": 0,
                    "total_ips_tracked": 0,
                    "attack_mode": False
                },
                "system_health": health_monitor.get_health_status()
            }
        )

@app.post("/api/admin/security/unblock-ip")
async def unblock_ip(
    request_data: dict,  # {"ip_address": "1.2.3.4"}
    db: AsyncSession = Depends(get_auth_db), 
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Admin unblock một IP (thao tác trên database)."""
    ip = request_data.get("ip_address")
    if not ip:
        raise HTTPException(status_code=400, detail="ip_address is required")
    
    success = await rate_limiter.unblock_ip(db, ip) 
    
    if success:
        logger.info(f"Admin '{current_user.username}' unblocked IP: {ip}")
        return {"status": "unblocked", "ip": ip}
    else:
        return {"status": "not_found", "ip": ip}

@app.post("/api/admin/security/block-ip")
async def block_ip_manually(
    request_data: dict,  # {"ip_address": "1.2.3.4"}
    db: AsyncSession = Depends(get_auth_db), 
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Admin block thủ công một IP (lưu vÃ o database)."""
    ip = request_data.get("ip_address")
    if not ip:
        raise HTTPException(status_code=400, detail="ip_address is required")
    
    await rate_limiter.block_ip(db, ip) 
    logger.warning(f"âš ï¸ Admin '{current_user.username}' permanently blocked IP: {ip}")
    
    return {"status": "blocked", "ip": ip}

# === USER MANAGEMENT ===
@app.get("/api/users", response_model=list[schemas.UserResponse])
async def get_users(db: AsyncSession = Depends(get_auth_db),
                    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    users = await crud.get_all_users(db)
    response_list = []
    for u in users:
        permissions = auth.get_user_permissions(u)
        user_resp = schemas.UserResponse.from_orm(u)
        user_resp.permissions = permissions
        response_list.append(user_resp)
    return response_list

@app.get("/api/users/{user_id}", response_model=schemas.UserResponse)
async def get_user_details(user_id: int, db: AsyncSession = Depends(get_auth_db),
                           current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    user = await crud.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    permissions = auth.get_user_permissions(user)
    user_response = schemas.UserResponse.from_orm(user)
    user_response.permissions = permissions
    return user_response

@app.put("/api/devices/{serial}/name")
async def rename_device(
    serial: str,
    request: schemas.DeviceRename, 
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_DEVICE_NAME))):
    # 1. Kiểm tra thiết bị
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if not request.name.strip():
        raise HTTPException(status_code=400, detail="Tên không được để trống")

    # 2. Cập nhật Database
    device.name = request.name
    await db.commit()
    
    # 3. Gửi lệnh xuống Agent
    try:
        await send_command_to_pi(serial, {
            "command": "PROVISION_DEVICE", 
            "payload": {"name": request.name}
        })
    except Exception as e:
        logging.warning(f"Rename DB ok but failed to send to Agent: {e}")

    await log_system_event(
        db=db,
        type="info",
        message=f"Device '{device.name}' renamed to '{request.name}' by {current_user.username}",
        user_id=current_user.id,
        serial=serial
    )
    
    return {"status": "success", "name": device.name}

@app.post("/api/users", response_model=schemas.UserResponse)
async def create_user(
    user_data: schemas.UserCreate,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    existing = await crud.get_user_by_username(auth_db, username=user_data.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    try:
        # Dòng này có thể ném ra lỗi ValueError
        hashed_password = await auth.get_password_hash(user_data.password)
    except ValueError as e:
        # Nếu có lỗi từ chính sách mật khẩu, trả về lỗi 400 với thông điệp cụ thể
        raise HTTPException(status_code=400, detail=str(e))
    # -----------------------------------------------------------
        
    new_user = await crud.create_user(
        auth_db, username=user_data.username, hashed_password=hashed_password,
        role=user_data.role, full_name=user_data.full_name
    )
    
    if user_data.assigned_devices and new_user.role == 'coordinator':
        for serial in user_data.assigned_devices:
            device = await crud.get_device_by_serial(devices_db, serial)
            if device:
                device.user_id = new_user.id
        await devices_db.commit()
    
    permissions = auth.get_user_permissions(new_user)
    user_response = schemas.UserResponse.from_orm(new_user)
    user_response.permissions = permissions
    await log_system_event(
        db=devices_db,
        type="success",
        message=f"Admin '{current_user.username}' created user '{new_user.username}' (role: {new_user.role})",
        user_id=current_user.id
    )
    
    return user_response

@app.put("/api/users/{user_id}", response_model=schemas.UserResponse)
async def update_user(
    user_id: int, 
    user_data: schemas.UserUpdate,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    
    user_to_update = await crud.get_user_by_id(auth_db, user_id)
    if not user_to_update:
        raise HTTPException(status_code=404, detail="User not found")

    update_dict = user_data.dict(exclude_unset=True)
    
    # ===================================================================
    # FIX: LOGIC CẬP NHẬT DANH SÁCH THIẾT BỊ (ASSIGN/UNASSIGN)
    # ===================================================================
    assigned_serials = update_dict.pop("assigned_devices", None)
    
    if assigned_serials is not None:
        # 1. Lấy tất cả thiết bị HIỆN TẠI đang gán cho user nÃ y
        current_devices = await crud.get_devices_by_user_id(devices_db, user_id)
        
        # 2. Gỡ bỏ tất cả trước (Reset về None)
        for dev in current_devices:
            dev.user_id = None
            # Broadcast cập nhật UI: Trạm này đã bị gỡ
            await ui_manager.broadcast({
                "type": "status_update",
                "data": schemas.Device.from_orm(dev).model_dump()
            })
        
        # 3. Nếu user là Coordinator -> Gán danh sách mới
        if user_to_update.role == 'coordinator' or user_data.role == 'coordinator':
            for serial in assigned_serials:
                device = await crud.get_device_by_serial(devices_db, serial)
                # Chỉ gán nếu trạm chưa có chủ hoặc chủ chính là user này (đề phòng tranh chấp)
                # Hoặc Admin có quyền ghi đè (tùy logic, ở đây cho phép ghi đè để tiện quản lý)
                if device:
                    device.user_id = user_id
                    # Broadcast cập nhật UI: Trạm nÃ y đã có chủ mới
                    await ui_manager.broadcast({
                        "type": "status_update",
                        "data": schemas.Device.from_orm(device).model_dump()
                    })

        await devices_db.commit()
    # ===================================================================

    if 'password' in update_dict and update_dict['password']:
        update_dict['hashed_password'] = await auth.get_password_hash(update_dict.pop('password'))
    
    updated_user = await crud.update_user(auth_db, user_id, update_dict)
    
    permissions = auth.get_user_permissions(updated_user)
    user_response = schemas.UserResponse.from_orm(updated_user)
    user_response.permissions = permissions
    
    await log_system_event(
        db=devices_db,
        type="info",
        message=f"Admin '{current_user.username}' updated user '{updated_user.username}'",
        user_id=current_user.id
    )
    
    return user_response

@app.delete("/api/users/{user_id}")
async def delete_user(
    user_id: int, 
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    
    # 1. Kiểm tra user tồn tại
    user = await crud.get_user_by_id(auth_db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    
    # ===================================================================
    # FIX: GỠ THIẾT BỊ + BROADCAST ĐỂ UI CẬP NHẬT NGAY LẬP TỨC
    # ===================================================================
    try:
        # Tìm các thiết bị đang thuộc về user này
        result = await devices_db.execute(select(models.Device).where(models.Device.user_id == user_id))
        devices_to_unlink = result.scalars().all()

        for dev in devices_to_unlink:
            dev.user_id = None # Gỡ bỏ user_id
            
            # QUAN TRỌNG: Gửi thông báo WebSocket xuống UI để Admin thấy ngay là trạm đã "trống"
            await ui_manager.broadcast({
                "type": "status_update",
                "data": schemas.Device.from_orm(dev).model_dump()
            })
        
        await devices_db.commit() # Lưu thay đổi vào DB Device
        logger.info(f"Unlinked {len(devices_to_unlink)} devices from deleted user {user_id}")

    except Exception as e:
        logger.error(f"Error unlinking devices: {e}")
        # Không raise lỗi để tiếp tục xóa user
    # ===================================================================

    # 3. Xóa user trong Auth DB
    success = await crud.delete_user(auth_db, user_id) 
    if not success:
         raise HTTPException(status_code=404, detail="User not found during deletion")

    await log_system_event(
        db=devices_db,
        type="warning",
        message=f"Admin '{current_user.username}' deleted user '{user.username}' and released devices.",
        user_id=current_user.id
    )
    
    return {"status": "deleted", "user_id": user_id}

#=== CASTER CONFIGURATION ===
@app.put("/api/admin/caster-config")
async def update_caster_config(
    config: schemas.CasterConfig,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Cập nhật Host/Port của NTRIP Caster"""
    settings_data = {
        "caster_host": config.host.strip(),
        "caster_port": config.port
    }

    _persist_caster_config_to_env(settings_data["caster_host"], int(settings_data["caster_port"]))
    await crud.update_system_settings(db, settings_data)
    return {"status": "success", "config": settings_data}

@app.get("/api/admin/caster-config")
async def get_caster_config_endpoint(
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    system_settings = await crud.get_system_settings(db)
    env_host, env_port = _read_caster_config_from_env()

    host = env_host or settings.CASTER_HOST or system_settings.get('caster_host', 'aitogy.com.vn')
    port = env_port or settings.CASTER_PORT or system_settings.get('caster_port', 2101)

    return {
        "host": host,
        "port": int(port)
    }

# === LICENSE ===
@app.post("/api/license/pi", response_model=schemas.LicenseResponse)
async def generate_pi_license(
    request: schemas.LicenseRequest,
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_LICENSE))
):
    serial = request.serial
    if not serial:
        raise HTTPException(status_code=400, detail="Serial number required.")
    
    try:
        base_code = license_manager.generate_pi_license_base(serial)
        license_key = license_manager.get_license_code_from_string(base_code)
        
        if "Lỗi" in license_key:
            raise ValueError("License generation failed.")

        return schemas.LicenseResponse(serial=serial, license_key=license_key)
    except Exception as e:
        logging.error(f"Failed to generate license for {serial}: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate license key.")

# === DEVICE ENDPOINTS ===
@app.get("/api/devices", response_model=list[schemas.Device])
async def get_initial_devices(
    user_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)) 
):
    """
    âœ… CHỐNG IDOR: Backend LUÔN ƯU TIÊN current_user.id từ JWT token
    """
    
    # ðŸ”’ COORDINATOR: Bỏ qua mọi tham số user_id (CHỐNG IDOR)
    if current_user.role == auth.Role.COORDINATOR:
        logging.info(
            f"Coordinator '{current_user.username}' requested devices. "
            f"Ignoring user_id parameter (if any) and using id={current_user.id}"
        )
        return await crud.get_devices_by_user_id(db, user_id=current_user.id)
    
    # ðŸ”’ ADMIN: Cho phép filter, nhưng validate
    if user_id is not None:
        if current_user.role != auth.Role.ADMIN:
            logging.warning(
                f"âš ï¸ IDOR ATTEMPT: User '{current_user.username}' (role={current_user.role}) "
                f"tried to filter by user_id={user_id}"
            )
            raise HTTPException(
                status_code=403, 
                detail="Only admins can filter devices by user_id"
            )
        return await crud.get_devices_by_user_id(db, user_id=user_id)
    
    # Mặc định: Admin vÃ  Viewer xem tất cả
    return await crud.get_all_devices(db)

@app.post("/api/devices/{serial}/command")
async def send_generic_command(
    serial: str, 
    command: schemas.Command, 
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)):
    if current_user.role == auth.Role.COORDINATOR:
        raise HTTPException(status_code=403, detail="Coordinators can only send specific commands.")
    if not auth.has_permission(current_user, auth.Permission.EDIT_CHIP_CONFIG):
         raise HTTPException(status_code=403, detail="Permission denied.")
    
    # 1. Lấy thiết bị
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    await log_system_event(
        db=db,
        type="info",
        message=f"Command '{command.command}' sent to '{device.name}' by {current_user.username}",
        user_id=current_user.id,
        serial=serial
    )
    return await send_command_to_pi(serial, command.model_dump())

# --- CÁC ENDPOINT CHO QUẢN LÝ ROVER ---
@app.get("/api/bases/{serial}/rovers", response_model=list[schemas.RoverAccountResponse])
async def get_rovers_for_base(
    serial: str,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db), # Thêm vÃ o để kiểm tra quyền
    current_user: models.User = Depends(auth.get_current_user)
):
    # Admin có thể xem Rover của bất kỳ trạm nÃ o
    if current_user.role == auth.Role.ADMIN:
        return await crud.get_rovers_by_base_serial(db, base_serial=serial)

    # Coordinator chỉ có thể xem Rover của các trạm họ quản lý
    if current_user.role == auth.Role.COORDINATOR:
        device = await crud.get_device_by_serial(devices_db, serial)
        if device and device.user_id == current_user.id:
            return await crud.get_rovers_by_base_serial(db, base_serial=serial)
    
    # Nếu không phải Admin hoặc không phải chủ sở hữu, từ chối
    raise HTTPException(status_code=403, detail="Bạn không có quyền xem danh sách Rover của trạm nÃ y.")

@app.post("/api/bases/{serial}/rovers", response_model=schemas.RoverAccountResponse)
async def create_rover_for_base(
    serial: str,
    rover_data: schemas.RoverAccountCreate,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # 1. Kiểm tra quyền
    is_admin = current_user.role == auth.Role.ADMIN
    is_authorized_coordinator = (
        current_user.role == auth.Role.COORDINATOR and 
        current_user.can_create_rovers
    )

    if not is_admin and not is_authorized_coordinator:
        raise HTTPException(status_code=403, detail="Bạn không có quyền tạo tÃ i khoản Rover.")

    if not is_admin:
        device = await crud.get_device_by_serial(devices_db, serial)
        if not device or device.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Bạn không được phép tạo Rover cho trạm Base nÃ y.")

    # 2. Kiểm tra trùng lặp trong DB Local
    existing_rover = await crud.get_rover_by_username(db, rover_data.username)
    if existing_rover:
        raise HTTPException(status_code=400, detail="Tên đăng nhập Rover đã tồn tại (DB).")

    # 1. Lấy thông tin thiết bị để kiểm tra Mountpoint hợp lệ
    device_info = await crud.get_device_by_serial(devices_db, serial)
    if not device_info:
        raise HTTPException(status_code=404, detail="Trạm Base không tồn tại.")
    
    service_conf = device_info.service_config or {}
    
    # 2. Xác định Mountpoint (Logic mới)
    target_mountpoint = rover_data.mountpoint_allow
    
    valid_mps = []
    if service_conf.get('server1_enabled'):
        valid_mps.append(service_conf.get('mountpoint1'))
    if service_conf.get('server2_enabled'):
        valid_mps.append(service_conf.get('mountpoint2'))
        
    # Lọc giá trị None/Rỗng
    valid_mps = [mp for mp in valid_mps if mp]

    if not valid_mps:
        target_mountpoint = f"MP_{serial}"
    elif target_mountpoint not in valid_mps:
        target_mountpoint = valid_mps[0]

    duration_seconds = 315360000 # 10 năm mặc định
    
    if rover_data.expiry_days > 0:
        duration_seconds = rover_data.expiry_days * 86400  # 1 ngÃ y = 86400 giây

    # 3. GỌI TCP ĐẾN NTRIP CASTER
    try:
        caster = await get_caster_client(db)
        
        creator_identifier = current_user.username 
        
        await caster.create_user(
            creator=creator_identifier,
            username=rover_data.username,
            password=rover_data.password,
            mountpoints=target_mountpoint, 
            expiry_seconds=duration_seconds, 
            max_users=1
        )
        logger.info(f"âœ“ Created Rover '{rover_data.username}' for MP '{target_mountpoint}'")
        
    except Exception as e:
        logger.error(f"Failed to create rover on Caster: {e}")
        raise HTTPException(status_code=502, detail=f"Lỗi Caster: {str(e)}")

    # 4. Lưu vÃ o DB
    new_rover = await crud.create_rover_account(
        db, 
        username=rover_data.username,
        password=rover_data.password,
        base_serial=serial,
        creator_id=current_user.id
    )
    
    return new_rover

@app.put("/api/rovers/{rover_id}", response_model=schemas.RoverAccountResponse)
async def update_rover(
    rover_id: int,
    update_req: schemas.RoverUpdate,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # 1. Lấy thông tin Rover cũ
    rover = await crud.get_rover_by_id(db, rover_id)
    if not rover:
        raise HTTPException(status_code=404, detail="Không tìm thấy tÃ i khoản Rover.")

    # 2. Kiểm tra quyền
    is_admin = current_user.role == auth.Role.ADMIN
    is_creator = rover.created_by_user_id == current_user.id
    if not is_admin and not is_creator:
        raise HTTPException(status_code=403, detail="Bạn không có quyền sửa tÃ i khoản nÃ y.")

    # 3. Chuẩn bị dữ liệu mới (nếu client không gửi thì dùng cái cũ)
    new_password = update_req.password if update_req.password else rover.password
    new_mountpoints = update_req.mountpoints if update_req.mountpoints else rover.mountpoints
    
    # Xử lý thời hạn (Mặc định reset lại thời hạn nếu có gửi expiry_days)
    duration_seconds = 315360000 # Default
    if update_req.expiry_days:
        duration_seconds = update_req.expiry_days * 86400
    
    # 4. GỌI CASTER ĐỂ CẬP NHẬT (Ghi đè user cũ)
    try:
        caster = await get_caster_client(db)
        creator_identifier = current_user.username 
        
        # HÃ m create_user của Caster thường hoạt động như "Upsert" (Tạo mới hoặc Ghi đè)
        await caster.create_user(
            creator=creator_identifier,
            username=rover.username, # Username không đổi
            password=new_password,
            mountpoints=new_mountpoints, 
            expiry_seconds=duration_seconds, 
            max_users=1
        )
    except Exception as e:
        logger.error(f"Caster Update Error: {e}")
        raise HTTPException(status_code=502, detail=f"Lỗi cập nhật Caster: {str(e)}")

    # 5. Cập nhật DB Local
    update_data = {
        "password": new_password,
        "mountpoints": new_mountpoints
    }
    updated_rover = await crud.update_rover_account(db, rover_id, update_data)
    
    return updated_rover

@app.delete("/api/rovers/{rover_id}")
async def delete_rover(
    rover_id: int,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # 1. Lấy thông tin Rover
    rover_to_delete = await crud.get_rover_by_id(db, rover_id)
    if not rover_to_delete:
        raise HTTPException(status_code=404, detail="Không tìm thấy tÃ i khoản Rover.")

    # 2. Kiểm tra quyền
    is_admin = current_user.role == auth.Role.ADMIN
    is_creator = rover_to_delete.created_by_user_id == current_user.id

    if not is_admin and not is_creator:
        raise HTTPException(status_code=403, detail="Bạn không có quyền xóa tÃ i khoản Rover nÃ y.")
    
    creator_user = await crud.get_user_by_id(db, rover_to_delete.created_by_user_id)
    creator_username = creator_user.username if creator_user else "admin"

    # 3. GỌI TCP ĐẾN NTRIP CASTER ĐỂ XÓA
    try:
        caster = await get_caster_client(db)
        await caster.delete_user(
            identifier=creator_username, 
            username=rover_to_delete.username
        )
        logger.info(f"âœ“ Deleted Rover '{rover_to_delete.username}' from Caster via TCP")
    except Exception as e:
        logger.warning(f"Failed to delete rover from Caster (might be already deleted): {e}")

    # 4. Xóa khỏi DB Local
    success = await crud.delete_rover_account(db, rover_id=rover_id)
    
    return {"status": "deleted", "rover_id": rover_id}

# === ROVER MANAGEMENT - GLOBAL VIEW ===
@app.get("/api/rovers/all", response_model=list[schemas.RoverAccountResponse])
async def get_all_rovers_global(
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):

    # 1. Kiểm tra quyền cơ bản
    if current_user.role == auth.Role.VIEWER:
        raise HTTPException(
            status_code=403, 
            detail="Viewer không có quyền xem danh sách Rover."
        )
    
    if current_user.role == auth.Role.COORDINATOR and not current_user.can_create_rovers:
        raise HTTPException(
            status_code=403, 
            detail="Bạn chưa được cấp quyền quản lý Rover."
        )
    
    # 2. Lấy danh sách Rover dựa trên role
    if current_user.role == auth.Role.ADMIN:
        rovers = await crud.get_all_rovers_by_user(db, user_id=current_user.id, role="admin")
    else:
        rovers = await crud.get_all_rovers_by_user(db, user_id=current_user.id, role="coordinator")
    
    # 3. LÃ m giÃ u dữ liệu (XỬ LÝ HIỂN THỊ NHIỀU TRẠM)
    enriched_rovers = []
    
    # Cache devices để tránh query DB quá nhiều lần trong vòng lặp
    all_devices_result = await devices_db.execute(select(models.Device))
    all_devices = {d.serial: d.name for d in all_devices_result.scalars().all()}

    for rover in rovers:
        rover_dict = schemas.RoverAccountResponse.from_orm(rover).model_dump()
        
        # Xử lý base_serial có thể lÃ  "SN1,SN2"
        serials = rover.base_serial.split(',')
        station_names = []
        
        for sn in serials:
            s_name = all_devices.get(sn.strip())
            if s_name:
                station_names.append(s_name)
            else:
                station_names.append(sn) # Nếu không tìm thấy tên thì hiện serial
        
        # Nối tên các trạm lại: "Trạm A, Trạm B"
        rover_dict['base_name'] = ", ".join(station_names)
         
        enriched_rovers.append(rover_dict)
    return enriched_rovers

@app.post("/api/rovers/create-multi", response_model=schemas.RoverAccountResponse)
async def create_multi_base_rover(
    request: schemas.RoverMultiCreate,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # 1. Kiểm tra quyền (như cũ)
    is_admin = current_user.role == auth.Role.ADMIN
    if not is_admin and not (current_user.role == auth.Role.COORDINATOR and current_user.can_create_rovers):
        raise HTTPException(status_code=403, detail="Bạn không có quyền tạo tÃ i khoản Rover.")

    # 2. Kiểm tra trùng (như cũ)
    existing = await crud.get_rover_by_username(db, request.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username đã tồn tại.")

    # 3. Chuẩn bị dữ liệu
    duration_seconds = request.expiry_days * 86400
    target_mountpoints = request.mountpoint_override.strip()
    
    if not request.selected_serials:
         raise HTTPException(status_code=400, detail="Phải chọn ít nhất 1 trạm.")
    
    # Gộp serial lại thành chuỗi để lưu vào base_serial
    combined_serials = ",".join(request.selected_serials)

    # 4. GỌI CASTER (Tạo user thật)
    try:
        caster = await get_caster_client(db)
        creator_identifier = current_user.username 
        
        await caster.create_user(
            creator=creator_identifier,
            username=request.username,
            password=request.password,
            mountpoints=target_mountpoints, 
            expiry_seconds=duration_seconds, 
            max_users=1
        )
        logging.info(f"✓ Created Multi-Rover '{request.username}' allowed: {target_mountpoints}")

    except Exception as e:
        logging.error(f"Caster Error: {e}")
        raise HTTPException(status_code=502, detail=f"Lỗi Caster: {str(e)}")

    # 5. LƯU VÀO DB (Ghi rõ mountpoint)
    new_rover = await crud.create_rover_account(
        db, 
        username=request.username,
        password=request.password,
        base_serial=combined_serials,   # Lưu: "SN1,SN2"
        mountpoints=target_mountpoints, # Lưu: "MP1;MP2" (Cột mới)
        creator_id=current_user.id
    )
    
    return new_rover

#=== DEVICE CONFIGURATION ENDPOINTS ===
@app.post("/api/devices/{serial}/configure-chip")
async def configure_chip_endpoint(
    serial: str, 
    config_request: schemas.Command,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_COORDINATES))
):
    # ==================== BƯỚC 1: KIỂM TRA THIẾT BỊ ====================
    device_in_db = await crud.get_device_by_serial(db, serial)
    if not device_in_db:
        raise HTTPException(
            status_code=404, 
            detail="Device not found in database"
        )
    
    # ==================== BƯỚC 2: PARSE PAYLOAD ====================
    payload = config_request.payload
    sensor_type = payload.get("sensor_type")
    params = payload.get("params", {})
    mode = payload.get("mode")
    method = params.get("base_setup_method")
    
    if not sensor_type or not mode:
        raise HTTPException(
            status_code=400,
            detail="Missing required fields: sensor_type or mode"
        )

    # ==================== BƯỚC 3: LƯU CẤU HÌNH VÀO DATABASE ====================
    try:
        if sensor_type:
            device_in_db.detected_chip_type = sensor_type
        if params:
            device_in_db.base_config = params
        device_in_db.is_synced = False
        
        await db.commit()
        await db.refresh(device_in_db)
        
        logging.info(f"✓ Saved base config for {serial} to database: Chip={sensor_type}, Method={method}, Marked as UNSYNCED")
        
        await ui_manager.broadcast({
            "type": "status_update",
            "data": schemas.Device.from_orm(device_in_db).model_dump()
        })

    except Exception as e:
        await db.rollback()
        logging.error(f"❌ Failed to save config to DB for {serial}: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to save configuration to database: {str(e)}"
        )

    # ==================== BƯỚC 4 & 5: BUILD VÀ GỬI LỆNH XUỐNG AGENT ====================
    try:
        commands_to_send = []
        pi_command = None
        
        if mode == "BASE":
            if method == "AUTO_CORS":
                import os
                pi_command = {
                    "command": "TRIGGER_AUTO_BASE",
                    "payload": {
                        "ip": os.getenv("AUTO_BASE_CORS_IP", "14.238.1.125"),
                        "port": int(os.getenv("AUTO_BASE_CORS_PORT", 2101)),
                        "user": os.getenv("AUTO_BASE_CORS_USER", "aitogy"),
                        "password": os.getenv("AUTO_BASE_CORS_PASS", "123"),
                        "mountpoint": os.getenv("AUTO_BASE_CORS_MOUNTPOINT", "VRS.105M3"),
                        "timeout": 3600,
                        "samples": 60,
                        "sensor_type": sensor_type
                    },
                    "original_config": config_request.payload
                }
                logging.info(f"Built Auto Base Setup trigger for {serial}")
            elif method == "SURVEY_IN":
                survey_duration = params.get('survey_in_duration')
                survey_accuracy = params.get('survey_in_accuracy')
                
                if not survey_duration or not survey_accuracy:
                    raise ValueError("Missing survey_in parameters")
                
                commands_to_send = command_builder.build_base_survey_in_command(
                    sensor_type, survey_duration, survey_accuracy
                )
                logging.info(f"Built Survey-In commands for {serial}: {survey_duration}s, {survey_accuracy}m")
            elif method == "FIXED_LLA":
                coords = params.get('coords')
                if not coords or not all(k in coords for k in ['lat', 'lon', 'alt']):
                    raise ValueError("Missing or incomplete coordinates")
                
                accuracy = params.get('accuracy', 10.0)
                commands_to_send = command_builder.build_base_fixed_lla_command(
                    sensor_type, coords['lat'], coords['lon'], coords['alt'], accuracy
                )
                logging.info(f"Built Fixed LLA commands for {serial}: lat/lon/alt, acc={accuracy}m")
            else:
                raise ValueError(f"Unknown base setup method: {method}")
        else:
            raise ValueError(f"Unsupported mode: {mode}")
        
        if not pi_command:
            encoded_commands = [base64.b64encode(cmd).decode('ascii') for cmd in commands_to_send if cmd]
            if not encoded_commands:
                raise ValueError("No commands generated")

            pi_command = {
                "command": "EXECUTE_RAW_COMMANDS",
                "payload": {"commands_b64": encoded_commands},
                "original_config": config_request.payload
            }
            
    except Exception as e:
        logging.error(f"❌ Failed to build configuration commands for {serial}: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to build configuration commands: {str(e)}"
        )

    # ==================== BƯỚC 6: GỬI LỆNH ====================
    try:
        response = await send_command_to_pi(serial, pi_command)
        
        logging.info(f"✓ Sent configuration commands to {serial}")
        
        await log_system_event(
            db=db,
            type="info",
            message=f"GNSS config updated for '{device_in_db.name}' by {current_user.username}",
            user_id=current_user.id,
            serial=serial
        )
        
        return {
            "status": "command_sent_waiting_sync",
            "channel": response['channel'],
            "commands_sent": len(commands_to_send) if not pi_command else 1,
            "message": "Configuration saved to database and sent to device. Waiting for device to apply and confirm sync status...",
            "is_synced": False
        }

    except HTTPException as http_exc:
        logging.warning(f"⚠️ Failed to send command to {serial}: {http_exc.detail}. Config auto-sync when online.")
        raise HTTPException(
            status_code=503,
            detail=f"Configuration saved to database successfully, but device is offline ({http_exc.detail}). Config will auto-sync when device comes back online."
        )
    except Exception as e:
        logging.error(f"❌ Unexpected error sending command to {serial}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal error while sending command: {str(e)}"
        )

@app.post("/api/devices/{serial}/configure-service")
async def configure_service_endpoint(
    serial: str,
    payload: schemas.ServiceConfig,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_SERVICE_CONFIG))
):
    device_in_db = await crud.get_device_by_serial(db, serial)
    if not device_in_db:
        raise HTTPException(status_code=404, detail="Device not found in database")
    # --- BƯỚC 1: LƯU CẤU HÌNH "CHUẨN" VÀO DATABASE ---
    try:
        device_in_db.service_config = payload.model_dump()
        await db.commit()
        await db.refresh(device_in_db)
        logging.info(f"âœ“ Saved service_config for {serial} to database.")
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save service config to database.")
    
    # --- BƯỚC 2: GỬI LỆNH ĐẾN PI ---
    pi_command = {
        "command": "DEPLOY_SERVICE_CONFIG",
        "payload": payload.model_dump()
    }
    try:
        response = await send_command_to_pi(serial, pi_command)
        await log_system_event(
            db=db,
            type="info",
            message=f"Service config updated for '{device_in_db.name}' by {current_user.username}",
            user_id=current_user.id,
            serial=serial
        )
        return {"status": "service_config_sent_and_saved", "channel": response.get('channel', 'unknown')}
    except HTTPException as e:
        raise HTTPException(status_code=503, detail=f"Config saved to DB, but failed to send command to Pi: {e.detail}")

@app.post("/api/devices/{serial}/reset")
async def reset_pi_device(
    serial: str,
    db: AsyncSession = Depends(get_db), 
    current_user: models.User = Depends(auth.require_permission(auth.Permission.DELETE_DEVICE))
):
    # 1. Lấy thiết bị từ DB TRƯỚC để kiểm tra sự tồn tại
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Thiết bị không tồn tại")

    # 3. Gửi lệnh xuống thiết bị (Chỉ gửi khi đã qua cửa kiểm tra an ninh)
    try:
        await send_command_to_pi(serial, {"command": "DELETE_DEVICE", "payload": {}})
    except Exception as e:
        # Log lỗi nhưng không chặn dòng chảy để cập nhật DB cho UI phản hồi
        logging.error(f"Failed to send reset command to {serial}: {e}")

    # 4. Cập nhật DB ngay lập tức
    device.status = "rebooting_for_reset"
    device.is_provisioned = False 
    device.detected_chip_type = "UNKNOWN"  
    device.base_config = {}                
    device.service_config = {}             
    device.ntrip_connected = False
    device.bps = 0
    
    await db.commit()
    # 5. Broadcast để UI cập nhật ngay
    await ui_manager.broadcast({
        "type": "status_update",
        "data": schemas.Device.from_orm(device).model_dump()
    })

    logging.info(f"User '{current_user.username}' sent RESET command to Pi '{serial}'")
    await log_system_event(
        db=db,
        type="warning",
        message=f"Device '{device.name}' reset by {current_user.username}",
        user_id=current_user.id,
        serial=serial
    )
    return {"status": "reset_command_sent"}

@app.delete("/api/devices/{serial}")
async def delete_device_from_list(
    serial: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.DELETE_DEVICE))
):
    device = await crud.get_device_by_serial(db, serial)
    
    if not device:
        raise HTTPException(status_code=404, detail="Trạm không tồn tại")
    
    # Kiểm tra quyền (Coordinator chỉ xóa được trạm của mình)
    if current_user.role == auth.Role.COORDINATOR:
        if device.user_id != current_user.id:
            raise HTTPException(
                status_code=403, 
                detail="Bạn chỉ có thể xóa trạm được gán cho mình"
            )
    
    # Gửi lệnh xuống cho Pi tự xóa file config.json và license.key rồi reboot!
    try:
        await send_command_to_pi(serial, {"command": "DELETE_DEVICE"})
        logging.info(f"Gửi lệnh xóa thành công xuống Pi '{serial}'")
    except Exception as e:
        logging.warning(f"Không thể gửi lệnh xóa xuống Pi '{serial}' (có thể thiết bị đang tắt): {e}")

    # Xóa khỏi database
    await db.delete(device)
    await db.commit()

    # Thêm: Dọn dẹp in-memory MQTT cache để tránh thiết bị bị zombie hóa
    for cache in [
        mqtt_handler.device_write_cache,
        mqtt_handler.device_status_cache,
        mqtt_handler.device_sync_cache,
        mqtt_handler.device_name_cache,
    ]:
        cache.pop(serial, None)
    
    # Xóa tất cả các keys liên quan đến serial (như config) trong cache đồng bộ
    for key in list(mqtt_handler.device_sync_cache.keys()):
        if str(key).startswith(f"{serial}_"):
            mqtt_handler.device_sync_cache.pop(key, None)

    logging.info(f"User '{current_user.username}' deleted device '{serial}' from list (not reset)")
    
    # Broadcast để các client khác cập nhật UI
    await ui_manager.broadcast({
        "type": "device_deleted",
        "serial": serial
    })
    
    await log_system_event(
        db=db,
        type="warning",
        message=f"Device '{device.name}' removed from list by {current_user.username}",
        user_id=current_user.id,
        serial=serial
    )
    
    return {"status": "deleted", "serial": serial}

@app.post("/api/devices/{serial}/lock")
async def lock_device(
    serial: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    device = await crud.get_device_by_serial(db, serial)
    try:
        response = await send_command_to_pi(serial, {"command": "LOCK_DEVICE", "payload": {}})
        await log_system_event(
            db=db,
            type="warning",
            message=f"Device '{device.name}' LOCKED by Admin {current_user.username}",
            user_id=current_user.id,
            serial=serial
        )
        return {"status": "lock_command_sent", "channel": response.get('channel', 'unknown')}
    except HTTPException as http_exc:
        # Neu send_command_to_pi raise 503, hay tra ve loi do
        raise http_exc
    except Exception as e:
        logging.error(f"Error in lock_device endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.post("/api/devices/{serial}/unlock")
async def unlock_device(
    serial: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    device = await crud.get_device_by_serial(db, serial)
    try:
        response = await send_command_to_pi(serial, {"command": "UNLOCK_DEVICE", "payload": {}})
        await log_system_event(
            db=db,
            type="success",
            message=f"Device '{device.name}' UNLOCKED by Admin {current_user.username}",
            user_id=current_user.id,
            serial=serial
        )
        return {"status": "unlock_command_sent", "channel": response.get('channel', 'unknown')}
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logging.error(f"Error in unlock_device endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.get("/api/devices/export/csv")
async def export_devices_to_csv(
    db: AsyncSession = Depends(get_db),
    auth_db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EXPORT_DATA))
):
    stream = io.StringIO()
    writer = csv.writer(stream)
    
    headers = [
        # === THÔNG TIN CHUNG ===
        "serial", 
        "name", 
        "status", 
        "timestamp", 
        "human_readable_time", 
        "total_bps",  # Tổng BPS (server1 + server2)
        "detected_chip_type", 
        "assigned_coordinator", 
        "ntrip_connected",
        
        # === CẤU HÌNH BASE STATION ===
        "base_setup_method",     # FIXED_LLA hoặc SURVEY_IN
        "base_lat", 
        "base_lon", 
        "base_alt", 
        "base_accuracy_m",       # Accuracy (mét)
        
        # === NTRIP SERVER 1 ===
        "server1_enabled",
        "server1_ntrip_version",  # v1.0 hoặc v2.0
        "server1_host",
        "server1_port",
        "server1_mountpoint",
        "server1_username",
        "server1_bps",            # Traffic hiện tại (B/s)
        "server1_connected",      # True/False
        
        # === NTRIP SERVER 2 ===
        "server2_enabled",
        "server2_ntrip_version",
        "server2_host",
        "server2_port",
        "server2_mountpoint",
        "server2_username",
        "server2_bps",
        "server2_connected",
        
        # === RTCM CLIENT (ROVER MODE) ===
        "rtcm_client_enabled",
        "rtcm_server",
        "rtcm_port",
        "rtcm_mountpoint",
        "rtcm_username"
    ]
    
    writer.writerow(headers)
    
    # ===================================================================
    # LẤY DANH SÁCH THIẾT BỊ VÀ XỬ LÝ
    # ===================================================================
    devices = await crud.get_all_devices(db)

    # Prefetch coordinator usernames in one query to avoid N+1 lookups.
    user_ids = sorted({int(d.user_id) for d in devices if getattr(d, "user_id", None) is not None})
    coordinator_map = {}
    if user_ids:
        rs_users = await auth_db.execute(
            select(models.User.id, models.User.username).where(models.User.id.in_(user_ids))
        )
        coordinator_map = {int(row[0]): str(row[1]) for row in rs_users.all() if row and row[0] is not None}
    
    for device in devices:
        # === THÔNG TIN CHUNG ===
        human_time = time.strftime(
            '%Y-%m-%d %H:%M:%S', 
            time.localtime(device.timestamp)
        ) if device.timestamp else "N/A"
        
        # Lấy tên Coordinator
        coordinator_name = ""
        if device.user_id:
            coordinator_name = coordinator_map.get(int(device.user_id), "")
        
        # === CẤU HÌNH BASE ===
        base_config = device.base_config or {}
        coords = base_config.get('coords', {})
        base_method = base_config.get('base_setup_method', '')
        base_lat = coords.get('lat', base_config.get('lat', ''))
        base_lon = coords.get('lon', base_config.get('lon', ''))
        base_alt = coords.get('alt', base_config.get('alt', ''))
        base_accuracy = base_config.get('accuracy', base_config.get('acc', ''))
        
        # === CẤU HÌNH SERVICE ===
        service_config = device.service_config or {}
        
        # --- SERVER 1 ---
        s1_enabled = service_config.get('server1_enabled', False)
        s1_version = service_config.get('ntrip_version1', 1)
        s1_host = service_config.get('serverhost1', service_config.get('host1', ''))
        s1_port = service_config.get('port1', service_config.get('serverport1', ''))
        s1_mountpoint = service_config.get('mountpoint1', service_config.get('mp1', ''))
        s1_username = service_config.get('username1', service_config.get('user1', 'source'))
        
        # --- SERVER 2 ---
        s2_enabled = service_config.get('server2_enabled', False)
        s2_version = service_config.get('ntrip_version2', 1)
        s2_host = service_config.get('serverhost2', service_config.get('host2', ''))
        s2_port = service_config.get('port2', service_config.get('serverport2', ''))
        s2_mountpoint = service_config.get('mountpoint2', service_config.get('mp2', ''))
        s2_username = service_config.get('username2', service_config.get('user2', 'source'))
        
        # --- RTCM CLIENT ---
        rtcm_enabled = service_config.get('rtcm_enabled', False)
        rtcm_server = service_config.get('rtcmserver1', '')
        rtcm_port = service_config.get('rtcmport1', '')
        rtcm_mountpoint = service_config.get('rtcmmountpoint1', '')
        rtcm_username = service_config.get('rtcmusername1', '')
        
        # === NTRIP STATUS (TRAFFIC HIỆN TẠI) ===
        ntrip_status = device.ntrip_status
        if not isinstance(ntrip_status, dict):
            ntrip_status = {}
        
        s1_status = ntrip_status.get('server1')
        if not isinstance(s1_status, dict):
            s1_status = {} 
            
        s1_bps = int(s1_status.get('bps', 0) or 0)
        s1_connected = bool(s1_status.get('connected', False))
        s2_status = ntrip_status.get('server2')
        if not isinstance(s2_status, dict):
            s2_status = {} 

        s2_bps = int(s2_status.get('bps', 0) or 0)
        s2_connected = bool(s2_status.get('connected', False))

        # Backward-compatible fallback for offline/legacy rows.
        if s1_bps == 0 and s2_bps == 0 and isinstance(device.ntrip_status, dict):
            s1_bps = int(device.ntrip_status.get('server1_bps', 0) or 0)
            s2_bps = int(device.ntrip_status.get('server2_bps', 0) or 0)
            s1_connected = s1_connected or (s1_bps > 0)
            s2_connected = s2_connected or (s2_bps > 0)
        
        total_bps = s1_bps + s2_bps
        
        # ===================================================================
        # TẠO HÀNG DỮ LIỆU
        # ===================================================================
        row = [
            # Thông tin chung
            device.serial,
            device.name,
            device.status,
            device.timestamp,
            human_time,
            total_bps,  # Tổng traffic
            device.detected_chip_type or "UNKNOWN",
            coordinator_name,
            device.ntrip_connected,
            
            # Base config
            base_method,
            base_lat,
            base_lon,
            base_alt,
            base_accuracy,
            
            # Server 1
            s1_enabled,
            f"v{s1_version}.0",  # Format: v1.0 hoặc v2.0
            s1_host,
            s1_port,
            s1_mountpoint,
            s1_username,
            s1_bps,
            s1_connected,
            
            # Server 2
            s2_enabled,
            f"v{s2_version}.0",
            s2_host,
            s2_port,
            s2_mountpoint,
            s2_username,
            s2_bps,
            s2_connected,
            
            # RTCM Client
            rtcm_enabled,
            rtcm_server,
            rtcm_port,
            rtcm_mountpoint,
            rtcm_username
        ]
        
        writer.writerow(row)
    
    # ===================================================================
    # TRẢ VỀ FILE CSV
    # ===================================================================
    response = StreamingResponse(
        iter([stream.getvalue()]), 
        media_type="text/csv"
    )
    
    filename = f"cors_devices_full_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    
    return response

# === ACTIVITY LOGGING ENDPOINTS ===
@app.get("/api/activity/feed", response_model=List[schemas.ActivityLogResponse])
async def get_activity_feed(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    return await crud.get_activity_logs(db, limit=limit)

@app.get("/api/activity/heatmap", response_model=schemas.HeatmapData)
async def get_heatmap_data(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    data = await crud.get_activity_heatmap(db)
    return {"data": data}

# === WEBSOCKETS ===
@app.websocket("/ws/updates")
async def ui_websocket_endpoint(websocket: WebSocket):
    await ui_manager.connect(websocket)
    # logging.info("New UI client connected.") 
    try:
        while True: 
            # Giữ kết nối sống, chờ tin nhắn (dù UI chỉ nhận chứ không gửi gì)
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        # Khi client ngắt kết nối, xóa khỏi manager
        ui_manager.disconnect(websocket)
        # logging.info("UI client disconnected.")

async def flush_device_buffer(db: AsyncSession):
    global device_update_buffer, buffer_metrics

    async with device_update_buffer_lock:
        if not device_update_buffer:
            return

        # Dedup in-place to avoid duplicating the whole buffer in memory.
        snapshot_size = len(device_update_buffer)
        unique_updates = {}
        while device_update_buffer:
            item = device_update_buffer.popleft()
            serial = item.get("serial") if isinstance(item, dict) else None
            if serial:
                unique_updates[serial] = item

    final_batch = list(unique_updates.values())
    duplicates_count = snapshot_size - len(final_batch)

    if final_batch:
        try:
            # Call batch update in crud.py
            await crud.batch_update_devices(db, final_batch)

            # Update metrics
            buffer_metrics["flush_count"] += 1
            buffer_metrics["total_updates_flushed"] += len(final_batch)
            buffer_metrics["total_duplicates_deduped"] += duplicates_count
            buffer_metrics["last_flush_time"] = time.time()
            buffer_metrics["last_buffer_size"] = snapshot_size

            if buffer_metrics["flush_count"] % 100 == 0:
                logger.info(
                    f"Buffer flush stats: flushes={buffer_metrics['flush_count']}, "
                    f"total_flushed={buffer_metrics['total_updates_flushed']}, "
                    f"dedup_ratio={buffer_metrics['total_duplicates_deduped']/max(1, buffer_metrics['total_updates_flushed']):.2%}"
                )
        except Exception as e:
            logger.error(f"Error flushing DB: {e}")

@app.websocket("/ws/pi/{serial}")
async def pi_websocket_endpoint(websocket: WebSocket, serial: str):
    await pi_manager.connect(serial, websocket)
    
    try:
        while True:
            data = await websocket.receive_json()
            message_type = data.get("type")
            payload = data.get("payload")

            if message_type == "status_update" and payload:
                compact_payload = _compact_status_payload(payload)
                if not compact_payload:
                    continue
                # Hot path: enqueue for async workers (broadcast + buffered DB write)
                try:
                    pi_status_ingest_queue.put_nowait(compact_payload)
                except asyncio.QueueFull:
                    # If overloaded, keep newest packet and drop oldest pending packet.
                    try:
                        _ = pi_status_ingest_queue.get_nowait()
                        pi_status_ingest_queue.task_done()
                        buffer_metrics["pi_ingest_queue_drop_count"] += 1
                    except Exception:
                        pass
                    try:
                        pi_status_ingest_queue.put_nowait(compact_payload)
                    except Exception:
                        pass
            
            elif message_type == "nmea_update" and payload:
                parsed_data = nmea_parser.parse(payload)
                if parsed_data:
                    await ui_manager.broadcast({
                        "type": "nmea_update",
                        "serial": serial,
                        "data": parsed_data
                    })

    except WebSocketDisconnect:
        logging.info(f"Pi '{serial}' disconnected.")
        pi_manager.disconnect(serial)

        # Lightweight offline payload: no DB lookup on disconnect to avoid pool exhaustion.
        # Device state (name, config, etc.) will be inferred by UI from last known state or loaded separately.
        offline_payload = {
            "serial": serial,
            "name": None,  # Will be filled by UI from previous cached state
            "status": "offline",
            "ntrip_connected": False,
            "bps": 0,
            "detected_chip_type": None,
            "base_config": None,
            "service_config": None,
            "is_provisioned": None,
            "ntrip_status": {
                "server1": {"connected": False, "bps": 0},
                "server2": {"connected": False, "bps": 0}
            },
            "timestamp": int(time.time())
        }
        
        # 1. Báo UI ngay (non-blocking, via queue)
        await enqueue_ui_broadcast({
            "type": "status_update",
            "data": offline_payload
        })
        
        # 2. Đẩy vÃ o buffer -> Background flush task sẽ lưu DB sau 1s
        async with device_update_buffer_lock:
            device_update_buffer.append(offline_payload)
    
    except Exception as e:
        logging.error(f"WS Error '{serial}': {e}")
        pi_manager.disconnect(serial)
        
# === GLOBAL EXCEPTION HANDLER ===
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Bắt mọi unhandled exceptions"""
    health_monitor.record_error('unhandled_exception', str(exc))
    
    logger.error(
        f"Unhandled exception: {str(exc)}",
        exc_info=True,
        extra={
            'path': request.url.path,
            'method': request.method,
            'client': request.client.host
        }
    )
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error",
            "timestamp": time.time()
        }
    )

# ============================================================================
# HEALTH MONITORING ENDPOINTS
# ============================================================================

@app.get("/api/health/detailed")
async def detailed_health_check():
    """
    Enhanced health check với system metrics cho monitoring dashboard
    """
    try:
        # System metrics
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Application metrics từ health_monitor
        health_status = health_monitor.get_health_status()
        
        return {
            "status": health_status.get("status", "unknown"),
            "timestamp": time.time(),
            
            # System Resources
            "system": {
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "memory_used_mb": memory.used / (1024 * 1024),
                "memory_total_mb": memory.total / (1024 * 1024),
                "disk_percent": disk.percent,
                "disk_used_gb": disk.used / (1024 * 1024 * 1024),
                "disk_total_gb": disk.total / (1024 * 1024 * 1024)
            },
            
            # Application Metrics
            "application": {
                "total_requests": health_status.get("total_requests", 0),
                "avg_response_time_ms": health_status.get("avg_response_time_ms", 0),
                "error_count": health_status.get("error_count", 0),
                "active_connections": len(ui_manager.active_connections) if hasattr(ui_manager, 'active_connections') else 0
            },
            
            # Circuit Breakers Status
            "circuit_breakers": {
                "mqtt": {
                    "state": mqtt_circuit_breaker.state,
                    "failure_count": mqtt_circuit_breaker.failure_count
                },
                "database": {
                    "state": db_circuit_breaker.state,
                    "failure_count": db_circuit_breaker.failure_count
                }
            }
        }
    
    except Exception as e:
        logger.error(f"Error in detailed health check: {e}", exc_info=True)
        
        # Return partial data on error
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "error",
                "error": str(e),
                "timestamp": time.time(),
                "system": {
                    "cpu_percent": 0,
                    "memory_percent": 0,
                    "disk_percent": 0
                },
                "application": {
                    "total_requests": 0,
                    "avg_response_time_ms": 0,
                    "error_count": health_monitor.error_count
                }
            }
        )

@app.get("/api/metrics/buffer")
async def buffer_metrics_endpoint():
    """Buffer and flush performance metrics for realtime monitoring"""
    global buffer_metrics, device_update_buffer
    
    current_buffer_size = len(device_update_buffer)
    uptime_since_flush = time.time() - buffer_metrics.get("last_flush_time", time.time())
    
    return {
        "status": "ok",
        "timestamp": time.time(),
        "buffer": {
            "current_size": current_buffer_size,
            "max_size": DEVICE_UPDATE_BUFFER_MAXSIZE,
            "utilization_percent": (current_buffer_size / max(1, DEVICE_UPDATE_BUFFER_MAXSIZE)) * 100,
            "queue_full_threshold": BUFFER_SIZE_THRESHOLD_HIGH,
            "warm_threshold": BUFFER_SIZE_THRESHOLD_NORMAL,
        },
        "flush": {
            "total_flushes": buffer_metrics["flush_count"],
            "total_updates_flushed": buffer_metrics["total_updates_flushed"],
            "total_duplicates_deduped": buffer_metrics["total_duplicates_deduped"],
            "ui_queue_drop_count": buffer_metrics["ui_queue_drop_count"],
            "pi_ingest_queue_drop_count": buffer_metrics["pi_ingest_queue_drop_count"],
            "status_payload_invalid_count": buffer_metrics["status_payload_invalid_count"],
            "status_payload_compacted_count": buffer_metrics["status_payload_compacted_count"],
            "dedup_ratio_percent": (
                (buffer_metrics["total_duplicates_deduped"] / 
                max(1, buffer_metrics["total_updates_flushed"])) * 100
            ),
            "seconds_since_last_flush": uptime_since_flush,
            "last_flush_size": buffer_metrics["last_buffer_size"],
        },
        "config": {
            "flush_interval_min_sec": BUFFER_FLUSH_INTERVAL_MIN,
            "flush_interval_max_sec": BUFFER_FLUSH_INTERVAL_MAX,
            "ui_broadcast_queue_size": UI_BROADCAST_QUEUE_MAXSIZE,
            "pi_ingest_queue_size": PI_STATUS_INGEST_QUEUE_MAXSIZE,
            "pi_ingest_worker_count": PI_INGEST_WORKER_COUNT,
            "device_update_buffer_max_size": DEVICE_UPDATE_BUFFER_MAXSIZE,
            "status_heavy_field_max_bytes": STATUS_HEAVY_FIELD_MAX_BYTES,
            "status_allow_heavy_fields": STATUS_ALLOW_HEAVY_FIELDS,
            "status_payload_compact_enabled": STATUS_PAYLOAD_COMPACT_ENABLED,
        }
    }

@app.get("/api/health/errors")
async def recent_errors():
    """Lấy danh sách lỗi gần đây"""
    return {
        "errors": health_monitor.get_recent_errors(),
        "total_errors": health_monitor.error_count
    }

@app.get("/health")
@app.get("/api/health")
async def simple_health_check():
    """Simple health check cho load balancer"""
    status = health_monitor.get_health_status()
    
    if status['status'] == 'healthy':
        return {"status": "ok"}
    
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "degraded", "details": status}
    )

# === ADMIN SERVER LOGS VIEWER ===
@app.get("/api/admin/server-logs")
async def get_server_logs(
    lines: int = 100, 
    level: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """
    Đọc N dòng cuối cùng từ file app.log để hiển thị lên UI.
    Chỉ Admin mới có quyền xem.
    """
    log_file = 'app.log'
    if not os.path.exists(log_file):
        return {"logs": ["Log file not found."]}

    try:
        # Tail-style read to avoid loading entire log file into memory.
        from collections import deque as _deque
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            last_lines = list(_deque(f, maxlen=max(1, lines)))
            
            # Filter theo level nếu cần (INFO, ERROR, WARNING)
            if level and level != 'ALL':
                last_lines = [line for line in last_lines if level in line]
                
            return {"logs": [line.strip() for line in last_lines]}
    except Exception as e:
        return {"logs": [f"Error reading log file: {str(e)}"]}

# === STATIC FILES ===
app.mount("/img", StaticFiles(directory="../frontend/img"), name="images")
app.mount("/", StaticFiles(directory="../frontend", html=True), name="static")

class MonitoringMiddleware(BaseHTTPMiddleware):
    """Middleware để ghi nhận metrics"""
    
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        # Rate limiting
        client_ip = request.client.host
        if not global_rate_limiter.is_allowed(client_ip):
            health_monitor.record_error('rate_limit', f'IP: {client_ip}')
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Too many requests"}
            )
        
        try:
            response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000
            
            # Ghi nhận metrics
            health_monitor.record_request(duration_ms)
            
            # Cảnh báo nếu response chậm
            if duration_ms > 1000:
                logger.warning(f"Slow request: {request.url.path} took {duration_ms:.2f}ms")
            
            return response
        
        except Exception as e:
            health_monitor.record_error('request_error', str(e))
            logger.error(f"Request error: {e}", exc_info=True)
            raise

# Global monitoring middleware

app.add_middleware(MonitoringMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://cors.aitogy.com"],  # âš ï¸ Trong production, hãy chỉ định origins cụ thể
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


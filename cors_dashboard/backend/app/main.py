# ==============================================================================
# == backend/app/main.py                ==
# ==============================================================================

import logging
import uuid
import json
import asyncio
from datetime import timedelta
import gc
import sys
import traceback
import base64
import io
import os
import time
import csv
import re
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
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse, FileResponse, StreamingResponse, JSONResponse, HTMLResponse
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
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

# --- Cấu hình Logging ---
log_file = 'app.log'
log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# Tạo FileHandler một lần để dùng chung
file_handler = logging.FileHandler(log_file, encoding='utf-8')
file_handler.setFormatter(logging.Formatter(log_format))

# Cấu hình Root Logger
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    handlers=[
        file_handler,
        logging.StreamHandler()
    ]
)

# Chèn FileHandler vào log của Uvicorn để bắt lỗi startup/server
for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    l = logging.getLogger(logger_name)
    l.addHandler(file_handler)
    l.propagate = False # Tránh bị lặp log ra console vì basicConfig đã bắt root rồi


def _fix_ntrip_pos_typo(data: bytes) -> bytes:
    # 1. Sửa lỗi gửi nhầm ký tự văn bản "\r\n" thay vì dấu xuống dòng thật
    if b"\\r\\n" in data:
        data = data.replace(b"\\r\\n", b"\r\n")

    # 2. Sửa lỗi "POS" thành "POST"
    if data.startswith(b"POS /api/ntrip/wake-"):
        idx = data.find(b" HTTP/1.")
        if idx != -1:
            return b"POST" + data[3:idx] + data[idx:]
            
    return data


try:
    from uvicorn.protocols.http.h11_impl import H11Protocol
    _orig_h11_data_received = H11Protocol.data_received
    def _patched_h11_data_received(self, data: bytes):
        _orig_h11_data_received(self, _fix_ntrip_pos_typo(data))
    H11Protocol.data_received = _patched_h11_data_received
except Exception:
    pass

try:
    from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol
    _orig_httptools_data_received = HttpToolsProtocol.data_received
    def _patched_httptools_data_received(self, data: bytes):
        _orig_httptools_data_received(self, _fix_ntrip_pos_typo(data))
    HttpToolsProtocol.data_received = _patched_httptools_data_received
except Exception:
    pass
# --- END CẢI TIẾN ---


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

# === SECURITY GATE (CHẶN QUÉT BOT RÁC) ===
ALLOWED_HTTP_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}

SECURITY_BLACKLIST_PATTERNS = {
    ".env", ".php", ".git", ".jsp", ".asp", ".aspx", ".bak", ".swp", ".sql", ".exe", ".cgi",
    "wp-admin", "wp-login", "phpmyadmin", "server-status", "cgi-bin", "xmlrpc.php",
    "config.php", "config.bak", "setup.php", "mysql-admin", "administrator",
    "info.php", "test.php", ".htaccess", ".htpasswd", ".DS_Store", "public/.env", "backend/.env"
}

# --- IP REPUTATION SYSTEM ---
VIOLATION_THRESHOLD = 5  # Số lần vi phạm tối đa trước khi BAN
BAN_DURATION = 3600      # Thời gian BAN (giây) - 1 giờ
security_rep_scores: Dict[str, int] = {} # IP -> score
banned_ips: Dict[str, float] = {}        # IP -> ban_expiry_timestamp


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


def _read_caster_config_from_env() -> tuple[list[str], dict]:
    """Reads caster config from .env and supports multiple CSV hosts."""
    env_values = dotenv_values(ENV_FILE_PATH)
    raw_host = str(env_values.get("CASTER_HOST", "")).strip()
    raw_port = env_values.get("CASTER_PORT")
    raw_user = env_values.get("CASTER_ADMIN_USER")
    raw_pass = env_values.get("CASTER_PASSWORD")

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

# TỐI ƯU CỰC ĐỘ: Cache mapping Mountpoint -> Serial để tránh quét DB liên tục
# Giúp xử lý hàng nghìn yêu cầu wake-station mỗi giây mà không làm treo Backend.
mountpoint_serial_cache: Dict[str, str] = {}
mountpoint_cache_last_updated: float = 0
mountpoint_cache_lock = asyncio.Lock()


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
        "version",
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
            
            # === CASTER STATUS SYNC: Detect per-server status changes ===
            serial = payload.get("serial") if isinstance(payload, dict) else None
            if serial:
                try:
                    async with device_session_scope() as db:
                        device = await crud.get_device_by_serial(db, serial)
                        if device:
                            # Trigger status change detection (pass payload for rich ntrip_stream_state)
                            await _on_device_status_update(device, payload)
                except Exception as e:
                    logger.warning(f"[CASTER SYNC] Error detecting status change for '{serial}': {e}")
            
            async with device_update_buffer_lock:
                device_update_buffer.append(payload)
        except Exception as e:
            logger.warning(f"PI status ingest worker error: {e}")
        finally:
            pi_status_ingest_queue.task_done()

rtcm_keepalive_miss_counts = {}  # {(username, mountpoint): miss_count}
rtcm_last_auto_wake_times = {} # {serial: last_timestamp}
rtcm_session_lock = asyncio.Lock()
_ENV_VALUES = dotenv_values(ENV_FILE_PATH)


def _get_env_preferring_dotenv(key: str, default: str) -> str:
    val = _ENV_VALUES.get(key)
    if val is not None and str(val).strip() != "":
        return str(val)
    return str(os.getenv(key, default))


RTCM_MIN_TTL = 30
RTCM_MAX_TTL = 600
RTCM_NTRIP_CLIENTTABLE_CHECK_INTERVAL = float(
    _get_env_preferring_dotenv("RTCM_NTRIP_CLIENTTABLE_CHECK_INTERVAL", "5")
)
RTCM_NTRIP_CLIENTTABLE_ACTIVE_CHECK_INTERVAL = max(
    0.5,
    float(
        _get_env_preferring_dotenv("RTCM_NTRIP_CLIENTTABLE_ACTIVE_CHECK_INTERVAL", "1.0")
    ),
)
RTCM_KEEPALIVE_MISS_THRESHOLD = max(
    1,
    int(
        _get_env_preferring_dotenv("RTCM_KEEPALIVE_MISS_THRESHOLD", "15")
    ),
)
RTCM_KEEPALIVE_EMPTY_MOUNTPOINT_MISS_THRESHOLD = max(
    1,
    int(
        _get_env_preferring_dotenv(
            "RTCM_KEEPALIVE_EMPTY_MOUNTPOINT_MISS_THRESHOLD",
            str(RTCM_KEEPALIVE_MISS_THRESHOLD),
        )
    ),
)
RTCM_KEEPALIVE_FETCH_GRACE_SECONDS = max(
    5,
    int(
        _get_env_preferring_dotenv("RTCM_KEEPALIVE_FETCH_GRACE_SECONDS", "45")
    ),
)
RTCM_KEEPALIVE_NEW_SESSION_GRACE_SECONDS = max(
    5,
    int(
        _get_env_preferring_dotenv("RTCM_KEEPALIVE_NEW_SESSION_GRACE_SECONDS", "25")
    ),
)
RTCM_KEEPALIVE_SKIP_CLIENTTABLE = str(
    _get_env_preferring_dotenv("RTCM_KEEPALIVE_SKIP_CLIENTTABLE", "0")
).strip().lower() in ("1", "true", "yes", "on")
RTCM_KEEPALIVE_DEBUG = str(
    _get_env_preferring_dotenv("RTCM_KEEPALIVE_DEBUG", "0")
).strip().lower() in ("1", "true", "yes", "on")


# Global to track last activation command time per station to prevent "command storms" (debounce)
last_station_activation_ts: Dict[str, float] = {}

# === CASTER STATUS SYNC ===
# Cache per-device last known per-server status để detect changes
# { serial: { "server1": {...}, "server2": {...} } }
device_status_cache: Dict[str, dict] = {}
device_status_cache_lock = asyncio.Lock()



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
    cleaned = str(value).strip().strip('/')
    return cleaned.lower() if cleaned else None


def _resolve_preferred_server_id_for_mountpoint(cfg: dict, mountpoint: Optional[str], requested_address: Optional[str] = None) -> Optional[int]:
    mountpoint_norm = _normalize_mountpoint(mountpoint)
    if not mountpoint_norm:
        return None

    candidates: List[int] = []
    for sid in (1, 2):
        # Route selection should still identify ownership even if enabled flag is stale.
        # Worker start is gated elsewhere by server{sid}_enabled.
        sid_mp_norm = _normalize_mountpoint(cfg.get(f"mountpoint{sid}"))
        if sid_mp_norm and sid_mp_norm == mountpoint_norm:
            candidates.append(sid)

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # CASE 1: Prioritize explicit address match (for newer casters)
    if requested_address:
        normalized_req_addr = requested_address.strip().lower()
        for sid in candidates:
            host = str(cfg.get(f"serverhost{sid}", "") or "").strip().lower()
            if host and (normalized_req_addr in host or host in normalized_req_addr):
                logger.info(
                    f"[RTCM ROUTE] Explicit address match: mountpoint='{mountpoint_norm}' "
                    f"requested_addr='{requested_address}' candidates={candidates} selected_server={sid}"
                )
                return sid

    # CASE 2: Fallback to .env preferred host (for older casters or missing address)
    if settings.ENABLE_PREFERRED_HOST and settings.PREFERRED_HOST_MARKER:
        marker = settings.PREFERRED_HOST_MARKER.strip().lower()
        for sid in candidates:
            host = str(cfg.get(f"serverhost{sid}", "") or "").strip().lower()
            if host and marker in host:
                logger.info(
                    f"[RTCM ROUTE] Preferred host match: mountpoint='{mountpoint_norm}' "
                    f"marker='{marker}' candidates={candidates} selected_server={sid}"
                )
                return sid

    selected = sorted(candidates)[0]
    logger.info(
        f"[RTCM ROUTE] mountpoint='{mountpoint_norm}' candidates={candidates} "
        f"selected_server={selected}"
    )
    return selected


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
        return "sleep"
    return "starting"


async def _set_station_stream(serial: str, active: bool, mountpoint: Optional[str] = None, requested_address: Optional[str] = None) -> None:
    requested_mp_norm = _normalize_mountpoint(mountpoint) if mountpoint else None
    payload = {"active": bool(active)}
    if mountpoint:
        payload["mountpoint"] = str(mountpoint)

    is_virtual_station = False
    try:
        async with device_session_scope() as db:
            _device = await crud.get_device_by_serial(db, serial)
            is_virtual_station = str(getattr(_device, "detected_chip_type", "") or "").upper() == "VIRTUAL"
            if _device and requested_mp_norm:
                cfg_preview = dict(getattr(_device, "service_config", None) or {})
                if bool(active) or requested_address:
                    preferred_sid = _resolve_preferred_server_id_for_mountpoint(cfg_preview, requested_mp_norm, requested_address)
                    if preferred_sid in (1, 2):
                        payload["server_id"] = preferred_sid
                        # Include the host address so the agent can match precisely
                        host_key = f"serverhost{preferred_sid}"
                        if host_key in cfg_preview:
                            payload["address"] = str(cfg_preview[host_key] or "").strip()
    except Exception:
        is_virtual_station = False

    if not is_virtual_station:
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

            target_server_id = None
            if bool(active) or requested_address:
                target_server_id = _resolve_preferred_server_id_for_mountpoint(cfg, requested_mp_norm, requested_address)

            # Keep per-server flags aligned so UI and control plane remain consistent
            # before the next status heartbeat from agent.
            if bool(active) and requested_mp_norm:
                if target_server_id in (1, 2):
                    cfg[f"server{target_server_id}_stream_on_demand"] = True
                    cfg[f"server{target_server_id}_stream_active"] = True
                else:
                    for sid in (1, 2):
                        sid_mp_norm = _normalize_mountpoint(cfg.get(f"mountpoint{sid}"))
                        if sid_mp_norm and sid_mp_norm == requested_mp_norm:
                            cfg[f"server{sid}_stream_on_demand"] = True
                            cfg[f"server{sid}_stream_active"] = True

            if not bool(active):
                if target_server_id in (1, 2):
                    sid_on_demand_key = f"server{target_server_id}_stream_on_demand"
                    sid_active_key = f"server{target_server_id}_stream_active"
                    sid_on_demand = bool(cfg.get(sid_on_demand_key, cfg.get("stream_on_demand", False)))
                    if sid_on_demand:
                        cfg[sid_on_demand_key] = True
                        cfg[sid_active_key] = False
                else:
                    for sid in (1, 2):
                        sid_mp_norm = _normalize_mountpoint(cfg.get(f"mountpoint{sid}"))
                        if (not requested_mp_norm) or (sid_mp_norm == requested_mp_norm):
                            sid_on_demand_key = f"server{sid}_stream_on_demand"
                            sid_active_key = f"server{sid}_stream_active"
                            sid_on_demand = bool(cfg.get(sid_on_demand_key, cfg.get("stream_on_demand", False)))
                            if sid_on_demand:
                                cfg[sid_on_demand_key] = True
                                cfg[sid_active_key] = False

            if bool(active):
                if mountpoint:
                    cfg["active_mountpoint"] = str(mountpoint)
            else:
                cfg.pop("active_mountpoint", None)
                # Reflect immediate off state in top-level flags.
                device.ntrip_connected = False
                device.bps = 0

            # Keep top-level stream_active aligned with effective per-server push state.
            any_server_can_push = False
            for sid in (1, 2):
                sid_enabled = bool(cfg.get(f"server{sid}_enabled", False))
                if not sid_enabled:
                    continue
                sid_on_demand = bool(cfg.get(f"server{sid}_stream_on_demand", cfg.get("stream_on_demand", False)))
                sid_active = bool(cfg.get(f"server{sid}_stream_active", cfg.get("stream_active", False)))
                if (not sid_on_demand) or sid_active:
                    any_server_can_push = True
                    break
            cfg["stream_active"] = any_server_can_push

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


def _normalize_keepalive_client_id(value: Optional[str]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered.startswith("ntrip-mqtt:"):
        return text.split(":", 1)[1].strip().lower()
    return text.lower()


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


async def _ntrip_mqtt_start(serial: str, mountpoint: str, client_instance_id: str, ttl_seconds: int = 90, requested_address: Optional[str] = None, caster_host: Optional[str] = None):
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
                    caster_host=caster_host or requested_address,
                    created_at=now,
                    last_heartbeat_at=now,
                )
            )
            await db.commit()

        # Only trigger an agent command if:
        # 1. This is the first active session (should_activate is True)
        # 2. OR the station is reported as not connected (might have dropped)
        # 3. AND we haven't sent a START command in the last 30 seconds (Debounce/Anti-Storm)
        last_ts = last_station_activation_ts.get(serial, 0)
        cooldown_elapsed = now - last_ts
        is_cooling_down = cooldown_elapsed < 30 # 30 seconds debounce

        wake_status = "joined-existing"
        if should_activate or not getattr(device, 'ntrip_connected', False):
            if is_cooling_down:
                logger.info(
                    f"[RTCM] Skipping redundant activation for {serial} (mp={selected_mountpoint}) "
                    f"- Command storm protection active ({int(cooldown_elapsed)}s elapsed)"
                )
                wake_status = "cooldown-skip"
            else:
                logger.info(f"[RTCM] Triggering activation for {serial} (mp={selected_mountpoint})")
                last_station_activation_ts[serial] = now
                await _set_station_stream(serial, active=True, mountpoint=selected_mountpoint, requested_address=requested_address)
                wake_status = "activation-sent"
        else:
            logger.info(f"[RTCM] Joined existing pool for {serial} (mp={selected_mountpoint}, count={active_before_same_mp + 1})")

        return {
            "wake_status": wake_status,
            "connected": bool(getattr(device, "ntrip_connected", False)),
            "selected_mountpoint": str(selected_mountpoint),
            "cooldown_elapsed": int(cooldown_elapsed),
            "should_activate": bool(should_activate),
            "active_sessions_same_mountpoint": int(active_before_same_mp + 1),
        }


def _device_has_enabled_mountpoint(device: models.Device, mountpoint: str) -> bool:
    requested = _normalize_mountpoint(mountpoint)
    if not requested:
        return False

    cfg = device.service_config or {}
    s1_ok = bool(cfg.get("server1_enabled")) and _normalize_mountpoint(cfg.get("mountpoint1")) == requested
    s2_ok = bool(cfg.get("server2_enabled")) and _normalize_mountpoint(cfg.get("mountpoint2")) == requested
    return s1_ok or s2_ok


async def _resolve_station_serial_by_mountpoint(db: AsyncSession, mountpoint: str) -> str:
    global mountpoint_serial_cache, mountpoint_cache_last_updated
    requested = _normalize_mountpoint(mountpoint)
    if not requested:
        raise ValueError("mountpoint is required")

    now = time.time()
    # Nếu cache còn hạn (60 giây), dùng luôn để phản hồi SIÊU TỐC
    async with mountpoint_cache_lock:
        if requested in mountpoint_serial_cache and (now - mountpoint_cache_last_updated) < 60:
            return mountpoint_serial_cache[requested]

        # Cache hết hạn hoặc không có -> Quét DB 1 lần duy nhất để rebuild toàn bộ cache
        if (now - mountpoint_cache_last_updated) >= 60:
            logger.info("[CACHE] Rebuilding Mountpoint-to-Serial cache...")
            new_cache = {}
            rs = await db.execute(select(models.Device.serial, models.Device.service_config))
            for row in rs.all():
                serial = str(row[0])
                
                # 🔥 FIX: Xử lý an toàn service_config (có thể là dict hoặc string)
                raw_cfg = row[1]
                if isinstance(raw_cfg, dict):
                    cfg = raw_cfg
                elif isinstance(raw_cfg, str):
                    try: cfg = json.loads(raw_cfg)
                    except: cfg = {}
                else:
                    cfg = {}
                
                for sid in (1, 2):
                    mp = _normalize_mountpoint(cfg.get(f"mountpoint{sid}"))
                    if mp:
                        # Nếu trùng mountpoint, ưu tiên trạm nào đang online (nhưng ở đây ta lấy hết)
                        if mp not in new_cache:
                            new_cache[mp] = serial
            
            mountpoint_serial_cache = new_cache
            mountpoint_cache_last_updated = now
        
        if requested in mountpoint_serial_cache:
            return mountpoint_serial_cache[requested]

    raise ValueError(f"No station found for mountpoint '{mountpoint}'")


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

        # === CHECK BLOCKLIST TRƯỚC KHI MỞ LUỒNG ===
        block_reason = await _check_rover_blocked(client_instance_id, mountpoint)
        if block_reason:
            logger.warning(
                f"[NTRIP MQTT] BLOCKED user='{client_instance_id}' "
                f"mountpoint='{mountpoint}' reason='{block_reason}'"
            )
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

    next_sleep_seconds = RTCM_NTRIP_CLIENTTABLE_CHECK_INTERVAL

    while True:
        await asyncio.sleep(next_sleep_seconds)
        now = int(time.time())

        try:
            # 1. Fetch active sessions from DB
            async with rtcm_session_lock:
                async with device_session_scope() as db:
                    rs = await db.execute(
                        select(
                            models.RTCMStreamSession.session_id,
                            models.RTCMStreamSession.client_id,
                            models.RTCMStreamSession.serial,
                            models.RTCMStreamSession.mountpoint,
                            models.RTCMStreamSession.caster_host,
                            models.RTCMStreamSession.expires_at,
                            models.RTCMStreamSession.created_at
                        ).where(models.RTCMStreamSession.expires_at > now)
                    )
                    active_rows = [
                        (str(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4] or ""), int(r[5] or 0), int(r[6] or 0)) 
                        for r in rs.all() if r and r[0]
                    ]

            if not active_rows:
                # No active sessions to check.
                continue

            # 2. Match each session against Global Caster Cache
            keepalive_to = now + 90
            updated_ids = []
            expired_ids = []
            candidates_for_deactivation = set()

            # Group sessions by host for easier lookup
            for sid, client_id, serial, mountpoint, host, expires_at, created_at in active_rows:
                # Resolve host to find cache entry
                target_host = host or settings.CASTER_HOST or "localhost"
                port = int(settings.CASTER_PORT or 2101)
                cache_key = f"{target_host}:{port}"
                
                hosts_to_check = [target_host]
                other_hosts = [h.strip() for h in settings.CASTER_HOST.split(",") if h.strip() and h.strip() != target_host]
                hosts_to_check.extend(other_hosts)

                found_on_host = None
                in_clienttable = False
                # Check if the primary host's cache has an error (connection failure, etc.)
                primary_cache = global_caster_data_cache.get(f"{target_host}:{port}")
                caster_error_on_primary = bool(primary_cache and primary_cache.get("error"))
                for h in hosts_to_check:
                    h_port = int(settings.CASTER_PORT or 2101)
                    h_key = f"{h}:{h_port}"
                    h_cache = global_caster_data_cache.get(h_key)
                    if not h_cache or h_cache.get("error"): continue

                    clients = h_cache.get("clients", [])
                    session_mp_norm = _normalize_mountpoint(mountpoint)
                    session_client_norm = _normalize_keepalive_client_id(client_id)
                    
                    found_in_h = False
                    for item in clients:
                        mp = _normalize_mountpoint(item.get("mountpoint"))
                        if mp != session_mp_norm: continue
                        
                        row_ids = {
                            _normalize_keepalive_client_id(item.get("client_id")),
                            _normalize_keepalive_client_id(item.get("receiver_id")),
                            _normalize_keepalive_client_id(item.get("username")),
                        } - {""}
                        
                        if session_client_norm in row_ids:
                            found_in_h = True
                            break
                    if found_in_h:
                        found_on_host = h
                        break

                if found_on_host:
                    rtcm_keepalive_miss_counts.pop((client_id, mountpoint), None)
                    updated_ids.append(sid)
                    if found_on_host != target_host:
                        logger.info(f"[RTCM KEEPALIVE] User '{client_id}' switched from {target_host} to {found_on_host}. Updating session.")
                        async with rtcm_session_lock:
                            async with device_session_scope() as db:
                                await db.execute(
                                    update(models.RTCMStreamSession)
                                    .where(models.RTCMStreamSession.session_id == sid)
                                    .values(caster_host=found_on_host)
                                )
                                await db.commit()
                else:
                    # Grace period for new sessions (60s default)
                    session_age = now - created_at
                    if session_age < RTCM_KEEPALIVE_NEW_SESSION_GRACE_SECONDS:
                        updated_ids.append(sid)
                        continue

                    # SAFETY LOCK: Only skip if the primary caster actually had an error.
                    # If it's healthy but empty, we MUST allow miss counting.
                    if caster_error_on_primary:
                        updated_ids.append(sid)
                        continue

                    m_key = (client_id, mountpoint)
                    miss_count = rtcm_keepalive_miss_counts.get(m_key, 0) + 1
                    rtcm_keepalive_miss_counts[m_key] = miss_count

                    if miss_count >= RTCM_KEEPALIVE_MISS_THRESHOLD:
                        expired_ids.append(sid)
                        candidates_for_deactivation.add((serial, _normalize_mountpoint(mountpoint)))
                        logger.warning(f"[RTCM] User '{client_id}' (@{mountpoint}) on station {serial} expired after {miss_count} misses.")
                    else:
                        if RTCM_KEEPALIVE_DEBUG:
                            logger.info(f"[RTCM DEBUG] User '{client_id}' miss {miss_count}/{RTCM_KEEPALIVE_MISS_THRESHOLD} on {target_host}")

            # 3. Update DB
            if updated_ids or expired_ids:
                async with rtcm_session_lock:
                    async with device_session_scope() as db:
                        if updated_ids:
                            # Batch update heartbeats
                            await db.execute(
                                update(models.RTCMStreamSession)
                                .where(models.RTCMStreamSession.session_id.in_(updated_ids))
                                .values(expires_at=keepalive_to, last_heartbeat_at=now)
                            )
                        if expired_ids:
                            # Batch expire sessions
                            await db.execute(
                                update(models.RTCMStreamSession)
                                .where(models.RTCMStreamSession.session_id.in_(expired_ids))
                                .values(expires_at=now - 1)
                            )
                        await db.commit()

            # 4. Station Deactivation (On-Demand only)
            for serial, mp_norm in candidates_for_deactivation:
                if not mp_norm: continue
                async with device_session_scope() as db:
                    # Check if ANY active session still remains for this (serial, mp)
                    remain_q = select(func.count(models.RTCMStreamSession.session_id)).where(
                        models.RTCMStreamSession.serial == serial,
                        models.RTCMStreamSession.expires_at > now
                    )
                    remain_rs = await db.execute(remain_q)
                    remaining = remain_rs.scalar() or 0

                    if remaining == 0:
                        await _set_station_stream(serial, active=False)
                        logger.info(f"[RTCM] Deactivated station {serial} (mp={mp_norm}, no remaining sessions)")

        except Exception as e:
            logger.error(f"[RTCM] Keepalive task error: {e}", exc_info=True)

async def rtcm_session_reaper_task():
    while True:
        await asyncio.sleep(5)
        now = int(time.time())
        stations_to_deactivate: Set[tuple[str, str]] = set()

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

                        # CRITICAL: Skip deactivation if station is Always-On
                        cfg = device.service_config or {}
                        if not cfg.get("stream_on_demand", True):
                            continue

                        active_count = await _count_active_mountpoint_sessions(db, serial, expired_mountpoint, now)
                        if active_count == 0:
                            stations_to_deactivate.add((serial, _normalize_mountpoint(expired_mountpoint) or ""))

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
                        stations_to_deactivate.add((str(device.serial), _normalize_mountpoint(active_mountpoint) or ""))

        for serial, mountpoint_norm in stations_to_deactivate:
            try:
                device_label = f"{serial}@{mountpoint_norm}" if mountpoint_norm else serial
                logger.info(f"[RTCM REAPER] Sending deactivation command to station {device_label}")
                await _set_station_stream(serial, active=False, mountpoint=mountpoint_norm or None)
            except Exception as e:
                logger.warning(f"[RTCM REAPER] Failed to auto-deactivate station '{serial}': {e}")

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
        "error": "â Œ",
        "warning": "âš ï¸ ",
        "info": "â„¹ï¸ "
    }
    try:
        log_entry = schemas.ActivityLogCreate(
            timestamp=int(time.time()),
            type=type,
            message=message,
            icon=icon_map.get(type, "ðŸ“ "),
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
                "icon": icon_map.get(type, "ðŸ“ "),
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
    """Filter thêm request_id vào mọi log"""
    def filter(self, record):
        record.request_id = getattr(record, 'request_id', 'N/A')
        return True

# Thêm filter vào root logger
for handler in logging.root.handlers:
    handler.addFilter(RequestIDFilter())

class SecurityGateMiddleware(BaseHTTPMiddleware):
    """
    Middleware bảo mật: Chặn các yêu cầu quét tự động (bots) có chứa pattern nhạy cảm.
    """
    async def dispatch(self, request: Request, call_next):
        method = request.method.upper()
        path = request.url.path.lower()
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        # 1. Kiểm tra xem IP có đang bị BAN không
        if client_ip in banned_ips:
            if now < banned_ips[client_ip]:
                # Vẫn đang bị BAN
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Your IP has been temporarily banned due to multiple security violations."}
                )
            else:
                # Đã hết hạn BAN -> Xóa khỏi danh sách để bắt đầu lại (ân xá)
                del banned_ips[client_ip]
                security_rep_scores[client_ip] = 0

        # Helper để ghi nhận vi phạm và thực hiện BAN nếu cần
        def record_violation():
            if client_ip == "unknown": return
            score = security_rep_scores.get(client_ip, 0) + 1
            security_rep_scores[client_ip] = score
            if score >= VIOLATION_THRESHOLD:
                banned_ips[client_ip] = now + BAN_DURATION
                logger.critical(f"🚨 AUTOMATIC BAN: IP {client_ip} has been BANNED for {BAN_DURATION}s after {score} violations.")

        # 2. Chặn Method lạ (Linh tinh)
        if method not in ALLOWED_HTTP_METHODS:
            logger.warning(f"🚫 SECURITY BLOCK (METHOD): IP {client_ip} used forbidden method {method} on {path}")
            record_violation()
            return JSONResponse(
                status_code=403,
                content={"detail": f"Method {method} is not allowed."}
            )

        # 3. Chặn các pattern nhạy cảm (Bots scanning)
        for pattern in SECURITY_BLACKLIST_PATTERNS:
            blocked = False
            if pattern.startswith("."):
                if path.endswith(pattern) or f"{pattern}?" in path or f"{pattern}/" in path:
                    blocked = True
            elif "/" in pattern: # Check path substring
                if pattern in path:
                    blocked = True
            else:
                if pattern in path:
                    blocked = True
            
            if blocked:
                logger.warning(f"⚠️ SECURITY BLOCK (PATH): IP {client_ip} tried suspicious path: {path} (Pattern: {pattern})")
                record_violation()
                return JSONResponse(
                    status_code=403, 
                    content={"detail": "Access Forbidden: Security Gate Blocked this request."}
                )
        
        return await call_next(request)

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

async def memory_guard_task():
    """Tác vụ định kỳ dọn dẹp RAM cho Backend."""
    logger.info("[MEM GUARD] Backend memory guard started")
    while True:
        try:
            collected = gc.collect()
            import psutil
            process = psutil.Process()
            ram_mb = process.memory_info().rss / (1024 * 1024)
            if ram_mb > 500: # Backend thường dùng nhiều hơn Agent
                logger.warning(f"[MEM GUARD] Backend RAM Usage: {ram_mb:.2f} MB. GC collected {collected} objects.")
            else:
                logger.debug(f"[MEM GUARD] RAM: {ram_mb:.2f} MB")
        except Exception as e:
            logger.error(f"[MEM GUARD] Error: {e}")
        await asyncio.sleep(1800) # Mỗi 30 phút

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context với proper error handling, khởi động MQTT vÃ  Rate Limiter.
    """
    global virtual_runtime_manager
    logger.info("ðŸš€ Application starting...")
    
    # List để track tất cả background tasks
    tasks = []
    
    try:
        # ===================================================================
        # BƯỚC 1: INITIALIZE DATABASES
        # ===================================================================
        try:
            from sqlalchemy import text
            async with engine.begin() as conn:
                await conn.run_sync(models.Base.metadata.create_all)
                
                # Auto-migrate missing columns for existing databases
                try:
                    await conn.execute(text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS version VARCHAR;"))
                    await conn.execute(text("ALTER TABLE rtcm_stream_sessions ADD COLUMN IF NOT EXISTS caster_host VARCHAR;"))
                except Exception as e_mig:
                    logger.warning(f"Auto-migration failed (ignores on fresh DBs): {e_mig}")
            logger.info("âœ“ Device database initialized")
            
            async with auth_engine.begin() as conn:
                await conn.run_sync(AuthBase.metadata.create_all)
                try:
                    await conn.execute(text("ALTER TABLE rover_accounts ADD COLUMN IF NOT EXISTS rtcm_cycle_enabled BOOLEAN DEFAULT FALSE;"))
                    await conn.execute(text("ALTER TABLE rover_accounts ADD COLUMN IF NOT EXISTS rtcm_disconnect_interval_seconds INTEGER DEFAULT 300;"))
                except Exception as e_mig_auth:
                    logger.warning(f"Auth auto-migration failed (ignored on fresh DBs): {e_mig_auth}")
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
                    logger.info(f"ðŸ—‘ï¸  Cleaned up {deleted_count} ghost devices on startup")
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
                                    models.Device.status.in_(['online', 'sleep']),
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
        tasks.append(asyncio.create_task(memory_guard_task()))
        for _ in range(max(1, PI_INGEST_WORKER_COUNT)):
            tasks.append(asyncio.create_task(pi_status_ingest_worker_task()))
            
        # Start global caster monitor (synchronizes multiple caster servers into local cache)
        tasks.append(asyncio.create_task(caster_monitor_task()))

        logger.info("âœ“ Background tasks started")
        logger.info("=" * 60)
        logger.info("ðŸŽ‰ Application startup complete!")
        logger.info("=" * 60)
        
        yield
        
    finally:
        logger.info("ðŸ›‘ Application shutting down...")

        # --- BƯỚC 1: DỪNG MQTT TRƯỚC (Ngắt nguồn dữ liệu vào) ---
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
                    .where(models.Device.status.in_(['online', 'sleep']))
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

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Xử lý lỗi 422 validation từ Pydantic và log ra chi tiết lỗi để debug.
    """
    exc_str = f"{exc}".replace("\n", " ").replace("   ", " ")
    logging.error(f"❌ Validation Error: {exc_str} | Payload: {await request.body()}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors(), "body": str(exc.body)},
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Bắt mọi lỗi không mong muốn, ghi log kèm Traceback chi tiết vào app.log.
    """
    logging.error(f"❌ Global Unhandled Exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Lỗi hệ thống nội bộ. Vui lòng kiểm tra app.log để biết chi tiết."}
    )

app.add_middleware(SecurityGateMiddleware) # SECURITY FIRST
app.add_middleware(RequestIDMiddleware) 

# === NTRIP WAKE STATION HTTP ENDPOINT ===
@app.get("/api/ntrip/wake-listen", response_class=PlainTextResponse)
@app.get("/api/ntrip/wake-station", response_class=PlainTextResponse)
async def ntrip_wake_listen_get(
    mountpoint: str = Query(...),
    client_instance_id: Optional[str] = Query(None),
    receiver_id: Optional[str] = Query(None),
    identifier: Optional[str] = Query(None),
    address: Optional[str] = Query(None),
    caster_address: Optional[str] = Query(None),
    host: Optional[str] = Query(None),
    ip: Optional[str] = Query(None),
    ttl_seconds: int = 90
):
    """
    Endpoint cho NTRIP gửi lệnh bật trạm qua HTTP GET.
    Ví dụ: /api/ntrip/wake-listen?mountpoint=HN01&identifier=test-client&address=aitogy.com.vn
    """
    id_to_use = client_instance_id or receiver_id or identifier or "http-get"
    addr_to_use = caster_address or address or host or ip
        
    return await _do_ntrip_wake(mountpoint, id_to_use, ttl_seconds, requested_address=addr_to_use)

@app.post("/api/ntrip/wake-station", response_class=PlainTextResponse)
async def ntrip_wake_station(payload: dict = Body(...)):
    """
    Endpoint cho NTRIP gửi lệnh bật trạm qua HTTP POST.
    Yêu cầu JSON: {"mountpoint": "...", "client_instance_id": "...", "ttl_seconds": 90}
    """
    mountpoint = payload.get("mountpoint")
    address = payload.get("caster_address") or payload.get("address") or payload.get("host") or payload.get("ip")
    # Hỗ trợ nhiều loại alias cho ID
    client_instance_id = (
        payload.get("client_instance_id") or 
        payload.get("receiver_id") or 
        payload.get("identifier")
    )
    ttl_seconds = int(payload.get("ttl_seconds", 90))
    
    
    if not mountpoint:
        return PlainTextResponse("ERROR: mountpoint is required", status_code=400)
        
    return await _do_ntrip_wake(mountpoint, client_instance_id or "http-post", ttl_seconds, requested_address=address)

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
        
        # Set timeout 3 seconds
        with urllib.request.urlopen(request, timeout=3) as response:
            result = response.read().decode('utf-8')
            state = status_payload.get('state', 'unknown')
            mp = status_payload.get('mountpoint', '?')
            # 📉 Thay đổi từ logger.info sang logger.debug để giảm rác log theo yêu cầu
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


async def _check_rover_blocked(username: str, mountpoint: Optional[str] = None) -> Optional[str]:
    """Kiểm tra xem rover có bị chặn trong blocklist DB không."""
    try:
        async with auth_session_scope() as auth_db:
            # Query tất cả block của user này
            block_q = select(models.RoverBlocklist).where(models.RoverBlocklist.username == username)
            block_rs = await auth_db.execute(block_q)
            blocks = block_rs.scalars().all()

            for b in blocks:
                # 1. Chặn tất cả (Global block)
                if not b.mountpoint:
                    return b.reason or "Account is globally blocked"
                
                # 2. Chặn theo Mountpoint cụ thể
                if mountpoint and b.mountpoint.lower().strip() == mountpoint.lower().strip():
                    return b.reason or f"Blocked on mountpoint: {mountpoint}"
                    
    except Exception as e:
        logger.warning(f"[NTRIP AUTH] Error checking blocklist for '{username}': {e}")
    return None

async def _do_ntrip_wake(mountpoint: str, client_id: str, ttl: int, requested_address: Optional[str] = None):
    """Xử lý wake trạm, trả về PlainTextResponse chuẩn cho Caster."""
    logger.info(f"[NTRIP Wake] mountpoint={mountpoint}, client_id={client_id}, address={requested_address or 'not-provided'}")

    # 1. Check Blocklist ngay lập tức
    reason = await _check_rover_blocked(client_id, mountpoint)
    if reason:
        msg = f"BLOCKED: {reason}"
        logger.warning(f"[NTRIP Wake] {msg} for {client_id}@{mountpoint}")
        return PlainTextResponse(f"WAKE_STATUS: blocked\n{msg}")

    # 2. Bật trạm nếu hợp lệ
    try:
        async with device_session_scope() as db:
            serial = await _resolve_station_serial_by_mountpoint(db, mountpoint)
            device = await crud.get_device_by_serial(db, serial)
            if not device:
                return PlainTextResponse(f"WAKE_STATUS: error\nERROR: Trạm {serial} không tồn tại")
            
            # Kiểm tra trạng thái trạm
            if str(getattr(device, 'status', '')).lower() == 'offline':
                logger.warning(f"[NTRIP Wake] Station '{serial}' is OFFLINE. Cannot wake up.")
                return PlainTextResponse(f"WAKE_STATUS: offline\nERROR: Trạm {device.name or serial} đang offline")

            name = device.name if device else serial
            
            # 🔥 FIX: Robustly handle service_config type to avoid 'length 29; 2 is required' error
            raw_cfg = getattr(device, "service_config", None)
            if isinstance(raw_cfg, dict):
                cfg = raw_cfg
            elif isinstance(raw_cfg, str):
                import json
                try: cfg = json.loads(raw_cfg)
                except: cfg = {}
            else:
                cfg = {}
            selected_server_id = _resolve_preferred_server_id_for_mountpoint(cfg, mountpoint, requested_address)
            selected_server_host = ""
            if selected_server_id in (1, 2):
                selected_server_host = str(cfg.get(f"serverhost{selected_server_id}", "") or "").strip()
            
            wake_result = await _ntrip_mqtt_start(
                serial=serial,
                mountpoint=mountpoint,
                client_instance_id=client_id,
                ttl_seconds=ttl,
                requested_address=requested_address,
                caster_host=selected_server_host
            )

            wake_status = str(wake_result.get("wake_status", "unknown"))
            connected_now = bool(wake_result.get("connected", False))
            selected_mp = str(wake_result.get("selected_mountpoint", mountpoint) or mountpoint)
            stream_hint = "streaming" if connected_now else "pending"

            if wake_status == "activation-sent":
                wake_note = "đã gửi lệnh bật trạm"
            elif wake_status == "cooldown-skip":
                wake_note = "bỏ qua lệnh bật do chống bão reconnect"
            elif wake_status == "joined-existing":
                wake_note = "đã ghép vào phiên đang hoạt động"
            else:
                wake_note = "đã xử lý yêu cầu wake"

            if selected_server_id in (1, 2):
                logger.info(
                    f"[NTRIP Wake] route serial={serial} mountpoint={mountpoint} "
                    f"server_id={selected_server_id} host={selected_server_host or 'unknown'}"
                )

            # Trả trạng thái rõ ràng để caster phân biệt "đã gửi lệnh" và "đang streaming".
            response_lines = [
                f"WAKE_STATUS: {wake_status}",
                f"STREAM_HINT: {stream_hint}",
                f"MOUNTPOINT: {selected_mp}",
                "Lắng nghe thành công",
                f"OK bật trạm thành công ({wake_note}) {name}",
            ]
            return PlainTextResponse("\n".join(response_lines))
            
    except Exception as e:
        msg = f"ERROR: {e}"
        logger.error(f"[NTRIP Wake ERR] {msg}")
        return PlainTextResponse(f"WAKE_STATUS: error\n{msg}", status_code=200)


# (Old single-instance Caster Rover Management API removed to prevent shadowing multi-instance logic)

@app.get("/api/ntrip/auth-check")
async def ntrip_auth_check(
    user: str = Query(...),
    mountpoint: Optional[str] = Query(None)
):
    """Public API cho Caster gọi kiểm tra quyền kết nối."""
    reason = await _check_rover_blocked(user, mountpoint)
    if reason:
        return JSONResponse({"status": "forbidden", "reason": reason}, status_code=403)
    return JSONResponse({"status": "ok"})



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
    """Helper để khởi tạo Caster Client an toàn từ .env hoặc settings"""
    hosts, env_cfg = _read_caster_config_from_env()
    
    # Ưu tiên .env -> settings -> default
    host = str(env_cfg["host"] or settings.CASTER_HOST or 'aitogy.com.vn')
    port = int(env_cfg["port"] or settings.CASTER_PORT or 2101)
    user = str(env_cfg["user"] or "admin")
    password = str(env_cfg["pass"] or "12345")
    
    if RTCM_KEEPALIVE_DEBUG:
        logger.warning(f"[RTCM DEBUG] Using caster host={host} port={port} user={user}")
    
    return CasterClient(host, port, admin_user=user, admin_password=password)

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
    request: Request,
    login_data: schemas.LoginRequest = Body(...), 
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db)  
):
    # Log login attempt
    logger.info(f"🔑 Login attempt for user: '{login_data.username}' from {request.client.host if request else 'unknown'}")
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
            f"â Œ Login failed: Unknown user '{login_data.username}' "
            f"from {request.client.host}"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    if not await auth.verify_password(login_data.password, user.hashed_password):
        logger.warning(
            f"â Œ Login failed: Wrong password for '{login_data.username}' "
            f"from {request.client.host}"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    if not user.is_active:
        logger.warning(
            f"â Œ Login failed: Inactive account '{login_data.username}'"
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
    
    # 🔥 FIX: Mặc định 24h nếu không chọn remember_me, 1 năm nếu có chọn
    if getattr(login_data, "remember_me", False):
        expires_delta = timedelta(minutes=auth.ACCESS_TOKEN_REMEMBER_ME_EXPIRE_MINUTES)
    else:
        expires_delta = timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    access_token = auth.create_access_token(
        data={"sub": user.username, "role": user.role},
        expires_delta=expires_delta
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

        def _dict_or_empty(value: Any) -> dict:
            return value if isinstance(value, dict) else {}

        def _extract_public_map_coords(dev: models.Device) -> tuple[Optional[Any], Optional[Any]]:
            candidate_sources = []

            base_config = _dict_or_empty(getattr(dev, "base_config", None))
            if base_config:
                candidate_sources.append(base_config)

            base_coords = getattr(dev, "base_coords", None)
            if isinstance(base_coords, dict):
                candidate_sources.append(base_coords)

            for source in candidate_sources:
                coords = _dict_or_empty(source.get("coords"))
                lat = coords.get("lat")
                lon = coords.get("lon")

                if lat is None or lon is None:
                    lat = source.get("lat")
                    lon = source.get("lon")

                if lat is not None and lon is not None:
                    return lat, lon

            return None, None
        
        public_data = []
        skipped_devices = 0
        
        for dev in devices:
            lat, lon = _extract_public_map_coords(dev)

            if lat is not None and lon is not None:
                try:
                    public_data.append({
                        "serial": dev.serial,
                        "name": dev.name,
                        "status": dev.status,
                        "lat": float(lat),
                        "lon": float(lon),
                        "service_config": dev.service_config,
                        "ntrip_stats": dev.ntrip_status,
                        "detected_chip_type": dev.detected_chip_type,
                        "is_provisioned": dev.is_provisioned,
                        "ntrip_connected": getattr(dev, "ntrip_connected", False),
                        "bps": getattr(dev, "bps", 0)
                    })
                except (ValueError, TypeError) as e:
                    logging.warning(f"⚠️ Invalid coordinates for {dev.name}: {e}")
                    skipped_devices += 1
                    continue
            else:
                skipped_devices += 1
        
        logging.info(
            f"✓ Public map API: found {len(devices)} devices, returning {len(public_data)} with valid coords, "
            f"skipped {skipped_devices} without usable coords"
        )
        return public_data
        
    except Exception as e:
        logging.error(f"â Œ Error fetching public map data: {e}")
        return []



@app.get("/api/caster/clienttable")
async def get_caster_clienttable(
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    """Aggregate clienttable from all scanned casters to show on map."""
    all_clients = []
    
    async with global_caster_cache_lock:
        for cache_data in global_caster_data_cache.values():
            host = cache_data.get("host", "unknown")
            users = cache_data.get("users", [])
            user_visibility_map = {u.get("username", "").strip().lower(): u.get("show_position", True) for u in users}
            
            clients = cache_data.get("clients", [])
            for c in clients:
                u_lower = str(c.get("username", "")).strip().lower()
                # Add server info and visibility for UI
                c_copy = dict(c)
                c_copy["server"] = host
                c_copy["show_position"] = user_visibility_map.get(u_lower, True)
                all_clients.append(c_copy)
                
    # Keep all clients; frontend will decide visibility by valid coordinates.
    filtered_clients = list(all_clients)

    # --- PHÂN QUYỀN HIỂN THỊ TRÊN MAP ---
    if current_user.role != auth.Role.ADMIN:
        # 1. Lấy danh sách trạm mà user quản lý
        managed_devices = await crud.get_devices_by_user_id(devices_db, current_user.id)
        managed_serials = {d.serial.upper().strip() for d in managed_devices}

        # 2. Lấy danh sách rover mà user tạo ra
        my_rovers = await crud.get_all_rovers_by_user(db, current_user.id, current_user.role)
        
        # 3. Lọc: Rover vừa do user tạo, vừa được gán vào trạm user quản lý
        allowed_usernames = set()
        for r in my_rovers:
            # base_serial có thể là "SN1, SN2"
            r_serials = [s.strip().upper() for s in (r.base_serial or "").split(',') if s.strip()]
            # Nếu có ít nhất 1 trạm của coordinator trong danh sách trạm của rover
            if any(s in managed_serials for s in r_serials):
                allowed_usernames.add(str(r.username).strip().lower())

        # 4. Chỉ giữ lại những client trên map thuộc danh sách allowed
        filtered_clients = [c for c in filtered_clients if str(c.get("username", "")).strip().lower() in allowed_usernames]
    
    return {
        "count": len(filtered_clients),
        "items": filtered_clients,
    }


@app.get("/api/caster/users", response_model=schemas.CasterUsersResponse)
async def get_caster_users(
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    """Aggregate total users from all scanned casters with enrichment."""
    all_caster_users = []
    
    async with global_caster_cache_lock:
        for cache_data in global_caster_data_cache.values():
            host = cache_data.get("host", "unknown")
            users = cache_data.get("users", [])
            for u in users:
                u_copy = dict(u)
                u_copy["server"] = host # Tag with server
                all_caster_users.append(u_copy)

    # --- PHÂN QUYỀN HIỂN THỊ (ĐỒNG BỘ VỚI MAP) ---
    if current_user.role != auth.Role.ADMIN:
        # 1. Lấy danh sách trạm mà user quản lý
        managed_devices = await crud.get_devices_by_user_id(devices_db, current_user.id)
        managed_serials = {d.serial.upper().strip() for d in managed_devices}

        # 2. Lấy danh sách rover mà user tạo ra
        my_rovers = await crud.get_all_rovers_by_user(auth_db, current_user.id, current_user.role)
        
        # 3. Lọc: 
        # - Cho phép những Rover do chính User tạo ra (đã lấy ở bước 2)
        # - HOẶC Rover thuộc trạm mà User quản lý
        allowed_usernames = set()
        for r in my_rovers:
            # Luôn cho phép nếu chính user là người tạo (bao gồm cả Caster-only MP)
            if r.created_by_user_id == current_user.id:
                allowed_usernames.add(str(r.username).strip().lower())
                continue
                
            # Nếu không phải người tạo, kiểm tra xem có thuộc trạm mình quản lý không
            r_serials = [s.strip().upper() for s in (r.base_serial or "").split(',') if s.strip()]
            if any(s in managed_serials for s in r_serials):
                allowed_usernames.add(str(r.username).strip().lower())

        # 4. Lọc lại danh sách toàn bộ caster users
        all_caster_users = [u for u in all_caster_users if str(u.get("username", "")).strip().lower() in allowed_usernames]


    # Local DB Lookups
    db_rovers_rs = await auth_db.execute(select(models.RoverAccount))
    db_rovers_map = {str(r.username).strip().lower(): r for r in db_rovers_rs.scalars().all()}

    blocklist_rs = await auth_db.execute(select(models.RoverBlocklist))
    blocklist_items = blocklist_rs.scalars().all()
    blocklist_map: Dict[str, List[models.RoverBlocklist]] = {}
    for bl in blocklist_items:
        key = str(bl.username).lower()
        if key not in blocklist_map: 
            blocklist_map[key] = []
        blocklist_map[key].append(bl)

    # Device lookup for version matching
    async with device_session_scope() as db:
        res_devices = await db.execute(select(models.Device.serial, models.Device.version))
        device_versions = {str(d.serial).upper(): d.version for d in res_devices.all()}

    # 1. Active Sessions Lookup Map (from Caster Cache)
    active_sessions_map = {}  # (server, username) -> [ {mp, uptime} ]

    async with global_caster_cache_lock:
        for cache_data in global_caster_data_cache.values():
            host = cache_data.get("host", "unknown")
            last_update = int(cache_data.get("last_update") or time.time())
            clients = cache_data.get("clients", [])
            for c in clients:
                user_key = (host, str(c.get("username", "")).strip().lower())
                if user_key not in active_sessions_map:
                    active_sessions_map[user_key] = []
                active_sessions_map[user_key].append({
                    "mountpoint": c.get("mountpoint"),
                    "uptime": int(c.get("uptime", 0) or 0),
                    "client_id": str(c.get("client_id", "") or "").strip(),
                    "last_update": last_update,
                })

    enriched_items = []
    in_db_count = 0
    blocked_count = 0

    for cu in all_caster_users:
        username = str(cu.get("username", "")).strip()
        username_lower = username.lower()
        server_host = cu.get("server")

        in_db = username_lower in db_rovers_map
        is_blocked = False
        blocked_reason = None
        blocked_at = None

        if username_lower in blocklist_map:
            for bl_entry in blocklist_map[username_lower]:
                if bl_entry.mountpoint is None:
                    is_blocked = True
                    blocked_reason = bl_entry.reason
                    blocked_at = bl_entry.blocked_at
                    break
                else:
                    user_mps = [mp.lower().strip() for mp in cu.get("mountpoints", [])]
                    if bl_entry.mountpoint.lower().strip() in user_mps:
                        is_blocked = True
                        blocked_reason = bl_entry.reason
                        blocked_at = bl_entry.blocked_at
                        break

        if in_db: in_db_count += 1
        if is_blocked: blocked_count += 1

        # Match active connections
        active_key = (server_host, username_lower)
        active_conns_raw = active_sessions_map.get(active_key, [])
        active_conns = []
        for ac in active_conns_raw:
            uptime = int(ac.get("uptime", 0) or 0)
            last_update = int(ac.get("last_update") or time.time())
            started_at = last_update - uptime if uptime > 0 else None
            active_conns.append({
                "mountpoint": ac.get("mountpoint"),
                "uptime": uptime,
                "duration_seconds": uptime,
                "client_id": ac.get("client_id", ""),
                "started_at": started_at,
                "last_seen_at": last_update,
            })

        enriched_items.append(schemas.CasterRoverItem(
            username=username,
            password=str(cu.get("password", "")),
            mountpoints=cu.get("mountpoints", []),
            datacount=cu.get("datacount", 0),
            status=cu.get("status", 0),
            identifier=str(cu.get("identifier", "")),
            nclient=cu.get("nclient", 0),
            maxclient=cu.get("maxclient", 0),
            in_db=in_db,
            is_blocked=is_blocked,
            blocked_reason=blocked_reason,
            blocked_at=blocked_at,
            server=server_host,
            version=device_versions.get(str(db_rovers_map[username_lower].base_serial or "").strip().upper()) if in_db and db_rovers_map[username_lower].base_serial else None,
            active_connections=active_conns,
            show_position=cu.get("show_position", True)
        ))

    return schemas.CasterUsersResponse(
        total=len(enriched_items),
        in_db_count=in_db_count,
        unknown_count=len(enriched_items) - in_db_count,
        blocked_count=blocked_count,
        items=enriched_items,
    )


@app.get("/api/admin/caster-sources")
async def get_admin_caster_sources(
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    """
    ✅ ADMIN ONLY: Fetch all available mountpoints/sources from NTRIP Caster(s).
    Separated by domain (aitogy.com.vn, aitogy.click, etc.).
    """
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ ADMIN mới có quyền xem toàn bộ mountpoints từ Caster")

    from app.caster_client import CasterClient
    
    hosts, env_cfg = _read_caster_config_from_env()
    if not hosts:
        raise HTTPException(status_code=500, detail="Chưa cấu hình Caster Server trong hệ thống")

    port = env_cfg.get("port") or 2101
    sources_by_host = {}
    all_sources = []

    for host in hosts:
        try:
            caster = CasterClient(host, port)
            sources = await caster.get_sources()
            
            if sources:
                # Tag each source with its host for UI filtering
                for src in sources:
                    src["host"] = host
                    src["domain"] = _extract_domain(host)  # aitogy.com.vn or aitogy.click
                
                sources_by_host[host] = sources
                all_sources.extend(sources)
                logger.info(f"[CASTER] Fetched {len(sources)} sources from {host}")
            else:
                sources_by_host[host] = []
                logger.warning(f"[CASTER] No sources fetched from {host}")
        except Exception as e:
            logger.error(f"[CASTER] Failed to fetch sources from {host}: {e}")
            sources_by_host[host] = []

    # Group by domain for frontend
    grouped_by_domain = {}
    for src in all_sources:
        domain = src.get("domain", "unknown")
        if domain not in grouped_by_domain:
            grouped_by_domain[domain] = []
        grouped_by_domain[domain].append(src)

    return {
        "total": len(all_sources),
        "by_host": sources_by_host,
        "by_domain": grouped_by_domain,
        "all": all_sources,
    }


@app.post("/api/admin/caster-sources")
async def create_admin_caster_source(
    req: schemas.CasterSourceCreateRequest,
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ ADMIN mới có quyền tạo mountpoint trên Caster")

    from app.caster_client import CasterClient

    hosts, env_cfg = _read_caster_config_from_env()
    target_host = (req.host or (hosts[0] if hosts else None) or env_cfg.get("host") or settings.CASTER_HOST or "").strip()
    if not target_host:
        raise HTTPException(status_code=500, detail="Chưa cấu hình Caster Server trong hệ thống")

    port = env_cfg.get("port") or int(settings.CASTER_PORT or 2101)
    caster = CasterClient(target_host, port)
    ok = await caster.create_source(
        req.identifier,
        req.mountpoint,
        req.password,
        req.counter,
        req.index,
        req.latitude,
        req.longitude,
        req.auth_required,
        req.gga_required,
        req.show_position,
        req.overlap_data,
        req.re_positioning,
        req.storing_data,
    )
    if not ok:
        raise HTTPException(status_code=502, detail=f"Không thể tạo mountpoint '{req.mountpoint}' trên caster {target_host}")

    return {"status": "ok", "host": target_host, "mountpoint": req.mountpoint, "identifier": req.identifier}


@app.delete("/api/admin/caster-sources")
async def delete_admin_caster_source(
    req: schemas.CasterSourceDeleteRequest,
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ ADMIN mới có quyền xóa mountpoint trên Caster")

    from app.caster_client import CasterClient

    hosts, env_cfg = _read_caster_config_from_env()
    target_host = (req.host or (hosts[0] if hosts else None) or env_cfg.get("host") or settings.CASTER_HOST or "").strip()
    if not target_host:
        raise HTTPException(status_code=500, detail="Chưa cấu hình Caster Server trong hệ thống")

    port = env_cfg.get("port") or int(settings.CASTER_PORT or 2101)
    caster = CasterClient(target_host, port)
    ok = await caster.delete_source(req.identifier, req.mountpoint)
    if not ok:
        raise HTTPException(status_code=502, detail=f"Không thể xóa mountpoint '{req.mountpoint}' trên caster {target_host}")

    return {"status": "ok", "host": target_host, "mountpoint": req.mountpoint, "identifier": req.identifier}


def _extract_domain(host: str) -> str:
    """Extract domain from hostname (e.g., 'aitogy.com.vn' from 'caster.aitogy.com.vn')"""
    h = str(host or "").strip().lower()
    
    # Match known domains
    if "aitogy.com.vn" in h:
        return "aitogy.com.vn"
    if "aitogy.click" in h:
        return "aitogy.click"
    if "localhost" in h or "127.0.0.1" in h:
        return "localhost"
    if "localhost" in h or h.startswith("127."):
        return "localhost"
    
    # Return as-is for unknown domains
    return h


@app.post("/api/caster/block-user")
async def block_caster_user(
    req: schemas.BlockRoverRequest,
    auth_db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    """
    Chặn rover trên hệ thống:
    1. Lưu vào blocklist DB (source of truth)
    2. Gọi caster TCP delete_user để kick ngay lập tức
    3. Cập nhật RoverAccount.is_active = False (nếu user có trong DB)
    """
    username = req.username.strip()
    mountpoint = req.mountpoint.strip() if req.mountpoint else None
    now = int(time.time())

    # 1. Thêm vào blocklist DB
    # Check xem đã block chưa (tránh duplicate)
    existing_block_q = select(models.RoverBlocklist).where(
        models.RoverBlocklist.username == username,
    )
    if mountpoint:
        existing_block_q = existing_block_q.where(
            models.RoverBlocklist.mountpoint == mountpoint
        )
    else:
        existing_block_q = existing_block_q.where(
            models.RoverBlocklist.mountpoint.is_(None)
        )

    existing_block_rs = await auth_db.execute(existing_block_q)
    if existing_block_rs.scalars().first():
        return {
            "status": "already_blocked",
            "username": username,
            "message": f"User '{username}' đã bị chặn trước đó."
        }

    new_block = models.RoverBlocklist(
        username=username,
        mountpoint=mountpoint,
        blocked_at=now,
        blocked_by=current_user.username,
        reason=req.reason or "Blocked by admin",
    )
    auth_db.add(new_block)

    # 2. Nếu user có trong DB local → set is_active = False
    rover = await crud.get_rover_by_username(auth_db, username)
    if rover:
        rover.is_active = False

    await auth_db.commit()

    # 3. Gọi các caster TCP để xóa user (kick ngay lập tức trên tất cả server)
    env_cfg = _read_caster_config_from_env()
    _env_host = env_cfg.get("host")
    target_hosts = [_env_host] if _env_host else [str(settings.CASTER_HOST or "localhost")]
    port = int(settings.CASTER_PORT or 2101)
    
    deleted_count_caster = 0
    for host in target_hosts:
        try:
            from app.caster_client import CasterClient
            caster = CasterClient(host, port)
            ok = await caster.delete_user(
                identifier=current_user.username,
                username=username,
            )
            if ok: deleted_count_caster += 1
            logger.info(f"[CASTER MGMT] Deleted user '{username}' from caster {host}")
        except Exception as e:
            logger.warning(f"[CASTER MGMT] Failed to delete user from caster {host}: {e}")

    return {
        "status": "blocked",
        "username": username,
        "mountpoint": mountpoint,
        "casters_affected": deleted_count_caster,
        "message": f"Đã chặn user '{username}' trên toàn hệ thống ({deleted_count_caster} server affected)."
    }


@app.post("/api/caster/unblock-user")
async def unblock_caster_user(
    req: schemas.UnblockRoverRequest,
    auth_db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    """
    Mở khóa rover:
    1. Xóa khỏi blocklist DB
    2. Tạo lại user trên caster (nếu có thông tin trong DB)
    3. Cập nhật RoverAccount.is_active = True (nếu có)
    """
    username = req.username.strip()
    mountpoint = req.mountpoint.strip() if req.mountpoint else None

    # 1. Xóa khỏi blocklist
    del_q = delete(models.RoverBlocklist).where(
        models.RoverBlocklist.username == username,
    )
    if mountpoint:
        del_q = del_q.where(models.RoverBlocklist.mountpoint == mountpoint)
    else:
        # Xóa tất cả block entries cho user này
        pass

    result = await auth_db.execute(del_q)
    deleted_count = result.rowcount

    # 2. Nếu user có trong DB local → set is_active = True
    rover = await crud.get_rover_by_username(auth_db, username)
    caster_recreated = False

    if rover:
        rover.is_active = True
        await auth_db.commit()

        # 3. Tạo lại user trên TẤT CẢ caster
        env_cfg = _read_caster_config_from_env()
        _env_host = env_cfg.get("host")
        target_hosts = [_env_host] if _env_host else [str(settings.CASTER_HOST or "localhost")]
        port = int(settings.CASTER_PORT or 2101)
        
        recreated_count = 0
        for host in target_hosts:
            try:
                from app.caster_client import CasterClient
                caster = CasterClient(host, port)
                rover_mps = rover.mountpoints or ""
                await caster.create_user(
                    creator=current_user.username,
                    username=rover.username,
                    password=rover.password,
                    mountpoints=rover_mps,
                    expiry_seconds=315360000,  # 10 năm
                    max_users=1,
                    show_position=bool(rover.show_position),
                    rtcm_cycle_enabled=bool(getattr(rover, "rtcm_cycle_enabled", False)),
                    rtcm_disconnect_interval_seconds=int(getattr(rover, "rtcm_disconnect_interval_seconds", 300) or 300),
                )
                recreated_count += 1
                logger.info(f"[CASTER MGMT] Re-created user '{username}' on caster {host}")
            except Exception as e:
                logger.warning(f"[CASTER MGMT] Failed to re-create user on caster {host}: {e}")
        
        caster_recreated = (recreated_count > 0)
    else:
        await auth_db.commit()

    if deleted_count == 0:
        return {
            "status": "not_found",
            "username": username,
            "message": f"User '{username}' không có trong danh sách chặn."
        }

    return {
        "status": "unblocked",
        "username": username,
        "mountpoint": mountpoint,
        "caster_recreated": caster_recreated,
        "in_db": rover is not None,
        "message": f"Đã mở khóa user '{username}'." + (" User đã được tạo lại trên caster." if caster_recreated else " (User không có trong DB, cần tạo thủ công trên caster)")
    }


@app.get("/api/ntrip/auth-check", response_class=PlainTextResponse)
async def ntrip_auth_check(
    user: str = Query(...),
    mountpoint: Optional[str] = Query(None),
):
    """
    Endpoint cho NTRIP Caster gọi để kiểm tra xem user có bị chặn không.
    Caster gọi: GET /api/ntrip/auth-check?user=xxx&mountpoint=yyy
    
    Response:
    - 200 "OK" → Caster cho phép kết nối (không bị chặn)
    - 403 "BLOCKED" → Caster từ chối kết nối (bị chặn)
    
    Endpoint này KHÔNG yêu cầu auth token vì được gọi bởi caster process.
    """
    username = user.strip()
    mountpoint_clean = mountpoint.strip() if mountpoint else None

    try:
        async with auth_session_scope() as auth_db:
            # Check blocklist
            block_q = select(models.RoverBlocklist).where(
                models.RoverBlocklist.username == username,
            )
            block_rs = await auth_db.execute(block_q)
            block_entries = block_rs.scalars().all()

            for entry in block_entries:
                # Block all mountpoints
                if entry.mountpoint is None:
                    logger.info(f"[NTRIP AUTH] BLOCKED user='{username}' reason='{entry.reason}' (all mountpoints)")
                    return PlainTextResponse(
                        f"BLOCKED: {entry.reason or 'User is blocked'}",
                        status_code=403,
                    )
                # Block specific mountpoint
                if mountpoint_clean and entry.mountpoint.lower().strip() == mountpoint_clean.lower():
                    logger.info(f"[NTRIP AUTH] BLOCKED user='{username}' mountpoint='{mountpoint_clean}' reason='{entry.reason}'")
                    return PlainTextResponse(
                        f"BLOCKED: {entry.reason or 'User is blocked on this mountpoint'}",
                        status_code=403,
                    )

            # Không bị chặn → cho phép
            return PlainTextResponse("OK", status_code=200)

    except Exception as e:
        logger.error(f"[NTRIP AUTH] Error checking auth for user='{username}': {e}")
        # Fail-open: nếu lỗi DB thì vẫn cho phép (tránh lock toàn bộ hệ thống)
        return PlainTextResponse("OK", status_code=200)


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


# --- GLOBAL CASTER MONITOR CACHE ---
import asyncio

# Global cache to store clienttable and usertable for each caster host
# { "host:port": { "last_update": int, "clients": [...], "users": [...], "error": str, "host": str, "port": int } }
global_caster_data_cache = {}
global_caster_cache_lock = asyncio.Lock()


async def caster_monitor_task():
    """Background task to periodically sync data from all configured casters."""
    logger.info("[CASTER MONITOR] Starting background sync task")
    while True:
        try:
            async with auth_session_scope() as db:
                # 1. Get all hosts to scan
                env_hosts, env_cfg = _read_caster_config_from_env()
                settings_host = getattr(settings, "CASTER_HOST", "")
                system_settings = {}
                try:
                    system_settings = await crud.get_system_settings(db)
                except Exception:
                    pass
                db_host = system_settings.get('caster_host', '')

                unique_hosts = set()
                # Add hosts from .env list
                for h in env_hosts:
                    unique_hosts.add(h)
                
                # Add hosts from settings and DB (might be CSV)
                for raw_str in [settings_host, db_host]:
                    if raw_str:
                        for h in str(raw_str).split(','):
                            h = h.strip()
                            if h: unique_hosts.add(h)

                # Also discover hosts from active sessions
                try:
                    sessions_rs = await db.execute(select(models.RTCMStreamSession.caster_host).distinct())
                    for row in sessions_rs.all():
                        if row[0]:
                            unique_hosts.add(str(row[0]).strip())
                except Exception:
                    pass

                port = int(getattr(settings, "CASTER_PORT", 2101) or system_settings.get('caster_port', 2101))
                user = env_cfg.get("user")
                password = env_cfg.get("pass")

                # 2. Scan each host
                tasks = []
                for host in unique_hosts:
                    tasks.append(_sync_single_caster(host, port, user, password))

                if tasks:
                    await asyncio.gather(*tasks)

        except Exception as e:
            logger.error(f"[CASTER MONITOR] Sync loop error: {e}")

        # Nới lỏng thời gian quét tránh bão request tới Caster
        monitor_interval = float(os.getenv("CASTER_MONITOR_INTERVAL", "30"))
        await asyncio.sleep(monitor_interval)


async def _sync_single_caster(host: str, port: int, user: str = None, password: str = None):
    """Worker to fetch data from one caster and update the global cache."""
    key = f"{host}:{port}"
    try:
        from app.caster_client import CasterClient
        caster = CasterClient(host, port, admin_user=user, admin_password=password)
        # Fetch data in parallel
        users, clients = await asyncio.gather(
            caster.get_users(),
            caster.get_clienttable(mountpoint="all")
        )

        async with global_caster_cache_lock:
            global_caster_data_cache[key] = {
                "last_update": int(time.time()),
                "users": users or [],
                "clients": clients or [],
                "error": None,
                "host": host,
                "port": port
            }
        if RTCM_KEEPALIVE_DEBUG:
            logger.info(f"[CASTER MONITOR] Scanned {key}: {len(users or [])} users, {len(clients or [])} clients")

    except Exception as e:
        logger.warning(f"[CASTER MONITOR] Failed to scan {key}: {e}")
        async with global_caster_cache_lock:
            if key not in global_caster_data_cache:
                global_caster_data_cache[key] = {"users": [], "clients": [], "host": host, "port": port}
            global_caster_data_cache[key]["error"] = str(e)
            global_caster_data_cache[key]["last_update"] = int(time.time())


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
    user_response.app_version = settings.APP_VERSION
    return user_response

#=== Admin endpoint ===
@app.get("/api/admin/settings", response_model=dict)
async def get_system_settings(
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Lấy trạng thái hiện tại của các cÃ i đặt bảo mật."""
    settings_data = await crud.get_system_settings(db)

    return settings_data

@app.post("/api/admin/settings")
async def update_system_settings(
    settings_data: dict,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Cập nhật cÃ i đặt bảo mật."""


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
        auth_db, 
        username=user_data.username, 
        hashed_password=hashed_password,
        role=user_data.role, 
        full_name=user_data.full_name,
        is_active=user_data.is_active,
        can_create_rovers=user_data.can_create_rovers,
        extra_mountpoints=user_data.extra_mountpoints
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


@app.post("/api/admin/virtual-stations", response_model=schemas.Device)
async def create_virtual_station(
    payload: schemas.VirtualStationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Only admin can create virtual stations.")

    station_name = str(payload.name or "").strip()
    if not station_name:
        raise HTTPException(status_code=400, detail="Station name is required.")

    serial = str(payload.serial).strip() if payload.serial else f"VIRTUAL_{uuid.uuid4().hex[:10].upper()}"
    existing = await crud.get_device_by_serial(db, serial)
    if existing:
        raise HTTPException(status_code=409, detail=f"Serial '{serial}' already exists.")

    service_cfg = payload.service_config.model_dump()
    # Ensure virtual station defaults are sensible for map/display and runtime compatibility.
    service_cfg.setdefault("stream_on_demand", False)
    service_cfg.setdefault("stream_active", True)

    # Virtual station policy: if any output server is enabled, rover input must be configured.
    s1_enabled = bool(service_cfg.get("server1_enabled", False))
    s2_enabled = bool(service_cfg.get("server2_enabled", False))
    any_output_enabled = s1_enabled or s2_enabled
    rtcm_enabled = bool(service_cfg.get("rtcm_enabled", False))

    if any_output_enabled and not rtcm_enabled:
        raise HTTPException(
            status_code=400,
            detail="Virtual station requires RTCM input (rover account) before relaying to CORS output."
        )

    required_input_fields = ["rtcmserver1", "rtcmport1", "rtcmmountpoint1", "rtcmusername1", "rtcmpassword1"]
    if rtcm_enabled and any(not str(service_cfg.get(k) or "").strip() for k in required_input_fields):
        raise HTTPException(
            status_code=400,
            detail="Missing RTCM input configuration: host/port/mountpoint/username/password are required."
        )

    required_output_fields_s1 = ["serverhost1", "port1", "mountpoint1"]
    if s1_enabled and any(not str(service_cfg.get(k) or "").strip() for k in required_output_fields_s1):
        raise HTTPException(
            status_code=400,
            detail="Missing CORS output configuration: serverhost1/port1/mountpoint1 are required."
        )

    required_output_fields_s2 = ["serverhost2", "port2", "mountpoint2"]
    if s2_enabled and any(not str(service_cfg.get(k) or "").strip() for k in required_output_fields_s2):
        raise HTTPException(
            status_code=400,
            detail="Missing CORS output configuration: serverhost2/port2/mountpoint2 are required."
        )

    device = models.Device(
        serial=serial,
        name=station_name,
        status="online",
        timestamp=int(time.time()),
        bps=0,
        detected_chip_type="VIRTUAL",
        user_id=current_user.id,
        base_config={
            "base_setup_method": "FIXED_LLA",
            "coords": {
                "lat": str(payload.lat),
                "lon": str(payload.lon),
                "alt": str(payload.alt or 0.0),
            },
            "auto_configured": True,
        },
        service_config=service_cfg,
        ntrip_connected=False,
        ntrip_status={
            "server1": {"connected": False, "bps": 0},
            "server2": {"connected": False, "bps": 0},
        },
        is_locked=False,
        is_provisioned=True,
        is_synced=True,
    )

    db.add(device)
    await db.commit()
    await db.refresh(device)

    await log_system_event(
        db=db,
        type="info",
        message=f"Virtual station '{device.name}' created by {current_user.username}",
        user_id=current_user.id,
        serial=device.serial,
    )

    await ui_manager.broadcast({
        "type": "status_update",
        "data": schemas.Device.from_orm(device).model_dump()
    })

    runtime_manager = globals().get("virtual_runtime_manager")
    if runtime_manager is not None:
        for method_name in ("refresh", "reconcile", "reconcile_all", "sync", "reload", "refresh_all"):
            method = getattr(runtime_manager, method_name, None)
            if not callable(method):
                continue
            result = method()
            if asyncio.iscoroutine(result):
                await result
            break

    return device

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

    # Gộp thêm extra mountpoints của user (nếu có)
    if current_user.extra_mountpoints:
        extra_list = [mp.strip() for mp in re.split(r'[;,\s\n]+', current_user.extra_mountpoints) if mp.strip()]
        valid_mps.extend(extra_list)

    if not valid_mps:
        target_mountpoint = f"MP_{serial}"
    elif target_mountpoint not in valid_mps:
        # Nếu là Admin hoặc MP nằm trong danh sách hợp lệ mở rộng thì cho qua
        if not is_admin and target_mountpoint not in valid_mps:
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
            max_users=1,
            show_position=rover_data.show_position,
            rtcm_cycle_enabled=rover_data.rtcm_cycle_enabled,
            rtcm_disconnect_interval_seconds=rover_data.rtcm_disconnect_interval_seconds,
            rtcm_disconnect_duration_seconds=rover_data.rtcm_disconnect_duration_seconds,
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
        creator_id=current_user.id,
        show_position=rover_data.show_position,
        rtcm_cycle_enabled=rover_data.rtcm_cycle_enabled,
        rtcm_disconnect_interval_seconds=rover_data.rtcm_disconnect_interval_seconds,
        rtcm_disconnect_duration_seconds=rover_data.rtcm_disconnect_duration_seconds
    )
    
    return new_rover

@app.put("/api/rovers/{rover_id}", response_model=schemas.RoverAccountResponse)
async def update_rover(
    rover_id: int,
    update_req: schemas.RoverUpdate,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db), # Add devices_db here
    current_user: models.User = Depends(auth.get_current_user)
):
    # 1. Lấy thông tin Rover cũ
    rover = await crud.get_rover_by_id(db, rover_id)
    if not rover:
        raise HTTPException(status_code=404, detail="Không tìm thấy tÃ i khoản Rover.")

    # 2. Kiểm tra quyền
    is_admin = current_user.role == auth.Role.ADMIN
    is_creator = rover.created_by_user_id == current_user.id
    
    # Ownership Check: COORDINATOR chỉ quản lý rover thuộc trạm mình sở hữu
    if not is_admin:
        serials = rover.base_serial.split(',')
        owned_res = await devices_db.execute(
            select(models.Device.serial).where(models.Device.user_id == current_user.id)
        )
        owned_serials = set(owned_res.scalars().all())
        
        for sn in serials:
            sn_clean = sn.strip()
            # Nếu trạm ảo (serial trống) và là người tạo thì cho qua
            if sn_clean == "" and is_creator:
                continue
            if sn_clean not in owned_serials:
                raise HTTPException(status_code=403, detail=f"Bạn không có quyền quản lý Rover thuộc trạm '{sn}'.")


    # 3. Chuẩn bị dữ liệu mới (nếu client không gửi thì dùng cái cũ)
    new_password = update_req.password if update_req.password else rover.password
    new_mountpoints = update_req.mountpoints if update_req.mountpoints else rover.mountpoints
    
    # Xử lý thời hạn (Mặc định reset lại thời hạn nếu có gửi expiry_days)
    duration_seconds = 315360000 # Default
    if update_req.expiry_days:
        duration_seconds = update_req.expiry_days * 86400
    
    # 4. GỌI CASTER ĐỂ CẬP NHẬT
    try:
        from app.caster_client import CasterClient
        creator_identifier = current_user.username 
        new_max_connections = update_req.max_connections if update_req.max_connections is not None else rover.max_connections
        target_show_pos = update_req.show_position if update_req.show_position is not None else rover.show_position
        target_rtcm_cycle_enabled = update_req.rtcm_cycle_enabled if update_req.rtcm_cycle_enabled is not None else bool(getattr(rover, "rtcm_cycle_enabled", False))
        target_rtcm_cycle_interval = update_req.rtcm_disconnect_interval_seconds if update_req.rtcm_disconnect_interval_seconds is not None else int(getattr(rover, "rtcm_disconnect_interval_seconds", 300) or 300)
        target_rtcm_cycle_duration = update_req.rtcm_disconnect_duration_seconds if update_req.rtcm_disconnect_duration_seconds is not None else int(getattr(rover, "rtcm_disconnect_duration_seconds", 10) or 10)
        
        # Determine updated target casters
        # If request provides target_casters, use it. Otherwise use what's in DB.
        old_hosts_str = rover.target_casters if rover.target_casters else ""
        old_hosts_set = set(h.strip() for h in old_hosts_str.split(";") if h.strip())
        
        # Resolve hosts where this rover actually exists from live caster cache.
        live_hosts_set = set()
        try:
            rover_key = str(rover.username or "").strip().lower()
            async with global_caster_cache_lock:
                for cache_data in global_caster_data_cache.values():
                    host = str(cache_data.get("host", "") or "").strip()
                    if not host:
                        continue
                    for u in (cache_data.get("users") or []):
                        if str(u.get("username", "") or "").strip().lower() == rover_key:
                            live_hosts_set.add(host)
                            break
        except Exception as e:
            logger.warning(f"Failed to resolve live caster hosts for '{rover.username}': {e}")

        if update_req.target_casters is not None:
            requested_hosts = [str(h).strip() for h in update_req.target_casters if str(h).strip()]
            if requested_hosts:
                updated_target_hosts = requested_hosts
            else:
                # Frontend legacy case: empty selection should not cause no-op update.
                updated_target_hosts = list(old_hosts_set or live_hosts_set)
        else:
            # Load from DB first, then from live cache.
            updated_target_hosts = list(old_hosts_set or live_hosts_set)

        if not updated_target_hosts:
            # Last fallback to default configured host
            env_hosts, env_cfg = _read_caster_config_from_env()
            default_h = str(env_cfg.get("host") or settings.CASTER_HOST or "").strip()
            if default_h:
                updated_target_hosts = [default_h]
            elif env_hosts:
                updated_target_hosts = [str(env_hosts[0]).strip()]

        # Keep order and deduplicate
        seen_hosts = set()
        updated_target_hosts = [h for h in updated_target_hosts if h and not (h in seen_hosts or seen_hosts.add(h))]
        
        new_hosts_set = set(updated_target_hosts)
        removed_hosts = old_hosts_set - new_hosts_set
        
        port = int(settings.CASTER_PORT or 2101)
        results_log = []
        creator_user = await crud.get_user_by_id(db, rover.created_by_user_id)
        creator_username = creator_user.username if creator_user else "admin"
        id_candidates = []
        for ident in (creator_username, creator_identifier, "admin", "id"):
            ident_s = str(ident or "").strip()
            if ident_s and ident_s not in id_candidates:
                id_candidates.append(ident_s)

        # 4a. Update/Create on selected hosts
        for host in updated_target_hosts:
            try:
                caster = CasterClient(host, port)
                created_ok = await caster.create_user(
                    creator=creator_username,
                    username=rover.username,
                    password=new_password,
                    mountpoints=new_mountpoints, 
                    expiry_seconds=duration_seconds, 
                    max_users=new_max_connections,
                    show_position=target_show_pos,
                    rtcm_cycle_enabled=bool(target_rtcm_cycle_enabled),
                    rtcm_disconnect_interval_seconds=int(target_rtcm_cycle_interval),
                    rtcm_disconnect_duration_seconds=int(target_rtcm_cycle_duration),
                )
                if not created_ok:
                    logger.warning(f"Caster {host} did not confirm create/update for rover '{rover.username}'")

                # Ensure show_position is synced on legacy caster variants.
                toggle_ok = False
                for ident in id_candidates:
                    if await caster.toggle_show_pos(
                        identifier=ident,
                        username=rover.username,
                        enabled=target_show_pos
                    ):
                        toggle_ok = True
                        break
                if not toggle_ok:
                    logger.warning(f"Caster {host} did not confirm show_position toggle for rover '{rover.username}'")
                results_log.append(host)
            except Exception as e:
                logger.error(f"Failed to update rover on caster {host}: {e}")

        # 4b. Cleanup removed hosts
        for host in removed_hosts:
            try:
                caster = CasterClient(host, port)
                deleted_ok = await caster.delete_user(
                    identifier=creator_username,
                    username=rover.username
                )
                if not deleted_ok:
                    logger.warning(f"Caster {host} did not confirm delete for rover '{rover.username}'")
                logger.info(f"✓ Cleaned up (deleted) rover '{rover.username}' from removed caster {host}")
            except Exception as e:
                logger.warning(f"Failed to cleanup rover from removed caster {host}: {e}")

    except Exception as e:
        logger.error(f"Caster Multi-Update Error: {e}")
        raise HTTPException(status_code=502, detail=f"Lỗi cập nhật Caster: {str(e)}")

    # 5. Cập nhật DB Local
    update_data = {
        "password": new_password,
        "mountpoints": new_mountpoints,
        "max_connections": new_max_connections,
        "show_position": target_show_pos,
        "rtcm_cycle_enabled": bool(target_rtcm_cycle_enabled),
        "rtcm_disconnect_interval_seconds": int(target_rtcm_cycle_interval),
        "rtcm_disconnect_duration_seconds": int(target_rtcm_cycle_duration),
        "target_casters": ";".join(updated_target_hosts)
    }
    
    # 🔥 FIX: Cập nhật base_serial nếu client gửi lên danh sách trạm mới
    if update_req.selected_serials:
        update_data["base_serial"] = ",".join(update_req.selected_serials)
        
    updated_rover = await crud.update_rover_account(db, rover_id, update_data)
    
    return updated_rover

@app.delete("/api/rovers/{rover_id}")
async def delete_rover(
    rover_id: int,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db), # Add devices_db here
    current_user: models.User = Depends(auth.get_current_user)
):
    # 1. Lấy thông tin Rover
    rover_to_delete = await crud.get_rover_by_id(db, rover_id)
    if not rover_to_delete:
        raise HTTPException(status_code=404, detail="Không tìm thấy tÃ i khoản Rover.")

    # 2. Kiểm tra quyền
    is_admin = current_user.role == auth.Role.ADMIN
    is_creator = rover_to_delete.created_by_user_id == current_user.id

    # Ownership Check: COORDINATOR chỉ quản lý rover thuộc trạm mình sở hữu
    if not is_admin:
        serials = rover_to_delete.base_serial.split(',')
        owned_res = await devices_db.execute(
            select(models.Device.serial).where(models.Device.user_id == current_user.id)
        )
        owned_serials = set(owned_res.scalars().all())
        
        for sn in serials:
            sn_clean = sn.strip()
            # Nếu trạm ảo (serial trống) và là người tạo thì cho qua
            if sn_clean == "" and is_creator:
                continue
            if sn_clean not in owned_serials:
                raise HTTPException(status_code=403, detail=f"Bạn không có quyền quản lý Rover thuộc trạm '{sn}'.")

    
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

# === CASTER USER MANAGEMENT (For non-DB users) ===
@app.delete("/api/caster/users/{username}")
async def delete_caster_user_endpoint(
    username: str,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # 1. Kiểm tra quyền ADMIN
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(
            status_code=403, 
            detail="Chỉ Administrator mới có quyền xóa tài khoản ngoại lai trực tiếp trên Caster."
        )

    # 2. Xóa trên toàn bộ Caster được cấu hình
    hosts, config = _read_caster_config_from_env()
    if not hosts:
        raise HTTPException(status_code=500, detail="Chưa cấu hình Caster Server trong hệ thống.")

    results_log = []
    errors = []
    port = config.get("port") or 2101
    
    for host in hosts:
        try:
            from app.caster_client import CasterClient
            caster = CasterClient(host, port)
            # Dùng 'admin' hoặc current_user.username làm identifier cho lệnh deluser
            await caster.delete_user(identifier='admin', username=username)
            results_log.append(host)
        except Exception as e:
            logger.error(f"Failed to delete user '{username}' on caster {host}: {e}")
            errors.append(f"{host}: {str(e)}")

    if errors and not results_log:
        raise HTTPException(status_code=502, detail=f"Lỗi Caster: {'; '.join(errors)}")
    
    # Track the event
    try:
        from .crud import log_system_event
        await log_system_event(
            db=db,
            type="warning",
            message=f"Xóa tài khoản ngoại lai '{username}' khỏi Caster bởi {current_user.username}",
            user_id=current_user.id
        )
    except Exception:
        pass

    return {
        "status": "deleted",
        "username": username,
        "casters": results_log,
        "errors": errors
    }

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
    all_devices = {d.serial.strip().upper(): d.name for d in all_devices_result.scalars().all() if d.serial}

    for rover in rovers:
        rover_dict = schemas.RoverAccountResponse.from_orm(rover).model_dump()
        
        # Xử lý base_serial có thể lÃ  "SN1,SN2"
        serials = rover.base_serial.split(',')
        station_names = []
        
        for sn in serials:
            s_name = all_devices.get(sn.strip().upper())
            if s_name:
                station_names.append(s_name)
            else:
                station_names.append(sn) # Nếu không tìm thấy tên thì hiện serial
        
        # Nối tên các trạm lại: "Trạm A, Trạm B"
        rover_dict['base_name'] = ", ".join(station_names)
         
        enriched_rovers.append(rover_dict)
    
    return enriched_rovers
@app.put("/api/rovers/sync/by-username/{username}", response_model=schemas.RoverAccountResponse)
async def sync_rover_by_username(
    username: str,
    update_req: schemas.RoverUpdate,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    """
    Thông minh: Đồng bộ rover bằng username. 
    Nếu rover chưa có trong DB, nó sẽ tự động tạo mới (Link/Adopt).
    Nếu đã có, nó cập nhật thông tin.
    """
    # 1. Kiểm tra Admin/Creator
    is_admin = current_user.role == auth.Role.ADMIN
    if not is_admin and not (current_user.role == auth.Role.COORDINATOR and current_user.can_create_rovers):
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý Rover.")

    # 2. Tìm trong DB
    rover = await crud.get_rover_by_username(db, username)
    
    # 3. TRƯỜNG HỢP: USER CHƯA CÓ TRONG DB (Unknown)
    if not rover:
        if not update_req.selected_serials:
            raise HTTPException(status_code=400, detail="Vui lòng chọn trạm để đồng bộ tài khoản này vào hệ thống.")
        
        # Tạo mới trong DB
        combined_serials = ",".join(update_req.selected_serials)
        rover = await crud.create_rover_account(
            db,
            username=username,
            password=update_req.password or "123456", # Mặc định nếu không cung cấp
            base_serial=combined_serials,
            mountpoints=update_req.mountpoints,
            creator_id=current_user.id,
            max_connections=update_req.max_connections or 1,
            show_position=update_req.show_position if update_req.show_position is not None else True,
            rtcm_cycle_enabled=getattr(update_req, 'rtcm_cycle_enabled', False),
            rtcm_disconnect_interval_seconds=getattr(update_req, 'rtcm_disconnect_interval_seconds', 300)
        )
        logger.info(f"âœ“ Adopted unknown rover '{username}' into DB")
    
    # 4. TRƯỜNG HỢP: USER ĐÃ CÓ - CẬP NHẬT
    else:
        # Cập nhật DB
        update_data = {}
        if update_req.password: update_data["password"] = update_req.password
        if update_req.mountpoints: update_data["mountpoints"] = update_req.mountpoints
        if update_req.max_connections is not None: update_data["max_connections"] = update_req.max_connections
        if update_req.show_position is not None: update_data["show_position"] = update_req.show_position
        if update_req.rtcm_cycle_enabled is not None: update_data["rtcm_cycle_enabled"] = update_req.rtcm_cycle_enabled
        if update_req.rtcm_disconnect_interval_seconds is not None: update_data["rtcm_disconnect_interval_seconds"] = update_req.rtcm_disconnect_interval_seconds
        
        if update_data:
            # 🔥 FIX: Cập nhật base_serial nếu có gửi trạm đích
            if update_req.selected_serials:
                update_data["base_serial"] = ",".join(update_req.selected_serials)
                
            rover = await crud.update_rover_account(db, rover.id, update_data)

    # 5. ĐỒNG BỘ SANG CASTER
    try:
        caster = await get_caster_client(db)
        
        # Tính toán thời hạn
        duration_seconds = 315360000 # Default 10 years
        if update_req.expiry_days:
            duration_seconds = update_req.expiry_days * 86400

        # Nếu CHỈ thay đổi show_position, hãy dùng lệnh nhẹ toggle_show_pos để tránh ghi đè MP
        # Nhưng thường khi Edit, Dashboard sẽ gửi full payload. 
        # CasterClient.create_user thực hiện ghi đè toàn bộ bản ghi USERTABLE.
        
        created_ok = await caster.create_user(
            creator=current_user.username,
            username=username,
            password=update_req.password or rover.password,
            mountpoints=update_req.mountpoints or rover.mountpoints,
            expiry_seconds=duration_seconds,
            max_users=update_req.max_connections if update_req.max_connections is not None else rover.max_connections,
            show_position=update_req.show_position if update_req.show_position is not None else rover.show_position,
            rtcm_cycle_enabled=bool(getattr(rover, "rtcm_cycle_enabled", False) if update_req.rtcm_cycle_enabled is None else update_req.rtcm_cycle_enabled),
            rtcm_disconnect_interval_seconds=int(getattr(rover, "rtcm_disconnect_interval_seconds", 300) if update_req.rtcm_disconnect_interval_seconds is None else update_req.rtcm_disconnect_interval_seconds),
        )
        if not created_ok:
            logger.warning(f"Sync-by-username did not get caster confirmation for '{username}'")
        
        # Sync show_position again specifically to be safe
        if update_req.show_position is not None:
            toggle_id_candidates = []
            for ident in (current_user.username, "admin", "id"):
                ident_s = str(ident or "").strip()
                if ident_s and ident_s not in toggle_id_candidates:
                    toggle_id_candidates.append(ident_s)

            toggle_ok = False
            for ident in toggle_id_candidates:
                if await caster.toggle_show_pos(ident, username, update_req.show_position):
                    toggle_ok = True
                    break
            if not toggle_ok:
                logger.warning(f"Sync-by-username could not confirm show_position toggle for '{username}'")
            
    except Exception as e:
        logger.error(f"Sync Caster Error for '{username}': {e}")
        # Không raise lỗi 500 nếu DB đã lưu xong, chỉ cảnh báo
        
    return rover


@app.post("/api/rovers/create-multi", response_model=schemas.RoverAccountResponse)
async def create_multi_base_rover(
    request: schemas.RoverMultiCreate,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db), # Add devices_db here
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

    # 2.1 Kiểm tra Quyền sở hữu trạm (Nếu là COORDINATOR)
    if current_user.role == auth.Role.COORDINATOR:
        # Lấy danh sách serial của các trạm do Coordinator này quản lý
        coord_devices_res = await devices_db.execute(
            select(models.Device.serial).where(models.Device.user_id == current_user.id)
        )
        coord_owned_serials = set(coord_devices_res.scalars().all())
        
        # Kiểm tra xem toàn bộ các trạm trong request có thuộc quyền quản lý không
        for serial in request.selected_serials:
            if serial not in coord_owned_serials:
                raise HTTPException(
                    status_code=403, 
                    detail=f"Bạn không có quyền quản lý trạm '{serial}'."
                )



    # 3. Chuẩn bị dữ liệu
    duration_seconds = request.expiry_days * 86400
    target_mountpoints = request.mountpoint_override.strip()
    
    selected_serials = list(request.selected_serials or [])
    
    # 3. Xác định danh sÃ¡ch Mountpoint hợp lệ (Đặc biệt cho Coordinator)
    if current_user.role == auth.Role.COORDINATOR:
        valid_mps = set()
        
        # 1. MP từ cÃ¡c trạm vật lÃ½ được chọn
        if request.selected_serials:
            for serial in request.selected_serials:
                dev = await crud.get_device_by_serial(devices_db, serial)
                if dev and dev.user_id == current_user.id:
                    sc = dev.service_config or {}
                    if sc.get('server1_enabled') and sc.get('mountpoint1'): 
                        valid_mps.add(sc.get('mountpoint1'))
                    if sc.get('server2_enabled') and sc.get('mountpoint2'): 
                        valid_mps.add(sc.get('mountpoint2'))
        
        # 2. MP bổ sung được gÃ¡n rÃªng
        if current_user.extra_mountpoints:
            extras = [mp.strip() for mp in re.split(r'[;,\s\n]+', current_user.extra_mountpoints) if mp.strip()]
            valid_mps.update(extras)
            
        # 3. Kiểm tra Mountpoint yÃªu cầu
        requested = [mp.strip() for mp in re.split(r'[;,\s\n]+', request.mountpoint_override) if mp.strip()]
        if not requested:
             raise HTTPException(status_code=400, detail="Mountpoint không được để trống.")
             
        for mp in requested:
            if mp not in valid_mps:
                raise HTTPException(status_code=403, detail=f"Mountpoint '{mp}' không thuộc quyền quản lý của bạn.")

    # 4. Nới lỏng yêu cầu chọn trạm: 
    # Nếu là Coordinator vÃ  đã có mountpoint_override (hợp lệ) thì khÃ´ng bắt buộc chọn trạm vật lÃ½.
    if not selected_serials and not is_admin:
        if current_user.role != auth.Role.COORDINATOR or not target_mountpoints:
            raise HTTPException(status_code=400, detail="Phải chọn ít nhất 1 trạm hoặc Mountpoint bổ sung.")

    combined_serials = ",".join(selected_serials) if selected_serials else ""

    # 4. GỌI CASTER (Tạo user thật)
    try:
        creator_identifier = current_user.username 
        
        # Determine which casters to send to
        target_hosts = request.target_casters or []
        if not target_hosts:
            # Fallback to default caster
            env_cfg = _read_caster_config_from_env()
            default_host = str(env_cfg.get("host") or settings.CASTER_HOST or "")
            if default_host:
                target_hosts = [default_host]

        if not target_hosts:
            raise ValueError("No target caster configured.")

        # Create user on each target caster
        results_log = []
        errors = []
        port = int(settings.CASTER_PORT or 2101)
        
        for host in target_hosts:
            try:
                from app.caster_client import CasterClient
                caster = CasterClient(host, port)
                await caster.create_user(
                    creator=creator_identifier,
                    username=request.username,
                    password=request.password,
                    mountpoints=target_mountpoints, 
                    expiry_seconds=duration_seconds, 
                    max_users=request.max_connections,
                    show_position=request.show_position,
                    rtcm_cycle_enabled=bool(getattr(request, "rtcm_cycle_enabled", False)),
                    rtcm_disconnect_interval_seconds=int(getattr(request, "rtcm_disconnect_interval_seconds", 300) or 300),
                    rtcm_disconnect_duration_seconds=int(getattr(request, "rtcm_disconnect_duration_seconds", 10) or 10),
                )
                results_log.append(host)
            except Exception as e:
                logger.error(f"Failed to create user on caster {host}: {e}")
                errors.append(f"{host}: {str(e)}")

        if errors and not results_log:
            # Failed on all targets
            raise Exception("; ".join(errors))
            
        logger.info(f"✓ Created Multi-Rover '{request.username}' on: {', '.join(results_log)}")

    except Exception as e:
        logger.error(f"Caster Multi-Create error: {e}")
        raise HTTPException(status_code=502, detail=f"Lỗi Caster: {str(e)}")

    # 5. LƯU VÀO DB (Ghi rõ mountpoint)
    new_rover = await crud.create_rover_account(
        db, 
        username=request.username,
        password=request.password,
        base_serial=combined_serials,   # Lưu: "SN1,SN2"
        mountpoints=target_mountpoints, # Lưu: "MP1;MP2" (Cột mới)
        creator_id=current_user.id,
        max_connections=request.max_connections,
        target_casters=";".join(results_log), # Lưu chính xác những server đã tạo thành công
        rtcm_cycle_enabled=getattr(request, 'rtcm_cycle_enabled', False),
        rtcm_disconnect_interval_seconds=getattr(request, 'rtcm_disconnect_interval_seconds', 300),
        rtcm_disconnect_duration_seconds=getattr(request, 'rtcm_disconnect_duration_seconds', 10)
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
        if isinstance(params, dict) and params:
            normalized_params = crud.normalize_base_config(params)
            if normalized_params:
                device_in_db.base_config = normalized_params
            else:
                current_base_config = device_in_db.base_config if isinstance(device_in_db.base_config, dict) else {}
                merged_base_config = dict(current_base_config)
                for key, value in params.items():
                    if key != "coords":
                        merged_base_config[key] = value
                if merged_base_config:
                    device_in_db.base_config = merged_base_config
        device_in_db.is_synced = False
        
        await db.commit()
        await db.refresh(device_in_db)
        
        # 🔥 FIX: Invalidate sync cache để background task không check dữ liệu cũ
        mqtt_handler.invalidate_device_cache(serial)
        
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
    current_user: models.User = Depends(auth.get_current_user) # Thay require_permission để check thủ công
):
    device_in_db = await crud.get_device_by_serial(db, serial)
    if not device_in_db:
        raise HTTPException(status_code=404, detail="Device not found in database")

    # 1. Kiểm tra quyền sở hữu (Nếu là COORDINATOR)
    if current_user.role == auth.Role.COORDINATOR and device_in_db.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý thiết bị nÃ y.")

    # 2. Kiểm tra MANAGE_STATION_MODE (Nếu thay đổi mode sleep/always-on toàn cục hoặc theo server)
    old_service_cfg = device_in_db.service_config or {}
    old_on_demand = old_service_cfg.get("stream_on_demand", False)
    new_on_demand = payload.stream_on_demand
    old_s1_on_demand = old_service_cfg.get("server1_stream_on_demand")
    old_s2_on_demand = old_service_cfg.get("server2_stream_on_demand")
    new_s1_on_demand = payload.server1_stream_on_demand
    new_s2_on_demand = payload.server2_stream_on_demand

    station_mode_changed = (
        old_on_demand != new_on_demand
        or old_s1_on_demand != new_s1_on_demand
        or old_s2_on_demand != new_s2_on_demand
    )

    if station_mode_changed:
        if not auth.has_permission(current_user, auth.Permission.MANAGE_STATION_MODE):
             raise HTTPException(
                 status_code=403, 
                 detail="Chỉ ADMIN mới có quyền thay đổi chế độ Sleep/Always-on."
             )
    
    # 3. Yêu cầu EDIT_SERVICE_CONFIG cho các config khác
    if not auth.has_permission(current_user, auth.Permission.EDIT_SERVICE_CONFIG):
         raise HTTPException(status_code=403, detail="Bạn không có quyền sửa cấu hình dịch vụ.")

    # --- BƯỚC 1: LƯU CẤU HÌNH "CHUẨN" VÀO DATABASE ---
    try:
        device_in_db.service_config = payload.model_dump()
        await db.commit()
        await db.refresh(device_in_db)
        
        # 🔥 FIX: Invalidate sync cache
        mqtt_handler.invalidate_device_cache(serial)
        
        logging.info(f"✓ Saved service_config for {serial} to database.")
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save service config to database.")

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
    
    # 🔥 FIX: Invalidate sync cache on reset
    mqtt_handler.invalidate_device_cache(serial)
    
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
    deleted_chip_type = str(device.detected_chip_type or "").upper()
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

    if deleted_chip_type == "VIRTUAL":
        runtime_manager = globals().get("virtual_runtime_manager")
        if runtime_manager is not None:
            for method_name in ("refresh", "reconcile", "reconcile_all", "sync", "reload", "refresh_all"):
                method = getattr(runtime_manager, method_name, None)
                if not callable(method):
                    continue
                result = method()
                if asyncio.iscoroutine(result):
                    await result
                break
    
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
        base_config = device.base_config if isinstance(device.base_config, dict) else {}
        coords = base_config.get('coords', {})
        if not isinstance(coords, dict):
            coords = {}
        base_method = base_config.get('base_setup_method', '')
        base_lat = coords.get('lat', base_config.get('lat', ''))
        base_lon = coords.get('lon', base_config.get('lon', ''))
        base_alt = coords.get('alt', base_config.get('alt', ''))
        base_accuracy = base_config.get('accuracy', base_config.get('acc', ''))
        
        # === CẤU HÌNH SERVICE ===
        service_config = device.service_config if isinstance(device.service_config, dict) else {}
        
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

        now_ts = int(time.time())

        # FIX: Read device from DB to get full config (name, base_config, service_config)
        # then broadcast FULL payload to UI so the map doesn't lose station position.
        # Only write status-related fields to DB, do NOT touch config.
        ui_broadcast_payload = {
            "serial": serial,
            "status": "offline",
            "ntrip_connected": False,
            "bps": 0,
            "ntrip_status": {
                "server1": {"connected": False, "bps": 0},
                "server2": {"connected": False, "bps": 0}
            },
            "timestamp": now_ts
        }

        try:
            async with device_session_scope() as offline_db:
                device = await crud.get_device_by_serial(offline_db, serial)
                if device:
                    # Include config in broadcast payload so UI keeps full data
                    ui_broadcast_payload["name"] = device.name
                    ui_broadcast_payload["base_config"] = device.base_config or {}
                    ui_broadcast_payload["service_config"] = device.service_config or {}
                    ui_broadcast_payload["detected_chip_type"] = device.detected_chip_type
                    ui_broadcast_payload["is_provisioned"] = device.is_provisioned
                    ui_broadcast_payload["version"] = device.version
                    ui_broadcast_payload["is_synced"] = device.is_synced if device.is_synced is not None else True

                    # Only update status-related fields, do NOT touch config
                    device.status = "offline"
                    device.bps = 0
                    device.ntrip_connected = False
                    device.ntrip_status = {
                        "server1": {"connected": False, "bps": 0},
                        "server2": {"connected": False, "bps": 0}
                    }
                    device.timestamp = now_ts
                    await offline_db.commit()
                    logger.info(f"Device '{serial}' set to offline (config preserved in DB)")
        except Exception as e:
            logger.warning(f"[OFFLINE] DB lookup/update failed for '{serial}': {e}")

        # Broadcast FULL payload (with config) to UI so map keeps station
        await enqueue_ui_broadcast({
            "type": "status_update",
            "data": ui_broadcast_payload
        })
    
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

@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Xử lý 404 cho API và các lỗi HTTP khác"""
    if exc.status_code == 404 and request.url.path.startswith("/api/"):
        client_ip = request.client.host if request.client else "unknown"
        logger.warning(f"🚫 SECURITY BLOCK (API 404): IP {client_ip} probed non-existent API: {request.url.path}")
        
        # Ghi nhận vi phạm
        if client_ip != "unknown":
            score = security_rep_scores.get(client_ip, 0) + 1
            security_rep_scores[client_ip] = score
            if score >= VIOLATION_THRESHOLD:
                banned_ips[client_ip] = time.time() + BAN_DURATION
                logger.critical(f"🚨 AUTOMATIC BAN (API PROBE): IP {client_ip} BANNED after {score} violations.")

        return JSONResponse(
            status_code=403,
            content={"detail": "Access Forbidden: API endpoint not found and blocked for security."}
        )
    return await http_exception_handler(request, exc)

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

@app.get("/api/caster/config")
async def get_caster_config(
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES))
):
    """Return the list of configured caster hosts and system version."""
    hosts, env_cfg = _read_caster_config_from_env()
    if not hosts:
        # Fallback to single host from settings
        hosts = [str(settings.CASTER_HOST or "localhost")]
    
    return {
        "hosts": list(set(hosts)),
        "version": os.getenv("APP_VERSION", "V1.1.1")
    }

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
    allow_origins=["https://cors.aitogy.com, http://118.70.171.179:8000"],  # âš ï¸ Trong production, hãy chỉ định origins cụ thể
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


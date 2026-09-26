# ==============================================================================
# == backend/app/main.py                ==
# ==============================================================================

import logging
import uuid
import hashlib
import json
import asyncio
import math
from datetime import date, datetime, timedelta, timezone
import gc
import sys
import traceback
import base64
import io
import os
import time
import csv
import re
import unicodedata
import difflib
import psutil
import secrets
import ctypes
from pathlib import Path
from collections import deque
from contextlib import asynccontextmanager
from typing import Optional, List, Any, Set, Dict
from urllib.parse import unquote
from dotenv import dotenv_values, set_key

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect, Depends, 
    HTTPException, Request, Query, status, Body
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse, FileResponse, StreamingResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select, update, delete, func
from starlette.middleware.base import BaseHTTPMiddleware
from .monitoring import health_monitor, CircuitBreaker, global_rate_limiter
from .redis_client import redis_client
from . import crud, models, schemas, command_builder, auth
from starlette.responses import Response
from .database import (
    engine, auth_engine, get_db, get_auth_db, 
    AuthBase, AsyncAuthSession, AsyncSessionLocal, settings,
    device_session_scope, auth_session_scope, DatabaseBusyError
)

from .websocket import manager as ui_manager, visitor_manager
from .pi_websocket import pi_manager
from . import mqtt as mqtt_handler
from .mqtt import mqtt_client
from .command_dispatcher import send_command_to_pi as dispatch_command_to_pi
from . import license_manager
from . import nmea_parser
from . import models, auth, crud
from .security import rate_limiter
from .monitoring import health_monitor
from .caster_client import CasterClient
from .schema_compat import ensure_auth_zalo_runtime_columns, ensure_device_runtime_columns
from .status_reconciler import status_reconciler
from .control_plane_ack import build_control_plane_ack
from .nonblocking_logging import configure_nonblocking_logging
from .export_workbook import make_workbook
from .rtcm_stream_state import (
    apply_stream_control_state,
    build_stream_control_payload,
    collect_reapable_server_streams,
    normalize_mountpoint as normalize_stream_mountpoint,
)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# --- Cấu hình Logging ---
log_file = 'app.log'
log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# High-frequency public reads are expected traffic. Keep failures visible while
# avoiding three INFO lines for every successful map refresh.
QUIET_REQUEST_LOG_PATHS = frozenset({
    "/api/public/map-stations",
})


class QuietEndpointAccessFilter(logging.Filter):
    def filter(self, record):
        try:
            args = record.args
            path = str(args[2]).split("?", 1)[0] if isinstance(args, tuple) and len(args) >= 3 else ""
            status_code = int(args[4]) if isinstance(args, tuple) and len(args) >= 5 else 0
            return path not in QUIET_REQUEST_LOG_PATHS or status_code >= 400
        except (TypeError, ValueError, IndexError):
            return True

# Tạo FileHandler một lần để dùng chung
logging_runtime = configure_nonblocking_logging(
    log_file=log_file,
    log_format=log_format,
    level=logging.INFO,
    queue_size=int(os.getenv("LOG_QUEUE_SIZE", "20000")),
)

# httpx logs full request URLs. Zalo embeds the bot credential in its URL, so
# INFO request logs would write that credential to both console and app.log.
logging.getLogger("httpx").setLevel(logging.WARNING)

# Chèn FileHandler vào log của Uvicorn để bắt lỗi startup/server
logging.getLogger("uvicorn.access").addFilter(QuietEndpointAccessFilter())


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
    ".aws", ".backup", ".bak", ".bz2", ".cgi", ".conf", ".config", ".db", ".env",
    ".exe", ".git", ".gz", ".htaccess", ".htpasswd", ".ini", ".jsp", ".old", ".orig",
    ".php", ".rar", ".sql", ".sqlite", ".swp", ".tar", ".tgz", ".zip",
    "actuator", "adminer", "administrator", "backup", "cgi-bin", "config.bak",
    "config.php", "debug", "dump", "info.php", "jenkins", "mysql-admin",
    "phpinfo", "phpmyadmin", "private", "public/.env", "server-status", "setup.php",
    "test.php", "vendor/phpunit", "wp-admin", "wp-content", "wp-includes",
    "wp-login", "xmlrpc.php", "backend/.env"
}

SECURITY_SCANNER_UA_PATTERNS = {
    "acunetix", "arachni", "dirbuster", "gobuster", "masscan", "nessus", "nikto",
    "nmap", "openvas", "sqlmap", "wpscan", "zgrab", "zmap", "nuclei", "fuzz",
    "ffuf", "wafw00f", "whatweb"
}

SECURITY_QUERY_PATTERNS = {
    "../", "..\\", "%2e%2e", "<script", "javascript:", "onerror=", "onload=",
    "union select", "select ", " from ", "drop table", "insert into", "benchmark(",
    "sleep(", "/etc/passwd", "cmd=", "exec=", "system(", "base64_decode"
}

SECURITY_ALLOWED_PATH_PREFIXES = (
    "/", "/api/", "/ws/", "/img/", "/assets/", "/css/", "/js/", "/pages/", "/static/"
)

SECURITY_MAX_PATH_LENGTH = int(os.getenv("SECURITY_MAX_PATH_LENGTH", "2048"))
SECURITY_MAX_QUERY_LENGTH = int(os.getenv("SECURITY_MAX_QUERY_LENGTH", "4096"))
SECURITY_MAX_CONTENT_LENGTH = int(os.getenv("SECURITY_MAX_CONTENT_LENGTH", str(25 * 1024 * 1024)))
SECURITY_BURST_WINDOW_SECONDS = float(os.getenv("SECURITY_BURST_WINDOW_SECONDS", "10"))
SECURITY_BURST_MAX_REQUESTS = int(os.getenv("SECURITY_BURST_MAX_REQUESTS", "180"))
SECURITY_STRICT_MODE = str(os.getenv("SECURITY_STRICT_MODE", "1")).strip().lower() in ("1", "true", "yes", "on")

# --- IP REPUTATION SYSTEM ---
VIOLATION_THRESHOLD = 5  # Số lần vi phạm tối đa trước khi BAN
BAN_DURATION = 3600      # Thời gian BAN (giây) - 1 giờ
security_rep_scores: Dict[str, int] = {} # IP -> score
banned_ips: Dict[str, float] = {}        # IP -> ban_expiry_timestamp
security_request_times: Dict[str, deque] = {}


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

CASTER_STATUS_NOTIFY_CONCURRENCY = max(
    1,
    int(os.getenv("CASTER_STATUS_NOTIFY_CONCURRENCY", "4")),
)
caster_status_notify_semaphore = asyncio.Semaphore(CASTER_STATUS_NOTIFY_CONCURRENCY)

PUBLIC_MAP_CACHE_TTL_SECONDS = max(
    1.0,
    float(os.getenv("PUBLIC_MAP_CACHE_TTL_SECONDS", "10")),
)
public_map_cache: Optional[list] = None
public_map_cache_updated_monotonic = 0.0

EVENT_LOOP_WATCHDOG_INTERVAL_SECONDS = max(
    0.5,
    float(os.getenv("EVENT_LOOP_WATCHDOG_INTERVAL_SECONDS", "2")),
)
EVENT_LOOP_WATCHDOG_WARN_LAG_SECONDS = max(
    0.1,
    float(os.getenv("EVENT_LOOP_WATCHDOG_WARN_LAG_SECONDS", "1")),
)
event_loop_watchdog_metrics = {
    "last_lag_ms": 0.0,
    "max_lag_ms": 0.0,
    "warning_count": 0,
}
device_update_buffer = deque(maxlen=DEVICE_UPDATE_BUFFER_MAXSIZE)
device_update_buffer_lock = asyncio.Lock()
latest_device_telemetry_cache: Dict[str, dict] = {}

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
pending_ui_status_updates: Dict[str, dict] = {}
pending_ui_status_lock = asyncio.Lock()
pending_ui_status_event = asyncio.Event()
ui_status_emit_locks: Dict[str, asyncio.Lock] = {}


def _get_ui_status_emit_lock(serial: str) -> asyncio.Lock:
    return ui_status_emit_locks.setdefault(str(serial), asyncio.Lock())

# TỐI ƯU CỰC ĐỘ: Cache mapping Mountpoint -> Serial để tránh quét DB liên tục
# Giúp xử lý hàng nghìn yêu cầu wake-station mỗi giây mà không làm treo Backend.
mountpoint_serial_cache: Dict[str, str] = {}
command_mountpoints_cache: Set[str] = set()
mountpoint_cache_last_updated: float = 0
mountpoint_cache_lock = asyncio.Lock()


def _is_command_mountpoint(mountpoint: str) -> bool:
    if not mountpoint:
        return False
    normalized = _normalize_mountpoint(mountpoint)
    if not normalized:
        return False
    # 1. Tra cứu trong cache động từ cơ sở dữ liệu
    if normalized in command_mountpoints_cache:
        return True
    # 2. Quy tắc dự phòng heuristics
    normalized_lower = normalized.lower()
    if normalized_lower == 'test_lte':
        return True
    if normalized_lower.startswith(('test_', 'cmd_')) or normalized_lower.endswith(('_lte', '_cmd')):
        return True
    return False


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
        "agent_timestamp",
        "status_boot_id",
        "status_sequence",
        "status_event",
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
        "sync_errors",
        "ntrip_stats",
        "nmea_data",
        "nmea_health",
        "version",
        "satellite_count",
        "hacc",
        "transport_status",
        "last_command_result",
        "reconnect_counters",
        "queue_metrics",
        "serial_health",
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
    if isinstance(message, dict) and message.get("type") == "status_update":
        data = message.get("data") or {}
        serial = str(message.get("serial") or data.get("serial") or "").strip()
        if serial:
            async with pending_ui_status_lock:
                if (
                    message.get("_drop_if_device_missing")
                    and mqtt_handler.device_metadata_is_missing(serial)
                ):
                    return
                previous_message = pending_ui_status_updates.get(serial) or {}
                previous_data = previous_message.get("data") or {}
                revision = int(data.get("status_revision") or 0)
                previous_revision = int(previous_data.get("status_revision") or 0)
                received_at = float(data.get("server_received_at") or data.get("timestamp") or 0)
                previous_received_at = float(
                    previous_data.get("server_received_at") or previous_data.get("timestamp") or 0
                )
                if previous_message and revision and previous_revision:
                    if revision < previous_revision:
                        return
                    if revision == previous_revision and received_at <= previous_received_at:
                        return
                pending_ui_status_updates[serial] = message
                pending_ui_status_event.set()
            return
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


async def ui_status_broadcast_worker_task():
    """Coalesce bursts per station without dropping another station's update."""
    while True:
        await pending_ui_status_event.wait()
        await asyncio.sleep(0.05)
        async with pending_ui_status_lock:
            batch = list(pending_ui_status_updates.values())
            pending_ui_status_updates.clear()
            pending_ui_status_event.clear()
        for message in batch:
            try:
                data = message.get("data") or {}
                serial = str(message.get("serial") or data.get("serial") or "").strip()
                async with _get_ui_status_emit_lock(serial):
                    if mqtt_handler.device_was_deleted(serial):
                        continue
                    if message.get("_drop_if_device_missing") and mqtt_handler.device_metadata_is_missing(serial):
                        continue
                    outbound = message
                    if "_drop_if_device_missing" in message:
                        outbound = dict(message)
                        outbound.pop("_drop_if_device_missing", None)
                    await ui_manager.broadcast(outbound)
            except Exception as exc:
                logger.warning("UI status broadcast worker error: %s", exc)


async def event_loop_watchdog_task():
    """Record and report event-loop stalls before nginx starts losing upstream responses."""
    loop = asyncio.get_running_loop()
    expected_at = loop.time() + EVENT_LOOP_WATCHDOG_INTERVAL_SECONDS
    while True:
        await asyncio.sleep(EVENT_LOOP_WATCHDOG_INTERVAL_SECONDS)
        now = loop.time()
        lag_seconds = max(0.0, now - expected_at)
        lag_ms = lag_seconds * 1000.0
        event_loop_watchdog_metrics["last_lag_ms"] = round(lag_ms, 2)
        event_loop_watchdog_metrics["max_lag_ms"] = max(
            float(event_loop_watchdog_metrics["max_lag_ms"]),
            round(lag_ms, 2),
        )
        if lag_seconds >= EVENT_LOOP_WATCHDOG_WARN_LAG_SECONDS:
            event_loop_watchdog_metrics["warning_count"] += 1
            logger.warning(
                "[EVENT LOOP] Backend stalled for %.0f ms (warning #%s)",
                lag_ms,
                event_loop_watchdog_metrics["warning_count"],
            )
        expected_at = now + EVENT_LOOP_WATCHDOG_INTERVAL_SECONDS


async def pi_status_ingest_worker_task():
    while True:
        payload = await pi_status_ingest_queue.get()
        try:
            serial = payload.get("serial") if isinstance(payload, dict) else None
            # Preserve reconciler order before any slower broadcast/DB lookup can
            # let another worker overtake this station update. The lifecycle
            # lock also orders these buffered writes against explicit deletion.
            async with mqtt_handler.mqtt_db_write_lock:
                if serial and mqtt_handler.device_was_deleted(serial):
                    continue
                async with device_update_buffer_lock:
                    device_update_buffer.append(payload)
                await enqueue_ui_broadcast({"type": "status_update", "data": payload})
            
            # === CASTER STATUS SYNC: Detect per-server status changes ===
            if serial:
                live_snapshot = dict(latest_device_telemetry_cache.get(str(serial)) or {})
                live_snapshot.update(payload)
                latest_device_telemetry_cache[str(serial)] = live_snapshot
                try:
                    async with device_session_scope() as db:
                        device = await crud.get_device_by_serial(db, serial)
                        if device:
                            # Trigger status change detection (pass payload for rich ntrip_stream_state)
                            await _on_device_status_update(device, payload)
                except Exception as e:
                    logger.warning(f"[CASTER SYNC] Error detecting status change for '{serial}': {e}")
            
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


try:
    ROVER_FIXED_POINT_INTERVAL_SECONDS = int(
        _get_env_preferring_dotenv("ROVER_FIXED_POINT_INTERVAL_SECONDS", "120")
    )
    if ROVER_FIXED_POINT_INTERVAL_SECONDS <= 0:
        raise ValueError("must be positive")
except (TypeError, ValueError):
    ROVER_FIXED_POINT_INTERVAL_SECONDS = 120
    logger.warning("Invalid ROVER_FIXED_POINT_INTERVAL_SECONDS; using 120 seconds")


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


def _device_service_config(device: Optional[models.Device]) -> dict:
    return crud.normalize_service_config(getattr(device, "service_config", None) if device else None)


def _preserve_relay_source_config(serial: str, previous: dict, incoming: dict) -> dict:
    updated = dict(incoming or {})
    source = previous.get("relay_source") if isinstance(previous, dict) else None
    if str(serial or "").strip().upper().startswith("RELAY_") and isinstance(source, dict):
        updated["relay_source"] = source
    return updated


def _extract_station_mountpoints(device: models.Device) -> List[str]:
    cfg = _device_service_config(device)
    result: List[str] = []
    if cfg.get("server1_enabled") and cfg.get("mountpoint1"):
        result.append(str(cfg.get("mountpoint1")))
    if cfg.get("server2_enabled") and cfg.get("mountpoint2"):
        result.append(str(cfg.get("mountpoint2")))
    return result


def _normalize_mountpoint(value: Optional[str]) -> Optional[str]:
    return normalize_stream_mountpoint(value)


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
    cfg = _device_service_config(device)
    active_mp = cfg.get("active_mountpoint")
    if not active_mp:
        is_active = bool(cfg.get("stream_active", cfg.get("server1_stream_active", False)))
        if is_active:
            active_mp = cfg.get("mountpoint1") or cfg.get("target_mp") or device.name or device.serial
    return str(active_mp) if active_mp else None


def _extract_reapable_server_streams(
    device: Optional[models.Device],
) -> tuple[tuple[Optional[int], str], ...]:
    """Return server-scoped On-Demand streams eligible for session reaping.

    Per-server state is authoritative for modern configurations. The legacy
    aggregate fallback is kept only when no per-server stream flags exist.
    """

    if not device:
        return ()

    cfg = _device_service_config(device)
    return collect_reapable_server_streams(
        cfg,
        legacy_active_mountpoint=_extract_active_mountpoint(device),
    )


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

    if str(getattr(device, "status", "offline") or "offline").lower() == "offline":
        return "offline"

    if bool(getattr(device, "ntrip_connected", False)):
        return "streaming"

    cfg = _device_service_config(device)
    if cfg.get("stream_on_demand") and not cfg.get("stream_active", True):
        return "sleep"
    return "starting"


async def _set_station_stream(
    serial: str,
    active: bool,
    mountpoint: Optional[str] = None,
    requested_address: Optional[str] = None,
    server_id: Optional[int] = None,
) -> None:
    requested_mp_norm = _normalize_mountpoint(mountpoint) if mountpoint else None
    try:
        target_server_id = int(server_id) if server_id is not None else None
    except (TypeError, ValueError):
        target_server_id = None
    if target_server_id not in (1, 2):
        target_server_id = None

    if target_server_id is None and requested_mp_norm and (bool(active) or requested_address):
        # Tự động phân giải server_idx (1 hoặc 2) dựa vào cấu hình trạm
        try:
            async with device_session_scope() as db:
                device = await crud.get_device_by_serial(db, serial)
                if device:
                    target_server_id = _resolve_preferred_server_id_for_mountpoint(
                        _device_service_config(device),
                        requested_mp_norm,
                        requested_address,
                    )
        except Exception:
            pass

    payload = build_stream_control_payload(
        active=active,
        mountpoint=mountpoint,
        address=requested_address,
        server_id=target_server_id,
    )

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

            cfg = dict(_device_service_config(device))

            if target_server_id is None and requested_mp_norm and (bool(active) or requested_address):
                target_server_id = _resolve_preferred_server_id_for_mountpoint(cfg, requested_mp_norm, requested_address)

            cfg = apply_stream_control_state(
                cfg,
                active=active,
                mountpoint=mountpoint,
                server_id=target_server_id,
            )

            # These are aggregate metrics. Keep S2 telemetry intact when an
            # On-Demand S1 is the only stream being put to sleep.
            if not bool(active) and not bool(cfg.get("stream_active", False)):
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

    cfg = _device_service_config(device)
    s1_ok = bool(cfg.get("server1_enabled")) and _normalize_mountpoint(cfg.get("mountpoint1")) == requested
    s2_ok = bool(cfg.get("server2_enabled")) and _normalize_mountpoint(cfg.get("mountpoint2")) == requested
    return s1_ok or s2_ok


async def _resolve_station_serial_by_mountpoint(db: AsyncSession, mountpoint: str) -> str:
    global mountpoint_serial_cache, command_mountpoints_cache, mountpoint_cache_last_updated
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
            logger.info("[CACHE] Rebuilding Mountpoint-to-Serial cache & Command Mountpoints cache...")
            new_cache = {}
            new_cmd_cache = set()
            rs = await db.execute(select(models.Device.serial, models.Device.service_config, models.Device.system_info))
            for row in rs.all():
                serial = str(row[0])
                
                # 🔥 FIX: Xử lý an toàn service_config
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
                        if mp not in new_cache:
                            new_cache[mp] = serial

                # Quét system_info để lấy command_mp động
                raw_sys = row[2]
                if isinstance(raw_sys, dict):
                    sys_info = raw_sys
                elif isinstance(raw_sys, str):
                    try: sys_info = json.loads(raw_sys)
                    except: sys_info = {}
                else:
                    sys_info = {}

                cmd_source = sys_info.get("command_source") or {}
                if isinstance(cmd_source, dict):
                    cmd_mp = _normalize_mountpoint(cmd_source.get("mountpoint") or cmd_source.get("command_mp"))
                    if cmd_mp:
                        new_cmd_cache.add(cmd_mp)
            
            mountpoint_serial_cache = new_cache
            command_mountpoints_cache = new_cmd_cache
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


async def _ntrip_mqtt_stop(serial: str, client_instance_id: str, caster_host: Optional[str] = None):
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
                select(
                    models.RTCMStreamSession.session_id, 
                    models.RTCMStreamSession.mountpoint,
                    models.RTCMStreamSession.caster_host
                ).where(
                    models.RTCMStreamSession.serial == serial,
                    models.RTCMStreamSession.client_id == client_id,
                    models.RTCMStreamSession.expires_at > now,
                )
            )
            active_rows = [
                (str(r[0]), str(r[1]), str(r[2] or "")) 
                for r in active_sessions_rs.all() if r and r[0] and r[1]
            ]
            
            if caster_host:
                norm_ch = caster_host.strip().lower()
                active_rows = [
                    r for r in active_rows 
                    if norm_ch in r[2].lower() or r[2].lower() in norm_ch
                ]

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
        await _set_station_stream(serial, active=False, requested_address=caster_host)


async def handle_ntrip_mqtt_command(topic: str, payload: bytes):
    try:
        data = json.loads(payload.decode("utf-8"))
    except Exception as e:
        logger.warning(f"[NTRIP MQTT] Invalid JSON on topic '{topic}': {e}")
        return

    if not (topic.endswith("/start") or topic.endswith("/stop")):
        logger.warning(f"[NTRIP MQTT] Unsupported topic '{topic}'. Use ntrip/rtcm/start or stop only")
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
            logger.warning("[NTRIP MQTT] Missing 'mountpoint' for command")
            return

        if mountpoint and _is_command_mountpoint(mountpoint):
            logger.debug(f"[NTRIP MQTT] Bypassing command for command/test mountpoint '{mountpoint}'")
            return

        if topic.endswith("/start"):
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
        elif topic.endswith("/stop"):
            force = bool(data.get("force", False))
            address = data.get("caster_address") or data.get("address") or data.get("host") or data.get("ip") or data.get("caster_host")
            await _do_ntrip_stop(
                mountpoint=mountpoint,
                client_id=client_instance_id,
                force=force,
                requested_address=address
            )
            logger.info(f"[NTRIP MQTT] stop accepted mountpoint={mountpoint} client={client_instance_id} force={force} address={address}")
    except Exception as e:
        logger.warning(f"[NTRIP MQTT] Command failed mountpoint={mountpoint}: {e}")


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
                primary_cache = await _get_caster_cache(f"{target_host}:{port}")
                caster_error_on_primary = bool(primary_cache and primary_cache.get("error"))
                for h in hosts_to_check:
                    h_port = int(settings.CASTER_PORT or 2101)
                    h_key = f"{h}:{h_port}"
                    h_cache = await _get_caster_cache(h_key)
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
                        device = await crud.get_device_by_serial(db, serial)
                        cfg = _device_service_config(device)
                        if not cfg.get("stream_on_demand", True):
                            continue
                        await _set_station_stream(serial, active=False)
                        logger.info(f"[RTCM] Deactivated station {serial} (mp={mp_norm}, no remaining sessions)")

        except Exception as e:
            logger.error(f"[RTCM] Keepalive task error: {e}", exc_info=True)

async def _rtcm_session_reaper_loop():
    while True:
        await asyncio.sleep(5)
        now = int(time.time())
        stations_to_deactivate: Set[tuple[str, Optional[int], str]] = set()

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
                        expired_mp_norm = _normalize_mountpoint(expired_mountpoint)
                        for server_id, mountpoint_norm in _extract_reapable_server_streams(device):
                            if mountpoint_norm != expired_mp_norm:
                                continue
                            active_count = await _count_active_mountpoint_sessions(
                                db,
                                serial,
                                mountpoint_norm,
                                now,
                            )
                            if active_count == 0:
                                stations_to_deactivate.add((serial, server_id, mountpoint_norm))

                # Safety sweep: if stream is marked active but there are no sessions for active mountpoint,
                # force stream off. This covers manual DB cleanup and stale state scenarios.
                devices_rs = await db.execute(select(models.Device))
                for device in devices_rs.scalars().all():
                    for server_id, mountpoint_norm in _extract_reapable_server_streams(device):
                        remaining_for_active_mp = await _count_active_mountpoint_sessions(
                            db,
                            str(device.serial),
                            mountpoint_norm,
                            now,
                        )
                        if remaining_for_active_mp == 0:
                            stations_to_deactivate.add(
                                (str(device.serial), server_id, mountpoint_norm)
                            )

        for serial, server_id, mountpoint_norm in stations_to_deactivate:
            try:
                # A wake may arrive between the initial scan and command send.
                # Re-check under the session lock so a fresh rover is never put
                # back to sleep by a stale reaper candidate.
                async with rtcm_session_lock:
                    async with device_session_scope() as db:
                        device = await crud.get_device_by_serial(db, serial)
                        current_targets = set(_extract_reapable_server_streams(device))
                        if (server_id, mountpoint_norm) not in current_targets:
                            continue
                        remaining = await _count_active_mountpoint_sessions(
                            db,
                            serial,
                            mountpoint_norm,
                            int(time.time()),
                        )
                        if remaining > 0:
                            continue

                    server_label = f" S{server_id}" if server_id in (1, 2) else ""
                    device_label = f"{serial}@{mountpoint_norm}{server_label}"
                    logger.info(f"[RTCM REAPER] Sending deactivation command to station {device_label}")
                    await _set_station_stream(
                        serial,
                        active=False,
                        mountpoint=mountpoint_norm or None,
                        server_id=server_id,
                    )
            except Exception as e:
                logger.warning(f"[RTCM REAPER] Failed to auto-deactivate station '{serial}': {e}")


async def rtcm_session_reaper_task():
    """Keep the session reaper alive through transient DB admission failures."""
    while True:
        try:
            await _rtcm_session_reaper_loop()
        except asyncio.CancelledError:
            raise
        except DatabaseBusyError as exc:
            logger.warning("[RTCM REAPER] Database busy; retrying: %s", exc)
        except Exception:
            logger.exception("[RTCM REAPER] Loop crashed; restarting")
        await asyncio.sleep(5)

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
        message = record.getMessage()
        exc_text = ""
        if record.exc_info:
            exc_text = "".join(traceback.format_exception(*record.exc_info))

        noisy_socket_errors = (
            "socket.send() raised exception",
            "Accept failed on a socket",
            "Task exception was never retrieved",
            "WinError 64",
            "The specified network name is no longer available",
        )
        if any(token in message or token in exc_text for token in noisy_socket_errors):
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

        # 1. Kiểm tra xem IP có đang bị BAN không (Via Redis)
        is_banned = await redis_client.is_ip_banned(client_ip)
        if is_banned:
            return JSONResponse(
                status_code=403,
                content={"detail": "Your IP has been temporarily banned due to multiple security violations."}
            )

        # Helper để ghi nhận vi phạm và thực hiện BAN nếu cần
        async def record_violation():
            if client_ip == "unknown": return
            score = security_rep_scores.get(client_ip, 0) + 1
            security_rep_scores[client_ip] = score
            if score >= VIOLATION_THRESHOLD:
                await redis_client.ban_ip(client_ip)
                # Dọn dẹp score vì đã ban
                security_rep_scores[client_ip] = 0
                logger.critical(f"🚨 AUTOMATIC BAN: IP {client_ip} has been BANNED after {score} violations.")

        # 2. Chặn Method lạ (Linh tinh)
        if method not in ALLOWED_HTTP_METHODS:
            logger.warning(f"🚫 SECURITY BLOCK (METHOD): IP {client_ip} used forbidden method {method} on {path}")
            await record_violation()
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
                await record_violation()
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
        quiet_success_path = request.url.path in QUIET_REQUEST_LOG_PATHS
        
        # Ghi log mÃ  không cần extra, vì filter sẽ tự thêm vÃ o
        if not quiet_success_path:
            logger.info(f"--> {request.method} {request.url.path}")
        
        start_time = time.time()
        response = await call_next(request)
        process_time = (time.time() - start_time) * 1000
        
        response.headers['X-Request-ID'] = request_id
        if not quiet_success_path or response.status_code >= 400:
            logger.info(f"<-- {request.method} {request.url.path} - Completed in {process_time:.2f}ms, Status: {response.status_code}")
        
        return response

class FrontendCacheMiddleware(BaseHTTPMiddleware):
    """Keep HTML fresh while caching versioned source assets for a short period."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "").lower()

        if "text/html" in content_type:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        elif path.startswith(("/css/", "/js/", "/img/")):
            response.headers["Cache-Control"] = "public, max-age=86400, stale-while-revalidate=604800"
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
    logger.info("ðŸš€ Application starting...")
    
    # List để track tất cả background tasks
    tasks = []
    
    try:
        # ===================================================================
        # BƯỚC 1: VERIFY DATABASES (Cleaned up, stable schema assumed)
        # ===================================================================
        try:
            from sqlalchemy import text
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("✓ Device database connection verified")
            await ensure_device_runtime_columns(engine)
            
            async with auth_engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("✓ Auth database connection verified")
            await ensure_auth_zalo_runtime_columns(auth_engine)
        except Exception as e:
            logger.critical(f"Database connection check failed: {e}", exc_info=True)
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
        mqtt_handler.set_status_broadcast_handler(enqueue_ui_broadcast)
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
                                    models.Device.status.in_([
                                        'online', 'sleep', 'streaming', 'starting',
                                        'initializing', 'configuring', 'rebooting',
                                        'rebooting_for_reset'
                                    ]),
                                    models.Device.timestamp < timeout_threshold
                                )
                            )
                            timed_out_devices = result.scalars().all()
                            
                            if timed_out_devices:
                                accepted_timeouts = []
                                timeout_payloads = {}
                                for device in timed_out_devices:
                                    if await status_reconciler.is_fresh(device.serial, HEARTBEAT_TIMEOUT, now=now):
                                        continue
                                    decision = await status_reconciler.accept_offline(
                                        device.serial,
                                        source="heartbeat",
                                        received_at=now,
                                    )
                                    if not decision.accepted:
                                        continue
                                    device.status = 'offline'
                                    device.timestamp = now
                                    device.bps = 0
                                    device.ntrip_connected = False
                                    accepted_timeouts.append(device)
                                    timeout_payloads[device.serial] = decision.payload
                                
                                if accepted_timeouts:
                                    await db.commit()
                                    logger.info(f"Set {len(accepted_timeouts)} devices to offline")
                                
                                # Broadcast updates
                                for device in accepted_timeouts:
                                    broadcast_data = schemas.Device.from_orm(device).model_dump()
                                    broadcast_data.update(timeout_payloads.get(device.serial) or {})
                                    await enqueue_ui_broadcast({
                                        "type": "status_update",
                                        "data": broadcast_data
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
        # Tạo và track tất cả tasks
        tasks.append(asyncio.create_task(check_device_heartbeats_with_retry()))
        tasks.append(asyncio.create_task(global_rate_limiter_cleanup_task()))
        tasks.append(asyncio.create_task(security_rate_limiter_cleanup_task()))
        tasks.append(asyncio.create_task(periodic_buffer_flush()))
        tasks.append(asyncio.create_task(rtcm_session_reaper_task()))
        tasks.append(asyncio.create_task(rtcm_ntrip_clienttable_keepalive_task()))
        tasks.append(asyncio.create_task(ui_broadcast_worker_task()))
        tasks.append(asyncio.create_task(ui_status_broadcast_worker_task()))
        tasks.append(asyncio.create_task(event_loop_watchdog_task()))
        tasks.append(asyncio.create_task(memory_guard_task()))
        for _ in range(max(1, PI_INGEST_WORKER_COUNT)):
            tasks.append(asyncio.create_task(pi_status_ingest_worker_task()))
            
        # Start global caster monitor (synchronizes multiple caster servers into local cache)
        tasks.append(asyncio.create_task(caster_monitor_task()))

        logger.info("=" * 60)
        logger.info("🎉 Application startup complete!")
        logger.info("=" * 60)
        
        await redis_client.connect()

        # --- ZALO BOT INTEGRATION ---
        if settings.ENABLE_ZALO_BOT:
            from .zalo_bot_service import zalo_bot_service
            
            async def _zalo_get_devices():
                from .database import device_session_scope, auth_session_scope
                from . import crud
                async with device_session_scope() as devices_db:
                    async with auth_session_scope() as auth_db:
                        user = await crud.get_user_by_username(auth_db, settings.ZALO_BOT_ADMIN_USERNAME)
                        if not user:
                            return []
                        devices = await _ops_visible_devices(devices_db, auth_db, user)
                        return [{
                            "serial": d.serial,
                            "name": d.name,
                            "status": getattr(d, "status", "unknown"),
                            "latest_throughput_bps": getattr(d, "latest_throughput_bps", 0),
                            "latitude": getattr(d, "latitude", 0),
                            "longitude": getattr(d, "longitude", 0),
                            "mountpoint": getattr(d, "mountpoint", "")
                        } for d in devices]
                        
            async def _zalo_get_alerts():
                from .database import device_session_scope, auth_session_scope
                from . import crud
                async with auth_session_scope() as auth_db:
                    async with device_session_scope() as devices_db:
                        user = await crud.get_user_by_username(auth_db, settings.ZALO_BOT_ADMIN_USERNAME)
                        if not user:
                            return []
                        alerts = await _build_ops_alerts(auth_db, devices_db, user)
                        return alerts # it's already a list of dicts

            def _zalo_parse_scalar(value: Any) -> Any:
                text_value = str(value).strip()
                lowered = text_value.lower()
                if lowered in {"true", "yes", "on", "1"}:
                    return True
                if lowered in {"false", "no", "off", "0"}:
                    return False
                if lowered in {"none", "null"}:
                    return None
                if re.fullmatch(r"-?\d+", text_value):
                    return int(text_value)
                if re.fullmatch(r"-?(?:\d+\.\d*|\d*\.\d+)", text_value):
                    return float(text_value)
                return text_value

            async def _zalo_find_device(
                devices_db: AsyncSession,
                identifier: str,
                devices_list: Optional[list[models.Device]] = None,
            ):
                wanted = str(identifier or "").strip().lower()
                if not wanted:
                    raise ValueError("Bạn chưa nhập tên hoặc serial trạm. Ví dụ: /station HCM-01")
                if devices_list is None:
                    result = await devices_db.execute(select(models.Device))
                    devices_list = result.scalars().all()
                exact = [
                    device for device in devices_list
                    if wanted in {
                        str(device.name or "").strip().lower(),
                        str(device.serial or "").strip().lower(),
                    }
                ]
                if len(exact) == 1:
                    return exact[0]
                partial = [
                    device for device in devices_list
                    if wanted in str(device.name or "").lower()
                    or wanted in str(device.serial or "").lower()
                ]
                if not partial:
                    label_lookup = {
                        str(device.name or device.serial or "").strip().lower():
                        str(device.name or device.serial or "").strip()
                        for device in devices_list
                        if device.name or device.serial
                    }
                    suggestion_keys = difflib.get_close_matches(
                        wanted, list(label_lookup), n=5, cutoff=0.35
                    )
                    suggestions = [label_lookup[key] for key in suggestion_keys]
                    if suggestions:
                        raise ValueError(
                            f"Không tìm thấy trạm '{identifier}'. Có phải bạn muốn: "
                            + ", ".join(suggestions)
                            + "?"
                        )
                    raise ValueError(
                        f"Không tìm thấy trạm '{identifier}'. Gõ /stations để xem danh sách trạm."
                    )
                if len(partial) > 1:
                    choices = ", ".join(str(device.name or "Chưa đặt tên") for device in partial[:8])
                    raise ValueError(f"Có nhiều trạm khớp '{identifier}': {choices}")
                return partial[0]

            async def _zalo_find_rover(
                auth_db: AsyncSession,
                devices_db: AsyncSession,
                identifier: str,
                allow_partial: bool = False,
                rover_items: Optional[list[models.RoverAccount]] = None,
                device_items: Optional[list[models.Device]] = None,
            ):
                wanted = str(identifier or "").strip().lower()
                if not wanted:
                    raise ValueError("Bạn chưa nhập username rover. Ví dụ: /rover rover01")
                if rover_items is None:
                    rover_rs = await auth_db.execute(select(models.RoverAccount))
                    rover_items = rover_rs.scalars().all()
                exact = [item for item in rover_items if str(item.username or "").strip().lower() == wanted]
                if len(exact) == 1:
                    return exact[0]

                partial = [item for item in rover_items if wanted in str(item.username or "").lower()]
                if allow_partial and len(partial) == 1:
                    return partial[0]
                if len(partial) > 1:
                    choices = ", ".join(str(item.username) for item in partial[:8])
                    raise ValueError(f"Có nhiều rover khớp '{identifier}': {choices}")

                # A common human mistake is entering a station name while the
                # bot is waiting for a rover username. Detect that explicitly.
                if device_items is None:
                    device_rs = await devices_db.execute(select(models.Device))
                    device_items = device_rs.scalars().all()
                matching_stations = [
                    item for item in device_items
                    if wanted in {
                        str(item.name or "").strip().lower(),
                        str(item.serial or "").strip().lower(),
                    }
                    or wanted in str(item.name or "").lower()
                    or wanted in str(item.serial or "").lower()
                ]
                if matching_stations:
                    station = matching_stations[0]
                    station_name = str(station.name or station.serial)
                    raise ValueError(
                        f"'{identifier}' là TRẠM, không phải rover. "
                        f"Để xem trạm, dùng /station {station_name}"
                    )

                username_lookup = {
                    str(item.username or "").strip().lower(): str(item.username or "").strip()
                    for item in rover_items if item.username
                }
                suggestion_keys = difflib.get_close_matches(wanted, list(username_lookup), n=5, cutoff=0.35)
                suggestions = [username_lookup[key] for key in suggestion_keys]
                if suggestions:
                    raise ValueError(
                        f"Không tìm thấy rover '{identifier}'. Có phải bạn muốn: "
                        + ", ".join(suggestions)
                        + "?"
                    )
                raise ValueError(
                    f"Không tìm thấy rover '{identifier}'. Gõ /rovers để xem danh sách rover."
                )

            def _zalo_safe_config(config: Any) -> dict:
                if not isinstance(config, dict):
                    return {}
                return {
                    str(key): ("<redacted>" if "password" in str(key).lower() else value)
                    for key, value in config.items()
                }

            def _zalo_format_speed(value: Any) -> str:
                try:
                    speed = max(0.0, float(value or 0))
                except (TypeError, ValueError):
                    speed = 0.0
                if speed < 1024:
                    return f"{speed:.0f} B/s"
                if speed < 1024 * 1024:
                    number = speed / 1024
                    return f"{number:.1f} KB/s" if number < 10 else f"{number:.0f} KB/s"
                return f"{speed / 1024 / 1024:.2f} MB/s"

            def _zalo_yes_no(value: Any, yes: str = "Có", no: str = "Không") -> str:
                return yes if bool(value) else no

            def _zalo_stream_mode(value: Any) -> str:
                return "Ngủ khi không có rover" if bool(value) else "Luôn truyền dữ liệu"

            def _zalo_device_traffic(device: models.Device, snapshot: dict) -> dict:
                stats = snapshot.get("ntrip_stats") if isinstance(snapshot.get("ntrip_stats"), dict) else {}
                status = {}
                if isinstance(device.ntrip_status, dict):
                    status.update(device.ntrip_status)
                if isinstance(snapshot.get("ntrip_status"), dict):
                    status.update(snapshot["ntrip_status"])

                def server_bps(server_id: int) -> int:
                    detail = status.get(f"server{server_id}")
                    detail_bps = detail.get("bps") if isinstance(detail, dict) else 0
                    try:
                        return int(detail_bps or stats.get(f"server{server_id}_bps") or 0)
                    except (TypeError, ValueError):
                        return 0

                server1 = server_bps(1)
                server2 = server_bps(2)
                try:
                    total = int(server1 + server2 or snapshot.get("bps") or device.bps or 0)
                except (TypeError, ValueError):
                    total = server1 + server2
                try:
                    rtcm_input = int(
                        stats.get("rtcm_input_bps") or stats.get("rtcm_in_bps")
                        or snapshot.get("rtcm_input_bps") or 0
                    )
                except (TypeError, ValueError):
                    rtcm_input = 0
                return {"server1": server1, "server2": server2, "total": total, "rtcm_input": rtcm_input}

            def _zalo_traffic_lines(device: models.Device, snapshot: dict) -> List[str]:
                traffic = _zalo_device_traffic(device, snapshot)
                lines = [
                    "📈 DÒNG DỮ LIỆU HIỆN TẠI",
                    f"• Tổng dữ liệu gửi đi: {_zalo_format_speed(traffic['total'])}",
                    f"• Server 1: {_zalo_format_speed(traffic['server1'])}",
                    f"• Server 2: {_zalo_format_speed(traffic['server2'])}",
                ]
                if traffic["rtcm_input"]:
                    lines.append(f"• RTCM nhận vào: {_zalo_format_speed(traffic['rtcm_input'])}")
                return lines

            def _zalo_config_summary(device: models.Device, snapshot: dict) -> str:
                base = device.base_config if isinstance(device.base_config, dict) else {}
                service = device.service_config if isinstance(device.service_config, dict) else {}
                coords = base.get("coords") if isinstance(base.get("coords"), dict) else {}
                gnss = base.get("gnss_options") if isinstance(base.get("gnss_options"), dict) else {}
                constellations = gnss.get("constellations") if isinstance(gnss.get("constellations"), dict) else {}
                constellation_labels = {
                    "gps": "GPS", "glo": "GLONASS", "gal": "Galileo",
                    "bds": "BeiDou", "qzss": "QZSS", "sbas": "SBAS",
                }
                enabled_constellations = [
                    label for key, label in constellation_labels.items() if constellations.get(key)
                ]
                method_labels = {
                    "FIXED_LLA": "Tọa độ cố định Lat/Lon/Alt",
                    "SURVEY_IN": "Tự khảo sát vị trí",
                }
                output_labels = {
                    "rtcm_only": "Chỉ xuất RTCM", "nmea_only": "Chỉ xuất NMEA",
                    "rtcm_nmea": "RTCM và NMEA",
                }
                traffic = _zalo_device_traffic(device, snapshot)
                lines = [
                    f"⚙️ CẤU HÌNH TRẠM {device.name or device.serial}",
                    "━━━━━━━━━━━━━━━━━━━━━━",
                    "",
                    "📍 VỊ TRÍ BASE",
                    f"• Chế độ: {method_labels.get(str(base.get('base_setup_method') or ''), base.get('base_setup_method') or 'Chưa cấu hình')}",
                    f"• Vĩ độ: {coords.get('lat', 'Chưa có')}",
                    f"• Kinh độ: {coords.get('lon', 'Chưa có')}",
                    f"• Độ cao ellipsoid: {str(coords.get('alt')) + ' m' if coords.get('alt') not in (None, '') else 'Chưa có'}",
                    f"• Độ chính xác yêu cầu: {str(base.get('accuracy')) + ' m' if base.get('accuracy') not in (None, '') else 'Chưa đặt'}",
                    "",
                    "🛰️ GNSS",
                    f"• Cổng nhận dữ liệu: {', '.join(str(item) for item in gnss.get('ports', []) if item) or 'Chưa cấu hình'}",
                    f"• Dữ liệu đầu ra: {output_labels.get(str(gnss.get('output_mode') or ''), gnss.get('output_mode') or 'Chưa cấu hình')}",
                    f"• Hệ vệ tinh bật: {', '.join(enabled_constellations) or 'Chưa chọn'}",
                ]

                for server_id in (1, 2):
                    enabled = bool(service.get(f"server{server_id}_enabled"))
                    host = service.get(f"serverhost{server_id}") or "Chưa cấu hình"
                    port = service.get(f"port{server_id}") or "--"
                    mountpoint = service.get(f"mountpoint{server_id}") or "Chưa cấu hình"
                    username = service.get(f"username{server_id}") or "Không dùng"
                    ntrip_version = service.get(f"ntrip_version{server_id}") or "--"
                    on_demand = service.get(
                        f"server{server_id}_stream_on_demand", service.get("stream_on_demand", False)
                    )
                    active = bool(service.get(f"server{server_id}_stream_active")) or traffic[f"server{server_id}"] > 0
                    lines.extend((
                        "",
                        f"🔗 SERVER {server_id}",
                        f"• Kích hoạt: {_zalo_yes_no(enabled, 'Đang bật', 'Đang tắt')}",
                        f"• Máy chủ: {host}:{port}",
                        f"• Mountpoint: {mountpoint}",
                        f"• Tài khoản: {username}",
                        f"• Mật khẩu: {'Đã thiết lập' if service.get(f'password{server_id}') else 'Chưa thiết lập'}",
                        f"• Phiên bản NTRIP: {ntrip_version}",
                        f"• Chế độ truyền: {_zalo_stream_mode(on_demand)}",
                        f"• Trạng thái dòng: {_zalo_yes_no(active, 'Đang truyền', 'Chưa truyền')}",
                        f"• Tốc độ hiện tại: {_zalo_format_speed(traffic[f'server{server_id}'])}",
                    ))

                lines.extend((
                    "",
                    "📥 NGUỒN RTCM ĐẦU VÀO",
                    f"• Kích hoạt: {_zalo_yes_no(service.get('rtcm_enabled'), 'Đang bật', 'Đang tắt')}",
                    f"• Máy chủ: {service.get('rtcmserver1') or 'Chưa cấu hình'}:{service.get('rtcmport1') or '--'}",
                    f"• Mountpoint: {service.get('rtcmmountpoint1') or 'Chưa cấu hình'}",
                    f"• Tài khoản: {service.get('rtcmusername1') or 'Không dùng'}",
                    f"• Mật khẩu: {'Đã thiết lập' if service.get('rtcmpassword1') else 'Chưa thiết lập'}",
                    f"• Phiên bản NTRIP: {service.get('rtcmntripversion1') or '--'}",
                    f"• Tốc độ nhận: {_zalo_format_speed(traffic['rtcm_input'])}",
                    "",
                    "🔄 KẾT NỐI",
                    f"• Thử kết nối lại sau: {service.get('reconnectioninterval', 10)} giây",
                    f"• Tổng dữ liệu gửi đi: {_zalo_format_speed(traffic['total'])}",
                ))
                return "\n".join(lines)

            def _zalo_live_telemetry(serial: str) -> dict:
                serial_text = str(serial)

                def cache_value(cache: dict) -> dict:
                    direct = cache.get(serial_text)
                    if isinstance(direct, dict):
                        return direct
                    wanted = serial_text.casefold()
                    for key, value in cache.items():
                        if str(key).casefold() == wanted and isinstance(value, dict):
                            return value
                    return {}

                snapshot: dict = {}
                merged_nmea: dict = {}
                for source in (
                    cache_value(mqtt_handler.device_telemetry_cache),
                    cache_value(latest_device_telemetry_cache),
                ):
                    source_nmea = source.get("nmea_data")
                    if isinstance(source_nmea, dict):
                        for key, value in source_nmea.items():
                            if key == "satellites" and not value and merged_nmea.get("satellites"):
                                continue
                            merged_nmea[key] = value
                    snapshot.update({key: value for key, value in source.items() if key != "nmea_data"})
                if merged_nmea:
                    snapshot["nmea_data"] = merged_nmea
                return snapshot

            def _zalo_search_key(value: Any) -> str:
                raw = str(value or "").lower().replace("đ", "d")
                normalized = unicodedata.normalize("NFD", raw)
                ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
                return "".join(ch for ch in ascii_text if ch.isalnum())

            def _zalo_search_needles(query: Any) -> Set[str]:
                raw = str(query or "").strip().lower().replace("đ", "d")
                normalized = unicodedata.normalize("NFD", raw)
                ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
                stop_words = {
                    "xem", "tim", "kiem", "tra", "cuu", "giup", "toi", "cho", "minh",
                    "thong", "tin", "chi", "tiet", "tram", "rover", "tai", "khoan",
                    # Bot command names — must not pollute search needles
                    "station", "stations", "stationdelete", "stationlock",
                    "stationunlock", "stationreset", "config", "configset",
                    "coord", "rovers", "rovercreate", "roverupdate",
                    "roverdelete", "roverrenew", "roverblock", "roverunblock",
                    "status", "errors", "menu", "help", "start", "confirm",
                    "cancel", "logout", "unlink", "link", "register",
                    "notifications", "overview",
                    # Vietnamese action words that are not station/rover identifiers
                    "xoa", "khoa", "mo", "reset", "sua", "dat", "tao",
                    "them", "chan", "gia", "han", "huy", "xac", "nhan",
                    "danh", "sach", "loi", "canh", "bao", "delete", "lock",
                    "unlock", "block", "unblock", "create", "update", "renew",
                    "info", "list", "view", "check",
                }
                tokens = [
                    token for token in re.findall(r"[a-z0-9_.-]+", ascii_text)
                    if token not in stop_words
                ]
                needles = {_zalo_search_key(raw)}
                needles.update(_zalo_search_key(token) for token in tokens)
                return {needle for needle in needles if len(needle) >= 2}

            def _zalo_satellite_summary(snapshot: dict) -> List[str]:
                nmea = snapshot.get("nmea_data") if isinstance(snapshot.get("nmea_data"), dict) else {}
                rtcm = snapshot.get("rtcm_signal_data") if isinstance(snapshot.get("rtcm_signal_data"), dict) else {}
                rtcm_satellites = rtcm.get("satellites") if isinstance(rtcm.get("satellites"), (list, dict)) else []
                now_ms = int(time.time() * 1000)
                if isinstance(rtcm_satellites, dict):
                    rtcm_satellites = list(rtcm_satellites.values())
                fresh_rtcm_satellites = []
                for item in rtcm_satellites:
                    if not isinstance(item, dict):
                        continue
                    try:
                        last_seen = int(item.get("lastSeen") or rtcm.get("updated_at_ms") or 0)
                    except (TypeError, ValueError):
                        last_seen = 0
                    if not last_seen or now_ms - last_seen <= 15_000:
                        fresh_rtcm_satellites.append(item)
                satellites = fresh_rtcm_satellites or nmea.get("satellites") or snapshot.get("satellites") or []
                if isinstance(satellites, dict):
                    satellites = list(satellites.values())
                if not isinstance(satellites, list):
                    satellites = []

                system_names = {
                    "us": "GPS (Mỹ)", "gps": "GPS (Mỹ)", "gp": "GPS (Mỹ)",
                    "ru": "GLONASS (Nga)", "glonass": "GLONASS (Nga)", "gl": "GLONASS (Nga)",
                    "eu": "Galileo (Châu Âu)", "galileo": "Galileo (Châu Âu)", "ga": "Galileo (Châu Âu)",
                    "cn": "BeiDou (Trung Quốc)", "beidou": "BeiDou (Trung Quốc)", "gb": "BeiDou (Trung Quốc)", "bd": "BeiDou (Trung Quốc)",
                    "jp": "QZSS (Nhật Bản)", "qzss": "QZSS (Nhật Bản)", "gq": "QZSS (Nhật Bản)", "qz": "QZSS (Nhật Bản)",
                    "un": "GNSS chưa phân loại", "gn": "GNSS chưa phân loại",
                }
                grouped: Dict[str, Set[str]] = {}
                active_satellites = []
                for index, sat in enumerate(satellites):
                    if not isinstance(sat, dict):
                        continue
                    try:
                        snr = float(sat.get("snr") or 0)
                    except (TypeError, ValueError):
                        snr = 0
                    if "snr" in sat and snr <= 0:
                        continue
                    active_satellites.append(sat)
                    raw_system = str(
                        sat.get("system") or sat.get("navSystem") or sat.get("talker") or "un"
                    ).strip().lower()
                    label = system_names.get(raw_system, raw_system.upper() or system_names["un"])
                    sat_key = str(sat.get("id") or sat.get("prn") or index)
                    grouped.setdefault(label, set()).add(sat_key)

                gga = nmea.get("gga") if isinstance(nmea.get("gga"), dict) else {}
                used_count = gga.get("satellites")
                tracking_count = sum(1 for sat in active_satellites if sat.get("isTracking"))
                if fresh_rtcm_satellites or used_count in (None, ""):
                    used_count = tracking_count
                total_count = sum(len(items) for items in grouped.values())
                if not total_count:
                    rtcm_is_fresh = bool(fresh_rtcm_satellites) or not rtcm
                    total_count = int((snapshot.get("satellite_count") if rtcm_is_fresh else 0) or used_count or 0)
                if not total_count:
                    return ["🛰️ Vệ tinh: chưa nhận được dữ liệu trực tiếp."]

                lines = [
                    "🛰️ VỆ TINH HIỆN TẠI",
                    f"• Đang thu tín hiệu: {total_count} vệ tinh",
                    f"• Đang dùng để định vị: {int(used_count or 0)} vệ tinh",
                ]
                for label in (
                    "GPS (Mỹ)", "GLONASS (Nga)", "Galileo (Châu Âu)",
                    "BeiDou (Trung Quốc)", "QZSS (Nhật Bản)",
                ):
                    lines.append(f"• {label}: {len(grouped.get(label, set()))}")
                if grouped.get("GNSS chưa phân loại"):
                    lines.append(f"• GNSS chưa phân loại: {len(grouped['GNSS chưa phân loại'])}")
                for label in sorted(set(grouped) - {
                    "GPS (Mỹ)", "GLONASS (Nga)", "Galileo (Châu Âu)",
                    "BeiDou (Trung Quốc)", "QZSS (Nhật Bản)", "GNSS chưa phân loại",
                }):
                    lines.append(f"• {label}: {len(grouped[label])}")
                return lines

            zalo_action_permissions = {
                "overview": auth.Permission.VIEW_DEVICES,
                "smart_search": auth.Permission.VIEW_DEVICES,
                "stations": auth.Permission.VIEW_DEVICES,
                "station": auth.Permission.VIEW_DEVICES,
                "alerts": auth.Permission.VIEW_CONFIG,
                "config": auth.Permission.VIEW_CONFIG,
                "config_edit_context": auth.Permission.EDIT_SERVICE_CONFIG,
                "coord_set": auth.Permission.EDIT_COORDINATES,
                "config_set": auth.Permission.EDIT_SERVICE_CONFIG,
                "station_lock": auth.Permission.MANAGE_USERS,
                "station_unlock": auth.Permission.MANAGE_USERS,
                "station_reset": auth.Permission.DELETE_DEVICE,
                "station_delete": auth.Permission.DELETE_DEVICE,
                "rovers": auth.Permission.MANAGE_CASTER,
                "rover": auth.Permission.MANAGE_CASTER,
                "rover_edit_context": auth.Permission.MANAGE_CASTER,
                "rover_create": auth.Permission.MANAGE_CASTER,
                "rover_update": auth.Permission.MANAGE_CASTER,
                "rover_delete": auth.Permission.MANAGE_CASTER,
                "rover_renew": auth.Permission.MANAGE_CASTER,
                "rover_block": auth.Permission.MANAGE_CASTER,
                "rover_unblock": auth.Permission.MANAGE_CASTER,
            }

            zalo_write_actions = {
                "config_edit_context", "coord_set", "config_set", "station_lock", "station_unlock",
                "station_reset", "station_delete", "rover_edit_context", "rover_create",
                "rover_update", "rover_delete", "rover_renew", "rover_block", "rover_unblock",
            }

            async def _zalo_admin_action(action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
                from .routers import devices as device_routes

                required_permission = zalo_action_permissions.get(action)
                granted_permissions = {
                    str(value) for value in (payload.get("permissions") or [])
                }
                if required_permission and required_permission not in granted_permissions:
                    raise PermissionError("Tài khoản Zalo hiện tại không còn quyền thực hiện chức năng này")
                if action in zalo_write_actions and not settings.ZALO_ENABLE_WRITE_ACTIONS:
                    raise PermissionError("Các thao tác ghi từ Zalo đang bị tắt bởi cấu hình hệ thống")

                async with auth_session_scope() as auth_db:
                    service_admin = await crud.get_user_by_username(auth_db, settings.ZALO_BOT_ADMIN_USERNAME)
                    if not service_admin or service_admin.role != auth.Role.ADMIN:
                        raise RuntimeError("ZALO_BOT_ADMIN_USERNAME phải trỏ tới tài khoản web có role admin")

                    async with device_session_scope() as devices_db:
                        try:
                            actor_user_id = payload.get("actor_user_id")
                            actor_user = await auth_db.get(models.User, int(actor_user_id)) if actor_user_id else None
                            if actor_user_id and (not actor_user or not actor_user.is_active):
                                raise PermissionError("Tài khoản dashboard liên kết đã bị vô hiệu hóa")
                            execution_user = actor_user or service_admin
                            fresh_permissions = set(auth.get_user_permissions(execution_user))
                            if required_permission and required_permission not in fresh_permissions:
                                raise PermissionError("Tài khoản dashboard hiện tại không còn quyền thực hiện chức năng này")

                            visible_devices = await _ops_visible_devices(devices_db, auth_db, execution_user)
                            visible_rovers = await _ops_visible_rovers(auth_db, devices_db, execution_user)
                            can_view_rovers = auth.Permission.MANAGE_CASTER in granted_permissions
                            if not actor_user:
                                scope_mode = str(payload.get("station_scope_mode") or "all")
                                allowed_serials = {
                                    str(value) for value in (payload.get("allowed_bases") or []) if str(value).strip()
                                }
                                if scope_mode == "none":
                                    visible_devices = []
                                elif scope_mode == "selected":
                                    visible_devices = [
                                        item for item in visible_devices if str(item.serial) in allowed_serials
                                    ]
                                if scope_mode in {"none", "selected"}:
                                    scoped_serials = {str(item.serial) for item in visible_devices}
                                    scoped_mountpoints = set()
                                    for item in visible_devices:
                                        config = item.service_config if isinstance(item.service_config, dict) else {}
                                        scoped_mountpoints.update(
                                            str(config.get(key)).strip() for key in ("mountpoint1", "mountpoint2")
                                            if str(config.get(key) or "").strip()
                                        )
                                    visible_rovers = [
                                        rover for rover in visible_rovers
                                        if str(rover.base_serial or "") in scoped_serials
                                        or bool({
                                            part.strip() for part in re.split(r"[;,]+", str(rover.mountpoints or "")) if part.strip()
                                        }.intersection(scoped_mountpoints))
                                    ]
                            if not can_view_rovers:
                                visible_rovers = []

                            if action == "overview":
                                online = sum(
                                    1 for item in visible_devices
                                    if str(item.status or "").lower() not in {"offline", "unknown"}
                                )
                                offline = len(visible_devices) - online
                                total_bps = sum(
                                    _zalo_device_traffic(item, _zalo_live_telemetry(item.serial))["total"]
                                    for item in visible_devices
                                )
                                overview_lines = [
                                    "📊 TỔNG QUAN HỆ THỐNG CORS",
                                    "━━━━━━━━━━━━━━━━━━━━━━",
                                    f"🟢 Online: {online}/{len(visible_devices)}",
                                    f"🔴 Offline: {offline}/{len(visible_devices)}",
                                ]
                                if can_view_rovers:
                                    overview_lines.append(f"🛰️ Rover được xem: {len(visible_rovers)}")
                                overview_lines.append(
                                    f"📈 Tổng dữ liệu gửi đi: {_zalo_format_speed(total_bps)}"
                                )
                                return {"message": "\n".join(overview_lines)}

                            if action == "alerts":
                                alert_items = await _build_ops_alerts(
                                    auth_db, devices_db, execution_user,
                                    devices=visible_devices, rovers=visible_rovers,
                                )
                                if not alert_items:
                                    return {"message": "✅ Không có cảnh báo trong phạm vi bạn được xem."}
                                lines = [f"⚠️ CẢNH BÁO ({len(alert_items)})"]
                                for item in alert_items[:30]:
                                    subject = item.get("rover_username") or item.get("device_name") or item.get("device_serial") or "Hệ thống"
                                    lines.append(f"• {subject}: {item.get('title') or item.get('message') or 'Cảnh báo'}")
                                return {"message": "\n".join(lines)}

                            if action == "smart_search":
                                query = str(payload.get("query") or "").strip()
                                needles = _zalo_search_needles(query)
                                search_devices = visible_devices
                                search_rovers = visible_rovers

                                # --- Scoring-based smart search ---
                                device_scores = []
                                for item in search_devices:
                                    score = 0
                                    name_key = _zalo_search_key(item.name)
                                    serial_key = _zalo_search_key(item.serial)
                                    for needle in needles:
                                        # Exact match on name or serial
                                        if needle == name_key:
                                            score += 10
                                        if needle == serial_key:
                                            score += 10
                                        # IMEI / long serial bonus
                                        if len(needle) >= 10 and needle.isdigit() and needle == serial_key:
                                            score += 20
                                        # Partial (substring) match
                                        if len(needle) >= 3:
                                            if needle != name_key and name_key and needle in name_key:
                                                score += 3
                                            if needle != serial_key and serial_key and needle in serial_key:
                                                score += 3
                                    if score > 0:
                                        device_scores.append((item, score))

                                rover_scores = []
                                for item in search_rovers:
                                    score = 0
                                    username_key = _zalo_search_key(item.username)
                                    for needle in needles:
                                        if needle == username_key:
                                            score += 10
                                        if len(needle) >= 10 and needle.isdigit() and needle == username_key:
                                            score += 20
                                        if len(needle) >= 3 and needle != username_key and username_key and needle in username_key:
                                            score += 3
                                    if score > 0:
                                        rover_scores.append((item, score))

                                device_scores.sort(key=lambda x: -x[1])
                                rover_scores.sort(key=lambda x: -x[1])

                                top_device = device_scores[0] if device_scores else None
                                top_rover = rover_scores[0] if rover_scores else None
                                second_device_score = device_scores[1][1] if len(device_scores) > 1 else -1
                                second_rover_score = rover_scores[1][1] if len(rover_scores) > 1 else -1

                                best_device_score = top_device[1] if top_device else -1
                                best_rover_score = top_rover[1] if top_rover else -1

                                winner = None  # ("station"|"rover", item)
                                if best_device_score > 0 or best_rover_score > 0:
                                    if best_device_score > best_rover_score:
                                        # Device is stronger — check if it's clearly the best device
                                        if best_device_score > second_device_score:
                                            winner = ("station", top_device[0])
                                    elif best_rover_score > best_device_score:
                                        if best_rover_score > second_rover_score:
                                            winner = ("rover", top_rover[0])
                                    else:
                                        # Tie between best device and best rover
                                        if best_device_score > second_device_score and best_rover_score <= second_rover_score:
                                            winner = ("station", top_device[0])
                                        elif best_rover_score > second_rover_score and best_device_score <= second_device_score:
                                            winner = ("rover", top_rover[0])

                                if winner:
                                    if winner[0] == "station":
                                        action = "station"
                                        payload["station"] = str(winner[1].serial)
                                    else:
                                        action = "rover"
                                        payload["username"] = str(winner[1].username)
                                elif device_scores or rover_scores:
                                    choices = [
                                        f"trạm {item.name or item.serial}" for item, _ in device_scores[:5]
                                    ] + [f"rover {item.username}" for item, _ in rover_scores[:5]]
                                    return {
                                        "message": "Mình tìm thấy nhiều kết quả: " + ", ".join(choices)
                                        + ". Hãy gửi rõ tên cần xem."
                                    }
                                else:
                                    # No scored matches — try fuzzy (difflib)
                                    label_lookup = {}
                                    for item in search_devices:
                                        key = _zalo_search_key(item.name or item.serial)
                                        if key:
                                            label_lookup[key] = f"trạm {item.name or item.serial}"
                                    for item in search_rovers:
                                        key = _zalo_search_key(item.username)
                                        if key:
                                            label_lookup[key] = f"rover {item.username}"
                                    query_key = _zalo_search_key(query)
                                    suggestion_keys = difflib.get_close_matches(
                                        query_key, list(label_lookup), n=5, cutoff=0.35
                                    )
                                    if suggestion_keys:
                                        suggestions = [label_lookup[k] for k in suggestion_keys]
                                        return {
                                            "message": f"Không tìm thấy kết quả cho '{query}'. Có phải bạn muốn: "
                                            + ", ".join(suggestions) + "?"
                                        }
                                    subject_label = "trạm hoặc rover" if can_view_rovers else "trạm"
                                    command_hint = "/stations hoặc /rovers" if can_view_rovers else "/stations"
                                    return {
                                        "message": f"Không tìm thấy {subject_label} phù hợp với '{query}'. "
                                        f"Gõ {command_hint} để xem danh sách."
                                    }

                            if action == "stations":
                                query = str(payload.get("query") or "").strip().lower()
                                station_items = list(visible_devices)
                                if query:
                                    station_items = [
                                        item for item in station_items
                                        if query in str(item.name or "").lower()
                                    ]
                                station_items = sorted(station_items, key=lambda item: str(item.name or item.serial))
                                if not station_items:
                                    return {"message": "Không tìm thấy trạm."}
                                total_matches = len(station_items)
                                lines = [f"📡 TRẠM ({total_matches})"]
                                for item in station_items[:30]:
                                    icon = "🔴" if str(item.status).lower() in {"offline", "unknown"} else "🟢"
                                    speed = _zalo_device_traffic(
                                        item, _zalo_live_telemetry(item.serial)
                                    )["total"]
                                    lines.append(
                                        f"{icon} {item.name or 'Chưa đặt tên'} | {item.status} | {_zalo_format_speed(speed)}"
                                    )
                                if total_matches > 30:
                                    lines.append("… Gõ /stations <từ khóa tên> để lọc danh sách.")
                                return {"message": "\n".join(lines)}

                            if action in {
                                "station", "config", "config_edit_context", "coord_set", "config_set", "rover_create",
                                "station_lock", "station_unlock", "station_reset", "station_delete",
                            }:
                                device = await _zalo_find_device(
                                    devices_db, payload.get("station"), devices_list=visible_devices
                                )
                            else:
                                device = None

                            if action == "config_edit_context":
                                return {
                                    "station": str(device.name or device.serial),
                                    "label": str(device.name or device.serial),
                                    "current": _zalo_safe_config(device.service_config),
                                }

                            if action == "station":
                                base_cfg = _zalo_safe_config(device.base_config)
                                service_cfg = _zalo_safe_config(device.service_config)
                                coords = base_cfg.get("coords") or getattr(device, "base_coords", None) or {}
                                telemetry = _zalo_live_telemetry(device.serial)
                                nmea = telemetry.get("nmea_data") if isinstance(telemetry.get("nmea_data"), dict) else {}
                                gga = nmea.get("gga") if isinstance(nmea.get("gga"), dict) else {}
                                lat = (coords.get("lat") if "lat" in coords else coords.get("latitude")) if isinstance(coords, dict) else None
                                lon = (coords.get("lon") if "lon" in coords else coords.get("longitude")) if isinstance(coords, dict) else None
                                alt = (coords.get("alt") if "alt" in coords else coords.get("altitude")) if isinstance(coords, dict) else None
                                lat = lat if lat is not None else gga.get("latitude")
                                lon = lon if lon is not None else gga.get("longitude")
                                alt = alt if alt is not None else gga.get("altitude")
                                status_text = {
                                    "online": "Trực tuyến", "offline": "Ngoại tuyến", "sleep": "Đang nghỉ",
                                    "unknown": "Chưa xác định", "awaiting_license": "Chờ cấp phép",
                                }.get(str(device.status or "").lower(), str(device.status or "Chưa xác định"))
                                lines = [
                                    f"📡 TRẠM {device.name or '-'}",
                                    f"• Serial: {device.serial}",
                                    f"• Trạng thái: {status_text}",
                                    f"• Chip GNSS: {device.detected_chip_type or 'Chưa xác định'}",
                                    f"• Đồng bộ cấu hình: {'Có' if getattr(device, 'is_synced', False) else 'Không'}",
                                    "",
                                    *_zalo_traffic_lines(device, telemetry),
                                    "",
                                    "📍 TỌA ĐỘ",
                                    f"• Vĩ độ (Lat): {lat if lat is not None else 'Chưa có'}",
                                    f"• Kinh độ (Lon): {lon if lon is not None else 'Chưa có'}",
                                    f"• Độ cao ellipsoid (Alt): {str(alt) + ' m' if alt is not None else 'Chưa có'}",
                                    "",
                                    *_zalo_satellite_summary(telemetry),
                                    "",
                                    "🔗 MOUNTPOINT",
                                    f"• Server 1: {service_cfg.get('mountpoint1') or 'Chưa cấu hình'}",
                                    f"• Server 2: {service_cfg.get('mountpoint2') or 'Chưa cấu hình'}",
                                ]
                                if str(device.status or "").lower() in {"offline", "unknown"}:
                                    lines.append("ℹ️ Trạm đang ngoại tuyến; số vệ tinh có thể là dữ liệu gần nhất.")
                                return {
                                    "message": "\n".join(lines),
                                    "context_target": str(device.serial),
                                    "context_type": "station",
                                    "context_name": str(device.name or device.serial),
                                }

                            if action == "config":
                                return {
                                    "message": _zalo_config_summary(device, _zalo_live_telemetry(device.serial)),
                                    "context_target": str(device.serial),
                                    "context_type": "station",
                                    "context_name": str(device.name or device.serial),
                                }

                            if action == "coord_set":
                                sensor_type = str(device.detected_chip_type or "").strip()
                                if not sensor_type or sensor_type.upper() == "UNKNOWN":
                                    raise ValueError("Trạm chưa xác định loại chip GNSS")
                                params = dict(device.base_config or {})
                                params.update({
                                    "base_setup_method": "FIXED_LLA",
                                    "coords": {
                                        "lat": float(payload["lat"]),
                                        "lon": float(payload["lon"]),
                                        "alt": float(payload["alt"]),
                                    },
                                    "accuracy": float(payload.get("accuracy", 0.01)),
                                })
                                command = schemas.Command(
                                    command="CONFIGURE_CHIP",
                                    payload={"sensor_type": sensor_type, "mode": "BASE", "params": params},
                                )
                                result = await configure_chip_endpoint(device.serial, command, devices_db, execution_user)
                                
                                from app.mqtt import device_sync_cache, _compare_configs
                                is_synced = False
                                wait_time = 0
                                is_relay = device.serial.startswith(("RELAY_", "MONITOR_"))
                                while wait_time < 12:
                                    await asyncio.sleep(1)
                                    wait_time += 1
                                    await devices_db.refresh(device)
                                    agent_base = device_sync_cache.get(f"{device.serial}_base_config", {})
                                    db_base = device.base_config or {}
                                    base_ok, _ = _compare_configs(agent_base, db_base, "Base", is_relay=is_relay)
                                    if base_ok:
                                        is_synced = True
                                        break

                                if is_synced:
                                    return {"message": f"✅ Đã cập nhật tọa độ cho trạm {device.name or device.serial} và đồng bộ thành công!"}
                                else:
                                    return {"message": f"✅ Đã gửi lệnh cập nhật tọa độ tới {device.name or device.serial}, đang chờ trạm đồng bộ."}

                            if action == "config_set":
                                changes = dict(payload.get("changes") or {})
                                allowed_fields = set(schemas.ServiceConfig.model_fields)
                                unknown = sorted(set(changes) - allowed_fields)
                                if unknown:
                                    raise ValueError(f"Config key không hợp lệ: {', '.join(unknown)}")
                                merged = dict(_device_service_config(device))
                                merged.update({key: _zalo_parse_scalar(value) for key, value in changes.items()})
                                service_payload = schemas.ServiceConfig(**merged)
                                result = await configure_service_endpoint(device.serial, service_payload, devices_db, execution_user)
                                
                                from app.mqtt import device_sync_cache, _compare_configs
                                is_synced = False
                                wait_time = 0
                                is_relay = device.serial.startswith(("RELAY_", "MONITOR_"))
                                while wait_time < 12:
                                    await asyncio.sleep(1)
                                    wait_time += 1
                                    await devices_db.refresh(device)
                                    agent_service = device_sync_cache.get(f"{device.serial}_service_config", {})
                                    db_service = _device_service_config(device)
                                    srv_ok, _ = _compare_configs(agent_service, db_service, "Service", is_relay=is_relay)
                                    if srv_ok:
                                        is_synced = True
                                        break

                                if is_synced:
                                    return {"message": f"✅ Đã cập nhật cấu hình cho trạm {device.name or device.serial} và đồng bộ thành công!"}
                                else:
                                    return {"message": f"✅ Đã gửi lệnh cập nhật cấu hình tới {device.name or device.serial}, đang chờ trạm đồng bộ."}

                            if action == "station_lock":
                                result = await lock_device(device.serial, devices_db, execution_user)
                                return {"message": f"✅ Đã gửi lệnh KHÓA tới trạm {device.name or device.serial} thành công (Đang chờ trạm đồng bộ)."}

                            if action == "station_unlock":
                                result = await unlock_device(device.serial, devices_db, execution_user)
                                return {"message": f"✅ Đã gửi lệnh MỞ KHÓA tới trạm {device.name or device.serial} thành công (Đang chờ trạm đồng bộ)."}

                            if action == "station_reset":
                                result = await reset_pi_device(device.serial, devices_db, execution_user)
                                return {"message": f"✅ Đã gửi lệnh RESET tới trạm {device.name or device.serial} thành công."}

                            if action == "station_delete":
                                station_label = str(device.name or device.serial)
                                serial = str(device.serial)
                                requested = str(payload.get("station") or "").strip().lower()
                                exact_identifiers = {
                                    str(device.name or "").strip().lower(),
                                    serial.lower(),
                                }
                                if requested not in exact_identifiers:
                                    raise ValueError(
                                        f"Để tránh xóa nhầm, hãy nhập chính xác tên '{station_label}' "
                                        f"hoặc serial '{serial}'."
                                    )
                                await delete_device_from_list(serial, devices_db, execution_user)
                                latest_device_telemetry_cache.pop(serial, None)
                                mqtt_handler.device_telemetry_cache.pop(serial, None)
                                return {"message": f"✅ Đã xóa trạm {station_label} ({serial}) khỏi hệ thống."}

                            if action == "rovers":
                                query = str(payload.get("query") or "").strip().lower()
                                rover_items = list(visible_rovers)
                                if query:
                                    rover_items = [item for item in rover_items if query in str(item.username or "").lower()]
                                rover_items = sorted(rover_items, key=lambda item: str(item.username))
                                if not rover_items:
                                    return {"message": "Không tìm thấy rover."}
                                total_matches = len(rover_items)
                                lines = [f"🛰️ ROVER ({total_matches})"]
                                for item in rover_items[:30]:
                                    icon = "✅" if item.is_active else "⛔"
                                    lines.append(f"{icon} {item.username} | {item.mountpoints or '-'} | max={item.max_connections}")
                                if total_matches > 30:
                                    lines.append("… Gõ /rovers <từ khóa> để lọc danh sách.")
                                return {"message": "\n".join(lines)}

                            if action in {
                                "rover", "rover_edit_context", "rover_update", "rover_delete", "rover_renew",
                                "rover_block", "rover_unblock",
                            }:
                                rover = await _zalo_find_rover(
                                    auth_db, devices_db, payload.get("username"),
                                    allow_partial=action in {"rover", "rover_edit_context"},
                                    rover_items=visible_rovers, device_items=visible_devices,
                                )
                            else:
                                rover = None

                            if action == "rover_edit_context":
                                available_mountpoints: Set[str] = set()
                                for station_item in visible_devices:
                                    service_cfg = station_item.service_config if isinstance(station_item.service_config, dict) else {}
                                    for server_id in (1, 2):
                                        mountpoint = str(service_cfg.get(f"mountpoint{server_id}") or "").strip()
                                        if mountpoint:
                                            available_mountpoints.add(mountpoint)
                                try:
                                    caster_sources = await get_admin_caster_sources(execution_user)
                                    for source in caster_sources.get("all", []):
                                        mountpoint = str(source.get("mountpoint") or "").strip()
                                        if mountpoint:
                                            available_mountpoints.add(mountpoint)
                                except Exception as exc:
                                    logger.warning("Zalo editor could not load live caster sources: %s", exc)
                                remaining_days = None
                                if rover.expires_at:
                                    remaining_days = max(0, int((int(rover.expires_at) - int(time.time()) + 86399) // 86400))
                                return {
                                    "username": str(rover.username),
                                    "label": str(rover.username),
                                    "available_mountpoints": sorted(available_mountpoints),
                                    "current": {
                                        "password": bool(rover.password),
                                        "mountpoints": rover.mountpoints or "",
                                        "expiry_days": remaining_days,
                                        "max_connections": rover.max_connections,
                                        "show_position": bool(rover.show_position),
                                        "rtcm_cycle_enabled": bool(getattr(rover, "rtcm_cycle_enabled", False)),
                                        "rtcm_disconnect_interval_seconds": int(getattr(rover, "rtcm_disconnect_interval_seconds", 300) or 300),
                                        "rtcm_disconnect_duration_seconds": int(getattr(rover, "rtcm_disconnect_duration_seconds", 10) or 10),
                                        "is_shared_with_managers": bool(getattr(rover, "is_shared_with_managers", False)),
                                    },
                                }

                            if action == "rover":
                                expiry = datetime.fromtimestamp(rover.expires_at).strftime("%d/%m/%Y %H:%M") if rover.expires_at else "không giới hạn"
                                live_hosts: Set[str] = set()
                                live_mountpoints: Set[str] = set()
                                try:
                                    rover_key = str(rover.username or "").strip().lower()
                                    for cache_data in (await _get_all_caster_cache()).values():
                                        host = str(cache_data.get("host") or "").strip()
                                        for caster_user in cache_data.get("users") or []:
                                            if str(caster_user.get("username") or "").strip().lower() != rover_key:
                                                continue
                                            if host:
                                                live_hosts.add(host)
                                            raw_mountpoints = caster_user.get("mountpoints") or []
                                            if isinstance(raw_mountpoints, str):
                                                raw_mountpoints = re.split(r"[;,\s]+", raw_mountpoints)
                                            live_mountpoints.update(
                                                str(item).strip() for item in raw_mountpoints if str(item).strip()
                                            )
                                except Exception as exc:
                                    logger.warning("Could not resolve live rover placement for %s: %s", rover.username, exc)

                                if not live_hosts:
                                    live_hosts.update(
                                        item.strip() for item in str(rover.target_casters or "").split(";") if item.strip()
                                    )
                                if not live_mountpoints:
                                    live_mountpoints.update(
                                        item.strip() for item in re.split(r"[;,]+", str(rover.mountpoints or "")) if item.strip()
                                    )
                                sorted_hosts = sorted(live_hosts)
                                sorted_mountpoints = sorted(live_mountpoints)
                                caster_label = "Caster" if len(sorted_hosts) <= 1 else "Các Caster"
                                mountpoint_label = "Mountpoint" if len(sorted_mountpoints) <= 1 else "Các mountpoint"
                                return {
                                    "message": "\n".join((
                                        f"🛰️ ROVER {rover.username}",
                                        f"• Trạng thái: {'Đang hoạt động' if rover.is_active else 'Đang khóa'}",
                                        f"• {mountpoint_label}: {', '.join(sorted_mountpoints) or 'Không có'}",
                                        f"• {caster_label}: {', '.join(sorted_hosts) or 'Chưa xác định'}",
                                        f"• Kết nối tối đa: {rover.max_connections}",
                                        f"• Hiển thị vị trí: {'Có' if rover.show_position else 'Không'}",
                                        f"• Hết hạn: {expiry}",
                                    )),
                                    "context_target": str(rover.username),
                                    "context_type": "rover",
                                    "context_name": str(rover.username),
                                }

                            if action == "rover_create":
                                service_cfg = device.service_config if isinstance(device.service_config, dict) else {}
                                default_mountpoint = next((
                                    str(service_cfg.get(key)).strip()
                                    for enabled_key, key in (
                                        ("server1_enabled", "mountpoint1"),
                                        ("server2_enabled", "mountpoint2"),
                                    )
                                    if service_cfg.get(enabled_key) and str(service_cfg.get(key) or "").strip()
                                ), None)
                                target_mountpoint = str(payload.get("mountpoint") or default_mountpoint or "").strip()
                                if not target_mountpoint:
                                    raise ValueError("Trạm chưa có mountpoint; hãy truyền mountpoint trong lệnh")
                                create_request = schemas.RoverMultiCreate(
                                    username=str(payload["username"]),
                                    password=str(payload["password"]),
                                    expiry_days=int(payload.get("expiry_days", 365)),
                                    selected_serials=[str(device.serial)],
                                    mountpoint_override=target_mountpoint,
                                )
                                created = await create_multi_base_rover(
                                    create_request,
                                    auth_db,
                                    devices_db,
                                    execution_user,
                                )
                                await crud.log_system_event(
                                    devices_db, "success",
                                    f"Zalo admin created rover '{created.username}'",
                                    user_id=execution_user.id, serial=device.serial,
                                )
                                return {"message": f"✅ Đã tạo rover {created.username} cho {device.name or device.serial}."}

                            if action == "rover_update":
                                changes = dict(payload.get("changes") or {})
                                allowed_fields = set(schemas.RoverUpdate.model_fields)
                                unknown = sorted(set(changes) - allowed_fields)
                                if unknown:
                                    raise ValueError(f"Rover key không hợp lệ: {', '.join(unknown)}")
                                list_fields = {"selected_serials", "target_casters"}
                                converted = {}
                                for key, value in changes.items():
                                    converted[key] = (
                                        [part.strip() for part in str(value).split(",") if part.strip()]
                                        if key in list_fields else _zalo_parse_scalar(value)
                                    )
                                update_request = schemas.RoverUpdate(**converted)
                                updated = await device_routes.update_rover(
                                    rover.id, update_request, auth_db, devices_db, execution_user
                                )
                                await crud.log_system_event(
                                    devices_db, "success",
                                    f"Zalo admin updated rover '{updated.username}'",
                                    user_id=execution_user.id,
                                )
                                return {"message": f"✅ Đã cập nhật rover {updated.username}."}

                            if action == "rover_delete":
                                await device_routes.delete_rover(rover.id, auth_db, devices_db, execution_user)
                                await crud.log_system_event(
                                    devices_db, "warning",
                                    f"Zalo admin deleted rover '{rover.username}'",
                                    user_id=execution_user.id,
                                )
                                return {"message": f"✅ Đã xóa rover {rover.username}."}

                            if action == "rover_renew":
                                request = schemas.RoverBulkRenewRequest(
                                    usernames=[rover.username],
                                    expiry_days=int(payload.get("expiry_days", 365)),
                                )
                                result = await bulk_renew_rovers(request, auth_db, devices_db, execution_user)
                                return {"message": (
                                    f"✅ Đã gia hạn rover {rover.username} {result.get('expiry_days')} ngày.\n"
                                    f"Hết hạn mới: {datetime.fromtimestamp(result['expires_at']).strftime('%d/%m/%Y %H:%M')}"
                                )}

                            if action == "rover_block":
                                request = schemas.BlockRoverRequest(
                                    username=rover.username,
                                    reason=str(payload.get("reason") or "Blocked via Zalo admin"),
                                )
                                result = await block_caster_user(request, auth_db, devices_db, execution_user)
                                return {"message": (
                                    f"✅ Đã chặn rover {rover.username}. "
                                    f"Caster ảnh hưởng: {result.get('casters_affected', 0)}"
                                )}

                            if action == "rover_unblock":
                                request = schemas.UnblockRoverRequest(
                                    username=rover.username,
                                    mountpoint=payload.get("mountpoint"),
                                )
                                result = await unblock_caster_user(request, auth_db, devices_db, execution_user)
                                return {"message": f"✅ Đã mở chặn rover {rover.username} thành công."}

                            raise ValueError(f"Admin action không được hỗ trợ: {action}")
                        except HTTPException as exc:
                            raise RuntimeError(str(exc.detail)) from exc
                        
            async def _zalo_get_binding_scopes() -> Dict[int, Set[str]]:
                scopes: Dict[int, Set[str]] = {}
                async with auth_session_scope() as auth_db:
                    binding_rs = await auth_db.execute(select(models.ZaloChatBinding).where(
                        models.ZaloChatBinding.binding_type == "private",
                        models.ZaloChatBinding.is_active.is_(True),
                    ))
                    bindings = binding_rs.scalars().all()
                    async with device_session_scope() as devices_db:
                        for binding in bindings:
                            user = await auth_db.get(models.User, binding.user_id) if binding.user_id else None
                            if not user or not user.is_active:
                                scopes[int(binding.id)] = set()
                                continue
                            devices = await _ops_visible_devices(devices_db, auth_db, user)
                            rovers = await _ops_visible_rovers(auth_db, devices_db, user)
                            values = {
                                str(value).strip().casefold()
                                for item in devices
                                for value in (item.serial, item.name)
                                if str(value or "").strip()
                            }
                            values.update(
                                str(item.username).strip().casefold()
                                for item in rovers if str(item.username or "").strip()
                            )
                            scopes[int(binding.id)] = values
                return scopes

            zalo_bot_service.set_callbacks(
                get_devices=_zalo_get_devices,
                get_alerts=_zalo_get_alerts,
                admin_action=_zalo_admin_action,
                get_binding_scopes=_zalo_get_binding_scopes,
            )
            tasks.append(asyncio.create_task(zalo_bot_service.start()))
            logger.info("✓ Zalo Bot service started")

        yield
        
    finally:
        logger.info("🛑 Application shutting down...")
        
        if settings.ENABLE_ZALO_BOT:
            try:
                from .zalo_bot_service import zalo_bot_service
                await zalo_bot_service.stop()
            except Exception as e:
                logger.error(f"Error stopping Zalo Bot: {e}")
                
        await redis_client.disconnect()

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
from .routers import users, devices
app.include_router(users.router)
app.include_router(devices.router)


# === SEO & DYNAMIC ROUTING FOR GNSS PLATFORM ===
@app.get("/station/{serial}")
async def get_station_seo_page(serial: str, db: AsyncSession = Depends(get_db)):
    """
    ENTERPRISE SEO ENGINE: Renders a high-performance, crawler-optimized page
    with deep JSON-LD structured data for GNSS stations.
    """
    def _station_slug(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        slug = re.sub(r"[^a-zA-Z0-9._-]+", ".", ascii_text).strip(".")
        slug = re.sub(r"\.{2,}", ".", slug)
        return slug.lower()

    token = (serial or "").strip()
    token_slug = _station_slug(token)

    device = await crud.get_device_by_serial(db, token)
    if not device:
        all_devices = (await db.execute(select(models.Device))).scalars().all()
        for item in all_devices:
            if _station_slug(item.name or item.serial or "") == token_slug:
                device = item
                break
    index_path = Path(__file__).parent.parent.parent / "frontend" / "index.html"
    
    if not index_path.exists():
        return HTMLResponse(content="<h1>System Error: Frontend missing</h1>", status_code=500)

    try:
        with open(index_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        if device:
            # 1. DATA EXTRACTION
            station_name = device.name or token
            status_text = "Online" if device.status == "online" else "Offline"
            
            # Extract Coordinates
            base_cfg = device.base_config or {}
            coords = base_cfg.get("coords", {})
            lat = coords.get("lat") or base_cfg.get("lat", 0.0)
            lon = coords.get("lon") or base_cfg.get("lon", 0.0)
            alt = coords.get("alt") or base_cfg.get("alt", 0.0)
            
            # 2. BUILD DYNAMIC SEO TAGS
            title = f"{station_name} GNSS CORS Station | RTK Network Vietnam | Aitogy"
            desc = f"Trạm CORS {station_name} ({serial}) tại {lat}, {lon}. Trạng thái: {status_text}. Theo dõi tín hiệu vệ tinh GPS/GLONASS/BDS và độ trễ RTK thời gian thực."
            
            # 3. BUILD DEEP JSON-LD (Schema.org)
            json_ld = {
                "@context": "https://schema.org",
                "@type": "Place",
                "name": f"GNSS CORS Station {station_name}",
                "description": desc,
                "identifier": serial,
                "geo": {
                    "@type": "GeoCoordinates",
                    "latitude": lat,
                    "longitude": lon,
                    "elevation": alt
                },
                "additionalType": "https://en.wikipedia.org/wiki/GNSS_augmentation",
                "provider": {
                    "@type": "Organization",
                    "name": "AITOGY .,JSC",
                    "legalName": "Aitogy Infrastructure Technology Joint Stock Company",
                    "url": "https://cors.aitogy.com"
                }
            }
            json_ld_script = f'<script type="application/ld+json">{json.dumps(json_ld)}</script>'

            # 4. SERVER-SIDE INJECTION
            # Inject standard meta
            html_content = html_content.replace("<title>Hệ thống trạm CORS Aitogy | Quản lý định vị chính xác cao GNSS/RTK</title>", f"<title>{title}</title>")
            html_content = html_content.replace('name="description" content="Aitogy CORS Control - Hệ thống quản trị mạng lưới trạm định vị vệ tinh (CORS) chuyên nghiệp. Cung cấp giải pháp định vị chính xác cao cho khảo sát và đo đạc."', f'name="description" content="{desc}"')
            
            # Inject OG Tags
            html_content = html_content.replace('property="og:title" content="Hệ thống trạm CORS Aitogy | Quản lý định vị chính xác cao"', f'property="og:title" content="{title}"')
            html_content = html_content.replace('property="og:description" content="Quản lý và vận hành mạng lưới trạm CORS Aitogy. Giải pháp định vị chính xác cao hàng đầu."', f'property="og:description" content="{desc}"')
            
            # Inject JSON-LD before head closing
            html_content = html_content.replace("</head>", f"{json_ld_script}\n</head>")

        return HTMLResponse(content=html_content)
    except Exception as e:
        logging.error(f"❌ Professional SEO Injection Failed: {e}")
        return FileResponse(index_path)

@app.get("/sitemap.xml")
async def get_dynamic_sitemap(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Generates a dynamic XML sitemap listing the homepage and all active CORS stations.
    This ensures Google always has an up-to-date list of every station page.
    """
    base_url = "https://cors.aitogy.com"
    
    def _station_slug(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        slug = re.sub(r"[^a-zA-Z0-9._-]+", ".", ascii_text).strip(".")
        slug = re.sub(r"\.{2,}", ".", slug)
        return slug.lower()

    # Fetch public station names from DB and build slug URLs (no serial leakage)
    result = await db.execute(select(models.Device.name, models.Device.serial))
    stations = result.all()
    
    xml_content = f'<?xml version="1.0" encoding="UTF-8"?>\n'
    xml_content += f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    
    # 1. Main Pages
    for path in ["/", "/network-status", "/coverage", "/blog"]:
        xml_content += f'  <url>\n    <loc>{base_url}{path}</loc>\n    <changefreq>daily</changefreq>\n    <priority>1.0</priority>\n  </url>\n'
    
    # 2. Dynamic Station Pages
    for station_name, station_serial in stations:
        station_slug = _station_slug(station_name or station_serial or "")
        if not station_slug:
            continue
        xml_content += f'  <url>\n    <loc>{base_url}/station/{station_slug}</loc>\n    <changefreq>weekly</changefreq>\n    <priority>0.8</priority>\n  </url>\n'
        
    xml_content += f'</urlset>'
    
    return Response(content=xml_content, media_type="application/xml")

@app.get("/blog/{slug}")
async def get_blog_post_seo(slug: str, request: Request):
    """
    Renders index.html with server-side SEO for individual blog posts.
    Each technical article becomes a landing page for Google Search.
    """
    index_path = Path(__file__).parent.parent.parent / "frontend" / "index.html"
    
    # Mock data for SEO injection (In a real system, this would come from a Blog DB table)
    blog_metadata = {
        "ntrip-caster-la-gi": {
            "title": "NTRIP Caster là gì? Hướng dẫn toàn tập về giao thức RTK | Aitogy",
            "desc": "Tìm hiểu về NTRIP Caster, vai trò của nó trong mạng lưới CORS và cách kết nối đầu thu GNSS để đạt độ chính xác centimet."
        },
        "huong-dan-ket-noi-cors": {
            "title": "Cách kết nối trạm CORS Aitogy cho Drone và Máy công trình",
            "desc": "Hướng dẫn chi tiết từng bước cấu hình tài khoản NTRIP để nhận tín hiệu hiệu chỉnh RTK từ hệ thống trạm CORS Aitogy."
        }
    }

    meta = blog_metadata.get(slug, {
        "title": "Blog Kỹ thuật GNSS/RTK | Aitogy Knowledge Base",
        "desc": "Kiến thức chuyên sâu về công nghệ định vị vệ tinh, hạ tầng CORS và giải pháp RTK chính xác cao."
    })

    try:
        with open(index_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        # Inject Blog Meta
        html_content = html_content.replace("<title>Hệ thống trạm CORS Aitogy | Quản lý định vị chính xác cao GNSS/RTK</title>", f"<title>{meta['title']}</title>")
        html_content = html_content.replace('name="description" content="Aitogy CORS Control - Hệ thống quản trị mạng lưới trạm định vị vệ tinh (CORS) chuyên nghiệp. Cung cấp giải pháp định vị chính xác cao cho khảo sát và đo đạc."', f'name="description" content="{meta["desc"]}"')
        
        # Article Schema
        article_schema = {
            "@context": "https://schema.org",
            "@type": "TechArticle",
            "headline": meta['title'],
            "description": meta['desc'],
            "author": {"@type": "Organization", "name": "Aitogy"},
            "publisher": {"@type": "Organization", "name": "Aitogy", "logo": "https://cors.aitogy.com/img/aitogy.png"},
            "mainEntityOfPage": {"@type": "WebPage", "@id": f"https://cors.aitogy.com/blog/{slug}"}
        }
        html_content = html_content.replace("</head>", f'<script type="application/ld+json">{json.dumps(article_schema)}</script>\n</head>')

        return HTMLResponse(content=html_content)
    except Exception:
        return FileResponse(index_path)

@app.get("/network-status")
@app.get("/coverage")
@app.get("/blog")
async def get_public_pages():
    """Catch-all for other public SEO pages to serve index.html."""
    index_path = Path(__file__).parent.parent.parent / "frontend" / "index.html"
    return FileResponse(index_path)

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

# Duplicate global exception handler intentionally left undecorated; the canonical
# handler is registered after the WebSocket endpoints.
async def _legacy_global_exception_handler_removed(request: Request, exc: Exception):
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
app.add_middleware(FrontendCacheMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=800, compresslevel=5)

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
    is_command_mp = mountpoint and _is_command_mountpoint(mountpoint)
    if is_command_mp:
        logger.debug(f"[NTRIP Wake] mountpoint={mountpoint}, client_id={client_id}, address={requested_address or 'not-provided'}")
    else:
        logger.info(f"[NTRIP Wake] mountpoint={mountpoint}, client_id={client_id}, address={requested_address or 'not-provided'}")

    # 1. Check Blocklist ngay lập tức
    reason = await _check_rover_blocked(client_id, mountpoint)
    if reason:
        msg = f"BLOCKED: {reason}"
        logger.warning(f"[NTRIP Wake] {msg} for {client_id}@{mountpoint}")
        return PlainTextResponse(f"WAKE_STATUS: blocked\n{msg}")

    # 1.5. Bypass cho command mountpoints
    if mountpoint and is_command_mp:
        logger.debug(f"[NTRIP Wake] Bypassing wake for command/test mountpoint '{mountpoint}'")
        return PlainTextResponse(f"WAKE_STATUS: ok\nSTREAM_HINT: streaming\nMOUNTPOINT: {mountpoint}\nOK")

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


async def _do_ntrip_stop(mountpoint: str, client_id: str, force: bool = False, requested_address: Optional[str] = None):
    """Xử lý ngắt/dừng trạm, trả về PlainTextResponse."""
    logger.info(f"[NTRIP Stop] mountpoint={mountpoint}, client_id={client_id}, force={force}, address={requested_address or 'not-provided'}")
    try:
        async with device_session_scope() as db:
            serial = await _resolve_station_serial_by_mountpoint(db, mountpoint)
            device = await crud.get_device_by_serial(db, serial)
            if not device:
                return PlainTextResponse(f"STOP_STATUS: error\nERROR: Trạm {serial} không tồn tại")
            
            # 🔥 Phân giải caster_host từ service_config của thiết bị nếu requested_address khớp một phần
            selected_server_host = ""
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
            if selected_server_id in (1, 2):
                selected_server_host = str(cfg.get(f"serverhost{selected_server_id}", "") or "").strip()

            target_host = selected_server_host or requested_address

            if force:
                # Force stop: xóa toàn bộ session của trạm này (và khớp host nếu có) và tắt luồng stream ngay lập tức
                async with rtcm_session_lock:
                    if target_host:
                        norm_ch = target_host.strip().lower()
                        # Lấy danh sách session để xóa theo caster_host
                        stmt = select(models.RTCMStreamSession.session_id, models.RTCMStreamSession.caster_host).where(
                            models.RTCMStreamSession.serial == serial
                        )
                        rs = await db.execute(stmt)
                        to_delete = []
                        for sid, host in rs.all():
                            h_str = str(host or "").strip().lower()
                            if norm_ch in h_str or h_str in norm_ch:
                                to_delete.append(sid)
                        
                        if to_delete:
                            await db.execute(
                                delete(models.RTCMStreamSession).where(
                                    models.RTCMStreamSession.session_id.in_(to_delete)
                                )
                            )
                    else:
                        await db.execute(
                            delete(models.RTCMStreamSession).where(
                                models.RTCMStreamSession.serial == serial
                            )
                        )
                    await db.commit()
                await _set_station_stream(serial, active=False, requested_address=target_host)
                logger.info(f"[NTRIP Stop] Force stopped station stream serial={serial} mountpoint={mountpoint} host={target_host}")
            else:
                await _ntrip_mqtt_stop(
                    serial=serial,
                    client_instance_id=client_id,
                    caster_host=target_host
                )
            return PlainTextResponse("STOP_STATUS: ok\nOK dừng trạm thành công")
    except Exception as e:
        msg = f"ERROR: {e}"
        logger.error(f"[NTRIP Stop ERR] {msg}")
        return PlainTextResponse(f"STOP_STATUS: error\n{msg}", status_code=200)


# === NTRIP STOP STATION HTTP ENDPOINT ===
@app.get("/api/ntrip/stop-listen", response_class=PlainTextResponse)
@app.get("/api/ntrip/stop-station", response_class=PlainTextResponse)
async def ntrip_stop_listen_get(
    mountpoint: str = Query(...),
    client_instance_id: Optional[str] = Query(None),
    receiver_id: Optional[str] = Query(None),
    identifier: Optional[str] = Query(None),
    force: bool = Query(False),
    caster_address: Optional[str] = Query(None),
    address: Optional[str] = Query(None),
    host: Optional[str] = Query(None),
    ip: Optional[str] = Query(None),
):
    id_to_use = client_instance_id or receiver_id or identifier or "http-get"
    addr_to_use = caster_address or address or host or ip
    return await _do_ntrip_stop(mountpoint, id_to_use, force=force, requested_address=addr_to_use)

@app.post("/api/ntrip/stop-station", response_class=PlainTextResponse)
async def ntrip_stop_station(payload: dict = Body(...)):
    mountpoint = payload.get("mountpoint")
    client_instance_id = (
        payload.get("client_instance_id") or 
        payload.get("receiver_id") or 
        payload.get("identifier")
    )
    force = bool(payload.get("force", False))
    address = payload.get("caster_address") or payload.get("address") or payload.get("host") or payload.get("ip")
    if not mountpoint:
        return PlainTextResponse("ERROR: mountpoint is required", status_code=400)
    return await _do_ntrip_stop(mountpoint, client_instance_id or "http-post", force=force, requested_address=address)


# (Old single-instance Caster Rover Management API removed to prevent shadowing multi-instance logic)

# Legacy JSON variant removed from routing; the canonical caster-facing endpoint
# below returns text/plain OK or BLOCKED.
async def _legacy_json_ntrip_auth_check_removed(
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
    return await dispatch_command_to_pi(serial, command)

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

    await crud.log_system_event(
        db=devices_db,
        type="success",
        message=f"User '{user.username}' logged in",
        user_id=user.id,
        ip=request.client.host
    )

    return {"access_token": access_token, "token_type": "bearer"}


def _extract_zalo_webhook_secret(request: Request) -> str:
    query_names = (
        "secret_token",
        "secret",
        "webhook_secret",
        "zalo_webhook_secret",
        "token",
    )
    for query_name in query_names:
        value = request.query_params.get(query_name)
        if value:
            return value.strip()

    header_names = (
        "x-bot-api-secret-token",
        "x-zalo-bot-api-secret-token",
        "x-zalo-bot-secret-token",
        "x-zalo-webhook-secret",
        "x-zalo-webhook-secret-token",
        "x-webhook-secret-token",
        "x-telegram-bot-api-secret-token",
    )
    for header_name in header_names:
        value = request.headers.get(header_name)
        if value:
            return value.strip()

    auth_header = request.headers.get("authorization", "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()

    return ""


def _validate_zalo_webhook_secret(request: Request) -> None:
    if not settings.ENABLE_ZALO_BOT:
        raise HTTPException(status_code=503, detail="Zalo Bot is disabled")

    expected_secret = str(getattr(settings, "ZALO_WEBHOOK_SECRET", "") or "").strip()
    if not expected_secret:
        logger.error("Zalo webhook request rejected because ZALO_WEBHOOK_SECRET is not configured.")
        raise HTTPException(status_code=503, detail="Zalo webhook secret is not configured")

    provided_secret = _extract_zalo_webhook_secret(request)
    if not provided_secret or not secrets.compare_digest(provided_secret, expected_secret):
        client_host = request.client.host if request.client else "unknown"
        logger.warning("Rejected Zalo webhook request with invalid secret from %s", client_host)
        raise HTTPException(status_code=401, detail="Invalid Zalo webhook secret")


def _persist_zalo_bot_enabled(enabled: bool) -> None:
    value = "true" if enabled else "false"
    set_key(str(ENV_FILE_PATH), "ENABLE_ZALO_BOT", value)
    os.environ["ENABLE_ZALO_BOT"] = value
    try:
        settings.ENABLE_ZALO_BOT = enabled
    except Exception:
        setattr(settings, "ENABLE_ZALO_BOT", enabled)


def _get_zalo_system_state() -> dict[str, Any]:
    from .zalo_bot_service import zalo_bot_service
    from .zalo_bot_service import ZALO_AVAILABLE

    token = str(getattr(settings, "ZALO_BOT_TOKEN", "") or "").strip()
    running = bool(getattr(zalo_bot_service, "app", None))
    sdk_available = bool(ZALO_AVAILABLE)
    return {
        "enabled": bool(settings.ENABLE_ZALO_BOT),
        "running": running,
        "token_configured": bool(token),
        "sdk_available": sdk_available,
        "available": bool(token) and sdk_available,
        "mode": str(getattr(settings, "ZALO_BOT_MODE", "polling") or "polling").strip().lower(),
        "env_file": str(ENV_FILE_PATH),
    }


@app.get("/api/zalo/webhook", response_class=PlainTextResponse)
async def zalo_webhook_verify(request: Request):
    _validate_zalo_webhook_secret(request)
    return "ok"


@app.post("/api/zalo/webhook", response_class=PlainTextResponse)
async def zalo_webhook(request: Request, response: Response):
    _validate_zalo_webhook_secret(request)

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    try:
        from .zalo_bot_service import zalo_bot_service
        result = await zalo_bot_service.process_webhook_payload(payload)
        response.headers["X-Zalo-Trace-Id"] = result["trace_id"]
        response.headers["X-Zalo-Outbound-Count"] = str(result["outbound_count"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.error("Zalo webhook runtime error: %s", e)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error("Unhandled Zalo webhook error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Zalo webhook processing failed")

    return "ok"


@app.get("/api/admin/zalo/diagnostics")
async def get_zalo_diagnostics(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    """Show recent, credential-redacted Zalo inbound/dispatch/outbound traces."""
    from .zalo_bot_service import zalo_bot_service
    return zalo_bot_service.get_debug_events(limit=limit)


@app.delete("/api/admin/zalo/diagnostics")
async def clear_zalo_diagnostics(
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    from .zalo_bot_service import zalo_bot_service
    zalo_bot_service.clear_debug_events()
    return {"status": "cleared"}


@app.get("/api/admin/zalo/system")
async def get_zalo_system_state(
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được xem trạng thái hệ thống Zalo.")
    return _get_zalo_system_state()


@app.patch("/api/admin/zalo/system")
async def update_zalo_system_state(
    payload: Dict[str, Any] = Body(...),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được bật/tắt hệ thống Zalo.")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload không hợp lệ.")

    if "enabled" not in payload:
        raise HTTPException(status_code=400, detail="Thiếu trường enabled.")

    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled phải là boolean.")

    current_state = _get_zalo_system_state()
    if enabled and not current_state["available"]:
        if not current_state["sdk_available"]:
            raise HTTPException(status_code=400, detail="Thiếu Zalo SDK trong môi trường hiện tại nên không thể bật bot.")
        raise HTTPException(status_code=400, detail="Thiếu ZALO_BOT_TOKEN trong .env nên không thể bật bot.")

    if enabled == current_state["enabled"]:
        return current_state

    from .zalo_bot_service import zalo_bot_service

    _persist_zalo_bot_enabled(enabled)
    try:
        if enabled:
            if not current_state["running"]:
                await zalo_bot_service.start(strict=True)
        else:
            await zalo_bot_service.stop()
    except Exception as exc:
        if enabled:
            try:
                await zalo_bot_service.stop()
            except Exception:
                pass
        _persist_zalo_bot_enabled(not enabled)
        raise HTTPException(status_code=500, detail=f"Không thể cập nhật trạng thái Zalo Bot: {exc}")

    return _get_zalo_system_state()


@app.post("/api/auth/zalo-link-code")
async def create_zalo_link_code(
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    from .zalo_assistant import zalo_repository
    result = await zalo_repository.create_link_code(current_user.id)
    result["can_register_group"] = False
    return result


@app.post("/api/auth/zalo-my-link-code")
async def create_my_zalo_link_code(
    current_user: models.User = Depends(auth.get_current_user),
):
    """Self-service: any authenticated user can create a personal link code."""
    from .zalo_assistant import zalo_repository
    result = await zalo_repository.create_link_code(current_user.id)
    result["can_register_group"] = False
    return result


@app.post("/api/admin/zalo-group-link-code")
async def create_zalo_group_link_code(
    body: schemas.ZaloGroupLinkCodeCreate,
    current_user: models.User = Depends(auth.get_current_user),
    devices_db: AsyncSession = Depends(get_db),
):
    """Create a 30-second OTP whose group role and station scope are immutable."""
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được tạo OTP liên kết group.")
    binding_type = str(body.binding_type or "")
    if binding_type not in {"group_admin", "group_public"}:
        raise HTTPException(status_code=400, detail="Loại group không hợp lệ.")
    scope_mode = str(body.station_scope_mode or "all")
    allowed_bases = sorted({str(value).strip() for value in body.allowed_bases if str(value).strip()})
    if binding_type == "group_admin":
        scope_mode, allowed_bases = "all", []
    elif scope_mode not in {"all", "selected", "none"}:
        raise HTTPException(status_code=400, detail="Phạm vi trạm không hợp lệ.")
    elif scope_mode == "selected":
        if not allowed_bases:
            raise HTTPException(status_code=400, detail="Hãy chọn ít nhất một trạm.")
        available_rs = await devices_db.execute(select(models.Device.serial))
        available = {str(serial) for serial in available_rs.scalars().all()}
        unknown = sorted(set(allowed_bases) - available)
        if unknown:
            raise HTTPException(status_code=400, detail=f"Trạm không tồn tại: {', '.join(unknown)}")
    else:
        allowed_bases = []

    from .zalo_assistant import zalo_repository
    return await zalo_repository.create_link_code(
        current_user.id,
        link_type=binding_type,
        station_scope_mode=scope_mode,
        allowed_bases=allowed_bases,
    )


@app.get("/api/auth/zalo-bindings")
async def get_zalo_bindings(
    current_user: models.User = Depends(auth.get_current_user),
    devices_db: AsyncSession = Depends(get_db),
):
    from .zalo_assistant import zalo_repository
    items = await zalo_repository.list_bindings_for_user(
        current_user.id, include_all=current_user.role == auth.Role.ADMIN,
    )
    stations = []
    if current_user.role == auth.Role.ADMIN:
        station_rows = await devices_db.execute(select(models.Device).order_by(models.Device.name, models.Device.serial))
        stations = [
            {"serial": str(item.serial), "name": str(item.name or item.serial), "status": str(item.status or "unknown")}
            for item in station_rows.scalars().all()
        ]
    return {
        "items": items,
        "total": len(items),
        "stations": stations,
        "is_admin": current_user.role == auth.Role.ADMIN,
        "system_enabled": bool(settings.ENABLE_ZALO_BOT),
    }


@app.patch("/api/auth/zalo-bindings/{binding_id}")
async def update_zalo_binding(
    binding_id: int,
    body: schemas.ZaloBindingAdminUpdate,
    current_user: models.User = Depends(auth.get_current_user),
    devices_db: AsyncSession = Depends(get_db),
):
    from .zalo_assistant import zalo_repository
    changes = body.model_dump(exclude_unset=True)
    is_admin = current_user.role == auth.Role.ADMIN
    if "allowed_bases" in changes and is_admin:
        available_rs = await devices_db.execute(select(models.Device.serial))
        available = {str(serial) for serial in available_rs.scalars().all()}
        unknown = sorted(set(changes["allowed_bases"] or []) - available)
        if unknown:
            raise HTTPException(status_code=400, detail=f"Trạm không tồn tại: {', '.join(unknown)}")
    try:
        item = await zalo_repository.update_binding_admin(
            binding_id, changes, actor_user_id=current_user.id, is_admin=is_admin,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "updated", "item": item}


@app.delete("/api/auth/zalo-bindings/{binding_id}")
async def revoke_zalo_binding(
    binding_id: int,
    current_user: models.User = Depends(auth.get_current_user),
):
    from .zalo_assistant import zalo_repository
    deleted_chat_id = await zalo_repository.revoke_binding(
        binding_id, current_user.id, current_user.role == auth.Role.ADMIN,
    )
    if not deleted_chat_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy liên kết Zalo hoặc bạn không có quyền xóa")
    from .zalo_bot_service import zalo_bot_service
    zalo_bot_service.forget_chat(deleted_chat_id)
    return {"status": "deleted", "binding_id": binding_id, "chat_id": deleted_chat_id}


@app.get("/api/public/map-stations")
async def get_public_map_stations(
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    global public_map_cache, public_map_cache_updated_monotonic

    response.headers["Cache-Control"] = (
        f"public, max-age={int(PUBLIC_MAP_CACHE_TTL_SECONDS)}, stale-while-revalidate=30"
    )
    now_monotonic = time.monotonic()
    if (
        public_map_cache is not None
        and now_monotonic - public_map_cache_updated_monotonic < PUBLIC_MAP_CACHE_TTL_SECONDS
    ):
        response.headers["X-Data-Cache"] = "HIT"
        return public_map_cache

    try:
        # Lấy tất cả thiết bị
        result = await db.execute(select(models.Device))
        devices = result.scalars().all()

        def _dict_or_empty(value: Any) -> dict:
            return value if isinstance(value, dict) else {}

        def _extract_public_map_coords(dev: models.Device) -> tuple[Optional[Any], Optional[Any], Optional[Any]]:
            candidate_sources = []

            base_config = _dict_or_empty(getattr(dev, "base_config", None))
            if base_config:
                candidate_sources.append(base_config)

            base_coords = getattr(dev, "base_coords", None)
            if isinstance(base_coords, dict):
                candidate_sources.append(base_coords)

            for source in candidate_sources:
                coords = _dict_or_empty(source.get("coords"))
                lat = coords.get("lat") if coords.get("lat") is not None else source.get("lat")
                lon = coords.get("lon") if coords.get("lon") is not None else source.get("lon")
                alt = coords.get("alt") if coords.get("alt") is not None else source.get("alt")

                if lat is not None and lon is not None:
                    try:
                        alt_val = float(alt) if alt is not None else 0.0
                    except (TypeError, ValueError):
                        alt_val = 0.0
                    return lat, lon, alt_val

            return None, None, None

        def _extract_public_mountpoint(dev: models.Device) -> Optional[str]:
            service_config = _dict_or_empty(getattr(dev, "service_config", None))
            for key in ("active_mountpoint", "mountpoint1", "mountpoint2"):
                value = str(service_config.get(key) or "").strip()
                if value:
                    return value
            return None

        def _extract_public_streams(dev: models.Device) -> list:
            service_config = _dict_or_empty(getattr(dev, "service_config", None))
            streams = []
            
            # Check S1
            mp1 = str(service_config.get("mountpoint1") or "").strip()
            if mp1:
                streams.append({
                    "stream_id": "s1",
                    "host": service_config.get("serverhost1", service_config.get("host1", "")),
                    "port": service_config.get("port1", service_config.get("serverport1", "")),
                    "mountpoint": mp1,
                    "enabled": bool(service_config.get("server1_enabled"))
                })
                
            # Check S2
            mp2 = str(service_config.get("mountpoint2") or "").strip()
            if mp2:
                streams.append({
                    "stream_id": "s2",
                    "host": service_config.get("serverhost2", service_config.get("host2", "")),
                    "port": service_config.get("port2", service_config.get("serverport2", "")),
                    "mountpoint": mp2,
                    "enabled": bool(service_config.get("server2_enabled"))
                })
                
            # Fallback
            if not streams:
                amp = str(service_config.get("active_mountpoint") or "").strip()
                if amp:
                    streams.append({
                        "stream_id": "active",
                        "host": "",
                        "port": "",
                        "mountpoint": amp,
                        "enabled": True
                    })
                    
            return streams
        
        public_data = []
        skipped_devices = 0
        
        for dev in devices:
            lat, lon, alt = _extract_public_map_coords(dev)

            if lat is not None and lon is not None:
                try:
                    public_data.append({
                        "serial": dev.serial,
                        "name": dev.name,
                        "status": dev.status,
                        "lat": float(lat),
                        "lon": float(lon),
                        "alt": float(alt) if alt is not None else 0.0,
                        "mountpoint": _extract_public_mountpoint(dev),
                        "streams": _extract_public_streams(dev),
                        "service_config": dev.service_config,
                        "is_provisioned": dev.is_provisioned,
                        "ntrip_connected": getattr(dev, "ntrip_connected", False),
                        "bps": _device_total_bps(dev)
                    })
                except (ValueError, TypeError) as e:
                    logging.warning(f"⚠️ Invalid coordinates for {dev.name}: {e}")
                    skipped_devices += 1
                    continue
            else:
                skipped_devices += 1
        
        public_map_cache = public_data
        public_map_cache_updated_monotonic = time.monotonic()
        response.headers["X-Data-Cache"] = "MISS"
        logger.debug(
            f"✓ Public map API: found {len(devices)} devices, returning {len(public_data)} with valid coords, "
            f"skipped {skipped_devices} without usable coords"
        )
        return public_data
        
    except Exception as e:
        logging.error(f"â Œ Error fetching public map data: {e}")
        return []


@app.get("/api/public/network-summary")
async def get_public_network_summary(db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(select(models.Device))
        devices = result.scalars().all()
        active_statuses = {"online", "sleep", "streaming", "starting"}
        return {
            "total_stations": len(devices),
            "active_stations": sum(1 for dev in devices if str(dev.status or "").lower() in active_statuses),
            "coverage_areas": len({
                str(dev.location).split(",")[-1].strip()
                for dev in devices
                if getattr(dev, "location", None)
            })
        }
    except Exception as e:
        logging.error(f"Error fetching public network summary: {e}")
        return {"total_stations": 0, "active_stations": 0, "coverage_areas": 0}



@app.get("/api/caster/clienttable")
async def get_caster_clienttable(
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Aggregate clienttable from all scanned casters to show on map."""
    has_rover_view_permission = auth.has_permission(current_user, auth.Permission.VIEW_ROVERS)
    if not has_rover_view_permission:
        raise HTTPException(status_code=403, detail="Bạn không có quyền xem rover trên bản đồ.")
    all_clients = []
    
    async with global_caster_cache_lock:
        for cache_data in (await _get_all_caster_cache()).values():
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
        # get_visible_rovers_for_user is the single source of truth for rover scope.
        my_rovers = await crud.get_visible_rovers_for_user(db, devices_db, current_user)
        allowed_usernames = {
            str(r.username).strip().lower() for r in my_rovers if r.username
        }
        filtered_clients = [c for c in filtered_clients if str(c.get("username", "")).strip().lower() in allowed_usernames]
    
    return {
        "count": len(filtered_clients),
        "items": filtered_clients,
    }


@app.get("/api/caster/users", response_model=schemas.CasterUsersResponse)
async def get_caster_users(
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_ROVERS)),
):
    """Aggregate total users from all scanned casters with enrichment."""
    all_caster_users = []
    
    async with global_caster_cache_lock:
        for cache_data in (await _get_all_caster_cache()).values():
            host = cache_data.get("host", "unknown")
            users = cache_data.get("users", [])
            for u in users:
                u_copy = dict(u)
                u_copy["server"] = host # Tag with server
                u_copy["last_update"] = int(cache_data.get("users_last_update") or cache_data.get("last_update") or 0)
                all_caster_users.append(u_copy)

    # --- PHÂN QUYỀN HIỂN THỊ (ĐỒNG BỘ VỚI MAP) ---
    if current_user.role != auth.Role.ADMIN:
        # Use the same resolved rover scope as the DB list and map endpoint.
        my_rovers = await crud.get_visible_rovers_for_user(auth_db, devices_db, current_user)
        allowed_usernames = {
            str(r.username).strip().lower() for r in my_rovers if r.username
        }
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
        for cache_data in (await _get_all_caster_cache()).values():
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
            last_update=cu.get("last_update", 0),
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
        server_time=int(time.time()),
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
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    """
    Chặn rover trên hệ thống:
    1. Lưu vào blocklist DB (source of truth)
    2. Gọi caster TCP block_user để kick và chặn ngay lập tức
    3. Cập nhật RoverAccount.is_active = False (nếu user có trong DB)
    """
    username = req.username.strip()
    
    # Permission check if rover exists in DB
    rover = await crud.get_rover_by_username(auth_db, username)
    if rover:
        has_perm = await crud.check_rover_management_permission(rover, current_user, devices_db, auth_db)
        if not has_perm:
            raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý Rover này.")
            
    username = req.username.strip()
    target_mps = req.mountpoints if req.mountpoints else [None]
    now = int(time.time())

    blocks_created = 0
    for mp in target_mps:
        mountpoint = mp.strip() if mp else None
        
        # Check duplicate
        existing_block_q = select(models.RoverBlocklist).where(models.RoverBlocklist.username == username)
        if mountpoint:
            existing_block_q = existing_block_q.where(models.RoverBlocklist.mountpoint == mountpoint)
        else:
            existing_block_q = existing_block_q.where(models.RoverBlocklist.mountpoint.is_(None))
        
        existing_block_rs = await auth_db.execute(existing_block_q)
        if existing_block_rs.scalars().first():
            continue

        new_block = models.RoverBlocklist(
            username=username,
            mountpoint=mountpoint,
            blocked_at=now,
            blocked_by=current_user.username,
            reason=req.reason or "Blocked by admin",
        )
        auth_db.add(new_block)
        blocks_created += 1

    if blocks_created > 0:
        await auth_db.commit()

    # 3. Gọi các caster TCP để xóa user (kick ngay lập tức trên tất cả server)
    hosts, env_cfg = _read_caster_config_from_env()
    target_hosts = hosts if hosts else [str(settings.CASTER_HOST or "localhost")]
    port = int(settings.CASTER_PORT or 2101)
    
    deleted_count_caster = 0
    for host in target_hosts:
        try:
            from app.caster_client import CasterClient
            caster = CasterClient(host, port)
            ok = await caster.block_user(
                identifier=current_user.username,
                username=username,
                reason=req.reason or "Blocked by admin",
            )
            if ok: deleted_count_caster += 1
            logger.info(f"[CASTER MGMT] Blocked user '{username}' on caster {host}")
        except Exception as e:
            logger.warning(f"[CASTER MGMT] Failed to block user on caster {host}: {e}")

    return {
        "status": "blocked",
        "username": username,
        "blocks_created": blocks_created,
        "casters_affected": deleted_count_caster,
        "message": f"Đã chặn user '{username}' ({blocks_created} mountpoints) trên toàn hệ thống ({deleted_count_caster} server affected)."
    }


@app.get("/api/caster/blocklist")
async def get_caster_blocklist(
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    """Fetch all active block rules."""
    rs = await db.execute(select(models.RoverBlocklist).order_by(models.RoverBlocklist.blocked_at.desc()))
    return rs.scalars().all()


@app.post("/api/caster/unblock-user")
async def unblock_caster_user(
    req: schemas.UnblockRoverRequest,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    """
    Mở khóa rover:
    1. Xóa khỏi blocklist DB
    2. Tạo lại user trên caster (nếu có thông tin trong DB)
    3. Cập nhật RoverAccount.is_active = True (nếu có)
    """
    username = req.username.strip()
    
    # Permission check if rover exists in DB
    rover = await crud.get_rover_by_username(auth_db, username)
    if rover:
        has_perm = await crud.check_rover_management_permission(rover, current_user, devices_db, auth_db)
        if not has_perm:
            raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý Rover này.")
            
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
    await auth_db.commit()

    caster_recreated = False
    rover = await crud.get_rover_by_username(auth_db, username)

    if rover:
        rover.is_active = True
        await auth_db.commit()

        # 3. Tạo lại user trên TẤT CẢ caster
        hosts, env_cfg = _read_caster_config_from_env()
        target_hosts = hosts if hosts else [str(settings.CASTER_HOST or "localhost")]
        port = int(settings.CASTER_PORT or 2101)
        
        recreated_count = 0
        for host in target_hosts:
            try:
                from app.caster_client import CasterClient
                caster = CasterClient(host, port)

                await caster.unblock_user(identifier=current_user.username, username=rover.username)
                recreated_count += 1
                logger.info(f"[CASTER MGMT] Unblocked user '{username}' on caster {host}")
            except Exception as e:
                logger.warning(f"[CASTER MGMT] Failed to unblock user on caster {host}: {e}")
        
        caster_recreated = (recreated_count > 0)
    else:
        await auth_db.commit()

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


async def _rtcm_visible_serials(auth_db: AsyncSession, devices_db: AsyncSession, user: models.User) -> set[str] | None:
    if user.role == auth.Role.ADMIN:
        return None
    if user.role == auth.Role.COORDINATOR:
        return await crud.get_coordinator_visible_base_serials(devices_db, user)
    return await crud.get_viewer_visible_base_serials(auth_db, devices_db, user)


@app.post("/api/rtcm/stations/{serial}/activate", response_model=schemas.RTCMSessionResponse)
async def activate_rtcm_station(
    serial: str,
    req: schemas.RTCMStationActivateRequest,
    db: AsyncSession = Depends(get_db),
    auth_db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Station not found")
    visible_serials = await _rtcm_visible_serials(auth_db, db, current_user)
    if visible_serials is not None and str(serial).strip().upper() not in visible_serials:
        raise HTTPException(status_code=403, detail="Station is outside your scope")

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
    auth_db: AsyncSession = Depends(get_auth_db),
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
        auth_db=auth_db,
        current_user=current_user,
    )


@app.post("/api/rtcm/sessions/{session_id}/heartbeat", response_model=schemas.RTCMSessionResponse)
async def heartbeat_rtcm_session(
    session_id: str,
    req: schemas.RTCMSessionHeartbeatRequest,
    db: AsyncSession = Depends(get_db),
    auth_db: AsyncSession = Depends(get_auth_db),
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
        visible_serials = await _rtcm_visible_serials(auth_db, db, current_user)
        if visible_serials is not None and str(session.serial).strip().upper() not in visible_serials:
            raise HTTPException(status_code=403, detail="Station is outside your scope")

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


async def _set_caster_cache(key: str, data: dict) -> None:
    global_caster_data_cache[key] = data
    try:
        await redis_client.set_caster_cache(key, data)
    except Exception as exc:
        logger.debug("Redis caster cache write skipped for %s: %s", key, exc)


async def _get_caster_cache(key: str) -> Optional[dict]:
    cached = global_caster_data_cache.get(key)
    if cached is not None:
        return cached
    try:
        return await redis_client.get_caster_cache(key)
    except Exception as exc:
        logger.debug("Redis caster cache read skipped for %s: %s", key, exc)
        return None


async def _get_all_caster_cache() -> dict:
    if global_caster_data_cache:
        return dict(global_caster_data_cache)
    try:
        return await redis_client.get_all_caster_cache()
    except Exception as exc:
        logger.debug("Redis caster cache read-all skipped: %s", exc)
        return {}


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
        async def fetch_users_snapshot():
            users = await caster.get_users()
            return users, int(time.time())

        (users, users_sampled_at), clients = await asyncio.gather(
            fetch_users_snapshot(),
            caster.get_clienttable(mountpoint="all")
        )

        scan_timestamp = int(time.time())
        async with global_caster_cache_lock:
            await _set_caster_cache(key, {
                "last_update": scan_timestamp,
                "users_last_update": users_sampled_at,
                "users": users or [],
                "clients": clients or [],
                "error": None,
                "host": host,
                "port": port
            })

        # A failed clienttable query must not end every recorded session. An
        # empty list, on the other hand, is a successful scan with no clients.
        if clients is not None:
            try:
                await _record_rover_session_snapshots(host, clients, scan_timestamp)
            except Exception as history_error:
                logger.warning("[CASTER HISTORY] Could not persist sessions for %s: %s", host, history_error)

        if RTCM_KEEPALIVE_DEBUG:
            logger.info(f"[CASTER MONITOR] Scanned {key}: {len(users or [])} users, {len(clients or [])} clients")

    except Exception as e:
        logger.warning(f"[CASTER MONITOR] Failed to scan {key}: {e}")
        async with global_caster_cache_lock:
            cached = dict((await _get_caster_cache(key)) or {"users": [], "clients": [], "host": host, "port": port})
            cached["error"] = str(e)
            await _set_caster_cache(key, cached)


def _rover_history_session_key(server: str, username: str, mountpoint: str, client_id: str, receiver_id: str) -> tuple[str, str, str, str]:
    session_identifier = str(client_id or receiver_id or "").strip().casefold()
    return (
        str(server or "").strip().casefold(),
        str(username or "").strip().casefold(),
        str(mountpoint or "").strip().casefold(),
        session_identifier,
    )


def _rover_history_position(client: dict) -> tuple[float, float] | None:
    try:
        # clienttable status is the GGA fix quality: only 4 is RTK fixed.
        # Do not label float or standalone positions as the rover's last fix.
        if int(client.get("status") or 0) != 4:
            return None
    except (TypeError, ValueError):
        return None
    raw_latitude = client.get("latitude", client.get("lat"))
    raw_longitude = client.get("longitude", client.get("lon"))
    if raw_latitude is None or raw_longitude is None:
        return None
    try:
        latitude, longitude = float(raw_latitude), float(raw_longitude)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (latitude, longitude)):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    # Aicaster reports 0/0 when position sharing is disabled.
    if latitude == 0 and longitude == 0:
        return None
    return latitude, longitude


async def _record_rover_session_snapshots(host: str, clients: list[dict], observed_at: int) -> None:
    """Keep one history row per observed caster connection session."""
    async with auth_session_scope() as db:
        active_result = await db.execute(
            select(models.RoverSessionHistory).where(
                models.RoverSessionHistory.server == host,
                models.RoverSessionHistory.ended_at.is_(None),
            )
        )
        active_rows = list(active_result.scalars().all())
        active_by_key = {
            _rover_history_session_key(
                row.server, row.username, row.mountpoint, row.client_id, row.receiver_id
            ): row
            for row in active_rows
        }
        client_usernames = {
            str(client.get("username") or "").strip().lower()
            for client in clients
            if isinstance(client, dict) and str(client.get("username") or "").strip()
        }
        auto_save_enabled_by_username = {}
        if client_usernames:
            auto_save_result = await db.execute(
                select(
                    func.lower(models.RoverAccount.username),
                    models.RoverAccount.auto_save_fixed_points,
                ).where(func.lower(models.RoverAccount.username).in_(client_usernames))
            )
            auto_save_enabled_by_username = {
                username: bool(enabled)
                for username, enabled in auto_save_result.all()
            }
        latest_points_by_session = {}
        active_session_ids = [row.id for row in active_rows if row.id is not None]
        if active_session_ids:
            point_result = await db.execute(
                select(
                    models.RoverFixedPoint.session_id,
                    func.max(models.RoverFixedPoint.recorded_at),
                )
                .where(models.RoverFixedPoint.session_id.in_(active_session_ids))
                .group_by(models.RoverFixedPoint.session_id)
            )
            latest_points_by_session = dict(point_result.all())
        observed_keys = set()

        for client in clients:
            if not isinstance(client, dict):
                continue
            username = str(client.get("username") or "").strip()
            if not username:
                continue
            mountpoint = str(client.get("mountpoint") or "").strip()
            client_id = str(client.get("client_id") or "").strip()
            receiver_id = str(client.get("receiver_id") or "").strip()
            session_key = _rover_history_session_key(host, username, mountpoint, client_id, receiver_id)
            if session_key in observed_keys:
                continue
            observed_keys.add(session_key)

            raw_uptime = client.get("uptime")
            try:
                uptime_seconds = max(0, int(raw_uptime)) if raw_uptime is not None else None
            except (TypeError, ValueError):
                uptime_seconds = None
            try:
                latest_fix_quality = int(client.get("status")) if client.get("status") is not None else None
            except (TypeError, ValueError):
                latest_fix_quality = None
            try:
                satellite_count = max(0, int(client.get("nsv", client.get("satellites"))))
            except (TypeError, ValueError):
                satellite_count = None

            row = active_by_key.pop(session_key, None)
            if (
                row is not None
                and uptime_seconds is not None
                and row.uptime_seconds is not None
                and uptime_seconds + 10 < row.uptime_seconds
            ):
                # The caster reused a client identifier after a reconnect.
                row.ended_at = observed_at
                row = None

            if row is None:
                row = models.RoverSessionHistory(
                    username=username,
                    server=host,
                    mountpoint=mountpoint,
                    client_id=client_id,
                    receiver_id=receiver_id,
                    started_at=observed_at - uptime_seconds if uptime_seconds else observed_at,
                    last_seen_at=observed_at,
                    uptime_seconds=uptime_seconds,
                    latest_fix_quality=latest_fix_quality,
                    satellite_count=satellite_count,
                )
                db.add(row)
            else:
                row.username = username
                row.mountpoint = mountpoint
                row.client_id = client_id
                row.receiver_id = receiver_id
                row.last_seen_at = observed_at
                row.latest_fix_quality = latest_fix_quality
                row.satellite_count = satellite_count
                if uptime_seconds is not None:
                    if row.uptime_seconds is None or row.started_at is None:
                        row.started_at = observed_at - uptime_seconds
                    row.uptime_seconds = uptime_seconds

            position = _rover_history_position(client)
            if position is not None:
                row.latitude, row.longitude = position
                row.position_fix_quality = 4
                row.position_updated_at = observed_at
                row.position_satellite_count = satellite_count
                if auto_save_enabled_by_username.get(username.lower(), False):
                    last_point_at = latest_points_by_session.get(row.id)
                    if (
                        last_point_at is None
                        or observed_at - last_point_at >= ROVER_FIXED_POINT_INTERVAL_SECONDS
                    ):
                        if row.id is None:
                            await db.flush()
                        db.add(models.RoverFixedPoint(
                            session_id=row.id,
                            label="Điểm tự động",
                            latitude=position[0],
                            longitude=position[1],
                            fix_quality=4,
                            satellite_count=satellite_count,
                            recorded_at=observed_at,
                            saved_at=observed_at,
                            created_by="Tự động",
                        ))
                        latest_points_by_session[row.id] = observed_at

        for row in active_by_key.values():
            row.ended_at = observed_at

        await db.commit()


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
    auth_db: AsyncSession = Depends(get_auth_db),
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
        visible_serials = await _rtcm_visible_serials(auth_db, db, current_user)
        serials = sorted(
            str(r[0]) for r in active_serials_rs.all()
            if r and r[0] and (visible_serials is None or str(r[0]).strip().upper() in visible_serials)
        )

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
        await crud.log_system_event(
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
    if not auth.has_permission(current_user, auth.Permission.VIEW_ROVERS):
        raise HTTPException(
            status_code=403, 
            detail="Bạn chưa được cấp quyền quản lý Rover."
        )
    
    rovers = await crud.get_visible_rovers_for_user(db, devices_db, current_user)
    
    # 3. LÃ m giÃ u dữ liệu (XỬ LÝ HIỂN THỊ NHIỀU TRẠM)
    enriched_rovers = []
    
    # Cache devices để tránh query DB quá nhiều lần trong vòng lặp
    all_devices_result = await devices_db.execute(select(models.Device))
    all_devices = {d.serial.strip().upper(): d.name for d in all_devices_result.scalars().all() if d.serial}

    for rover in rovers:
        rover_dict = schemas.RoverAccountResponse.from_orm(rover).model_dump()
        
        # Xử lý base_serial có thể lÃ  "SN1,SN2"
        serials = (rover.base_serial or "").split(',')
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
    if not auth.can_edit_rovers(current_user):
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý Rover.")
    if update_req.auto_save_fixed_points is not None and current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được cấu hình tự lưu điểm Fixed.")

    # 2. Tìm trong DB
    rover = await crud.get_rover_by_username(db, username)
    if current_user.role == auth.Role.VIEWER and not rover and not update_req.selected_serials:
        raise HTTPException(status_code=403, detail="Viewer phải chọn ít nhất một trạm trong Rover Access.")
    if update_req.selected_serials and current_user.role != auth.Role.ADMIN:
        manageable_serials = await crud.get_rover_manageable_base_serials(devices_db, current_user, db)
        selected_serials = {
            str(serial).strip().upper()
            for serial in update_req.selected_serials
            if str(serial).strip()
        }
        if not selected_serials.issubset(manageable_serials):
            raise HTTPException(status_code=403, detail="Rover chỉ được gán vào trạm trong phạm vi của bạn.")
    if current_user.role != auth.Role.ADMIN and update_req.mountpoints is not None:
        serials_for_mps = update_req.selected_serials or ((rover.base_serial or "").split(",") if rover else [])
        if not await crud.rover_mountpoints_within_scope(devices_db, current_user, serials_for_mps, update_req.mountpoints):
            raise HTTPException(status_code=403, detail="Mountpoint không thuộc trạm được cấp cho bạn.")
    
    if rover:
        has_perm = await crud.check_rover_management_permission(rover, current_user, devices_db, db)
        if not has_perm:
            raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý Rover này.")
    
    # 3. TRƯỜNG HỢP: USER CHƯA CÓ TRONG DB (Unknown)
    if not rover:
        if not update_req.selected_serials and not update_req.mountpoints:
            raise HTTPException(status_code=400, detail="Vui lòng chọn trạm hoặc nhập Allowed Mountpoints để đồng bộ tài khoản này vào hệ thống.")
        
        # Tạo mới trong DB
        combined_serials = ",".join(update_req.selected_serials) if update_req.selected_serials else ""
        rover = await crud.create_rover_account(
            db,
            username=username,
            password=update_req.password or "123456", # Mặc định nếu không cung cấp
            base_serial=combined_serials,
            mountpoints=update_req.mountpoints,
            creator_id=current_user.id,
            max_connections=update_req.max_connections if update_req.max_connections is not None else 1,
            show_position=update_req.show_position if update_req.show_position is not None else True,
            rtcm_cycle_enabled=getattr(update_req, 'rtcm_cycle_enabled', False),
            rtcm_disconnect_interval_seconds=getattr(update_req, 'rtcm_disconnect_interval_seconds', 300),
            auto_save_fixed_points=update_req.auto_save_fixed_points if update_req.auto_save_fixed_points is not None else False,
            is_shared_with_managers=getattr(update_req, 'is_shared_with_managers', False)
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
        if update_req.auto_save_fixed_points is not None: update_data["auto_save_fixed_points"] = update_req.auto_save_fixed_points
        
        if update_data:
            # 🔥 FIX: Cập nhật base_serial nếu có gửi trạm đích
            if update_req.selected_serials:
                update_data["base_serial"] = ",".join(update_req.selected_serials)
                
            rover = await crud.update_rover_account(db, rover.id, update_data)

    # 5. ĐỒNG BỘ SANG CASTER (Đồng bộ trên toàn bộ Caster được cấu hình)
    try:
        hosts, config = _read_caster_config_from_env()
        port = int(config.get("port") or settings.CASTER_PORT or 2101)
        
        target_hosts = []
        if update_req.target_casters:
            target_hosts = [str(h).strip() for h in update_req.target_casters if str(h).strip()]
        
        if not target_hosts:
            if rover.target_casters:
                target_hosts = [h.strip() for h in rover.target_casters.split(";") if h.strip()]
            else:
                target_hosts = [str(h).strip() for h in hosts if h.strip()]

        if not target_hosts:
            default_host = str(config.get("host") or settings.CASTER_HOST or "").strip()
            if default_host:
                target_hosts = [default_host]

        # Deduplicate
        seen_hosts = set()
        target_hosts = [h for h in target_hosts if h and not (h in seen_hosts or seen_hosts.add(h))]

        for host in target_hosts:
            try:
                from app.caster_client import CasterClient
                caster = CasterClient(host, port)
                if update_req.expiry_days is not None:
                    duration_seconds = update_req.expiry_days * 86400
                else:
                    duration_seconds = await caster.get_user_remaining_seconds(username)
                    if duration_seconds is None:
                        if host in (rover.target_casters or "").split(";"):
                            raise ValueError(f"Không đọc được thời hạn Rover '{username}' trên Caster {host}")
                        duration_seconds = max(0, int(rover.expires_at - time.time())) if rover.expires_at else 315360000
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
                    logger.warning(f"Sync-by-username did not get caster confirmation for '{username}' on host {host}")
                
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
                        logger.warning(f"Sync-by-username could not confirm show_position toggle for '{username}' on host {host}")
            except Exception as host_err:
                logger.error(f"Failed to sync '{username}' to caster host {host}: {host_err}")

        # Cập nhật target_casters trong DB
        if target_hosts:
            db_target_casters = ";".join(target_hosts)
            if db_target_casters != rover.target_casters:
                rover = await crud.update_rover_account(db, rover.id, {"target_casters": db_target_casters})
    except Exception as e:
        logger.error(f"Sync Caster Error for '{username}': {e}")
        
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
    if not auth.can_edit_rovers(current_user):
        raise HTTPException(status_code=403, detail="Bạn không có quyền tạo tÃ i khoản Rover.")

    # 2. Kiểm tra trùng (như cũ)
    existing = await crud.get_rover_by_username(db, request.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username đã tồn tại.")

    # 2.1 Coordinator may create Rover for editable or view-only stations.
    if current_user.role == auth.Role.COORDINATOR:
        coord_visible_serials = await crud.get_coordinator_visible_base_serials(devices_db, current_user)
        
        # Every requested station must be in the coordinator's visible scope.
        for serial in request.selected_serials:
            if str(serial).strip().upper() not in coord_visible_serials:
                raise HTTPException(
                    status_code=403, 
                    detail=f"Bạn không có quyền quản lý trạm '{serial}'."
                )



    # 3. Chuẩn bị dữ liệu
    duration_seconds = request.expiry_days * 86400
    target_mountpoints = request.mountpoint_override.strip()
    
    selected_serials = list(request.selected_serials or [])

    if current_user.role == auth.Role.VIEWER:
        manageable_serials = await crud.get_rover_manageable_base_serials(devices_db, current_user, db)
        selected_serials_upper = {
            str(serial).strip().upper()
            for serial in selected_serials
            if str(serial).strip()
        }
        if not selected_serials_upper.issubset(manageable_serials):
            raise HTTPException(status_code=403, detail="Rover chỉ được gán vào trạm trong phạm vi Rover Access của bạn.")
        if not await crud.rover_mountpoints_within_scope(devices_db, current_user, selected_serials, target_mountpoints):
            raise HTTPException(status_code=403, detail="Mountpoint không thuộc trạm được cấp cho bạn.")
    
    # 3. Xác định danh sÃ¡ch Mountpoint hợp lệ (Đặc biệt cho Coordinator)
    if current_user.role == auth.Role.COORDINATOR:
        valid_mps = set()
        
        # 1. MP từ cÃ¡c trạm vật lÃ½ được chọn
        if request.selected_serials:
            for serial in request.selected_serials:
                dev = await crud.get_device_by_serial(devices_db, serial)
                if dev and str(dev.serial).strip().upper() in coord_visible_serials:
                    sc = _device_service_config(dev)
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
            # Fallback to all configured hosts to ensure complete sync
            env_hosts, env_cfg = _read_caster_config_from_env()
            if env_hosts:
                target_hosts = [str(h).strip() for h in env_hosts if h.strip()]
            else:
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
        show_position=request.show_position,
        target_casters=";".join(results_log), # Lưu chính xác những server đã tạo thành công
        rtcm_cycle_enabled=getattr(request, 'rtcm_cycle_enabled', False),
        rtcm_disconnect_interval_seconds=getattr(request, 'rtcm_disconnect_interval_seconds', 300),
        rtcm_disconnect_duration_seconds=getattr(request, 'rtcm_disconnect_duration_seconds', 10),
        expires_at=int(time.time()) + duration_seconds,
        is_shared_with_managers=getattr(request, 'is_shared_with_managers', False)
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
    if current_user.role == auth.Role.COORDINATOR and device_in_db.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Trạm này chỉ được xem")
    
    # ==================== BƯỚC 2: PARSE PAYLOAD ====================
    payload = config_request.payload
    sensor_type = payload.get("sensor_type")
    params = payload.get("params", {})
    mode = payload.get("mode")
    method = params.get("base_setup_method")
    gnss_options = params.get("gnss_options") if isinstance(params, dict) else None
    
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
                        "ip": _get_env_preferring_dotenv("AUTO_BASE_CORS_IP", "14.238.1.125"),
                        "port": int(_get_env_preferring_dotenv("AUTO_BASE_CORS_PORT", "2101")),
                        "user": _get_env_preferring_dotenv("AUTO_BASE_CORS_USER", ""),
                        "password": _get_env_preferring_dotenv("AUTO_BASE_CORS_PASS", ""),
                        "mountpoint": _get_env_preferring_dotenv("AUTO_BASE_CORS_MOUNTPOINT", "VRS.105M3"),
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
                    sensor_type, survey_duration, survey_accuracy, gnss_options
                )
                logging.info(f"Built Survey-In commands for {serial}: {survey_duration}s, {survey_accuracy}m")
            elif method == "FIXED_LLA":
                coords = params.get('coords')
                if not coords or not all(k in coords for k in ['lat', 'lon', 'alt']):
                    raise ValueError("Missing or incomplete coordinates")
                
                accuracy = params.get('accuracy', 10.0)
                commands_to_send = command_builder.build_base_fixed_lla_command(
                    sensor_type, coords['lat'], coords['lon'], coords['alt'], accuracy, gnss_options
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
        
        await crud.log_system_event(
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
    old_service_cfg = _device_service_config(device_in_db)
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
    service_config_payload = _preserve_relay_source_config(
        serial, old_service_cfg, payload.model_dump()
    )
    try:
        device_in_db.service_config = service_config_payload
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
        "payload": service_config_payload
    }
    try:
        response = await send_command_to_pi(serial, pi_command)
        await crud.log_system_event(
            db=db,
            type="info",
            message=f"Service config updated for '{device_in_db.name}' by {current_user.username}",
            user_id=current_user.id,
            serial=serial
        )
        return {"status": "service_config_sent_and_saved", "channel": response.get('channel', 'unknown')}
    except HTTPException as e:
        raise HTTPException(status_code=503, detail=f"Config saved to DB, but failed to send command to Pi: {e.detail}")

@app.post("/api/devices/{serial}/configured-relay-power")
async def set_configured_relay_power(
    serial: str,
    request: schemas.ConfiguredRelayPowerRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Thiết bị không tồn tại")
    if not serial.strip().upper().startswith("RELAY_"):
        raise HTTPException(status_code=404, detail="Thiết bị không phải Relay cấu hình")
    if not auth.has_permission(current_user, auth.Permission.MANAGE_STATION_MODE):
        raise HTTPException(status_code=403, detail="Bạn không có quyền bật/tắt trạm Relay.")
    if current_user.role == auth.Role.COORDINATOR and device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý thiết bị này.")

    service_config = _device_service_config(device)
    if service_config.get("agent_station_power_control") is not True:
        raise HTTPException(status_code=409, detail="Relay chưa hỗ trợ điều khiển bật/tắt toàn trạm.")

    try:
        response = await send_command_to_pi(
            serial,
            {"command": "SET_CONFIGURED_RELAY_ACTIVE", "payload": {"active": request.active}},
        )
    except HTTPException as exc:
        raise HTTPException(status_code=503, detail=f"Không gửi được lệnh đến Relay: {exc.detail}")

    service_config["agent_station_enabled"] = request.active
    device.service_config = service_config
    await db.commit()
    await db.refresh(device)
    await ui_manager.broadcast({"type": "status_update", "data": schemas.Device.from_orm(device).model_dump()})
    await crud.log_system_event(
        db=db,
        type="info" if request.active else "warning",
        message=f"Relay '{device.name or serial}' {'enabled' if request.active else 'fully disabled'} by {current_user.username}",
        user_id=current_user.id,
        serial=serial,
    )
    return {"status": "command_sent", "active": request.active, "channel": response.get("channel", "unknown")}


@app.post("/api/devices/{serial}/reset")
async def reset_pi_device(
    serial: str,
    db: AsyncSession = Depends(get_db), 
    current_user: models.User = Depends(auth.get_current_user)
):
    # 1. Lấy thiết bị từ DB TRƯỚC để kiểm tra sự tồn tại
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Thiết bị không tồn tại")

    # 2. Phân quyền: Cần EDIT_SERVICE_CONFIG. Nếu là Coordinator thì phải là người sở hữu.
    if not auth.has_permission(current_user, auth.Permission.EDIT_SERVICE_CONFIG):
        raise HTTPException(status_code=403, detail="Bạn không có quyền khởi động lại thiết bị.")

    if current_user.role == auth.Role.COORDINATOR and device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý thiết bị này.")

    # 3. Gửi lệnh xuống thiết bị (Chỉ gửi khi đã qua cửa kiểm tra an ninh)
    try:
        await send_command_to_pi(serial, {"command": "REBOOT_DEVICE", "payload": {}})
    except Exception as e:
        # Log lỗi nhưng không chặn dòng chảy để cập nhật DB cho UI phản hồi
        logging.error(f"Failed to send reboot command to {serial}: {e}")

    # 4. Cập nhật DB ngay lập tức
    device.status = "rebooting"
    
    await db.commit()
    
    # 🔥 FIX: Invalidate sync cache on reset
    mqtt_handler.invalidate_device_cache(serial)
    
    # 5. Broadcast để UI cập nhật ngay
    await ui_manager.broadcast({
        "type": "status_update",
        "data": schemas.Device.from_orm(device).model_dump()
    })

    logging.info(f"User '{current_user.username}' sent REBOOT command to Pi '{serial}'")
    await crud.log_system_event(
        db=db,
        type="warning",
        message=f"Device '{device.name}' rebooted by {current_user.username}",
        user_id=current_user.id,
        serial=serial
    )
    return {"status": "reboot_command_sent"}

@app.post("/api/devices/{serial}/hard-reset")
async def hard_reset_pi_device(
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
        logging.error(f"Failed to send hard-reset command to {serial}: {e}")

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

    logging.info(f"User '{current_user.username}' sent HARD-RESET command to Pi '{serial}'")
    await crud.log_system_event(
        db=db,
        type="warning",
        message=f"Device '{device.name}' hard-reset (factory reset) by {current_user.username}",
        user_id=current_user.id,
        serial=serial
    )
    return {"status": "hard_reset_command_sent"}

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
    # Serialize deletion with MQTT status writes. If a retained relay status
    # was already writing, it finishes first and this delete wins; if deletion
    # wins, the metadata tombstone is visible before later writes proceed.
    async with mqtt_handler.mqtt_db_write_lock:
        await db.delete(device)
        await db.commit()
        await mqtt_handler.mark_device_metadata_missing(serial)
        async with device_update_buffer_lock:
            retained_updates = [
                item for item in device_update_buffer
                if str((item or {}).get("serial") or "") != str(serial)
            ]
            device_update_buffer.clear()
            device_update_buffer.extend(retained_updates)

    # Thêm: Dọn dẹp in-memory MQTT cache để tránh thiết bị bị zombie hóa
    for cache in [
        mqtt_handler.device_write_cache,
        mqtt_handler.device_status_cache,
        mqtt_handler.device_sync_cache,
        mqtt_handler.device_name_cache,
        mqtt_handler.device_telemetry_cache,
        latest_device_telemetry_cache,
    ]:
        cache.pop(serial, None)
    
    # Xóa tất cả các keys liên quan đến serial (như config) trong cache đồng bộ
    for key in list(mqtt_handler.device_sync_cache.keys()):
        if str(key).startswith(f"{serial}_"):
            mqtt_handler.device_sync_cache.pop(key, None)

    # Serialize deletion with an already-extracted status broadcast for the
    # same station. If status started first, delete is sent last; if delete
    # started first, the retained status sees the tombstone and is dropped.
    async with _get_ui_status_emit_lock(serial):
        async with pending_ui_status_lock:
            pending_ui_status_updates.pop(serial, None)

        logging.info(f"User '{current_user.username}' deleted device '{serial}' from list (not reset)")

        # Broadcast để các client khác cập nhật UI
        await ui_manager.broadcast({
            "type": "device_deleted",
            "serial": serial
        })

    
    await crud.log_system_event(
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
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được khóa trạm")
    device = await crud.get_device_by_serial(db, serial)
    try:
        response = await send_command_to_pi(serial, {"command": "LOCK_DEVICE", "payload": {}})
        await crud.log_system_event(
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
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được mở khóa trạm")
    device = await crud.get_device_by_serial(db, serial)
    try:
        response = await send_command_to_pi(serial, {"command": "UNLOCK_DEVICE", "payload": {}})
        await crud.log_system_event(
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

# === OPERATIONS CENTER / V1 ADDITIVE OPS APIS ===
def _split_ops_tokens(value: str | None) -> list[str]:
    return [token.strip() for token in re.split(r"[;,\s\n]+", str(value or "")) if token.strip()]


def _device_total_bps(device: models.Device) -> int:
    status_data = device.ntrip_status if isinstance(device.ntrip_status, dict) else {}
    total = 0
    has_detailed_bps = False
    for key in ("server1_bps", "server2_bps", "rtcm_input_bps", "rtcm_in_bps"):
        value = status_data.get(key)
        if value is not None:
            has_detailed_bps = True
            total += int(value or 0)
    for server_data in status_data.values():
        if isinstance(server_data, dict):
            has_detailed_bps = True
            total += int(server_data.get("bps") or 0)
    return total if has_detailed_bps else int(getattr(device, "bps", 0) or 0)


def _device_stream_alerts(device: models.Device, now_ts: int) -> list[dict]:
    alerts = []
    service = device.service_config if isinstance(device.service_config, dict) else {}
    ntrip = device.ntrip_status if isinstance(device.ntrip_status, dict) else {}
    raw_status = str(device.status or "offline").lower()
    stale_seconds = now_ts - int(device.timestamp or 0)

    if raw_status == "offline":
        alerts.append({
            "id": f"device-offline-{device.serial}",
            "severity": "critical",
            "type": "device_offline",
            "title": "Station offline",
            "message": f"{device.name or device.serial} is offline",
            "device_serial": device.serial,
            "created_at": int(device.timestamp or now_ts),
        })
    elif stale_seconds > 900:
        alerts.append({
            "id": f"device-stale-{device.serial}",
            "severity": "warning",
            "type": "device_stale",
            "title": "Station stale",
            "message": f"{device.name or device.serial} has no heartbeat for {stale_seconds // 60} minutes",
            "device_serial": device.serial,
            "created_at": int(device.timestamp or now_ts),
        })

    for idx in (1, 2):
        if not service.get(f"server{idx}_enabled"):
            continue
        if service.get(f"server{idx}_stream_on_demand") or service.get("stream_on_demand"):
            continue
        server_status = ntrip.get(f"server{idx}") if isinstance(ntrip, dict) else {}
        bps = int(server_status.get("bps") or 0) if isinstance(server_status, dict) else 0
        mountpoint = service.get(f"mountpoint{idx}") or f"S{idx}"
        if raw_status in ("online", "streaming", "starting") and bps <= 0:
            alerts.append({
                "id": f"stream-silent-{device.serial}-{idx}",
                "severity": "warning",
                "type": "stream_silent",
                "title": "Stream has no data",
                "message": f"{device.name or device.serial} has no outgoing data on {mountpoint}",
                "device_serial": device.serial,
                "mountpoint": mountpoint,
                "created_at": now_ts,
            })

    if device.is_synced is False:
        alerts.append({
            "id": f"device-unsynced-{device.serial}",
            "severity": "info",
            "type": "device_unsynced",
            "title": "Config pending sync",
            "message": f"{device.name or device.serial} has pending configuration changes",
            "device_serial": device.serial,
            "created_at": now_ts,
        })

    # Antenna health checks based on agent reported antenna_status or rtcm_input_bps
    if raw_status in ("online", "streaming", "sleep", "starting"):
        system_info = device.system_info if isinstance(device.system_info, dict) else {}
        antenna_status = system_info.get("antenna_status") or ntrip.get("antenna_status")
        if isinstance(antenna_status, dict) and antenna_status.get("state") == "FAULT":
            alerts.append({
                "id": f"device-antenna-fault-{device.serial}",
                "severity": "warning",
                "type": "antenna_fault",
                "title": "Antenna Fault",
                "message": antenna_status.get("message") or f"{device.name or device.serial} has an antenna fault.",
                "device_serial": device.serial,
                "created_at": now_ts,
            })
        else:
            rtcm_in_bps = int(ntrip.get("rtcm_input_bps") or 0)
            if 0 < rtcm_in_bps < 100:
                alerts.append({
                    "id": f"device-antenna-low-bps-{device.serial}",
                    "severity": "warning",
                    "type": "antenna_fault",
                    "title": "Low RTCM Input Rate",
                    "message": f"{device.name or device.serial} has extremely low RTCM input rate ({rtcm_in_bps} B/s). Possible antenna fault.",
                    "device_serial": device.serial,
                    "created_at": now_ts,
                })

    return alerts


async def _ops_visible_devices(
    devices_db: AsyncSession,
    auth_db: AsyncSession,
    current_user: models.User,
) -> list[models.Device]:
    if current_user.role == auth.Role.ADMIN:
        return await crud.get_all_devices(devices_db)
    if current_user.role == auth.Role.COORDINATOR:
        serials = await crud.get_coordinator_visible_base_serials(devices_db, current_user)
        rs = await devices_db.execute(select(models.Device).where(func.upper(models.Device.serial).in_(serials)).order_by(models.Device.name))
        return rs.scalars().all()
    serials = await crud.get_viewer_visible_base_serials(auth_db, devices_db, current_user)
    if not serials:
        return []
    rs = await devices_db.execute(select(models.Device).where(func.upper(models.Device.serial).in_(serials)).order_by(models.Device.name))
    return rs.scalars().all()


async def _ops_visible_rovers(
    auth_db: AsyncSession,
    devices_db: AsyncSession,
    current_user: models.User,
) -> list[models.RoverAccount]:
    if not auth.can_view_rover_page(current_user):
        return []
    return await crud.get_visible_rovers_for_user(auth_db, devices_db, current_user)
    
async def _ops_cached_rover_status(usernames: set[str] | None = None) -> list[dict]:
    statuses = []
    async with global_caster_cache_lock:
        for cache_data in (await _get_all_caster_cache()).values():
            host = cache_data.get("host", "unknown")
            last_update = int(cache_data.get("last_update") or time.time())
            clients = cache_data.get("clients", []) or []
            client_map: dict[str, list[dict]] = {}
            for client in clients:
                uname = str(client.get("username") or "").strip().lower()
                if uname:
                    client_map.setdefault(uname, []).append(client)
            for user in cache_data.get("users", []) or []:
                username = str(user.get("username") or "").strip()
                key = username.lower()
                if usernames and key not in usernames:
                    continue
                active_clients = client_map.get(key, [])
                statuses.append({
                    "username": username,
                    "server": host,
                    "mountpoints": user.get("mountpoints", []),
                    "status": user.get("status", 0),
                    "nclient": user.get("nclient", 0),
                    "maxclient": user.get("maxclient", 0),
                    "active": len(active_clients) > 0 or int(user.get("nclient") or 0) > 0,
                    "active_connections": active_clients,
                    "last_seen_at": last_update,
                })
    return statuses


def _ops_event_payload(log: models.ActivityLog) -> dict:
    return {
        "id": log.id,
        "timestamp": log.timestamp,
        "severity": log.type,
        "type": log.type,
        "message": log.message,
        "icon": log.icon,
        "actor_user_id": log.user_id,
        "device_serial": log.device_serial,
        "ip_address": log.ip_address,
    }


async def _ops_events_query(
    db: AsyncSession,
    limit: int = 50,
    severity: str | None = None,
    device_serial: str | None = None,
    q: str | None = None,
    timestamp_from: int | None = None,
    timestamp_to: int | None = None,
) -> list[dict]:
    stmt = select(models.ActivityLog)
    if severity:
        stmt = stmt.where(models.ActivityLog.type == severity)
    if device_serial:
        stmt = stmt.where(models.ActivityLog.device_serial == device_serial)
    if q:
        stmt = stmt.where(models.ActivityLog.message.ilike(f"%{q}%"))
    if timestamp_from is not None:
        stmt = stmt.where(models.ActivityLog.timestamp >= timestamp_from)
    if timestamp_to is not None:
        stmt = stmt.where(models.ActivityLog.timestamp < timestamp_to)
    stmt = stmt.order_by(models.ActivityLog.timestamp.desc()).limit(max(1, min(int(limit or 50), 200)))
    result = await db.execute(stmt)
    return [_ops_event_payload(log) for log in result.scalars().all()]


async def _ops_alert_states(auth_db: AsyncSession) -> dict:
    settings_data = await crud.get_system_settings(auth_db)
    states = settings_data.get("ops_alert_states", {})
    return states if isinstance(states, dict) else {}


def _apply_ops_alert_states(alerts: list[dict], states: dict) -> list[dict]:
    for alert in alerts:
        saved = states.get(alert.get("id"), {}) if isinstance(states, dict) else {}
        if not isinstance(saved, dict):
            saved = {}
        alert["state"] = saved.get("state") or "open"
        alert["acknowledged_by"] = saved.get("acknowledged_by")
        alert["acknowledged_at"] = saved.get("acknowledged_at")
        alert["resolved_by"] = saved.get("resolved_by")
        alert["resolved_at"] = saved.get("resolved_at")
        alert["reason"] = saved.get("reason")
    return alerts


@app.get("/api/ops/summary")
async def get_ops_summary(
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    now_ts = int(time.time())
    devices = await _ops_visible_devices(devices_db, auth_db, current_user)
    rovers = await _ops_visible_rovers(auth_db, devices_db, current_user)
    alerts = await _build_ops_alerts(auth_db, devices_db, current_user, devices=devices, rovers=rovers)
    active_alerts = [a for a in alerts if a.get("state") != "resolved"]
    events = await _ops_events_query(devices_db, limit=8)
    caster_hosts = []
    async with global_caster_cache_lock:
        for cache_data in (await _get_all_caster_cache()).values():
            last_update = int(cache_data.get("last_update") or 0)
            caster_hosts.append({
                "host": cache_data.get("host", "unknown"),
                "port": cache_data.get("port"),
                "users": len(cache_data.get("users", []) or []),
                "clients": len(cache_data.get("clients", []) or []),
                "error": cache_data.get("error"),
                "last_update": last_update,
                "stale": bool(last_update and now_ts - last_update > 180),
            })

    online_devices = [d for d in devices if str(d.status or "").lower() != "offline"]
    expiring_rovers = [
        r for r in rovers
        if r.expires_at and now_ts < int(r.expires_at) <= now_ts + 14 * 86400
    ]
    blocked_result = await auth_db.execute(select(models.RoverBlocklist))
    blocked_count = len(blocked_result.scalars().all())

    return {
        "timestamp": now_ts,
        "devices": {
            "total": len(devices),
            "online": len(online_devices),
            "offline": len(devices) - len(online_devices),
            "unsynced": len([d for d in devices if d.is_synced is False]),
            "total_bps": sum(_device_total_bps(d) for d in devices),
        },
        "rovers": {
            "total": len(rovers),
            "active": len([r for r in rovers if r.is_active]),
            "expiring_14d": len(expiring_rovers),
            "blocked": blocked_count if current_user.role == auth.Role.ADMIN else 0,
        },
        "casters": caster_hosts,
        "alerts": {
            "total": len(active_alerts),
            "critical": len([a for a in active_alerts if a.get("severity") == "critical"]),
            "warning": len([a for a in active_alerts if a.get("severity") == "warning"]),
            "info": len([a for a in active_alerts if a.get("severity") == "info"]),
            "acknowledged": len([a for a in active_alerts if a.get("state") == "acknowledged"]),
        },
        "recent_events": events,
    }


async def _build_ops_alerts(
    auth_db: AsyncSession,
    devices_db: AsyncSession,
    current_user: models.User,
    devices: list[models.Device] | None = None,
    rovers: list[models.RoverAccount] | None = None,
) -> list[dict]:
    now_ts = int(time.time())
    devices = devices if devices is not None else await _ops_visible_devices(devices_db, auth_db, current_user)
    rovers = rovers if rovers is not None else await _ops_visible_rovers(auth_db, devices_db, current_user)
    alerts: list[dict] = []

    for device in devices:
        alerts.extend(_device_stream_alerts(device, now_ts))

    for rover in rovers:
        if rover.expires_at:
            exp = int(rover.expires_at)
            if exp <= now_ts:
                severity, alert_type, title = "critical", "rover_expired", "Rover expired"
            elif exp <= now_ts + 14 * 86400:
                severity, alert_type, title = "warning", "rover_expiring", "Rover expiring"
            else:
                continue
            alerts.append({
                "id": f"{alert_type}-{rover.username}",
                "severity": severity,
                "type": alert_type,
                "title": title,
                "message": f"{rover.username} expires at {time.strftime('%Y-%m-%d %H:%M', time.localtime(exp))}",
                "rover_username": rover.username,
                "created_at": now_ts,
            })

    if current_user.role == auth.Role.ADMIN:
        blocklist_rs = await auth_db.execute(select(models.RoverBlocklist).order_by(models.RoverBlocklist.blocked_at.desc()).limit(50))
        for item in blocklist_rs.scalars().all():
            alerts.append({
                "id": f"rover-blocked-{item.id}",
                "severity": "warning",
                "type": "rover_blocked",
                "title": "Rover blocked",
                "message": f"{item.username} blocked on {item.mountpoint or 'all mountpoints'}",
                "rover_username": item.username,
                "mountpoint": item.mountpoint,
                "created_at": item.blocked_at,
            })

    async with global_caster_cache_lock:
        for cache_data in (await _get_all_caster_cache()).values():
            last_update = int(cache_data.get("last_update") or 0)
            has_error = bool(cache_data.get("error"))
            stale = last_update and now_ts - last_update > 180
            if has_error or stale:
                alerts.append({
                    "id": f"caster-{cache_data.get('host', 'unknown')}",
                    "severity": "critical" if has_error else "warning",
                    "type": "caster_error" if has_error else "caster_stale",
                    "title": "Caster needs attention",
                    "message": cache_data.get("error") or f"Caster cache stale for {(now_ts - last_update) // 60} minutes",
                    "caster_host": cache_data.get("host", "unknown"),
                    "created_at": last_update or now_ts,
                })

    states = await _ops_alert_states(auth_db)
    alerts = _apply_ops_alert_states(alerts, states)
    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    return sorted(alerts, key=lambda a: (severity_rank.get(a.get("severity"), 3), -int(a.get("created_at") or 0)))


@app.get("/api/ops/alerts")
async def get_ops_alerts(
    severity: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    device_serial: Optional[str] = Query(None),
    rover_username: Optional[str] = Query(None),
    caster_host: Optional[str] = Query(None),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    alerts = await _build_ops_alerts(auth_db, devices_db, current_user)
    if state:
        alerts = [a for a in alerts if a.get("state") == state]
    else:
        alerts = [a for a in alerts if a.get("state") != "resolved"]
    if severity:
        alerts = [a for a in alerts if a.get("severity") == severity]
    if type:
        alerts = [a for a in alerts if a.get("type") == type]
    if device_serial:
        alerts = [a for a in alerts if a.get("device_serial") == device_serial]
    if rover_username:
        wanted = rover_username.strip().lower()
        alerts = [a for a in alerts if str(a.get("rover_username") or "").strip().lower() == wanted]
    if caster_host:
        wanted_host = caster_host.strip().lower()
        alerts = [a for a in alerts if str(a.get("caster_host") or "").strip().lower() == wanted_host]
    return {"timestamp": int(time.time()), "count": len(alerts), "items": alerts}


async def _set_ops_alert_workflow_state(
    alert_id: str,
    new_state: str,
    payload: dict,
    auth_db: AsyncSession,
    devices_db: AsyncSession,
    current_user: models.User,
) -> dict:
    alerts = await _build_ops_alerts(auth_db, devices_db, current_user)
    alert = next((item for item in alerts if item.get("id") == alert_id), None)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found or outside your scope")

    now_ts = int(time.time())
    states = await _ops_alert_states(auth_db)
    saved = states.get(alert_id, {}) if isinstance(states.get(alert_id, {}), dict) else {}
    reason = payload.get("reason") if isinstance(payload, dict) else None
    state_payload = dict(saved)
    state_payload["state"] = new_state
    state_payload["reason"] = reason or saved.get("reason")
    if new_state == "acknowledged":
        state_payload["acknowledged_by"] = current_user.username
        state_payload["acknowledged_at"] = now_ts
    if new_state == "resolved":
        state_payload["resolved_by"] = current_user.username
        state_payload["resolved_at"] = now_ts
        state_payload.setdefault("acknowledged_by", current_user.username)
        state_payload.setdefault("acknowledged_at", now_ts)
    states[alert_id] = state_payload
    await crud.update_system_settings(auth_db, {"ops_alert_states": states})
    await crud.log_system_event(
        devices_db,
        "warning" if new_state == "acknowledged" else "success",
        f"Ops alert {new_state}: {alert.get('title') or alert_id}",
    )
    alert.update(state_payload)
    return {"status": "success", "alert": alert}


@app.post("/api/ops/alerts/{alert_id}/ack")
async def acknowledge_ops_alert(
    alert_id: str,
    payload: dict = Body(default={}),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    return await _set_ops_alert_workflow_state(alert_id, "acknowledged", payload, auth_db, devices_db, current_user)


@app.post("/api/ops/alerts/{alert_id}/resolve")
async def resolve_ops_alert(
    alert_id: str,
    payload: dict = Body(default={}),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    return await _set_ops_alert_workflow_state(alert_id, "resolved", payload, auth_db, devices_db, current_user)


@app.get("/api/ops/events")
async def get_ops_events(
    limit: int = Query(50, ge=1, le=200),
    severity: Optional[str] = Query(None),
    device_serial: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    return {"items": await _ops_events_query(db, limit=limit, severity=severity, device_serial=device_serial, q=q)}


@app.get("/api/rovers/validate")
async def validate_rover_username(
    username: str = Query(..., min_length=1),
    mountpoints: Optional[str] = Query(None),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_ROVERS)),
):
    clean_username = username.strip()
    existing = await crud.get_rover_by_username(auth_db, clean_username)
    requested_mps = {mp.lower() for mp in _split_ops_tokens(mountpoints)}
    scoped_rovers = await _ops_visible_rovers(auth_db, devices_db, current_user)
    scoped_names = {str(r.username).strip().lower() for r in scoped_rovers}
    if current_user.role != auth.Role.ADMIN and existing and clean_username.lower() not in scoped_names:
        raise HTTPException(status_code=403, detail="Rover is outside your scope")

    caster_statuses = await _ops_cached_rover_status({clean_username.lower()})
    mountpoint_conflicts = []
    if requested_mps:
        for rover in scoped_rovers:
            if existing and rover.id == existing.id:
                continue
            rover_mps = {mp.lower() for mp in _split_ops_tokens(rover.mountpoints)}
            overlap = sorted(requested_mps & rover_mps)
            if overlap:
                mountpoint_conflicts.append({"username": rover.username, "mountpoints": overlap})

    return {
        "username": clean_username,
        "available": not existing and not caster_statuses,
        "exists_db": bool(existing),
        "exists_caster": bool(caster_statuses),
        "caster_records": caster_statuses,
        "mountpoint_conflicts": mountpoint_conflicts,
    }


@app.post("/api/rovers/bulk-renew")
async def bulk_renew_rovers(
    request: schemas.RoverBulkRenewRequest,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    if not auth.can_edit_rovers(current_user):
        raise HTTPException(status_code=403, detail="You cannot renew rover accounts")
    names = {name.strip().lower() for name in request.usernames if name.strip()}
    if not names:
        raise HTTPException(status_code=400, detail="usernames is required")
    scoped_rovers = await _ops_visible_rovers(auth_db, devices_db, current_user)
    matched = [rover for rover in scoped_rovers if str(rover.username).strip().lower() in names]
    new_expiry = int(time.time()) + int(request.expiry_days) * 86400
    updated = []
    skipped = sorted(names - {str(r.username).strip().lower() for r in matched})

    for rover in matched:
        rover.expires_at = new_expiry
        rover.is_active = True
        updated.append(rover.username)
    await auth_db.commit()

    await crud.log_system_event(
        db=devices_db,
        type="success",
        message=f"{current_user.username} renewed {len(updated)} rover account(s) for {request.expiry_days} days",
        user_id=current_user.id,
    )
    return {
        "status": "ok",
        "expiry_days": request.expiry_days,
        "expires_at": new_expiry,
        "updated": updated,
        "skipped": skipped,
        "sync_caster": False,
    }


@app.get("/api/rovers/export/csv")
async def export_rovers_csv(
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_ROVERS)),
):
    rovers = await _ops_visible_rovers(auth_db, devices_db, current_user)
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["username", "base_serial", "mountpoints", "target_casters", "active", "max_connections", "expires_at", "created_at"])
    for rover in rovers:
        writer.writerow([
            rover.username,
            rover.base_serial or "",
            rover.mountpoints or "",
            rover.target_casters or "",
            "yes" if rover.is_active else "no",
            rover.max_connections,
            rover.expires_at or "",
            rover.created_at or "",
        ])
    return Response(
        content=stream.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=rovers-export.csv"},
    )


@app.get("/api/rovers/history/export/csv")
async def export_rover_session_history_csv(
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_ROVERS)),
):
    """Export every saved session in the viewer's Rover scope."""
    query = select(models.RoverSessionHistory)
    if current_user.role != auth.Role.ADMIN:
        rovers = await _ops_visible_rovers(auth_db, devices_db, current_user)
        usernames = {str(rover.username).strip().lower() for rover in rovers}
        query = query.where(func.lower(models.RoverSessionHistory.username).in_(usernames))
    query = query.order_by(models.RoverSessionHistory.last_seen_at.desc())

    def safe(value):
        if value is None:
            return ""
        if isinstance(value, str) and re.match(r"^\s*[=+\-@]", value):
            return "'" + value
        return value

    async def csv_stream():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "username", "server", "mountpoint", "receiver_id", "client_id",
            "started_at_unix", "last_seen_at_unix", "ended_at_unix",
            "latitude", "longitude", "position_updated_at_unix", "google_maps_url",
            "position_fix_quality",
        ])
        yield "\ufeff" + buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)

        async with auth_session_scope() as db:
            result = await db.stream_scalars(query)
            count = 0
            async for session in result:
                has_position = (
                    session.position_fix_quality == 4
                    and session.latitude is not None
                    and session.longitude is not None
                )
                maps_url = (
                    f"https://www.google.com/maps/search/?api=1&query={session.latitude},{session.longitude}"
                    if has_position else ""
                )
                writer.writerow([
                    safe(session.username), safe(session.server), safe(session.mountpoint),
                    safe(session.receiver_id), safe(session.client_id),
                    safe(session.started_at), safe(session.last_seen_at), safe(session.ended_at),
                    session.latitude if has_position else "", session.longitude if has_position else "",
                    safe(session.position_updated_at) if has_position else "", maps_url,
                    session.position_fix_quality if has_position else "",
                ])
                count += 1
                if count % 256 == 0:
                    yield buffer.getvalue()
                    buffer.seek(0)
                    buffer.truncate(0)
            if buffer.tell():
                yield buffer.getvalue()

    return StreamingResponse(
        csv_stream(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=rover-session-history.csv"},
    )


@app.get("/api/ops/export/options")
async def get_ops_export_options(
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EXPORT_DATA)),
):
    devices = await _ops_visible_devices(devices_db, auth_db, current_user)
    rovers = await _ops_visible_rovers(auth_db, devices_db, current_user)
    names = {str(rover.username).casefold(): str(rover.username) for rover in rovers if rover.username}
    if current_user.role == auth.Role.ADMIN:
        result = await auth_db.execute(select(models.RoverSessionHistory.username).distinct())
        for name in result.scalars():
            if name:
                names.setdefault(str(name).casefold(), str(name))
    return {
        "rovers": sorted(names.values(), key=str.casefold),
        "devices": [{"serial": d.serial, "name": d.name} for d in devices],
    }


@app.post("/api/ops/export/xlsx")
async def export_ops_xlsx(
    request: dict = Body(...),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EXPORT_DATA)),
):
    """Build a readable workbook from the requested, permission-scoped report."""
    kind = str(request.get("kind") or "").strip()
    rover_name = str(request.get("rover_username") or "").strip()
    device_serial = str(request.get("device_serial") or "").strip()
    if len(rover_name) > 128 or len(device_serial) > 128:
        raise HTTPException(status_code=400, detail="Bộ lọc quá dài")

    def when(value):
        try:
            return datetime.fromtimestamp(int(value), timezone(timedelta(hours=7))).replace(tzinfo=None) if value else None
        except (TypeError, ValueError, OverflowError):
            return None

    def words(value):
        if isinstance(value, (list, tuple, set)):
            return "; ".join(str(item) for item in value if item is not None)
        return "" if value is None else str(value)

    rows = []
    subtitle = f"Xuất lúc {when(time.time()).strftime('%d/%m/%Y %H:%M:%S')} (GMT+7)"
    if kind == "devices":
        devices = await _ops_visible_devices(devices_db, auth_db, current_user)
        if device_serial:
            devices = [d for d in devices if str(d.serial).casefold() == device_serial.casefold()]
            if not devices:
                raise HTTPException(status_code=404, detail="Không tìm thấy trạm trong phạm vi được phép")
        coordinator_ids = {d.user_id for d in devices if d.user_id}
        coordinators = {}
        if coordinator_ids:
            result = await auth_db.execute(select(models.User.id, models.User.username).where(models.User.id.in_(coordinator_ids)))
            coordinators = dict(result.all())

        def config(device, name, key):
            source = getattr(device, name, None)
            return source.get(key, "") if isinstance(source, dict) else ""

        def base_coord(device, key):
            base = device.base_config if isinstance(device.base_config, dict) else {}
            coords = base.get("coords")
            return coords.get(key, base.get(key, "")) if isinstance(coords, dict) else base.get(key, "")

        def stream_bps(device, index):
            status = device.ntrip_status if isinstance(device.ntrip_status, dict) else {}
            server = status.get(f"server{index}")
            value = server.get("bps", 0) if isinstance(server, dict) else status.get(f"server{index}_bps", 0)
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        def stream_connected(device, index):
            status = device.ntrip_status if isinstance(device.ntrip_status, dict) else {}
            server = status.get(f"server{index}")
            return bool(isinstance(server, dict) and server.get("connected")) or bool(stream_bps(device, index))

        def ntrip_version(device, index):
            value = str(config(device, "service_config", f"ntrip_version{index}") or 1).removeprefix("v")
            return f"v{value}" if "." in value else f"v{value}.0"

        station_fields = {
            "serial": ("Số sê-ri", lambda d: d.serial),
            "name": ("Tên trạm", lambda d: d.name),
            "status": ("Trạng thái", lambda d: d.status),
            "updated_at": ("Cập nhật lúc", lambda d: when(d.timestamp)),
            "version": ("Phiên bản", lambda d: d.version),
            "chip": ("Loại chip", lambda d: d.detected_chip_type),
            "coordinator": ("Điều phối viên", lambda d: coordinators.get(d.user_id, "")),
            "synced": ("Đã đồng bộ", lambda d: d.is_synced),
            "locked": ("Đã khóa", lambda d: d.is_locked),
            "ntrip_connected": ("NTRIP kết nối", lambda d: d.ntrip_connected),
            "total_bps": ("Tổng lưu lượng (B/s)", lambda d: (stream_bps(d, 1) or 0) + (stream_bps(d, 2) or 0)),
            "base_method": ("Chế độ Base", lambda d: config(d, "base_config", "base_setup_method")),
            "base_lat": ("Vĩ độ Base", lambda d: base_coord(d, "lat")),
            "base_lon": ("Kinh độ Base", lambda d: base_coord(d, "lon")),
            "base_alt": ("Cao độ Base (m)", lambda d: base_coord(d, "alt")),
            "base_accuracy": ("Độ chính xác Base (m)", lambda d: config(d, "base_config", "accuracy") or config(d, "base_config", "acc")),
            "server1_enabled": ("Bật luồng 1", lambda d: config(d, "service_config", "server1_enabled")),
            "server1_version": ("NTRIP phiên bản 1", lambda d: ntrip_version(d, 1)),
            "server1_host": ("Caster 1", lambda d: config(d, "service_config", "serverhost1") or config(d, "service_config", "host1")),
            "server1_port": ("Cổng 1", lambda d: config(d, "service_config", "port1") or config(d, "service_config", "serverport1")),
            "server1_mountpoint": ("Mountpoint 1", lambda d: config(d, "service_config", "mountpoint1") or config(d, "service_config", "mp1")),
            "server1_bps": ("Lưu lượng 1 (B/s)", lambda d: stream_bps(d, 1)),
            "server1_username": ("Tài khoản luồng 1", lambda d: config(d, "service_config", "username1") or config(d, "service_config", "user1")),
            "server1_connected": ("Kết nối luồng 1", lambda d: stream_connected(d, 1)),
            "server2_enabled": ("Bật luồng 2", lambda d: config(d, "service_config", "server2_enabled")),
            "server2_version": ("NTRIP phiên bản 2", lambda d: ntrip_version(d, 2)),
            "server2_host": ("Caster 2", lambda d: config(d, "service_config", "serverhost2") or config(d, "service_config", "host2")),
            "server2_port": ("Cổng 2", lambda d: config(d, "service_config", "port2") or config(d, "service_config", "serverport2")),
            "server2_mountpoint": ("Mountpoint 2", lambda d: config(d, "service_config", "mountpoint2") or config(d, "service_config", "mp2")),
            "server2_bps": ("Lưu lượng 2 (B/s)", lambda d: stream_bps(d, 2)),
            "server2_username": ("Tài khoản luồng 2", lambda d: config(d, "service_config", "username2") or config(d, "service_config", "user2")),
            "server2_connected": ("Kết nối luồng 2", lambda d: stream_connected(d, 2)),
            "rtcm_enabled": ("Bật RTCM client", lambda d: config(d, "service_config", "rtcm_enabled")),
            "rtcm_server": ("RTCM server", lambda d: config(d, "service_config", "rtcmserver1")),
            "rtcm_port": ("RTCM port", lambda d: config(d, "service_config", "rtcmport1")),
            "rtcm_mountpoint": ("RTCM mountpoint", lambda d: config(d, "service_config", "rtcmmountpoint1")),
            "rtcm_username": ("RTCM tài khoản", lambda d: config(d, "service_config", "rtcmusername1")),
        }
        selected = request.get("device_fields")
        if selected is None:
            selected = ["serial", "name", "status", "updated_at", "coordinator", "synced"]
        if not isinstance(selected, list) or not selected or len(selected) > len(station_fields) or any(not isinstance(key, str) or key not in station_fields for key in selected):
            raise HTTPException(status_code=400, detail="Thông số trạm không hợp lệ")
        selected = list(dict.fromkeys(selected))
        headers = [station_fields[key][0] for key in selected]
        rows = [[station_fields[key][1](device) for key in selected] for device in devices]
        title = "BÁO CÁO TRẠM BASE"
        subtitle += f" · {len(rows)} trạm" + (f" · {device_serial}" if device_serial else "")
    elif kind in {"rovers", "rover-history"}:
        rovers = await _ops_visible_rovers(auth_db, devices_db, current_user)
        allowed_names = {str(rover.username).casefold() for rover in rovers}
        if rover_name and current_user.role != auth.Role.ADMIN and rover_name.casefold() not in allowed_names:
            raise HTTPException(status_code=404, detail="Không tìm thấy Rover trong phạm vi được phép")
        if kind == "rovers":
            selected_rovers = [r for r in rovers if not rover_name or str(r.username).casefold() == rover_name.casefold()]
            if rover_name and not selected_rovers:
                raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản Rover")
            headers = ["Tài khoản", "Số sê-ri Base", "Mountpoint", "Caster", "Hoạt động", "Kết nối tối đa", "Hết hạn", "Ngày tạo"]
            rows = [[r.username, r.base_serial, words(r.mountpoints), words(r.target_casters), r.is_active, r.max_connections, when(r.expires_at), when(r.created_at)] for r in selected_rovers]
            title = "DANH SÁCH ROVER"
        else:
            query = select(models.RoverSessionHistory)
            if rover_name:
                query = query.where(func.lower(models.RoverSessionHistory.username) == rover_name.lower())
            elif current_user.role != auth.Role.ADMIN:
                query = query.where(func.lower(models.RoverSessionHistory.username).in_(allowed_names))
            result = await auth_db.execute(query.order_by(models.RoverSessionHistory.last_seen_at.desc()))
            sessions = result.scalars().all()
            if rover_name and not sessions:
                raise HTTPException(status_code=404, detail="Rover này chưa có phiên được lưu")
            headers = ["Tài khoản", "Caster", "Mountpoint", "Receiver", "Bắt đầu", "Cập nhật cuối", "Kết thúc", "Vĩ độ cuối", "Kinh độ cuối", "Ghi nhận vị trí", "Google Maps"]
            rows = [[s.username, s.server, s.mountpoint, s.receiver_id, when(s.started_at), when(s.last_seen_at), when(s.ended_at), s.latitude if s.position_fix_quality == 4 else None, s.longitude if s.position_fix_quality == 4 else None, when(s.position_updated_at) if s.position_fix_quality == 4 else None, f"https://www.google.com/maps/search/?api=1&query={s.latitude},{s.longitude}" if s.position_fix_quality == 4 and s.latitude is not None and s.longitude is not None else ""] for s in sessions]
            title = "LỊCH SỬ PHIÊN ROVER"
        subtitle += f" · {len(rows)} dòng" + (f" · Rover {rover_name}" if rover_name else "")
    elif kind == "caster-users":
        data = await get_caster_users(auth_db=auth_db, devices_db=devices_db, current_user=current_user)
        headers = ["Tài khoản", "Caster", "Mountpoint", "Kết nối", "Giới hạn", "Trong DB", "Bị chặn", "Cập nhật lúc"]
        rows = [[u.username, u.server, words(u.mountpoints), u.nclient, u.maxclient, u.in_db, u.is_blocked, when(u.last_update)] for u in data.items]
        title = "ROVER TRÊN CASTER"
    elif kind == "blocklist":
        result = await auth_db.execute(select(models.RoverBlocklist).order_by(models.RoverBlocklist.blocked_at.desc()))
        headers = ["Tài khoản", "Mountpoint", "Bị chặn lúc", "Người chặn", "Lý do"]
        rows = [[item.username, item.mountpoint, when(item.blocked_at), item.blocked_by, item.reason] for item in result.scalars()]
        title = "DANH SÁCH ROVER BỊ CHẶN"
    elif kind in {"alerts", "resolved-alerts"}:
        alerts = await _build_ops_alerts(auth_db, devices_db, current_user)
        desired_state = "resolved" if kind == "resolved-alerts" else None
        alerts = [item for item in alerts if (item.get("state") == "resolved") == bool(desired_state)]
        headers = ["Mức độ", "Loại", "Tiêu đề", "Nội dung", "Trạm", "Rover", "Caster", "Trạng thái", "Thời gian"]
        rows = [[a.get("severity"), a.get("type"), a.get("title"), a.get("message"), a.get("device_serial"), a.get("rover_username"), a.get("caster_host"), a.get("state"), when(a.get("created_at"))] for a in alerts]
        title = "CẢNH BÁO ĐÃ XỬ LÝ CÒN TỒN TẠI" if desired_state else "CẢNH BÁO HIỆN TẠI"
    elif kind == "events":
        events = await _ops_events_query(devices_db, limit=200)
        headers = ["Thời gian", "Mức độ", "Sự kiện", "Trạm", "Người dùng ID", "IP"]
        rows = [[when(e.get("timestamp")), e.get("severity"), e.get("message"), e.get("device_serial"), e.get("actor_user_id"), e.get("ip_address")] for e in events]
        title = "200 SỰ KIỆN GẦN NHẤT"
    elif kind == "casters":
        summary = await get_ops_summary(auth_db=auth_db, devices_db=devices_db, current_user=current_user)
        headers = ["Máy chủ", "Cổng", "Tài khoản", "Kết nối", "Lỗi", "Dữ liệu cũ", "Cập nhật lúc"]
        rows = [[c.get("host"), c.get("port"), c.get("users"), c.get("clients"), c.get("error"), c.get("stale"), when(c.get("last_update"))] for c in summary["casters"]]
        title = "TÌNH TRẠNG CASTER"
    elif kind == "users":
        users = await crud.get_all_users(auth_db)
        headers = ["ID", "Tài khoản", "Họ tên", "Vai trò", "Hoạt động", "Số sê-ri trạm", "Quyền trạm", "Xem Rover", "Tạo Rover", "Tạo tài khoản", "Ngày tạo"]
        def assigned(value):
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except (TypeError, ValueError):
                    return []
            return value if isinstance(value, list) else []
        for user in users:
            view_only = {str(serial).strip() for serial in assigned(user.view_only_bases)}
            assignments = [(serial, "Được sửa") for serial in assigned(user.allowed_bases) if str(serial).strip() not in view_only]
            assignments.extend((serial, "Chỉ xem") for serial in sorted(view_only))
            for serial, access in assignments or [("", "")]:
                rows.append([user.id, user.username, user.full_name, user.role, user.is_active, serial, access, user.can_view_rovers, user.can_create_rovers, user.can_create_users, when(user.created_at)])
        title = "TÀI KHOẢN HỆ THỐNG"
    elif kind == "templates":
        settings_data = await crud.get_system_settings(auth_db)
        headers = ["Tên mẫu", "Phần", "Thông số", "Giá trị", "Người tạo", "Ngày tạo"]
        def parameters(value, prefix=""):
            if isinstance(value, dict):
                for key, nested in value.items():
                    yield from parameters(nested, f"{prefix}.{key}" if prefix else str(key))
            elif isinstance(value, list):
                for index, nested in enumerate(value, 1):
                    yield from parameters(nested, f"{prefix} [{index}]")
            else:
                yield prefix, words(value)
        for template in settings_data.get("device_config_templates", []) or []:
            if not isinstance(template, dict):
                continue
            for section, label in (("base_config", "Base"), ("service_config", "Dịch vụ")):
                config = template.get(section) or {}
                if isinstance(config, dict):
                    for key, value in parameters(config):
                        rows.append([template.get("name"), label, key, value, template.get("created_by"), when(template.get("created_at"))])
            if not (template.get("base_config") or template.get("service_config")):
                rows.append([template.get("name"), "", "", "", template.get("created_by"), when(template.get("created_at"))])
        title = "MẪU CẤU HÌNH TRẠM"
    else:
        raise HTTPException(status_code=400, detail="Loại dữ liệu không hợp lệ")

    if kind not in {"devices", "rovers", "rover-history"}:
        subtitle += f" · {len(rows)} dòng"
    workbook = await asyncio.to_thread(make_workbook, title, subtitle, headers, rows)
    return Response(
        content=workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{kind}-{time.strftime("%Y-%m-%d")}.xlsx"'},
    )


ROVER_HISTORY_LOCAL_TIMEZONE = timezone(timedelta(hours=7))
ROVER_HISTORY_PAGE_SIZE = 10


def _rover_history_day_bounds(day: date) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, tzinfo=ROVER_HISTORY_LOCAL_TIMEZONE)
    return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())


def _rover_history_overlap_filters(start_at: int, end_at: int):
    session = models.RoverSessionHistory
    return (
        func.coalesce(session.started_at, session.last_seen_at) < end_at,
        func.coalesce(session.ended_at, session.last_seen_at) > start_at,
    )


async def _rover_history_user_scope(
    auth_db: AsyncSession,
    devices_db: AsyncSession,
    current_user: models.User,
) -> set[str] | None:
    if current_user.role == auth.Role.ADMIN:
        return None
    rovers = await _ops_visible_rovers(auth_db, devices_db, current_user)
    return {str(rover.username).strip().casefold() for rover in rovers if rover.username}


@app.get("/api/rovers/activity/daily")
async def get_rover_activity_daily(
    date_from: date = Query(...),
    date_to: date = Query(...),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_ROVERS)),
):
    if date_to < date_from:
        raise HTTPException(status_code=400, detail="Ngày kết thúc phải từ ngày bắt đầu trở đi")
    if (date_to - date_from).days > 365:
        raise HTTPException(status_code=400, detail="Khoảng ngày tối đa là 366 ngày")

    visible_usernames = await _rover_history_user_scope(auth_db, devices_db, current_user)
    daily_receivers = {
        date_from + timedelta(days=offset): set()
        for offset in range((date_to - date_from).days + 1)
    }
    if visible_usernames is None or visible_usernames:
        range_start, _ = _rover_history_day_bounds(date_from)
        _, range_end = _rover_history_day_bounds(date_to)
        stmt = select(
            models.RoverSessionHistory.receiver_id,
            models.RoverSessionHistory.started_at,
            models.RoverSessionHistory.last_seen_at,
            models.RoverSessionHistory.ended_at,
        ).where(
            *_rover_history_overlap_filters(range_start, range_end),
            models.RoverSessionHistory.receiver_id.is_not(None),
            func.trim(models.RoverSessionHistory.receiver_id) != "",
        )
        if visible_usernames is not None:
            stmt = stmt.where(func.lower(models.RoverSessionHistory.username).in_(visible_usernames))
        result = await auth_db.execute(stmt)
        for receiver_id, started_at, last_seen_at, ended_at in result.all():
            receiver_key = str(receiver_id or "").strip().casefold()
            if not receiver_key:
                continue
            try:
                first_active_at = int(started_at if started_at is not None else last_seen_at)
                last_active_at = int(ended_at if ended_at is not None else last_seen_at)
                first_active_day = datetime.fromtimestamp(
                    first_active_at,
                    ROVER_HISTORY_LOCAL_TIMEZONE,
                ).date()
                last_active_day = datetime.fromtimestamp(
                    max(first_active_at, last_active_at - 1),
                    ROVER_HISTORY_LOCAL_TIMEZONE,
                ).date()
            except (TypeError, ValueError, OverflowError, OSError):
                continue
            cursor = max(date_from, first_active_day)
            last_day = min(date_to, last_active_day)
            while cursor <= last_day:
                daily_receivers[cursor].add(receiver_key)
                cursor += timedelta(days=1)

    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "days": [
            {"date": day.isoformat(), "machine_count": len(receivers)}
            for day, receivers in daily_receivers.items()
        ],
    }


@app.get("/api/rovers/activity/day")
async def get_rover_activity_day(
    activity_date: date = Query(..., alias="date"),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_ROVERS)),
):
    visible_usernames = await _rover_history_user_scope(auth_db, devices_db, current_user)
    start_at, end_at = _rover_history_day_bounds(activity_date)
    items: dict[str, dict] = {}
    if visible_usernames is not None and not visible_usernames:
        return {"date": activity_date.isoformat(), "machine_count": 0, "machines": []}

    session = models.RoverSessionHistory
    stmt = select(
        session.id,
        session.username, session.mountpoint, session.receiver_id,
    ).where(
        *_rover_history_overlap_filters(start_at, end_at),
        session.receiver_id.is_not(None),
        func.trim(session.receiver_id) != "",
    )
    if visible_usernames is not None:
        stmt = stmt.where(func.lower(session.username).in_(visible_usernames))
    result = await auth_db.execute(stmt)
    receiver_by_session: dict[int, str] = {}
    for session_id, username, mountpoint, receiver_id in result.all():
        receiver_key = str(receiver_id or "").strip().casefold()
        if not receiver_key:
            continue
        item = items.setdefault(receiver_key, {
            "receiver_id": str(receiver_id).strip(),
            "usernames": set(),
            "session_count": 0,
            "point_count": 0,
        })
        if username:
            item["usernames"].add(str(username))
        item["session_count"] += 1
        receiver_by_session[session_id] = receiver_key

    if receiver_by_session:
        point_stmt = select(
            models.RoverFixedPoint.session_id,
            func.count(models.RoverFixedPoint.id),
        ).where(
            models.RoverFixedPoint.session_id.in_(list(receiver_by_session)),
            models.RoverFixedPoint.recorded_at >= start_at,
            models.RoverFixedPoint.recorded_at < end_at,
        ).group_by(models.RoverFixedPoint.session_id)
        point_result = await auth_db.execute(point_stmt)
        for session_id, point_count in point_result.all():
            item = items.get(receiver_by_session.get(session_id, ""))
            if item is not None:
                item["point_count"] += int(point_count or 0)

    machines = [
        {
            **item,
            "usernames": sorted(item["usernames"], key=str.casefold),
        }
        for item in sorted(items.values(), key=lambda value: value["receiver_id"].casefold())
    ]
    return {"date": activity_date.isoformat(), "machine_count": len(machines), "machines": machines}


@app.get("/api/rovers/activity/track")
async def get_rover_activity_track(
    activity_date: date = Query(..., alias="date"),
    receiver_id: str = Query(..., min_length=1, max_length=256),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_ROVERS)),
):
    if not receiver_id.strip():
        raise HTTPException(status_code=400, detail="Receiver không được để trống")
    visible_usernames = await _rover_history_user_scope(auth_db, devices_db, current_user)
    if visible_usernames is not None and not visible_usernames:
        return {"date": activity_date.isoformat(), "receiver_id": receiver_id, "points": []}

    start_at, end_at = _rover_history_day_bounds(activity_date)
    session = models.RoverSessionHistory
    session_query = select(session.id, session.username, session.mountpoint).where(
        *_rover_history_overlap_filters(start_at, end_at),
        func.lower(func.trim(session.receiver_id)) == receiver_id.strip().casefold(),
    )
    if visible_usernames is not None:
        session_query = session_query.where(func.lower(session.username).in_(visible_usernames))
    session_result = await auth_db.execute(session_query)
    session_info = {
        session_id: {"username": username, "mountpoint": mountpoint}
        for session_id, username, mountpoint in session_result.all()
    }
    if not session_info:
        return {"date": activity_date.isoformat(), "receiver_id": receiver_id, "points": []}

    point_result = await auth_db.execute(
        select(
            models.RoverFixedPoint.id,
            models.RoverFixedPoint.latitude,
            models.RoverFixedPoint.longitude,
            models.RoverFixedPoint.recorded_at,
            models.RoverFixedPoint.satellite_count,
            models.RoverFixedPoint.session_id,
        ).where(
            models.RoverFixedPoint.session_id.in_(list(session_info)),
            models.RoverFixedPoint.recorded_at >= start_at,
            models.RoverFixedPoint.recorded_at < end_at,
        ).order_by(models.RoverFixedPoint.recorded_at, models.RoverFixedPoint.id)
    )
    points = [
        {
            "id": point_id,
            "lat": latitude,
            "lon": longitude,
            "recorded_at": recorded_at,
            "satellites": satellite_count,
            "username": session_info[session_id]["username"],
            "mountpoint": session_info[session_id]["mountpoint"],
            "session_id": session_id,
        }
        for point_id, latitude, longitude, recorded_at, satellite_count, session_id in point_result.all()
    ]
    return {"date": activity_date.isoformat(), "receiver_id": receiver_id, "points": points}


@app.get("/api/rovers/{username}/history")
async def get_rover_history(
    username: str,
    activity_date: date | None = Query(None, alias="date"),
    page: int = Query(1, ge=1),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_ROVERS)),
):
    rover = await crud.get_rover_by_username(auth_db, username)
    scoped = await _ops_visible_rovers(auth_db, devices_db, current_user)
    scoped_names = {str(r.username).strip().lower() for r in scoped}
    if current_user.role != auth.Role.ADMIN and username.strip().lower() not in scoped_names:
        raise HTTPException(status_code=403, detail="Rover is outside your scope")
    date_start, date_end = _rover_history_day_bounds(activity_date) if activity_date else (None, None)
    events = await _ops_events_query(
        devices_db,
        limit=100,
        q=username,
        timestamp_from=date_start,
        timestamp_to=date_end,
    )
    statuses = await _ops_cached_rover_status({username.strip().lower()})
    session_query = select(models.RoverSessionHistory).where(
        func.lower(models.RoverSessionHistory.username) == username.strip().lower()
    )
    if date_start is not None and date_end is not None:
        session_query = session_query.where(*_rover_history_overlap_filters(date_start, date_end))
    total_sessions_result = await auth_db.execute(
        select(func.count()).select_from(session_query.order_by(None).subquery())
    )
    total_sessions = int(total_sessions_result.scalar_one() or 0)
    receiver_count_result = await auth_db.execute(
        select(func.count(func.distinct(func.lower(func.trim(models.RoverSessionHistory.receiver_id)))))
        .where(
            func.lower(models.RoverSessionHistory.username) == username.strip().lower(),
            models.RoverSessionHistory.receiver_id.is_not(None),
            func.trim(models.RoverSessionHistory.receiver_id) != "",
            *(_rover_history_overlap_filters(date_start, date_end) if date_start is not None else ()),
        )
    )
    receiver_count = int(receiver_count_result.scalar_one() or 0)
    total_pages = (total_sessions + ROVER_HISTORY_PAGE_SIZE - 1) // ROVER_HISTORY_PAGE_SIZE
    page = min(page, max(total_pages, 1))
    sessions_result = await auth_db.execute(
        session_query.order_by(models.RoverSessionHistory.last_seen_at.desc())
        .offset((page - 1) * ROVER_HISTORY_PAGE_SIZE)
        .limit(ROVER_HISTORY_PAGE_SIZE)
    )
    session_rows = sessions_result.scalars().all()
    session_ids = [session.id for session in session_rows]
    points_by_session: dict[int, list[dict]] = {session_id: [] for session_id in session_ids}
    if session_ids:
        point_query = select(models.RoverFixedPoint).where(
            models.RoverFixedPoint.session_id.in_(session_ids)
        )
        if date_start is not None and date_end is not None:
            point_query = point_query.where(
                models.RoverFixedPoint.recorded_at >= date_start,
                models.RoverFixedPoint.recorded_at < date_end,
            )
        point_result = await auth_db.execute(
            point_query.order_by(models.RoverFixedPoint.recorded_at, models.RoverFixedPoint.id)
        )
        for point in point_result.scalars().all():
            points_by_session[point.session_id].append({
                "id": point.id,
                "label": point.label,
                "lat": point.latitude,
                "lon": point.longitude,
                "fix_quality": point.fix_quality,
                "satellites": point.satellite_count,
                "recorded_at": point.recorded_at,
                "saved_at": point.saved_at,
                "created_by": point.created_by,
            })

    sessions = []
    for session in session_rows:
        has_complete_position = (
            session.position_fix_quality in (None, 4)
            and session.latitude is not None
            and session.longitude is not None
            and (
                date_start is None
                or date_start <= int(session.position_updated_at or 0) < date_end
            )
        )
        sessions.append({
            "id": session.id,
            "server": session.server,
            "mountpoint": session.mountpoint,
            "client_id": session.client_id,
            "receiver_id": session.receiver_id,
            "started_at": session.started_at,
            "last_seen_at": session.last_seen_at,
            "ended_at": session.ended_at,
            "uptime_seconds": session.uptime_seconds,
            "current_fix_quality": session.latest_fix_quality,
            "satellites": session.satellite_count,
            "last_position": {
                "lat": session.latitude,
                "lon": session.longitude,
                "recorded_at": session.position_updated_at,
                "fix_quality": session.position_fix_quality if session.position_fix_quality is not None else 4,
                "satellites": session.position_satellite_count,
            } if has_complete_position else None,
            "points": points_by_session[session.id],
        })
    return {
        "username": username,
        "in_db": bool(rover),
        "account": {"username": rover.username} if rover else None,
        "auto_save_fixed_points": bool(getattr(rover, "auto_save_fixed_points", False)) if rover else False,
        "fixed_point_auto_interval_seconds": ROVER_FIXED_POINT_INTERVAL_SECONDS,
        "caster_status": statuses,
        "sessions": sessions,
        "date": activity_date.isoformat() if activity_date else None,
        "receiver_count": receiver_count,
        "total_sessions": total_sessions,
        "page": page,
        "page_size": ROVER_HISTORY_PAGE_SIZE,
        "total_pages": total_pages,
        "events": events,
    }


@app.delete("/api/rovers/{username}/history/{session_id}")
async def delete_rover_history_session(
    username: str,
    session_id: int,
    auth_db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_ROVERS)),
):
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được xóa phiên Rover")
    result = await auth_db.execute(
        select(models.RoverSessionHistory).where(
            models.RoverSessionHistory.id == session_id,
            func.lower(models.RoverSessionHistory.username) == username.strip().lower(),
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên Rover")
    if session.ended_at is None:
        raise HTTPException(status_code=409, detail="Chỉ xóa được phiên đã kết thúc")

    await auth_db.delete(session)
    await auth_db.commit()
    return {"status": "deleted", "session_id": session_id}


@app.delete("/api/rovers/{username}/history/{session_id}/points/{point_id}")
async def delete_rover_history_point(
    username: str,
    session_id: int,
    point_id: int,
    auth_db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_ROVERS)),
):
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được xóa điểm Rover")

    result = await auth_db.execute(
        select(models.RoverFixedPoint)
        .join(
            models.RoverSessionHistory,
            models.RoverFixedPoint.session_id == models.RoverSessionHistory.id,
        )
        .where(
            models.RoverFixedPoint.id == point_id,
            models.RoverFixedPoint.session_id == session_id,
            func.lower(models.RoverSessionHistory.username) == username.strip().lower(),
        )
    )
    point = result.scalar_one_or_none()
    if point is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy điểm đo trong phiên Rover")

    await auth_db.delete(point)
    await auth_db.commit()
    return {"status": "deleted", "point_id": point_id, "session_id": session_id}


@app.post("/api/rovers/sync-status")
async def sync_rover_status(
    request: schemas.RoverSyncStatusRequest,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_ROVERS)),
):
    scoped = await _ops_visible_rovers(auth_db, devices_db, current_user)
    allowed = {str(r.username).strip().lower() for r in scoped}
    requested = {name.strip().lower() for name in (request.usernames or []) if name.strip()}
    target_names = requested & allowed if requested else allowed
    statuses = await _ops_cached_rover_status(target_names)
    return {
        "timestamp": int(time.time()),
        "requested": sorted(requested),
        "count": len(statuses),
        "items": statuses,
    }


@app.get("/api/devices/alerts")
async def get_device_alerts(
    serial: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    devices = await _ops_visible_devices(devices_db, auth_db, current_user)
    if serial:
        devices = [d for d in devices if d.serial == serial]
    now_ts = int(time.time())
    alerts = []
    for device in devices:
        alerts.extend(_device_stream_alerts(device, now_ts))
    if severity:
        alerts = [a for a in alerts if a.get("severity") == severity]
    if type:
        alerts = [a for a in alerts if a.get("type") == type]
    return {"timestamp": now_ts, "count": len(alerts), "items": alerts}


@app.get("/api/devices/{serial}/history")
async def get_device_history(
    serial: str,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    visible = await _ops_visible_devices(devices_db, auth_db, current_user)
    visible_map = {d.serial: d for d in visible}
    if serial not in visible_map:
        raise HTTPException(status_code=403, detail="Device is outside your scope")
    events = await _ops_events_query(devices_db, limit=100, device_serial=serial)
    device = visible_map[serial]
    return {
        "serial": serial,
        "current": schemas.Device.from_orm(device).model_dump(),
        "events": events,
        "alerts": _device_stream_alerts(device, int(time.time())),
    }


@app.post("/api/devices/bulk-action")
async def bulk_device_action(
    request: schemas.DeviceBulkActionRequest,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    action = str(request.action or "").strip().lower()
    if action not in {"lock", "unlock", "reset"}:
        raise HTTPException(status_code=400, detail="action must be lock, unlock, or reset")
    if action in {"lock", "unlock"} and not auth.has_permission(current_user, auth.Permission.MANAGE_USERS):
        raise HTTPException(status_code=403, detail="Missing permission for lock/unlock")
    if action in {"lock", "unlock"} and current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Only admin can lock or unlock stations")
    if action == "reset" and not auth.has_permission(current_user, auth.Permission.EDIT_SERVICE_CONFIG):
        raise HTTPException(status_code=403, detail="Missing permission for reset")

    visible = await _ops_visible_devices(devices_db, auth_db, current_user)
    visible_map = {d.serial: d for d in visible}
    results = []
    for serial in [s.strip() for s in request.serials if s.strip()]:
        device = visible_map.get(serial)
        if not device:
            results.append({"serial": serial, "status": "skipped", "detail": "outside scope or not found"})
            continue
        if current_user.role == auth.Role.COORDINATOR and device.user_id != current_user.id:
            results.append({"serial": serial, "status": "skipped", "detail": "view only station"})
            continue
        try:
            if action == "lock":
                response = await send_command_to_pi(serial, {"command": "LOCK_DEVICE", "payload": {}})
                device.is_locked = True
                status_text = "locked"
            elif action == "unlock":
                response = await send_command_to_pi(serial, {"command": "UNLOCK_DEVICE", "payload": {}})
                device.is_locked = False
                status_text = "unlocked"
            else:
                response = await send_command_to_pi(serial, {"command": "REBOOT_DEVICE", "payload": {}})
                device.status = "rebooting"
                status_text = "reset"
            await devices_db.commit()
            await devices_db.refresh(device)
            await ui_manager.broadcast({"type": "status_update", "data": schemas.Device.from_orm(device).model_dump()})
            results.append({"serial": serial, "status": status_text, "channel": response.get("channel", "unknown")})
        except Exception as exc:
            results.append({"serial": serial, "status": "error", "detail": str(exc)})

    await crud.log_system_event(
        db=devices_db,
        type="warning" if action == "reset" else "info",
        message=f"{current_user.username} executed bulk device action '{action}' on {len(results)} station(s)",
        user_id=current_user.id,
    )
    return {"action": action, "results": results}


@app.get("/api/device-config-templates")
async def get_device_config_templates(
    auth_db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_CONFIG)),
):
    settings_data = await crud.get_system_settings(auth_db)
    templates = settings_data.get("device_config_templates", [])
    if not isinstance(templates, list):
        templates = []
    return {"items": templates}


@app.post("/api/device-config-templates")
async def create_device_config_template(
    request: schemas.DeviceConfigTemplateCreateRequest,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_SERVICE_CONFIG)),
):
    settings_data = await crud.get_system_settings(auth_db)
    templates = settings_data.get("device_config_templates", [])
    if not isinstance(templates, list):
        templates = []
    template = {
        "id": str(uuid.uuid4()),
        "name": request.name.strip(),
        "description": request.description or "",
        "base_config": request.base_config or {},
        "service_config": request.service_config or {},
        "created_by": current_user.username,
        "created_at": int(time.time()),
    }
    templates.append(template)
    await crud.update_system_settings(auth_db, {"device_config_templates": templates})
    await crud.log_system_event(
        db=devices_db,
        type="info",
        message=f"{current_user.username} created station config template '{template['name']}'",
        user_id=current_user.id,
    )
    return template


@app.post("/api/devices/apply-template")
async def apply_device_config_template(
    request: schemas.DeviceApplyTemplateRequest,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_SERVICE_CONFIG)),
):
    settings_data = await crud.get_system_settings(auth_db)
    templates = settings_data.get("device_config_templates", [])
    template = next((t for t in templates if str(t.get("id")) == request.template_id), None) if isinstance(templates, list) else None
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    visible = await _ops_visible_devices(devices_db, auth_db, current_user)
    visible_map = {d.serial: d for d in visible}
    results = []
    for serial in [s.strip() for s in request.serials if s.strip()]:
        device = visible_map.get(serial)
        if not device:
            results.append({"serial": serial, "status": "skipped", "detail": "outside scope or not found"})
            continue
        if current_user.role == auth.Role.COORDINATOR and device.user_id != current_user.id:
            results.append({"serial": serial, "status": "skipped", "detail": "view only station"})
            continue
        if request.apply_base_config and isinstance(template.get("base_config"), dict) and template.get("base_config"):
            device.base_config = template["base_config"]
        if request.apply_service_config and isinstance(template.get("service_config"), dict) and template.get("service_config"):
            device.service_config = _preserve_relay_source_config(
                serial, _device_service_config(device), template["service_config"]
            )
        device.is_synced = False
        results.append({"serial": serial, "status": "applied"})
    await devices_db.commit()

    for serial in [r["serial"] for r in results if r.get("status") == "applied"]:
        device = visible_map.get(serial)
        if device:
            await devices_db.refresh(device)
            await ui_manager.broadcast({"type": "status_update", "data": schemas.Device.from_orm(device).model_dump()})

    await crud.log_system_event(
        db=devices_db,
        type="info",
        message=f"{current_user.username} applied template '{template.get('name')}' to {len([r for r in results if r.get('status') == 'applied'])} station(s)",
        user_id=current_user.id,
    )
    return {"template": template, "results": results}

# === WEBSOCKETS ===
async def _authenticate_ui_websocket(websocket: WebSocket) -> Optional[dict]:
    try:
        await websocket.accept()
        auth_message = await asyncio.wait_for(websocket.receive_json(), timeout=5)
        token = auth_message.get("token") if isinstance(auth_message, dict) else None
        message_type = auth_message.get("type") if isinstance(auth_message, dict) else None
        if message_type != "auth" or not token:
            await websocket.close(code=1008, reason="Missing authentication token")
            return None

        payload = auth.decode_token(token)
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid websocket token")

        async with auth_session_scope() as auth_db:
            user = await crud.get_user_by_username(auth_db, username=username)
            if not user or not user.is_active:
                raise HTTPException(status_code=401, detail="Invalid websocket user")

            context = {
                "user_id": user.id,
                "username": user.username,
                "role": user.role,
                "allowed_serials": None,
            }

            if user.role == auth.Role.COORDINATOR:
                async with device_session_scope() as devices_db:
                    context["allowed_serials"] = await crud.get_coordinator_visible_base_serials(devices_db, user)
            elif user.role == auth.Role.VIEWER:
                async with device_session_scope() as devices_db:
                    context["allowed_serials"] = await crud.get_viewer_visible_base_serials(auth_db, devices_db, user)

            return context
    except asyncio.TimeoutError:
        logger.warning(f"Rejected UI websocket from {websocket.client}: auth timeout")
        await websocket.close(code=1008, reason="Authentication timeout")
        return None
    except Exception as exc:
        logger.warning(f"Rejected UI websocket from {websocket.client}: {exc}")
        await websocket.close(code=1008, reason="Authentication failed")
        return None

@app.websocket("/ws/updates")
async def ui_websocket_endpoint(websocket: WebSocket):
    context = await _authenticate_ui_websocket(websocket)
    if not context:
        return

    await ui_manager.connect(websocket, context, accepted=True)
    # logging.info("New UI client connected.") 
    try:
        while True: 
            # Nhận tin nhắn từ client và xử lý lệnh start_rtcm/stop_rtcm
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                action = msg.get("action")
                if action in ("start_rtcm", "stop_rtcm"):
                    mountpoint = msg.get("mountpoint")
                    client_id = msg.get("client_instance_id", msg.get("identifier", "ws-client"))
                    ttl_seconds = int(msg.get("ttl_seconds", 90))
                    
                    if not mountpoint:
                        await websocket.send_json({"status": "error", "error": "mountpoint is required"})
                        continue
                        
                    if action == "start_rtcm":
                        resp = await _do_ntrip_wake(mountpoint, client_id, ttl_seconds)
                        await websocket.send_json({
                            "status": "ok",
                            "action": "start_rtcm",
                            "mountpoint": mountpoint,
                            "response": getattr(resp, "body", b"").decode("utf-8")
                        })
                    elif action == "stop_rtcm":
                        force = bool(msg.get("force", False))
                        address = msg.get("caster_address") or msg.get("address") or msg.get("host") or msg.get("ip") or msg.get("caster_host")
                        resp = await _do_ntrip_stop(mountpoint, client_id, force=force, requested_address=address)
                        await websocket.send_json({
                            "status": "ok",
                            "action": "stop_rtcm",
                            "mountpoint": mountpoint,
                            "response": getattr(resp, "body", b"").decode("utf-8")
                        })
            except Exception:
                pass
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
                previous = unique_updates.get(serial)
                item_order = (
                    int(item.get("status_revision") or 0),
                    float(item.get("server_received_at") or item.get("timestamp") or 0),
                )
                previous_order = (
                    int(previous.get("status_revision") or 0),
                    float(previous.get("server_received_at") or previous.get("timestamp") or 0),
                ) if previous else (-1, -1.0)
                if item_order >= previous_order:
                    unique_updates[serial] = item

    final_batch = list(unique_updates.values())
    duplicates_count = snapshot_size - len(final_batch)

    if final_batch:
        try:
            # Order UPSERT against explicit device deletion and re-check the
            # tombstone only after acquiring that ordering lock.
            async with mqtt_handler.mqtt_db_write_lock:
                final_batch = [
                    item for item in final_batch
                    if not mqtt_handler.device_was_deleted(item.get("serial"))
                ]
                if not final_batch:
                    return
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
async def pi_websocket_endpoint(websocket: WebSocket, serial: str, token: str = Query(None)):
    import os
    enforce_auth = os.getenv("ENFORCE_AGENT_AUTH", "true").lower() != "false"
    # Never leave an unaccepted socket queued behind a saturated DB pool.  The
    # short-lived metadata cache removes the per-reconnect SELECT in steady state.
    try:
        metadata = await asyncio.wait_for(
            mqtt_handler.get_device_metadata(serial),
            timeout=float(os.getenv("AGENT_WS_AUTH_TIMEOUT_SECONDS", "3")),
        )
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning("Pi '%s' auth lookup timed out; asking agent to retry", serial)
        await websocket.close(code=1013, reason="Server busy; retry")
        return
    except Exception as exc:
        logger.warning("Pi '%s' auth lookup failed; asking agent to retry: %s", serial, exc)
        await websocket.close(code=1013, reason="Server busy; retry")
        return

    expected_token = str(metadata.get("device_token") or "").strip()
    db_sync_state = bool(metadata.get("is_synced", False))
    license_bypass = bool(metadata.get("license_bypass"))
    provided_token = str(token or "").strip()

    logger.debug(
        "Agent WS auth serial=%s expected_token=%s provided_token=%s bypass=%s",
        serial,
        bool(expected_token),
        bool(provided_token),
        license_bypass,
    )

    if enforce_auth and expected_token and provided_token != expected_token and not license_bypass:
        logger.warning("Pi '%s' connection rejected: invalid or missing token.", serial)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    if expected_token and provided_token != expected_token and not license_bypass:
        logger.warning("Pi '%s' connection allowed with invalid/missing token because ENFORCE_AGENT_AUTH=false.", serial)
    if license_bypass and expected_token and provided_token != expected_token:
        logger.warning("Pi '%s' connection allowed with invalid/missing token because per-device license bypass is enabled.", serial)

    await pi_manager.connect(serial, websocket)
    
    try:
        while True:
            data = await websocket.receive_json()
            message_type = data.get("type")
            payload = data.get("payload")

            if message_type == "status_update" and payload:
                control_ack = build_control_plane_ack(
                    payload,
                    channel="websocket",
                    serial=serial,
                )
                if control_ack:
                    await pi_manager.send_personal_message(serial, control_ack)
                payload = dict(payload)
                payload["serial"] = serial
                sync_errors = list(mqtt_handler.device_sync_cache.get(f"{serial}_errors", []) or [])
                cached_sync = mqtt_handler.device_sync_cache.get(serial, db_sync_state)
                payload["is_synced"] = bool(cached_sync)
                if not cached_sync and not sync_errors:
                    sync_errors.append("Pending server configuration sync")
                agent_version = str(payload.get("version") or "").strip().upper()
                server_version = str(settings.APP_VERSION or "").strip().upper()
                if agent_version and server_version and agent_version != server_version:
                    payload["is_synced"] = False
                    version_error = f"Agent version {agent_version} != server {server_version}"
                    if version_error not in sync_errors:
                        sync_errors.append(version_error)
                if not license_bypass and not expected_token:
                    payload["status"] = "awaiting_license"
                    payload["bps"] = 0
                    payload["ntrip_connected"] = False
                    payload["ntrip_status"] = {}
                    payload["ntrip_stats"] = {}
                    payload["is_synced"] = False
                    sync_errors.append("Server token has not been issued to this agent")
                payload["sync_errors"] = sync_errors
                compact_payload = _compact_status_payload(payload)
                if not compact_payload:
                    continue
                status_decision = await status_reconciler.accept(
                    compact_payload,
                    source="websocket",
                )
                if not status_decision.accepted:
                    logger.debug(
                        "Ignored WebSocket status for %s: %s",
                        serial,
                        status_decision.reason,
                    )
                    continue
                compact_payload = status_decision.payload
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
                    live = latest_device_telemetry_cache.setdefault(str(serial), {})
                    nmea_live = live.setdefault("nmea_data", {})
                    parsed_type = str(parsed_data.get("type") or "").upper()
                    if parsed_type == "GGA":
                        nmea_live["gga"] = parsed_data
                    elif parsed_type == "GSA":
                        nmea_live["gsa"] = parsed_data
                    elif parsed_type == "GSV":
                        nmea_live["satellites"] = parsed_data.get("satellites") or []
                    elif parsed_type:
                        nmea_live[parsed_type.lower()] = parsed_data
                    await ui_manager.broadcast({
                        "type": "nmea_update",
                        "serial": serial,
                        "data": parsed_data
                    })
            elif message_type == "rtcm_signal_update" and payload:
                decoded_data = payload
                if isinstance(decoded_data, str):
                    try:
                        decoded_data = json.loads(decoded_data)
                    except Exception:
                        decoded_data = None
                if isinstance(decoded_data, dict):
                    now_ms = int(time.time() * 1000)
                    live = latest_device_telemetry_cache.setdefault(str(serial), {})
                    satellite_map = live.setdefault("rtcm_satellites", {})
                    if not isinstance(satellite_map, dict):
                        satellite_map = {}
                        live["rtcm_satellites"] = satellite_map
                    satellites = decoded_data.get("satellites") or []
                    if isinstance(satellites, dict):
                        satellites = list(satellites.values())
                    for index, item in enumerate(satellites):
                        if not isinstance(item, dict):
                            continue
                        satellite = dict(item)
                        try:
                            last_seen = int(float(satellite.get("lastSeen") or satellite.get("last_seen") or now_ms))
                        except (TypeError, ValueError):
                            last_seen = now_ms
                        if 0 < last_seen < 10_000_000_000:
                            last_seen *= 1000
                        if last_seen <= 0 or last_seen > now_ms + 60_000:
                            last_seen = now_ms
                        satellite["lastSeen"] = last_seen
                        key = str(
                            satellite.get("id")
                            or f"RTCM:{satellite.get('system') or satellite.get('sys') or 'un'}:{satellite.get('prn') or index}"
                        )
                        satellite_map[key] = satellite
                    for key, item in list(satellite_map.items()):
                        try:
                            last_seen = int(item.get("lastSeen") or 0)
                        except (TypeError, ValueError, AttributeError):
                            last_seen = 0
                        if last_seen and now_ms - last_seen > 10_000:
                            satellite_map.pop(key, None)
                    decoded_data = {
                        **decoded_data,
                        "satellites": list(satellite_map.values()),
                        "satellite_count": len(satellite_map),
                        "updated_at_ms": now_ms,
                    }
                    live["rtcm_signal_data"] = decoded_data
                    live["satellite_count"] = len(satellite_map)
                    await ui_manager.broadcast({
                        "type": "rtcm_signal_update",
                        "serial": serial,
                        "data": decoded_data
                    })

    except WebSocketDisconnect:
        logging.debug("Pi '%s' disconnected", serial)
        pi_manager.disconnect(serial, websocket)
        if pi_manager.has_replacement(serial, websocket):
            logger.debug("Pi '%s' old WebSocket closed after a newer connection replaced it", serial)
            return
        if not metadata.get("exists") or mqtt_handler.device_was_deleted(serial):
            return

        # WebSocket is the secondary channel. Give MQTT/LWT time to prove the
        # device is still alive before changing the effective station status.
        disconnected_at = time.time()
        await asyncio.sleep(12)
        if mqtt_handler.device_was_deleted(serial):
            return
        if await status_reconciler.has_update_after(serial, disconnected_at):
            logger.debug("Pi '%s' WebSocket dropped but another status channel is active", serial)
            return
        if await status_reconciler.current_status(serial) == "offline":
            return

        now_ts = int(time.time())
        offline_decision = await status_reconciler.accept_offline(
            serial,
            source="disconnect",
            received_at=now_ts,
        )
        if not offline_decision.accepted:
            return

        # FIX: Read device from DB to get full config (name, base_config, service_config)
        # then broadcast FULL payload to UI so the map doesn't lose station position.
        # Only write status-related fields to DB, do NOT touch config.
        ui_broadcast_payload = {
            **(offline_decision.payload or {}),
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
                    ui_broadcast_payload["service_config"] = _device_service_config(device)
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
                else:
                    return
        except Exception as e:
            logger.warning(f"[OFFLINE] DB lookup/update failed for '{serial}': {e}")

        # Broadcast FULL payload (with config) to UI so map keeps station
        if mqtt_handler.device_was_deleted(serial):
            return
        await enqueue_ui_broadcast({
            "type": "status_update",
            "data": ui_broadcast_payload
        })
    
    except Exception as e:
        if isinstance(e, RuntimeError) and "accept" in str(e).lower():
            logging.debug("Stale WS '%s' closed before accept completed: %s", serial, e)
        else:
            logging.warning("WS Error '%s': %s", serial, e)
        pi_manager.disconnect(serial, websocket)
        
# === GLOBAL EXCEPTION HANDLER ===
@app.exception_handler(DatabaseBusyError)
async def database_busy_exception_handler(request: Request, exc: DatabaseBusyError):
    logger.warning("Database admission timeout for %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Server database is busy; retry shortly"},
        headers={"Retry-After": "3"},
    )


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
    if exc.status_code == 404 and exc.detail == "Not Found" and request.url.path.startswith("/api/"):
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
async def detailed_health_check(
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    """
    Enhanced health check với system metrics cho monitoring dashboard
    """
    try:
        # System metrics
        # Never block the single Uvicorn event loop while sampling CPU.
        # interval=None returns the percentage since the previous sample.
        cpu_percent = psutil.cpu_percent(interval=None)
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
async def buffer_metrics_endpoint(
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    """Buffer and flush performance metrics for realtime monitoring"""
    global buffer_metrics, device_update_buffer
    
    current_buffer_size = len(device_update_buffer)
    uptime_since_flush = time.time() - buffer_metrics.get("last_flush_time", time.time())
    reconciler_metrics = await status_reconciler.metrics_snapshot()
    async with pending_ui_status_lock:
        pending_station_updates = len(pending_ui_status_updates)
    
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
        "status_reconciler": reconciler_metrics,
        "realtime": {
            "pending_station_updates": pending_station_updates,
            "ui_queue_depth": ui_broadcast_queue.qsize(),
            "pi_ingest_queue_depth": pi_status_ingest_queue.qsize(),
            "event_loop": dict(event_loop_watchdog_metrics),
            "logging": logging_runtime.snapshot(),
            "mqtt_ingress": {
                **dict(mqtt_handler.mqtt_ingress_metrics),
                "pending": len(mqtt_handler.mqtt_ingress_pending),
                "max_pending": mqtt_handler.MQTT_INGRESS_MAX_PENDING,
            },
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
async def recent_errors(
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
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
        "version": settings.APP_VERSION
    }

@app.get("/login", include_in_schema=False)
async def serve_login():
    return RedirectResponse(url="/", status_code=307)

@app.get("/monitoring", include_in_schema=False)
async def serve_monitoring():
    return FileResponse("../frontend/monitoring.html")

@app.get("/users", include_in_schema=False)
async def serve_users():
    return FileResponse("../frontend/users.html")

@app.get("/security", include_in_schema=False)
async def serve_security():
    return FileResponse("../frontend/security.html")

@app.get("/operations", include_in_schema=False)
async def serve_operations():
    return FileResponse("../frontend/operations.html")

@app.get("/zalo", include_in_schema=False)
async def serve_zalo():
    return FileResponse("../frontend/zalo.html")

@app.get("/rovers-global", include_in_schema=False)
async def serve_rovers_global():
    return FileResponse("../frontend/rovers-global.html")

@app.get("/network-status", include_in_schema=False)
@app.get("/coverage", include_in_schema=False)
@app.get("/blog", include_in_schema=False)
@app.get("/blog/{rest_of_path:path}", include_in_schema=False)
@app.get("/station/{rest_of_path:path}", include_in_schema=False)
@app.get("/api/docs", include_in_schema=False)
async def serve_spa():
    return FileResponse("../frontend/index.html")

# === STATIC FILES ===
class PublicAssetFiles(StaticFiles):
    def __init__(self, directory: str, suffixes: tuple[str, ...], nested: bool = False):
        super().__init__(directory=directory)
        self.suffixes = suffixes
        self.nested = nested

    async def get_response(self, path: str, scope):
        if (not self.nested and ("/" in path or "\\" in path)) or not path.lower().endswith(self.suffixes):
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
async def serve_index():
    return FileResponse("../frontend/index.html")


@app.get("/robots.txt", include_in_schema=False)
async def serve_robots():
    return FileResponse("../frontend/robots.txt")


@app.get("/{page_name}.html", include_in_schema=False)
async def serve_public_html(page_name: str):
    if page_name not in {"operations", "rovers-global", "security", "users", "zalo"}:
        raise HTTPException(status_code=404)
    return FileResponse(f"../frontend/{page_name}.html")


app.mount("/assets/js/dist", PublicAssetFiles("../frontend/js/dist", (".min.js",)), name="asset-scripts")
app.mount("/assets/css/dist", PublicAssetFiles("../frontend/css/dist", (".min.css",)), name="asset-styles")
images = (".png", ".jpg", ".jpeg", ".svg", ".webp", ".ico")
app.mount("/assets/img", PublicAssetFiles("../frontend/img", images, nested=True), name="asset-images")
app.mount("/img", PublicAssetFiles("../frontend/img", images, nested=True), name="images")

class MonitoringMiddleware(BaseHTTPMiddleware):
    """Middleware để ghi nhận metrics"""
    
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        # Rate limiting
        client_ip = request.client.host
        if not await redis_client.check_rate_limit(f"rate_limit:{client_ip}", 1000, 60):
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
    allow_origins=[
        "https://cors.aitogy.com",
        "http://118.70.171.179:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8082",
        "http://localhost:8082",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

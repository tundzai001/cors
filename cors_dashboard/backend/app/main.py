"""Application entry point, HTTP middleware and remaining control endpoints."""

import logging
import uuid
import hmac
import asyncio
import sys
import traceback
import os
import time
import psutil
import ctypes
from collections import deque
from typing import Optional, Dict
from fastapi import FastAPI, WebSocket, Depends, HTTPException, Request, Query, status, Body, Header
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .device_command_routes import configure_chip_endpoint, configure_service_endpoint
from .station_runtime import (
    BUFFER_FLUSH_INTERVAL_MAX,
    BUFFER_FLUSH_INTERVAL_MIN,
    BUFFER_SIZE_THRESHOLD_HIGH,
    BUFFER_SIZE_THRESHOLD_NORMAL,
    DEVICE_UPDATE_BUFFER_MAXSIZE,
    PI_INGEST_WORKER_COUNT,
    PI_STATUS_INGEST_QUEUE_MAXSIZE,
    STATUS_ALLOW_HEAVY_FIELDS,
    STATUS_HEAVY_FIELD_MAX_BYTES,
    STATUS_PAYLOAD_COMPACT_ENABLED,
    UI_BROADCAST_QUEUE_MAXSIZE,
    buffer_metrics,
    db_circuit_breaker,
    device_update_buffer,
    event_loop_watchdog_metrics,
    mqtt_circuit_breaker,
    pending_ui_status_lock,
    pending_ui_status_updates,
    pi_status_ingest_queue,
    pi_websocket_endpoint_router,
    ui_broadcast_queue,
    ui_websocket_endpoint_router,
)
from .map_routes import _get_map_station_items
from .ntrip_control import rtcm_session_lock
from .ntrip_control import (
    _build_rtcm_client_id,
    _check_rover_blocked,
    _count_active_mountpoint_sessions,
    _count_active_station_sessions,
    _do_ntrip_stop,
    _do_ntrip_wake,
    _extract_active_mountpoint,
    _extract_station_mountpoints,
    _fetch_active_station_client_ids,
    _normalize_mountpoint,
    _resolve_requested_mountpoint,
    _resolve_stream_status,
    _sanitize_ttl,
    _set_station_stream,
    _upsert_rtcm_session,
)
from .monitoring import health_monitor, global_rate_limiter
from .redis_client import redis_client
from . import crud, models, schemas, auth
from .database import get_db, get_auth_db, settings, DatabaseBusyError
from .websocket import manager as ui_manager, visitor_manager
from . import mqtt as mqtt_handler
from .command_dispatcher import send_command_to_pi as dispatch_command_to_pi
from . import license_manager
from .caster_helpers import is_configured_caster_peer
from .security import rate_limiter
from .status_reconciler import status_reconciler
from .nonblocking_logging import configure_nonblocking_logging


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# --- Cấu hình Logging ---
log_file = 'app.log'
log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# High-frequency public reads are expected traffic. Keep failures visible while
# avoiding three INFO lines for every successful map refresh.
QUIET_REQUEST_LOG_PATHS = frozenset({
    "/api/public/map-stations",
    "/api/ionosphere/latest",
    "/api/ionosphere/status",
    "/api/gnss/satellites/tle",
    "/api/gnss/satellites/summary",
})


class QuietEndpointAccessFilter(logging.Filter):
    def filter(self, record):
        try:
            args = record.args
            path = str(args[2]).split("?", 1)[0] if isinstance(args, tuple) and len(args) >= 3 else ""
            if path.startswith("/ws/pi/") and isinstance(args, tuple) and len(args) >= 3:
                safe_args = list(args)
                safe_args[2] = path
                record.args = tuple(safe_args)
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


logger = logging.getLogger(__name__)


from .runtime_config import _read_caster_config_from_env
# Metrics for buffer performance tracking

# TỐI ƯU CỰC ĐỘ: Cache mapping Mountpoint -> Serial để tránh quét DB liên tục
# Giúp xử lý hàng nghìn yêu cầu wake-station mỗi giây mà không làm treo Backend.


# === CASTER STATUS SYNC ===
# Cache per-device last known per-server status để detect changes
# { serial: { "server1": {...}, "server2": {...} } }


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
        client_ip = rate_limiter._get_client_ip(request)
        now = time.time()

        # 1. Kiểm tra xem IP có đang bị BAN không (Via Redis)
        is_banned = await redis_client.is_ip_banned(client_ip)
        if is_banned:
            rate_limiter.record_block_event(client_ip, "Security Gate: banned IP", "security_gate", permanent=True)
            return JSONResponse(
                status_code=403,
                content={"detail": "Your IP has been blocked due to multiple security violations."}
            )

        # Helper để ghi nhận vi phạm và thực hiện BAN nếu cần
        async def record_violation():
            rate_limiter.record_block_event(client_ip, f"Security Gate: rejected {method} {path[:160]}", "security_gate")
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
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        if not quiet_success_path or response.status_code >= 400:
            logger.info(f"<-- {request.method} {request.url.path} - Completed in {process_time:.2f}ms, Status: {response.status_code}")

        return response

class FrontendCacheMiddleware(BaseHTTPMiddleware):
    """Keep HTML fresh while caching versioned source assets for a short period."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "").lower()

        if path.startswith("/api/admin/api-keys"):
            response.headers["Cache-Control"] = "no-store"
        elif "text/html" in content_type:
            response.headers.setdefault("Cache-Control", "no-cache, must-revalidate")
        elif path.startswith(("/css/", "/js/", "/img/")):
            response.headers["Cache-Control"] = "public, max-age=86400, stale-while-revalidate=604800"
        return response

# === HEARTBEAT CHECKER (FIXED) ===


async def cleanup_rate_limiter():
    """Dọn dẹp rate limiter định kỳ"""
    while True:
        await asyncio.sleep(300)  # Mỗi 5 phút
        global_rate_limiter.cleanup()
        logger.debug("Rate limiter cleanup completed")

# === LIFESPAN ===


from .lifecycle import lifespan

app = FastAPI(title="CORS Geodetic Backend", lifespan=lifespan, version="0.1.0", docs_url=None, openapi_url=None)
from .station_host_lock import check_host_lock
from .routers import users, devices, api_keys
app.include_router(users.router)
app.include_router(devices.router)
app.include_router(api_keys.router)


# === SEO & DYNAMIC ROUTING FOR GNSS PLATFORM ===
from .public_pages import router as seo_router
app.include_router(seo_router)

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


async def _require_ntrip_control_token(
    request: Request,
    token: Optional[str] = Header(None, alias="X-NTRIP-Control-Token"),
) -> None:
    expected = str(settings.NTRIP_CONTROL_TOKEN or os.getenv("NTRIP_CONTROL_TOKEN", "")).strip()
    if len(expected) < 32:
        # Keep configured legacy casters working without exposing control publicly.
        if request.client and await is_configured_caster_peer(request.client.host):
            return
        raise HTTPException(status_code=503, detail="NTRIP control authentication is not configured for this caller")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid NTRIP control token")


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
    ttl_seconds: int = 90,
    _: None = Depends(_require_ntrip_control_token),
):
    """
    Endpoint cho NTRIP gửi lệnh bật trạm qua HTTP GET.
    Ví dụ: /api/ntrip/wake-listen?mountpoint=HN01&identifier=test-client&address=aitogy.com.vn
    """
    id_to_use = client_instance_id or receiver_id or identifier or "http-get"
    addr_to_use = caster_address or address or host or ip

    return await _do_ntrip_wake(mountpoint, id_to_use, ttl_seconds, requested_address=addr_to_use)

@app.post("/api/ntrip/wake-station", response_class=PlainTextResponse)
async def ntrip_wake_station(
    payload: dict = Body(...),
    _: None = Depends(_require_ntrip_control_token),
):
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
    _: None = Depends(_require_ntrip_control_token),
):
    id_to_use = client_instance_id or receiver_id or identifier or "http-get"
    addr_to_use = caster_address or address or host or ip
    return await _do_ntrip_stop(mountpoint, id_to_use, force=force, requested_address=addr_to_use)

@app.post("/api/ntrip/stop-station", response_class=PlainTextResponse)
async def ntrip_stop_station(
    payload: dict = Body(...),
    _: None = Depends(_require_ntrip_control_token),
):
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

# === ðŸ†• WEBSOCKET THEO DÕI KHÁCH TRUY CẬP (PUBLIC) ===
@app.websocket("/ws/track-visitors")
async def track_visitors_endpoint(websocket: WebSocket):
    client_ip = websocket.client.host
    if not await visitor_manager.connect(websocket, client_ip):
        return

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
from .auth_routes import login_router
app.include_router(login_router)


from .zalo_routes import router as zalo_router
app.include_router(zalo_router)


from .map_routes import get_public_map_stations_router
app.include_router(get_public_map_stations_router)


from .map_routes import get_authenticated_map_stations_router
app.include_router(get_authenticated_map_stations_router)


from .ionosphere_routes import router as ionosphere_router
app.include_router(ionosphere_router)


from .satellite_routes import router as satellite_router
app.include_router(satellite_router)


@app.get("/api/integrations/stations")
async def get_integration_stations(
    response: Response,
    db: AsyncSession = Depends(get_db),
    key: models.ApiKey = Depends(api_keys.require_integration_key),
):
    """Apply current key policy on every request, including cached station data."""
    items = await _get_map_station_items(response, db)
    configs = {}
    if "service_config" in (key.data_fields or []):
        result = await db.execute(select(models.Device.serial, models.Device.service_config))
        configs = {serial: config for serial, config in result.all()}
    response.headers["Cache-Control"] = "private, no-store"
    return api_keys.project_station_items(items, key, configs)


@app.post("/api/integrations/stations/{serial}/configure-service")
async def configure_integration_station(
    serial: str,
    payload: schemas.ServiceConfig,
    db: AsyncSession = Depends(get_db),
    key: models.ApiKey = Depends(api_keys.require_integration_key),
):
    api_keys.require_station_edit(key, serial)
    device = await crud.get_device_for_update(db, serial)
    if not device:
        raise HTTPException(404, "Device not found")
    changes = payload.model_dump(exclude_unset=True)
    for field in schemas.SERVICE_PASSWORD_FIELDS:
        if changes.get(field) in (None, ""):
            changes.pop(field, None)
    check_host_lock(device, changes)
    api_user = models.User(id=key.created_by, username=f"api-key:{key.id}", role=auth.Role.ADMIN, is_active=True)
    return await configure_service_endpoint(serial, payload, db, api_user)


@app.post("/api/integrations/stations/{serial}/configure-chip")
async def configure_integration_station_chip(
    serial: str,
    payload: schemas.Command,
    db: AsyncSession = Depends(get_db),
    key: models.ApiKey = Depends(api_keys.require_integration_key),
):
    api_keys.require_station_edit(key, serial)
    api_user = models.User(id=key.created_by, username=f"api-key:{key.id}", role=auth.Role.ADMIN, is_active=True)
    return await configure_chip_endpoint(serial, payload, db, api_user)


from .map_routes import get_public_network_summary_router
app.include_router(get_public_network_summary_router)


from .caster_routes import router as caster_router
app.include_router(caster_router)

from .station_scope import _rtcm_visible_serials

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

        session_id = await _upsert_rtcm_session(
            db,
            session_id=session_id,
            serial=serial,
            mountpoint=str(selected_mountpoint),
            client_id=client_id,
            expires_at=expires_at,
            now=now,
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


from .auth_routes import get_current_user_info_router
app.include_router(get_current_user_info_router)

#=== Admin endpoint ===
@app.get("/api/admin/settings", response_model=dict)
async def get_system_settings(
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Lấy trạng thái hiện tại của các cÃ i đặt bảo mật."""
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được xem cài đặt toàn hệ thống")
    settings_data = await crud.get_system_settings(db)

    return settings_data

@app.post("/api/admin/settings")
async def update_system_settings(
    settings_data: dict,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Cập nhật cÃ i đặt bảo mật."""
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được thay đổi cài đặt toàn hệ thống")

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
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được xem thống kê bảo mật")
    try:
        # 1. Security stats
        security_stats = await rate_limiter.get_stats(db)
        security_stats = rate_limiter.merge_runtime_stats(security_stats, await redis_client.get_banned_ips())
        if security_stats.get("error"):
            raise RuntimeError("Security database statistics unavailable")

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
                "error": "Failed to load security statistics",
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
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được quản lý danh sách chặn IP")
    """Admin unblock một IP (thao tác trên database)."""
    ip = request_data.get("ip_address")
    if not ip:
        raise HTTPException(status_code=400, detail="ip_address is required")

    was_banned = await redis_client.is_ip_banned(ip)
    was_rate_limited = ip in rate_limiter.runtime_blocks
    success = await rate_limiter.unblock_ip(db, ip)
    await redis_client.unban_ip(ip)
    await redis_client.clear_rate_limit(f"rate_limit:{ip}")
    security_rep_scores.pop(ip, None)
    success = success or was_banned or was_rate_limited

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
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được quản lý danh sách chặn IP")
    """Admin block thủ công một IP (lưu vÃ o database)."""
    ip = request_data.get("ip_address")
    if not ip:
        raise HTTPException(status_code=400, detail="ip_address is required")

    await rate_limiter.block_ip(db, ip)
    logger.warning(f"âš ï¸ Admin '{current_user.username}' permanently blocked IP: {ip}")

    return {"status": "blocked", "ip": ip}


# ==============================================================================
# === SESSION & DEVICE ACCESS CONTROL (Quản lý phiên đăng nhập & Chặn máy)   ===
# ==============================================================================

@app.get("/api/admin/security/sessions")
async def get_admin_sessions(
    request: Request,
    auth_db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Lấy danh sách các phiên đăng nhập đang hoạt động và các máy tính bị chặn."""
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được xem danh sách phiên đăng nhập")

    auth_header = request.headers.get("Authorization", "")
    current_sid = None
    if auth_header.startswith("Bearer "):
        try:
            token_payload = auth.decode_token(auth_header.split(" ", 1)[1])
            current_sid = token_payload.get("sid")
        except Exception:
            pass

    now = int(time.time())
    # Lấy các phiên chưa bị hủy và chưa hết hạn
    stmt = (
        select(models.UserSession)
        .filter(
            models.UserSession.is_revoked == False,
            models.UserSession.expires_at > now
        )
        .order_by(models.UserSession.last_active_at.desc())
    )
    result = await auth_db.execute(stmt)
    sessions = list(result.scalars().all())

    # Tự động đồng bộ/tạo phiên cho admin hiện tại nếu chưa có trong DB (ví dụ đăng nhập từ trước khi có bảng session)
    current_has_session = any(s.user_id == current_user.id and (s.session_id == current_sid if current_sid else True) for s in sessions)
    if not current_has_session:
        import secrets
        import urllib.parse
        new_sid = current_sid or secrets.token_hex(24)

        client_ip = request.headers.get("x-forwarded-for")
        if client_ip:
            client_ip = client_ip.split(",")[0].strip()
        else:
            client_ip = request.client.host if request.client else "127.0.0.1"

        raw_dev_name = request.headers.get("x-device-name")
        device_name = urllib.parse.unquote(raw_dev_name) if raw_dev_name else "Trình duyệt hiện tại"
        device_id = request.headers.get("x-device-id") or f"dev_{secrets.token_hex(8)}"
        local_ip = request.headers.get("x-local-ip")

        check_sess = await auth_db.execute(select(models.UserSession).filter(models.UserSession.session_id == new_sid))
        existing_s = check_sess.scalar_one_or_none()
        if existing_s:
            existing_s.is_revoked = False
            existing_s.last_active_at = now
            existing_s.expires_at = now + 86400 * 7
            if local_ip: existing_s.local_ip = local_ip
            current_sess_obj = existing_s
        else:
            current_sess_obj = models.UserSession(
                session_id=new_sid,
                user_id=current_user.id,
                username=current_user.username,
                role=current_user.role,
                ip_address=client_ip,
                local_ip=local_ip,
                device_id=device_id,
                device_name=device_name,
                user_agent=request.headers.get("user-agent"),
                created_at=now,
                last_active_at=now,
                expires_at=now + 86400 * 7,
                is_revoked=False
            )
            auth_db.add(current_sess_obj)
        try:
            await auth_db.commit()
            sessions.insert(0, current_sess_obj)
            current_sid = new_sid
        except Exception as e:
            logger.warning(f"Could not auto-register current session: {e}")
    else:
        req_local_ip = request.headers.get("x-local-ip")
        if req_local_ip:
            for s in sessions:
                if s.session_id == current_sid or (not current_sid and s.user_id == current_user.id):
                    if not s.local_ip:
                        s.local_ip = req_local_ip
                        try:
                            await auth_db.commit()
                        except Exception:
                            pass
                    break

    # Lấy danh sách máy tính bị chặn
    blocked_stmt = select(models.BlockedDevice).order_by(models.BlockedDevice.blocked_at.desc())
    blocked_result = await auth_db.execute(blocked_stmt)
    blocked_devices = blocked_result.scalars().all()

    # Map tên đầy đủ của user
    user_ids = {s.user_id for s in sessions}
    full_name_map = {}
    if user_ids:
        users_result = await auth_db.execute(select(models.User).filter(models.User.id.in_(user_ids)))
        for u in users_result.scalars().all():
            full_name_map[u.id] = u.full_name or u.username

    session_list = []
    for s in sessions:
        session_list.append({
            "session_id": s.session_id,
            "username": s.username,
            "full_name": full_name_map.get(s.user_id, s.username),
            "role": s.role,
            "ip_address": s.ip_address,
            "local_ip": s.local_ip,
            "device_id": s.device_id,
            "device_name": s.device_name or "Thiết bị",
            "user_agent": s.user_agent,
            "created_at": s.created_at,
            "last_active_at": s.last_active_at,
            "expires_at": s.expires_at,
            "is_current": (s.session_id == current_sid if current_sid else (s.user_id == current_user.id))
        })

    blocked_list = []
    for b in blocked_devices:
        blocked_list.append({
            "device_id": b.device_id,
            "device_name": b.device_name or "Thiết bị",
            "local_ip": b.local_ip,
            "last_ip": b.last_ip,
            "username": b.username,
            "blocked_at": b.blocked_at,
            "blocked_by": b.blocked_by,
            "reason": b.reason or "Chặn bởi quản trị viên"
        })

    return {
        "status": "ok",
        "active_sessions": session_list,
        "blocked_devices": blocked_list,
        "total_active": len(session_list),
        "total_blocked_devices": len(blocked_list)
    }


@app.post("/api/admin/security/sessions/revoke")
async def revoke_session(
    request_data: dict,  # {"session_id": "sess_..."}
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Hủy một phiên đăng nhập cụ thể (ngắt kết nối máy đó)."""
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được hủy phiên đăng nhập")

    session_id = request_data.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    stmt = select(models.UserSession).filter(models.UserSession.session_id == session_id)
    result = await auth_db.execute(stmt)
    target_session = result.scalar_one_or_none()
    if not target_session:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn")

    target_session.is_revoked = True
    target_session.revoked_at = int(time.time())
    target_session.revoked_by = current_user.username
    await auth_db.commit()

    # Cập nhật cache tức thì
    auth.revoke_session_in_memory(session_id)

    # Ghi log sự kiện
    await crud.log_system_event(
        db=devices_db,
        type="warning",
        message=f"Admin '{current_user.username}' đã ngắt phiên đăng nhập của '{target_session.username}' (IP mạng: {target_session.ip_address}, IP máy: {target_session.local_ip or 'N/A'}, Thiết bị: {target_session.device_name})",
        user_id=current_user.id,
        ip=target_session.ip_address
    )

    logger.warning(f"Admin '{current_user.username}' revoked session {session_id} of '{target_session.username}'")
    return {"status": "ok", "message": f"Đã ngắt phiên đăng nhập của '{target_session.username}'", "session_id": session_id}


@app.post("/api/admin/security/sessions/block-device")
async def block_device_endpoint(
    request_data: dict,  # {"device_id": "dev_...", "reason": "..."}
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Chặn riêng 1 máy tính (không chặn toàn mạng, các máy khác cùng mạng vẫn dùng bình thường)."""
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được chặn thiết bị")

    device_id = request_data.get("device_id")
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id is required")

    reason = request_data.get("reason", "Chặn máy bởi quản trị viên")

    # Lấy thông tin từ phiên gần nhất của thiết bị
    sess_stmt = (
        select(models.UserSession)
        .filter(models.UserSession.device_id == device_id)
        .order_by(models.UserSession.last_active_at.desc())
    )
    sess_res = await auth_db.execute(sess_stmt)
    latest_sess = sess_res.scalars().first()

    device_name = latest_sess.device_name if latest_sess else None
    local_ip = latest_sess.local_ip if latest_sess else None
    last_ip = latest_sess.ip_address if latest_sess else None
    username = latest_sess.username if latest_sess else None

    # Kiểm tra xem máy đã có trong danh sách chặn chưa
    check_stmt = select(models.BlockedDevice).filter(models.BlockedDevice.device_id == device_id)
    check_res = await auth_db.execute(check_stmt)
    blocked_entry = check_res.scalar_one_or_none()

    if not blocked_entry:
        blocked_entry = models.BlockedDevice(
            device_id=device_id,
            device_name=device_name,
            local_ip=local_ip,
            last_ip=last_ip,
            username=username,
            blocked_at=int(time.time()),
            blocked_by=current_user.username,
            reason=reason
        )
        auth_db.add(blocked_entry)

    # Hủy TẤT CẢ các phiên đang mở của máy này
    all_sess_stmt = select(models.UserSession).filter(
        models.UserSession.device_id == device_id,
        models.UserSession.is_revoked == False
    )
    all_sess_res = await auth_db.execute(all_sess_stmt)
    for s in all_sess_res.scalars().all():
        s.is_revoked = True
        s.revoked_at = int(time.time())
        s.revoked_by = current_user.username
        auth.revoke_session_in_memory(s.session_id)

    await auth_db.commit()

    # Cập nhật cache tức thì
    auth.block_device_in_memory(device_id)

    # Ghi log
    await crud.log_system_event(
        db=devices_db,
        type="danger",
        message=f"Admin '{current_user.username}' đã chặn riêng máy tính (Device ID: {device_id}, IP máy: {local_ip or 'N/A'}, IP mạng: {last_ip or 'N/A'}, User: {username or 'N/A'}). Mạng IP chung không bị ảnh hưởng.",
        user_id=current_user.id,
        ip=last_ip or "0.0.0.0"
    )

    logger.warning(f"Admin '{current_user.username}' blocked machine {device_id} (machine IP: {local_ip}, remote IP: {last_ip})")
    return {
        "status": "ok",
        "message": f"Đã chặn riêng máy tính này (IP máy: {local_ip or 'N/A'}, Thiết bị: {device_name or 'N/A'}). Các máy tính khác trong cùng mạng hoàn toàn không bị ảnh hưởng.",
        "device_id": device_id
    }


@app.post("/api/admin/security/sessions/unblock-device")
async def unblock_device_endpoint(
    request_data: dict,  # {"device_id": "dev_..."}
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Bỏ chặn cho một máy tính."""
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được bỏ chặn thiết bị")

    device_id = request_data.get("device_id")
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id is required")

    stmt = select(models.BlockedDevice).filter(models.BlockedDevice.device_id == device_id)
    res = await auth_db.execute(stmt)
    entry = res.scalar_one_or_none()
    if entry:
        await auth_db.delete(entry)
        await auth_db.commit()

    auth.unblock_device_in_memory(device_id)

    await crud.log_system_event(
        db=devices_db,
        type="info",
        message=f"Admin '{current_user.username}' đã bỏ chặn máy tính (Device ID: {device_id})",
        user_id=current_user.id
    )

    logger.info(f"Admin '{current_user.username}' unblocked device {device_id}")
    return {"status": "ok", "message": f"Đã bỏ chặn máy tính {device_id}", "device_id": device_id}


@app.post("/api/admin/security/sessions/revoke-all-others")
async def revoke_all_other_sessions(
    request: Request,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Đăng xuất tất cả các phiên đăng nhập khác, chỉ giữ lại phiên hiện tại."""
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được hủy tất cả phiên")

    auth_header = request.headers.get("Authorization", "")
    current_sid = None
    if auth_header.startswith("Bearer "):
        try:
            token_payload = auth.decode_token(auth_header.split(" ", 1)[1])
            current_sid = token_payload.get("sid")
        except Exception:
            pass

    stmt = select(models.UserSession).filter(
        models.UserSession.is_revoked == False,
        models.UserSession.session_id != current_sid if current_sid else True
    )
    res = await auth_db.execute(stmt)
    revoked_count = 0
    now = int(time.time())
    for s in res.scalars().all():
        s.is_revoked = True
        s.revoked_at = now
        s.revoked_by = current_user.username
        auth.revoke_session_in_memory(s.session_id)
        revoked_count += 1

    await auth_db.commit()

    await crud.log_system_event(
        db=devices_db,
        type="warning",
        message=f"Admin '{current_user.username}' đã ngắt {revoked_count} phiên đăng nhập khác",
        user_id=current_user.id
    )

    return {"status": "ok", "message": f"Đã ngắt {revoked_count} phiên đăng nhập khác", "revoked_count": revoked_count}


# === LICENSE ===
@app.post("/api/license/pi", response_model=schemas.LicenseResponse)
async def generate_pi_license(
    request: schemas.LicenseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_LICENSE))
):
    serial = request.serial
    if not serial:
        raise HTTPException(status_code=400, detail="Serial number required.")

    try:
        device = await crud.get_device_by_serial(db, serial)
        license_key = license_manager.get_or_create_device_token(serial, device.device_token if device else None)
        if device is None:
            device = models.Device(
                serial=serial,
                name=serial,
                status="unprovisioned",
                is_provisioned=False,
                device_token=license_key,
            )
            db.add(device)
        else:
            device.device_token = license_key
        await db.commit()
        await db.refresh(device)
        await mqtt_handler.cache_device_metadata(device)
        return schemas.LicenseResponse(serial=serial, license_key=license_key)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Could not issue a unique device token") from None
    except Exception as e:
        await db.rollback()
        logging.error(f"Failed to generate license for {serial}: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate license key.")

# === CASTER USER MANAGEMENT (For non-DB users) ===
from .rover_accounts_routes import router as rover_accounts_router
app.include_router(rover_accounts_router)

# === ROVER MANAGEMENT - GLOBAL VIEW ===


#=== DEVICE CONFIGURATION ENDPOINTS ===
from .device_command_routes import router as device_commands_router
app.include_router(device_commands_router)


# === ACTIVITY LOGGING ENDPOINTS ===
from .operations_routes import router as operations_router
app.include_router(operations_router)

from .rover_tools_routes import router as rover_tools_router
app.include_router(rover_tools_router)

from .export_routes import router as export_router
app.include_router(export_router)


from .rover_history_routes import router as rover_history_router
app.include_router(rover_history_router)


from .device_command_routes import tools_router as device_tools_router
app.include_router(device_tools_router)


# === WEBSOCKETS ===

from .station_runtime import ui_websocket_endpoint_router
app.include_router(ui_websocket_endpoint_router)
        # logging.info("UI client disconnected.")


from .station_runtime import pi_websocket_endpoint_router
app.include_router(pi_websocket_endpoint_router)

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
    lines: int = Query(100, ge=1, le=1000),
    level: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """
    Đọc N dòng cuối cùng từ file app.log để hiển thị lên UI.
    Chỉ Admin mới có quyền xem.
    """
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được xem log máy chủ")
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

from .frontend_routes import router as frontend_router, PublicAssetFiles
app.include_router(frontend_router)
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
        client_ip = rate_limiter._get_client_ip(request)
        rate_limiter.record_http_request(client_ip, start_time)
        if not await redis_client.check_rate_limit(f"rate_limit:{client_ip}", 1000, 60):
            health_monitor.record_error('rate_limit', f'IP: {client_ip}')
            retry_after = await redis_client.rate_limit_retry_after(f"rate_limit:{client_ip}")
            rate_limiter.record_block_event(client_ip, 'HTTP rate limit: 1000 requests / 60s', 'http_rate_limit', blocked_until=time.time() + retry_after)
            return JSONResponse(
                headers={"Retry-After": str(retry_after)},
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

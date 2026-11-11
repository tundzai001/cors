# ==============================================================================
# == backend/app/mqtt.py - FIXED: Batch Sync Checking + Pool Optimization
# ==============================================================================
import json
import logging
import asyncio
from .station_host_lock import locked_server_ids, supports_server_device_lock
import os
import time
import hashlib
import threading
from collections import OrderedDict
from typing import Awaitable, Callable, Optional
import paho.mqtt.client as mqtt
from sqlalchemy import select

from . import schemas
from .background_tasks import schedule_background
from .websocket import manager
from .database import settings, device_session_scope
from . import crud, models
from .utils import NMEAParser
from .rtcm_signal_decoder import rtcm_signal_decoder
from .status_reconciler import status_reconciler
from .control_plane_ack import build_control_plane_ack
from .license_sync import evaluate_license_sync
from . import license_manager

# Serialize MQTT DB writes to avoid connection pool spikes under burst traffic.
mqtt_db_write_lock = asyncio.Lock()


def _fingerprint_token(token: str | None) -> str | None:
    value = str(token or "").strip()
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _version_matches(agent_version: str | None) -> bool:
    if not agent_version:
        return False
    return str(agent_version).strip().upper() == str(settings.APP_VERSION).strip().upper()

main_loop = None
client_id = f"backend-listen-{settings.MQTT_HOST}-{os.getpid()}"
# Paho 2.x requires an explicit version for the existing legacy callbacks.
if hasattr(mqtt, "CallbackAPIVersion"):
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
else:
    mqtt_client = mqtt.Client(client_id=client_id)

if hasattr(settings, 'MQTT_USERNAME') and settings.MQTT_USERNAME:
    mqtt_client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)

# --- CACHE ---
device_write_cache = {}      # Lưu thời gian lần cuối ghi DB (Save)
device_status_cache = {}     # Lưu trạng thái cũ (online/offline)
device_sync_cache = {}       # Lưu trạng thái sync (True/False)
device_last_sync_check = {}  # Lưu thời gian lần cuối check DB
device_name_cache = {}       # Lưu tên trạm để ghép vào bản tin offline (LWT không kèm tên)
device_telemetry_cache = {}  # Snapshot live cho bot/API đọc tọa độ và vệ tinh hiện tại
device_metadata_cache = {}
device_metadata_locks = {}
device_metadata_tasks = {}
device_deletion_tombstones = set()
device_deletion_tombstones_lock = threading.Lock()
DEVICE_METADATA_CACHE_TTL_SECONDS = max(
    5.0,
    float(os.getenv("DEVICE_METADATA_CACHE_TTL_SECONDS", "60")),
)
RTCM_SATELLITE_CACHE_TTL_MS = 10_000
NMEA_PARSER_CACHE_MAXSIZE = 2048
nmea_parser_cache: OrderedDict[str, NMEAParser] = OrderedDict()


def parse_nmea_sentence(serial: str, sentence: str) -> dict | None:
    key = str(serial or "").strip().upper()
    if not key or len(key) > 128:
        return None
    parser = nmea_parser_cache.get(key)
    if parser is None:
        parser = NMEAParser()
        nmea_parser_cache[key] = parser
    nmea_parser_cache.move_to_end(key)
    while len(nmea_parser_cache) > NMEA_PARSER_CACHE_MAXSIZE:
        nmea_parser_cache.popitem(last=False)
    return parser.parse(sentence)


def cache_agent_sync_inputs(serial: str, payload: dict) -> None:
    """Keep the latest reported configs available to the periodic sync checker."""
    if not serial or not isinstance(payload, dict):
        return

    if isinstance(payload.get("base_config"), dict):
        device_sync_cache[f"{serial}_base_config"] = payload["base_config"]

    if "service_config" in payload:
        service_config = crud.normalize_service_config(payload.get("service_config"))
        if isinstance(service_config, dict):
            device_sync_cache[f"{serial}_service_config"] = service_config


def _metadata_snapshot(device) -> dict:
    if not device:
        return {"exists": False, "cached_at": time.monotonic()}
    system_info = getattr(device, "system_info", None)
    return {
        "exists": True,
        "cached_at": time.monotonic(),
        "name": getattr(device, "name", None),
        "status": getattr(device, "status", None),
        "timestamp": getattr(device, "timestamp", None),
        "offline_since": getattr(device, "offline_since", None),
        "device_token": str(getattr(device, "device_token", "") or "").strip(),
        "is_synced": bool(getattr(device, "is_synced", False)),
        "device_lock": system_info.get("device_lock") if isinstance(system_info, dict) else None,
        "license_bypass": bool(
            isinstance(system_info, dict)
            and system_info.get("auth_license_bypass")
        ),
        "token_deployment_pending": bool(
            isinstance(system_info, dict)
            and isinstance(system_info.get("auth_status"), dict)
            and system_info["auth_status"].get("token_deployment_pending")
        ),
        "base_config": getattr(device, "base_config", None) or {},
        "service_config": crud.normalize_service_config(getattr(device, "service_config", None)),
        "detected_chip_type": getattr(device, "detected_chip_type", None),
    }


async def get_device_metadata(serial: str, *, force_refresh: bool = False) -> dict:
    """Return a short-lived primitive snapshot instead of SELECTing every heartbeat."""
    serial_key = str(serial)
    cached = device_metadata_cache.get(serial_key)
    now = time.monotonic()
    if (
        not force_refresh
        and cached
        and now - float(cached.get("cached_at") or 0) < DEVICE_METADATA_CACHE_TTL_SECONDS
    ):
        return cached

    task = device_metadata_tasks.get(serial_key)
    if task is None:
        task = asyncio.create_task(_refresh_device_metadata(serial_key, force_refresh=force_refresh))
        device_metadata_tasks[serial_key] = task
        task.add_done_callback(lambda done: _metadata_lookup_done(serial_key, done))
    # A short WebSocket handshake deadline must not cancel SQL/connection cleanup.
    # Retries share this lookup until it completes and populates the cache.
    return await asyncio.shield(task)


def _metadata_lookup_done(serial_key: str, task: asyncio.Task) -> None:
    if device_metadata_tasks.get(serial_key) is task:
        device_metadata_tasks.pop(serial_key, None)
    # Retrieve failures even if every waiting handshake has already timed out.
    if not task.cancelled():
        task.exception()


async def _refresh_device_metadata(serial_key: str, *, force_refresh: bool = False) -> dict:
    lock = device_metadata_locks.setdefault(serial_key, asyncio.Lock())
    async with lock:
        cached = device_metadata_cache.get(serial_key)
        now = time.monotonic()
        if (
            not force_refresh
            and cached
            and now - float(cached.get("cached_at") or 0) < DEVICE_METADATA_CACHE_TTL_SECONDS
        ):
            return cached
        async with device_session_scope() as session:
            device = await crud.get_device_by_serial(session, serial_key)
        snapshot = _metadata_snapshot(device)
        device_metadata_cache[serial_key] = snapshot
        return snapshot


async def cache_device_metadata(device) -> None:
    """Publish a committed ORM device into the auth cache without another SELECT.

    Taking the same per-device lock as ``get_device_metadata`` prevents an
    older in-flight lookup from overwriting a freshly rotated token or bypass
    setting after the mutation endpoint returns.
    """
    serial_key = str(getattr(device, "serial", "") or "").strip()
    if not serial_key:
        return
    lock = device_metadata_locks.setdefault(serial_key, asyncio.Lock())
    async with lock:
        device_metadata_cache[serial_key] = _metadata_snapshot(device)
        if getattr(device, "name", None):
            device_name_cache[serial_key] = device.name
        with device_deletion_tombstones_lock:
            device_deletion_tombstones.discard(serial_key)


async def mark_device_metadata_missing(serial: str) -> None:
    """Install a locked tombstone after deletion so retained status stays ignored."""
    serial_key = str(serial or "").strip()
    if not serial_key:
        return
    lock = device_metadata_locks.setdefault(serial_key, asyncio.Lock())
    async with lock:
        device_metadata_cache[serial_key] = {
            "exists": False,
            "cached_at": time.monotonic(),
        }
        with device_deletion_tombstones_lock:
            device_deletion_tombstones.add(serial_key)


def device_metadata_is_missing(serial: str) -> bool:
    cached = device_metadata_cache.get(str(serial or "").strip()) or {}
    return cached.get("exists") is False


def device_was_deleted(serial: str) -> bool:
    serial_key = str(serial or "").strip()
    with device_deletion_tombstones_lock:
        return serial_key in device_deletion_tombstones


def _cache_rtcm_signal_snapshot(serial: str, decoded: dict) -> None:
    """Accumulate fresh RTCM satellites exactly as the dashboard live view does."""
    if not isinstance(decoded, dict):
        return
    now_ms = int(time.time() * 1000)
    live = device_telemetry_cache.setdefault(str(serial), {})
    satellite_map = live.setdefault("rtcm_satellites", {})
    if not isinstance(satellite_map, dict):
        satellite_map = {}
        live["rtcm_satellites"] = satellite_map
    raw_satellites = decoded.get("satellites") or []
    if isinstance(raw_satellites, dict):
        raw_satellites = list(raw_satellites.values())
    for index, item in enumerate(raw_satellites):
        if not isinstance(item, dict):
            continue
        satellite = dict(item)
        try:
            last_seen = int(float(satellite.get("lastSeen") or now_ms))
        except (TypeError, ValueError):
            last_seen = now_ms
        if 0 < last_seen < 10_000_000_000:
            last_seen *= 1000
        if last_seen <= 0 or last_seen > now_ms + 60_000:
            last_seen = now_ms
        satellite["lastSeen"] = last_seen
        key = str(
            satellite.get("id")
            or f"RTCM:{satellite.get('system') or 'un'}:{satellite.get('prn') or index}"
        )
        satellite_map[key] = satellite
    for key, item in list(satellite_map.items()):
        try:
            last_seen = int(item.get("lastSeen") or 0)
        except (TypeError, ValueError, AttributeError):
            last_seen = 0
        if last_seen and now_ms - last_seen > RTCM_SATELLITE_CACHE_TTL_MS:
            satellite_map.pop(key, None)
    fresh_satellites = list(satellite_map.values())
    live["rtcm_signal_data"] = {
        **decoded,
        "satellites": fresh_satellites,
        "satellite_count": len(fresh_satellites),
        "updated_at_ms": now_ms,
    }
    live["satellite_count"] = len(fresh_satellites)
    if isinstance(decoded.get("base_coords"), dict):
        live["base_coords"] = decoded["base_coords"]
    if decoded.get("packets"):
        live["rtcm_stats"] = decoded["packets"]
    elif decoded.get("rtcm_stats"):
        live["rtcm_stats"] = decoded["rtcm_stats"]


def _expects_nmea_skyview(serial: str) -> bool:
    try:
        serial_key = str(serial)
        live = device_telemetry_cache.get(serial_key) or {}
        base_config = live.get("base_config") or device_sync_cache.get(f"{serial_key}_base_config") or {}
        if not isinstance(base_config, dict) or not base_config:
            return False
        gnss_options = base_config.get("gnss_options") or {}
        output_mode = str(gnss_options.get("output_mode") or "diagnostics_rtcm").strip().lower()
        nmea = live.get("nmea_data") or {}
        last_gsv = float(nmea.get("last_gsv_ts") or 0)
        return output_mode != "rtcm_only" and bool(nmea.get("satellites")) and time.time() - last_gsv < 15
    except Exception:
        return False

# 🔥 NEW: Global sync check tracking
LAST_GLOBAL_SYNC_CHECK = 0
GLOBAL_SYNC_CHECK_INTERVAL = 60.0  # Batch check 60s/lần

DB_WRITE_INTERVAL = 120.0    # Ghi DB định kỳ 2 phút/lần nếu không có gì thay đổi
IS_SHUTTING_DOWN = False 
external_command_handler: Optional[Callable[[str, bytes], Awaitable[None]]] = None
status_broadcast_handler: Optional[Callable[[dict], Awaitable[None]]] = None
EXTERNAL_COMMAND_MAX_PENDING = max(16, int(os.getenv("MQTT_EXTERNAL_COMMAND_MAX_PENDING", "512")))
EXTERNAL_COMMAND_CONCURRENCY = max(1, int(os.getenv("MQTT_EXTERNAL_COMMAND_CONCURRENCY", "4")))
EXTERNAL_COMMAND_MAX_BYTES = 64 * 1024
external_command_pending = {}
external_command_drain_scheduled = False
MQTT_INGRESS_MAX_PENDING = max(100, int(os.getenv("MQTT_INGRESS_MAX_PENDING", "2000")))
MQTT_INGRESS_CONCURRENCY = max(1, int(os.getenv("MQTT_INGRESS_CONCURRENCY", "8")))
MQTT_INGRESS_CONTROL_TYPES = frozenset({
    "status",
    "status_update",
    "base_config_state",
    "service_config_state",
})
mqtt_ingress_lock = threading.Lock()
mqtt_ingress_pending = {}
mqtt_ingress_drain_scheduled = False
mqtt_control_ack_pending = {}
mqtt_control_ack_drain_scheduled = False
mqtt_ingress_metrics = {
    "received": 0,
    "coalesced": 0,
    "dropped": 0,
    "processed": 0,
    "control_ack_coalesced": 0,
    "control_ack_dropped": 0,
    "control_ack_published": 0,
}


def _mqtt_ingress_key(topic: str, payload: bytes) -> tuple[str, str]:
    message_type = str(topic).rsplit("/", 1)[-1]
    if message_type == "raw_data":
        if payload.startswith(b"$") and len(payload) >= 6:
            return topic, payload[3:6].decode("ascii", errors="ignore") or "nmea"
        if payload.startswith(b"{"):
            return topic, "json"
        return topic, "binary"
    return topic, message_type


def _start_mqtt_ingress_drain() -> None:
    schedule_background(_drain_mqtt_ingress())


def _drain_mqtt_control_acks() -> None:
    """Run on asyncio's thread so an ACK proves the event loop is responsive."""
    global mqtt_control_ack_drain_scheduled
    with mqtt_ingress_lock:
        pending = list(mqtt_control_ack_pending.values())
        mqtt_control_ack_pending.clear()
        mqtt_control_ack_drain_scheduled = False

    for topic, payload in pending:
        try:
            topic_parts = str(topic).split("/")
            if len(topic_parts) < 4 or topic_parts[-1] != "status":
                continue
            serial = topic_parts[2]
            data = json.loads(payload.decode())
            control_ack = build_control_plane_ack(data, channel="mqtt", serial=serial)
            if not control_ack:
                continue
            result = mqtt_client.publish(
                f"pi/devices/{serial}/control_ack",
                json.dumps(control_ack),
                qos=1,
                retain=False,
            )
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                with mqtt_ingress_lock:
                    mqtt_ingress_metrics["control_ack_published"] += 1
        except Exception as exc:
            logging.debug("MQTT control ACK fast-path failed for %s: %s", topic, exc)


def _enqueue_mqtt_control_ack(topic: str, payload: bytes) -> None:
    """Coalesce newest status probe per topic without waiting for persistence."""
    global mqtt_control_ack_drain_scheduled
    if str(topic).rsplit("/", 1)[-1] != "status":
        return
    should_schedule = False
    with mqtt_ingress_lock:
        topic_key = str(topic)
        if topic_key in mqtt_control_ack_pending:
            mqtt_ingress_metrics["control_ack_coalesced"] += 1
        elif len(mqtt_control_ack_pending) >= MQTT_INGRESS_MAX_PENDING:
            mqtt_control_ack_pending.pop(next(iter(mqtt_control_ack_pending)), None)
            mqtt_ingress_metrics["control_ack_dropped"] += 1
        mqtt_control_ack_pending[topic_key] = (topic_key, bytes(payload))
        if not mqtt_control_ack_drain_scheduled:
            mqtt_control_ack_drain_scheduled = True
            should_schedule = True
    if should_schedule:
        try:
            if not main_loop or not main_loop.is_running():
                raise RuntimeError("asyncio loop is not running")
            main_loop.call_soon_threadsafe(_drain_mqtt_control_acks)
        except RuntimeError:
            with mqtt_ingress_lock:
                mqtt_control_ack_drain_scheduled = False


def _enqueue_mqtt_ingress(topic: str, payload: bytes, is_retained: bool) -> None:
    """Called from Paho's thread; keep only bounded newest telemetry."""
    global mqtt_ingress_drain_scheduled
    key = _mqtt_ingress_key(topic, payload)
    should_schedule = False
    with mqtt_ingress_lock:
        mqtt_ingress_metrics["received"] += 1
        if key in mqtt_ingress_pending:
            mqtt_ingress_metrics["coalesced"] += 1
        elif len(mqtt_ingress_pending) >= MQTT_INGRESS_MAX_PENDING:
            if key[1] in {"binary", "json", "GGA", "GSA", "GSV", "GST", "nmea"}:
                mqtt_ingress_metrics["dropped"] += 1
                return
            mqtt_ingress_pending.pop(next(iter(mqtt_ingress_pending)), None)
            mqtt_ingress_metrics["dropped"] += 1
        mqtt_ingress_pending[key] = (topic, payload, is_retained)
        if not mqtt_ingress_drain_scheduled:
            mqtt_ingress_drain_scheduled = True
            should_schedule = True
    if should_schedule:
        try:
            if not main_loop or not main_loop.is_running():
                raise RuntimeError("asyncio loop is not running")
            main_loop.call_soon_threadsafe(_start_mqtt_ingress_drain)
        except RuntimeError:
            with mqtt_ingress_lock:
                mqtt_ingress_drain_scheduled = False


def _enqueue_external_command(topic: str, payload: bytes) -> None:
    """Keep external NTRIP command work bounded before scheduling on asyncio."""
    global external_command_drain_scheduled
    raw_payload = bytes(payload or b"")
    if len(raw_payload) > EXTERNAL_COMMAND_MAX_BYTES:
        logging.warning("Dropped oversized external MQTT command")
        return
    key = (str(topic), hashlib.sha256(raw_payload).digest())
    should_schedule = False
    with mqtt_ingress_lock:
        if key in external_command_pending:
            return
        if len(external_command_pending) >= EXTERNAL_COMMAND_MAX_PENDING:
            logging.warning("Dropped external MQTT command because the bounded queue is full")
            return
        external_command_pending[key] = (str(topic), raw_payload)
        if not external_command_drain_scheduled:
            external_command_drain_scheduled = True
            should_schedule = True
    if should_schedule:
        try:
            if not main_loop or not main_loop.is_running():
                raise RuntimeError("asyncio loop is not running")
            main_loop.call_soon_threadsafe(_start_external_command_drain)
        except RuntimeError:
            with mqtt_ingress_lock:
                external_command_drain_scheduled = False


def _start_external_command_drain() -> None:
    schedule_background(_drain_external_commands())


async def _drain_external_commands() -> None:
    global external_command_drain_scheduled
    while not IS_SHUTTING_DOWN:
        with mqtt_ingress_lock:
            handler = external_command_handler
            if handler is None:
                external_command_pending.clear()
                external_command_drain_scheduled = False
                return
            batch = []
            while external_command_pending and len(batch) < EXTERNAL_COMMAND_CONCURRENCY:
                _, envelope = external_command_pending.popitem()
                batch.append(envelope)
            if not batch:
                external_command_drain_scheduled = False
                return
        results = await asyncio.gather(
            *(handler(topic, payload) for topic, payload in batch),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                logging.error("External MQTT command worker failed: %s", result)
    with mqtt_ingress_lock:
        external_command_drain_scheduled = False


async def _drain_mqtt_ingress() -> None:
    global mqtt_ingress_drain_scheduled
    while not IS_SHUTTING_DOWN:
        with mqtt_ingress_lock:
            batch = []
            while mqtt_ingress_pending and len(batch) < MQTT_INGRESS_CONCURRENCY:
                # Control/status packets carry fail-safe ACKs and config state,
                # so serve their oldest key first. FIFO within each class keeps
                # continuously reinserted raw-data keys from starving peers.
                selected_key = next(
                    (
                        key for key in mqtt_ingress_pending
                        if key[1] in MQTT_INGRESS_CONTROL_TYPES
                    ),
                    next(iter(mqtt_ingress_pending)),
                )
                envelope = mqtt_ingress_pending.pop(selected_key)
                batch.append(envelope)
            if not batch:
                mqtt_ingress_drain_scheduled = False
                return

        results = await asyncio.gather(
            *(
                handle_message_async(topic, payload, is_retained=is_retained)
                for topic, payload, is_retained in batch
            ),
            return_exceptions=True,
        )
        with mqtt_ingress_lock:
            mqtt_ingress_metrics["processed"] += len(batch)
        for result in results:
            if isinstance(result, Exception):
                logging.error("MQTT ingress worker error: %s", result)

    with mqtt_ingress_lock:
        mqtt_ingress_drain_scheduled = False

def invalidate_device_cache(serial: str):
    """Xóa cache của thiết bị để buộc background worker fetch lại từ DB."""
    # A config/token mutation is unsynced until the server-side batch checker
    # proves otherwise. This prevents a stale agent self-report from briefly
    # flipping the UI back to synced.
    device_sync_cache[serial] = False
    device_last_sync_check.pop(serial, None)
    device_write_cache.pop(serial, None)
    device_metadata_cache.pop(serial, None)
    with device_deletion_tombstones_lock:
        device_deletion_tombstones.discard(str(serial))
    logging.info(f"♻️ Cache invalidated for device {serial}")

# ==============================================================================
# MQTT CALLBACKS
# ==============================================================================

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logging.info("✓ MQTT Connected Successfully.")
        client.subscribe("pi/devices/+/status", qos=0)
        client.subscribe("pi/devices/+/raw_data", qos=0)
        client.subscribe("pi/devices/+/base_config_state", qos=1)
        client.subscribe("pi/devices/+/service_config_state", qos=1)
        # NTRIP control plane -> backend listens and translates to agent commands.
        client.subscribe("ntrip/rtcm/start", qos=1)
        client.subscribe("ntrip/rtcm/stop", qos=1)
        client.subscribe("pi/decoder/esp_base/sync_request", qos=1)
    else:
        logging.error(f"MQTT Connect Failed with code: {rc}")

def on_disconnect(client, userdata, rc, properties=None):
    if rc != 0:
        logging.warning(f"⚠️ MQTT Disconnected unexpectedly (RC: {rc}). Auto-reconnecting...")

def on_message(client, userdata, msg):
    if IS_SHUTTING_DOWN: 
        return

    if msg.topic == "pi/decoder/esp_base/sync_request":
        if main_loop and main_loop.is_running():
            from .esp_base_manager import resync_decoder_workers
            main_loop.call_soon_threadsafe(lambda: schedule_background(resync_decoder_workers()))
        return

    if msg.topic.startswith("ntrip/rtcm/"):
        if external_command_handler and main_loop and main_loop.is_running():
            _enqueue_external_command(msg.topic, msg.payload)
        else:
            logging.warning("Received NTRIP MQTT command but no external command handler is registered")
        return
    
    if main_loop and main_loop.is_running():
        _enqueue_mqtt_control_ack(msg.topic, msg.payload)
        _enqueue_mqtt_ingress(
            msg.topic,
            msg.payload,
            bool(getattr(msg, 'retain', False)),
        )

# ==============================================================================
# 🔥 BACKGROUND TASK: BATCH SYNC CHECKER (Chạy 60s/lần)
# ==============================================================================

async def batch_sync_checker():
    """
    Background task chạy mỗi 60s để check sync status của TẤT CẢ devices online.
    Chỉ MỞ 1 KẾT NỐI DB duy nhất cho tất cả devices.
    
    BEFORE: 30 devices × 1 DB conn/device/msg = 30+ conn/s → Pool tràn
    AFTER:  1 DB conn cho tất cả devices/60s → Giảm 1800 lần!
    """
    global LAST_GLOBAL_SYNC_CHECK
    
    while True:
        await asyncio.sleep(GLOBAL_SYNC_CHECK_INTERVAL)
        
        if IS_SHUTTING_DOWN:
            break
        
        try:
            # Lấy danh sách devices online từ cache (không cần DB)
            online_serials = [
                serial for serial, status in device_status_cache.items()
                if status in ['online', 'sleep', 'streaming', 'starting', 'configuring', 'locked']
            ]
            
            if not online_serials:
                continue
            
            # 🔥 CHỈ MỞ 1 KẾT NỐI DB DUY NHẤT
            async with device_session_scope() as session:
                # Lấy tất cả devices cùng lúc bằng 1 query
                result = await session.execute(
                    select(models.Device).filter(
                        models.Device.serial.in_(online_serials)
                    )
                )
                devices = result.scalars().all()
                
                checked_count = 0
                
                # Check sync cho từng device (KHÔNG mở thêm DB connection)
                for device in sorted(devices, key=lambda item: item.serial):
                    serial = device.serial
                    if device.name:
                        device_name_cache[serial] = device.name
                    
                    # Lấy config từ cache (đã nhận qua MQTT)
                    agent_base = device_sync_cache.get(f"{serial}_base_config", {})
                    agent_service = crud.normalize_service_config(
                        device_sync_cache.get(f"{serial}_service_config")
                    )
                    
                    # Serialize policy retries with admin edits so an older retry
                    # cannot re-enable a host the admin just blocked.
                    policy_row_locked = bool(device.server_config_lock)
                    if policy_row_locked:
                        device = await crud.get_device_for_update(session, serial)
                        if not device:
                            continue
                    # DB config (chuẩn)
                    db_base = device.base_config or {}
                    db_service = crud.normalize_service_config(device.service_config)
                    
                    is_relay_like = serial.startswith(("RELAY_", "MONITOR_"))
                    
                    # Base 4G reports decoded observations, not editable target settings.
                    from .mobile_base import is_readonly_base
                    readonly_4g = is_readonly_base(device)
                    # So sánh
                    base_ok, base_errs = _compare_configs(agent_base, db_base, "Base", is_relay=is_relay_like)
                    srv_ok, srv_errs = _compare_configs(agent_service, db_service, "Service", is_relay=is_relay_like)
                    
                    if readonly_4g:
                        base_ok, srv_ok, base_errs, srv_errs = True, True, [], []

                    if device.server_config_lock and not readonly_4g:
                        desired = locked_server_ids(device.server_config_lock)
                        report = (device.system_info or {}).get("device_lock") or {}
                        applied = report.get("locked_servers")
                        if supports_server_device_lock(device):
                            if applied == desired and not report.get("all"):
                                device.is_locked = False
                            if applied != desired or device.is_locked or report.get("all"):
                                from .command_dispatcher import send_command_to_pi
                                try:
                                    await send_command_to_pi(serial, {"command": "LOCK_DEVICE", "payload": {"locked_servers": desired}})
                                except Exception as exc:
                                    logging.warning("Device lock for %s awaits delivery (%s)", serial, type(exc).__name__)
                            elif not desired:
                                device.server_config_lock = None
                        elif not desired:
                            from .command_dispatcher import send_command_to_pi
                            try:
                                await send_command_to_pi(serial, {"command": "UNLOCK_DEVICE", "payload": {}})
                                device.server_config_lock = None
                                device.is_locked = False
                            except Exception as exc:
                                logging.warning("Unlock for %s awaits delivery (%s)", serial, type(exc).__name__)
                        else:
                            logging.warning("Device lock for %s requires an agent update", serial)

                    version_ok = _version_matches(device.version)
                    version_errs = [] if version_ok else [f"[Version] Agent={device.version or '??'} vs Server={settings.APP_VERSION}"]
                    report = (device.system_info or {}).get("device_lock") or {}
                    lock_ok = readonly_4g or not device.server_config_lock or (report.get("locked_servers") == locked_server_ids(device.server_config_lock) and not report.get("all"))
                    if not lock_ok:
                        version_errs.append("Khoá server chưa được agent xác nhận")
                    is_synced = base_ok and srv_ok and version_ok and lock_ok
                    
                    # Cập nhật cache
                    old_sync = device_sync_cache.get(serial)
                    device_sync_cache[serial] = is_synced
                    if not is_synced:
                        device_sync_cache[f"{serial}_errors"] = base_errs + srv_errs + version_errs
                    else:
                        device_sync_cache.pop(f"{serial}_errors", None)
                    device_last_sync_check[serial] = time.time()
                    
                    # Log nếu sync status thay đổi
                    if old_sync is not None and old_sync != is_synced:
                        if is_synced:
                            logging.info("Device %s SYNCED", serial)
                        else:
                            logging.warning("Device %s OUT OF SYNC: %s", serial, base_errs + srv_errs + version_errs)
                    
                    sync_changed = device.is_synced != is_synced
                    if sync_changed:
                        device.is_synced = is_synced
                    if sync_changed or policy_row_locked:
                        await session.commit()
                    checked_count += 1
            
            LAST_GLOBAL_SYNC_CHECK = time.time()
            logging.info(f"✓ Batch sync check completed: {checked_count}/{len(online_serials)} devices")
        
        except Exception as e:
            logging.error(f"❌ Batch sync checker error: {e}", exc_info=True)

# ==============================================================================
# MAIN MESSAGE HANDLER (ĐàTỐI ƯU HÓA - KHÔNG MỞ DB MỖI MESSAGE)
# ==============================================================================

async def handle_message_async(topic: str, payload: bytes, is_retained: bool = False):
    if IS_SHUTTING_DOWN:
        return
        
    try:
        topic_parts = topic.split('/')
        if len(topic_parts) != 4 or topic_parts[:2] != ["pi", "devices"]:
            return
        
        message_type = topic_parts[-1]
        serial = topic_parts[2]
        if crud.is_blocked_device_serial(serial) or device_was_deleted(serial):
            return
        
        # ==================== 1. XỬ LÝ NMEA DATA ====================
        if message_type == "raw_data":
            if payload.startswith(b'{'):
                try:
                    data = json.loads(payload.decode('ascii'))
                    if data.get("type") == "rtcm_skyview_calculated":
                        # Preserve decoded packet statistics even when NMEA owns the skyview.
                        _cache_rtcm_signal_snapshot(serial, data)
                        if _expects_nmea_skyview(serial):
                            data = {**data, "satellites": []}
                        await manager.broadcast({
                            "type": "rtcm_signal_update",
                            "serial": serial,
                            "data": data
                        })
                        return
                    elif data.get("type") == "rtcm_skyview":
                        decoded_rtcm = rtcm_signal_decoder.decode_json(serial, data)
                        if decoded_rtcm:
                            _cache_rtcm_signal_snapshot(serial, decoded_rtcm)
                            await manager.broadcast({
                                "type": "rtcm_signal_update",
                                "serial": serial,
                                "data": decoded_rtcm
                            })
                        return
                except Exception:
                    pass

            # Optimize: If payload is NMEA, parse it immediately and return
            if payload.startswith(b'$'):
                try:
                    line = payload.decode('ascii', errors='ignore')
                    parsed_data = parse_nmea_sentence(serial, line)
                    if parsed_data:
                        live = device_telemetry_cache.setdefault(serial, {})
                        nmea_live = live.setdefault("nmea_data", {})
                        parsed_type = str(parsed_data.get("type") or "").upper()
                        nmea_live["last_nmea_ts"] = time.time()
                        if parsed_type == "GGA":
                            nmea_live["gga"] = parsed_data
                        elif parsed_type == "GSA":
                            nmea_live["gsa"] = parsed_data
                        elif parsed_type == "GSV":
                            nmea_live["satellites"] = parsed_data.get("satellites") or []
                            nmea_live["last_gsv_ts"] = nmea_live["last_nmea_ts"]
                        elif parsed_type:
                            nmea_live[parsed_type.lower()] = parsed_data
                        await manager.broadcast({
                            "type": "nmea_update",
                            "serial": serial,
                            "data": parsed_data
                        })
                except Exception as e:
                    logging.debug(f"NMEA parse error: {e}")
                return

            # If payload is RTCM (0xD3), only skip decoding when NMEA GSV
            # already provides skyview data. GGA/GSA-only streams still need
            # RTCM decode to populate the skyview.
            is_rtcm = len(payload) > 0 and payload[0] == 0xD3
            if is_rtcm:
                live = device_telemetry_cache.get(serial, {})
                nmea_live = live.get("nmea_data", {})
                last_gsv_ts = nmea_live.get("last_gsv_ts") or 0
                has_nmea_skyview = (time.time() - last_gsv_ts) < 60
                
                if not has_nmea_skyview:
                    decoded_rtcm = await rtcm_signal_decoder.decode(serial, payload)
                    if decoded_rtcm:
                        _cache_rtcm_signal_snapshot(serial, decoded_rtcm)
                        await manager.broadcast({
                            "type": "rtcm_signal_update",
                            "serial": serial,
                            "data": decoded_rtcm
                        })
            return

        # ==================== 2. XỬ LÝ STATUS UPDATE ====================
        try:
            data = json.loads(payload.decode())
            # 🔥 FIX: Đảm bảo data là dict, nếu là list hoặc kiểu khác thì bỏ qua
            if not isinstance(data, dict):
                logging.warning(f"⚠️ Received non-dict payload on topic {topic}: {type(data)}")
                return

            payload_serial = str(data.get("serial") or "").strip()
            if payload_serial and payload_serial.upper() != serial.upper():
                logging.warning("Rejected MQTT status serial mismatch: topic=%s payload=%s", serial, payload_serial)
                return
            data["serial"] = serial
            if isinstance(data.get("service_config"), (dict, list)):
                data["service_config"] = schemas.strip_status_config_secrets(data["service_config"])

            if 'rtk_fix_status' not in data:
                # Fallback: Dựa vào status cũ (nếu agent chưa update)
                if data.get('status') in ['online', 'sleep']:
                    data['rtk_fix_status'] = 'DGPS'
                    data['base_mode_active'] = bool(data.get('base_config'))
                else:
                    data['rtk_fix_status'] = 'NO_FIX'
                    data['base_mode_active'] = False
        except json.JSONDecodeError as e:
            logging.error(f"❌ JSON decode error on topic {topic}: {e}")
            return
        except UnicodeDecodeError as e:
            logging.error(f"❌ Unicode decode error on topic {topic}: {e}")
            return

        if message_type in ["status", "status_update"]:
            metadata = None

            # Live heartbeats can discover valid identities, but a station
            # explicitly deleted during this server run must stay deleted.
            if is_retained:
                try:
                    metadata = await get_device_metadata(serial)
                    if not metadata.get("exists"):
                        logging.info(f"Ignoring retained ghost status for unknown serial: {serial}")
                        return
                except Exception as e:
                    logging.warning(f"Retained ghost-check failed for {serial}: {e}")
                    return

            now = time.time()
            # Reject duplicate/out-of-order dual-channel heartbeats before any
            # DB lookup or enrichment work.
            status_decision = await status_reconciler.accept(
                data,
                source="mqtt",
                retained=is_retained,
                received_at=now,
            )
            if not status_decision.accepted:
                logging.debug("Ignored MQTT status for %s: %s", serial, status_decision.reason)
                return
            data = status_decision.payload
            
            # --- 2.1. TÍNH TOÁN BPS ---
            ntrip_stats = data.get('ntrip_stats')
            if ntrip_stats and isinstance(ntrip_stats, dict):
                data['bps'] = sum(int(value or 0) for key, value in ntrip_stats.items() if key.startswith('server') and key.endswith('_bps'))
            
            # --- 2.2. LƯU CONFIG VÀO CACHE ĐỂ BATCH CHECKER DÙNG ---
            # 🔥 QUAN TRỌNG: Lưu config vào cache để batch checker có thể so sánh
            cache_agent_sync_inputs(serial, data)
            command_source = data.get('command_source')
            command_source_changed = False
            if isinstance(command_source, dict):
                system_info = data.get('system_info') if isinstance(data.get('system_info'), dict) else {}
                system_info['command_source'] = command_source
                data['system_info'] = system_info
                command_cache_key = f"{serial}_command_source"
                command_source_changed = device_sync_cache.get(command_cache_key) != command_source
                if command_source_changed:
                    device_sync_cache[command_cache_key] = command_source
            
            # --- 2.3. ĐỌC SYNC STATUS TỪ CACHE (KHÔNG MỞ DB) ---
            # 🔥 THAY ĐỔI CHÍNH: Không check DB mỗi message nữa!
            # Batch checker sẽ update cache mỗi 60s
            raw_status = str(data.get("status") or "").strip().lower()
            is_synced = device_sync_cache.get(serial, True)
            sync_errors = device_sync_cache.get(f"{serial}_errors", [])[:]
            agent_version = data.get("version")
            if agent_version and not _version_matches(agent_version):
                is_synced = False
                err_msg = f"Agent version {agent_version or '??'} != server {settings.APP_VERSION}"
                if err_msg not in sync_errors:
                    sync_errors.append(err_msg)

            agent_token_fp = data.get("agent_token_fingerprint")
            if metadata is None:
                try:
                    metadata = await get_device_metadata(serial)
                except Exception as e:
                    metadata = {"exists": False}
                    logging.debug("Device metadata lookup failed for %s: %s", serial, e)

            # A discovered agent can hold a local token while no DB row exists.
            # Its fingerprint alone cannot establish server-side registration.
            if metadata.get("exists") or crud.is_valid_serial(serial):
                agent_reported_status = raw_status
                license_bypass = bool(metadata.get("license_bypass"))
                configured_token = str(metadata.get("device_token") or "").strip()
                expected_fp = (
                    _fingerprint_token(configured_token)
                    if license_manager.is_compatible_device_token(configured_token, serial)
                    else None
                )
                license_state = evaluate_license_sync(
                    expected_fp,
                    agent_token_fp,
                    license_bypass=license_bypass,
                )
                token_check_available = license_state.token_check_available
                token_synced = license_state.token_synced
                token_deployment_pending = bool(
                    metadata.get("token_deployment_pending")
                    and expected_fp
                    and not (token_check_available and token_synced)
                )
                needs_token_deploy = license_state.needs_token_deploy or (
                    token_deployment_pending and not license_bypass
                )
                if needs_token_deploy:
                    is_synced = False
                    if expected_fp:
                        sync_errors.append("Agent token is missing or does not match server token")
                    else:
                        sync_errors.append("Server token has not been issued to this agent")
                    data["status"] = "awaiting_license"
                    data["bps"] = 0
                    data["ntrip_connected"] = False
                    data["ntrip_status"] = {}
                    data["ntrip_stats"] = {}
                system_info = data.get("system_info") if isinstance(data.get("system_info"), dict) else {}
                system_info["auth_status"] = {
                    "server_token_present": bool(expected_fp),
                    "token_required": bool(expected_fp),
                    "token_synced": token_synced if token_check_available else None,
                    "agent_token_present": bool(agent_token_fp) if token_check_available else None,
                    "needs_token_deploy": needs_token_deploy,
                    "license_bypass": license_bypass,
                    "agent_reported_status": agent_reported_status,
                    "token_check_available": token_check_available,
                    "token_deployment_pending": token_deployment_pending,
                }
                system_info["auth_license_bypass"] = license_bypass
                data["system_info"] = system_info
                if metadata.get("name"):
                    data["name"] = metadata["name"]

            status_for_offline_time = str(data.get("status") or "").strip().lower()
            if status_for_offline_time == "offline":
                if str(metadata.get("status") or "").strip().lower() == "offline":
                    data["offline_since"] = metadata.get("offline_since")
                else:
                    try:
                        previous_timestamp = int(metadata.get("timestamp") or 0)
                    except (TypeError, ValueError):
                        previous_timestamp = 0
                    data["offline_since"] = (
                        min(int(now), previous_timestamp + 180)
                        if previous_timestamp > 0 and now - previous_timestamp > 180
                        else int(now)
                    )
            elif status_for_offline_time:
                data["offline_since"] = None

            # Ensure savings_stats and antenna_status are persisted in system_info JSONB
            system_info = data.get("system_info") if isinstance(data.get("system_info"), dict) else {}
            savings_stats = data.get("savings_stats")
            if savings_stats and isinstance(savings_stats, dict):
                system_info["savings_stats"] = savings_stats
            antenna_status = data.get("antenna_status")
            if antenna_status and isinstance(antenna_status, dict):
                system_info["antenna_status"] = antenna_status
            data["system_info"] = system_info

            # --- CẬP NHẬT DATA ĐỂ GỬI UI ---
            data['is_synced'] = is_synced
            data['sync_errors'] = sync_errors
            cached_name = metadata.get("name") or device_name_cache.get(serial)
            if cached_name:
                data['name'] = cached_name

            # --- 2.4. BẢO VỆ TÊN & CONFIG TRẠM ---
            # Bản tin LWT (offline) không kèm tên/config -> lấy từ DB trước khi broadcast
            new_status = data.get('status')
            if not data.get('name') or new_status == 'offline':
                try:
                    if metadata.get("name"):
                        data['name'] = metadata["name"]
                    if new_status == 'offline':
                        if not data.get('base_config') and metadata.get("base_config"):
                            data['base_config'] = metadata["base_config"]
                        if not data.get('service_config') and metadata.get("service_config"):
                            data['service_config'] = metadata["service_config"]
                        if not data.get('detected_chip_type') and metadata.get("detected_chip_type"):
                            data['detected_chip_type'] = metadata["detected_chip_type"]
                except Exception:
                    pass  # Không tên vẫn broadcast được, không chặn luồng

            # --- 2.5. BROADCAST REAL-TIME TO UI ---
            live_snapshot = dict(device_telemetry_cache.get(serial) or {})
            if crud.is_mobile_base(serial) or str(serial or "").lower().startswith("esp_"):
                incoming_rtcm = data.get("rtcm_stats")
                existing_rtcm = live_snapshot.get("rtcm_stats")
                if (not incoming_rtcm or incoming_rtcm.get("decoder") is False) and existing_rtcm and any(k.isdigit() for k in existing_rtcm.keys()):
                    data["rtcm_stats"] = existing_rtcm
                existing_nmea = live_snapshot.get("nmea_data") or {}
                incoming_nmea = data.get("nmea_data")
                if (not incoming_nmea or not incoming_nmea.get("satellites")) and existing_nmea.get("satellites"):
                    data.setdefault("nmea_data", {})["satellites"] = existing_nmea["satellites"]
            live_snapshot.update(data)
            device_telemetry_cache[serial] = live_snapshot
            status_message = {
                "type": "status_update", 
                "data": data,
                # Main's coalescing boundary rechecks this after taking its
                # queue lock, closing the delete-vs-retained-broadcast race.
                "_drop_if_device_missing": bool(is_retained),
            }
            if status_broadcast_handler:
                await status_broadcast_handler(status_message)
            else:
                await manager.broadcast(status_message)
            
            # --- 2.5. LOGIC QUYẾT ĐỊNH GHI DB ---
            last_write_time = device_write_cache.get(serial, 0)
            last_known_status = device_status_cache.get(serial, "unknown")
            new_status = data.get("status")
            reported_system_info = data.get("system_info")
            reported_auth_status = (
                reported_system_info.get("auth_status")
                if isinstance(reported_system_info, dict)
                else None
            )
            token_pending_state_changed = (
                bool(metadata.get("token_deployment_pending"))
                != bool(
                    isinstance(reported_auth_status, dict)
                    and reported_auth_status.get("token_deployment_pending")
                )
            )
            
            should_write_db = False
            reported_device_lock = (data.get('system_info') or {}).get('device_lock')
            device_lock_changed = reported_device_lock != metadata.get('device_lock')
            # Detection can finish between heartbeats without changing online status.
            # Persist it immediately so offline enrichment cannot restore an old chip.
            reported_chip = data.get('detected_chip_type')
            chip_type_changed = (
                new_status != 'offline'
                and bool(reported_chip)
                and reported_chip != 'UNKNOWN'
                and reported_chip != metadata.get('detected_chip_type')
            )
            boot_result_changed = (
                not is_retained and crud.confirmed_boot_auto_base(data)
                and ((metadata.get('base_config') or {}).get('base_setup_method') in ('SURVEY_IN', 'AUTO_CORS')
                     or (metadata.get('base_config') or {}).get('coordinate_source') == 'AUTO_CORS')
                and (metadata.get('base_config') or {}).get('auto_base_started_boot_id') != data.get('status_boot_id')
            )
            
            # Điều kiện 1: Trạng thái Online/Offline thay đổi
            if command_source_changed or device_lock_changed or chip_type_changed or boot_result_changed:
                should_write_db = True
            elif token_pending_state_changed:
                should_write_db = True
            elif new_status and new_status != last_known_status:
                should_write_db = True
                if last_known_status == "online" and new_status == "offline":
                    logging.warning(f"⚠️ DEVICE OFFLINE: {serial}")
                elif last_known_status == "offline" and new_status == "online":
                    logging.info(f"✓ Device back online: {serial}")

            # Điều kiện 2: Heartbeat (Quá thời gian chưa ghi)
            elif (now - last_write_time) > DB_WRITE_INTERVAL:
                should_write_db = True
                logging.debug(f"⏰ Heartbeat update for {serial}")
            
            # --- 2.6. THỰC HIỆN GHI DB ---
            if should_write_db:
                # Chuẩn bị dữ liệu sạch cho DB
                db_data = data.copy()
                db_data['timestamp'] = int(now)
                db_data['is_synced'] = is_synced
                
                # 🔥 PRESERVE CONFIG KHI DEVICE OFFLINE
                # Nếu device offline, KHÔNG cập nhật config & chip_type
                # Để tránh để config bị xóa khi device tạm ngoại tuyến
                if new_status == 'offline':
                    # Xóa config từ db_data để CRUD không cập nhật
                    db_data.pop('base_config', None)
                    db_data.pop('service_config', None)
                    db_data.pop('detected_chip_type', None)
                
                # Loại bỏ các field tạm thời
                fields_to_remove = [
                    'bps', 'base_coords', 'gnss_stats', 'sync_errors', 'rtcm_stats'
                ]
                
                for field in fields_to_remove:
                    if field in db_data:
                        del db_data[field]
                
                # Retry logic với exponential backoff
                max_db_retries = 3
                for attempt in range(max_db_retries):
                    try:
                        # Single-writer section: avoids pool exhaustion under reconnect storms.
                        async with mqtt_db_write_lock:
                            if device_was_deleted(serial):
                                logging.info("Skipping MQTT status write after deletion: %s", serial)
                                return
                            if is_retained:
                                current_metadata = device_metadata_cache.get(serial) or {}
                                if current_metadata.get("exists") is False:
                                    logging.info(
                                        "Skipping retained status write after deletion: %s",
                                        serial,
                                    )
                                    return
                            latest_write_time = device_write_cache.get(serial, 0)
                            latest_known_status = device_status_cache.get(serial, "unknown")
                            still_needs_write = bool(
                                command_source_changed
                                or device_lock_changed
                                or chip_type_changed
                                or boot_result_changed
                                or token_pending_state_changed
                                or (new_status and new_status != latest_known_status)
                                or (now - latest_write_time) > DB_WRITE_INTERVAL
                            )
                            if not still_needs_write:
                                break
                            async with device_session_scope() as session:
                                updated_device = await crud.update_or_create_device(
                                    session,
                                    device_data=db_data
                                )
                                await session.commit()
                                if updated_device:
                                    await cache_device_metadata(updated_device)
                            device_write_cache[serial] = time.time()
                            if new_status:
                                device_status_cache[serial] = new_status
                        
                        break # Success
                    except Exception as db_error:
                        if attempt < max_db_retries - 1:
                            # Exponential backoff: 0.5s, 1s, 2s
                            await asyncio.sleep(0.5 * (2 ** attempt))
                        else:
                            logging.error(f"❌ DB Write Failed for {serial}: {db_error}")

    except Exception as e:
        logging.error(f"❌ Critical error in handle_message_async: {e}", exc_info=True)

# ==============================================================================
# CONFIG COMPARISON (Giữ nguyên)
# ==============================================================================

def _compare_configs(config1: dict, config2: dict, prefix: str = "", is_relay: bool = False) -> tuple[bool, list[str]]:
    """
    So sánh cấu hình: config1 (Agent/Real), config2 (DB/Target).
    Đã tối ưu để tránh lỗi NoneType và sai số Float.
    """
    errors = []
    
    if not isinstance(config1, dict): 
        config1 = {}
    if not isinstance(config2, dict): 
        config2 = {}

    ignore_fields = {
        'timestamp', 'last_update', 'created_at', 'updated_at',
        'is_provisioned', 'device_name', 'serial',
        'is_synced', 'sync_errors', 'warning',
        'bps', 'ntrip_connected', 'ntrip_status', 'gnss_stats',
        'user_id',
        # Runtime wake/sleep changes independently of the saved service config.
        'stream_active', 'server1_stream_active', 'server2_stream_active'
    }
    if prefix == 'Base.conversion_report.location':
        # The province lookup echoes its input. The actual Base position is compared under Base.coords.
        ignore_fields.add('input_coord')
    if (prefix == 'Base' and config1.get('base_setup_method') == 'SURVEY_IN'
            and config2.get('base_setup_method') == 'SURVEY_IN'):
        # Fixed LLA fields may remain in the DB after switching to Survey-In.
        # Survey targets are survey_in_duration and survey_in_accuracy instead.
        ignore_fields.update({'coords', 'accuracy', 'altitude_reference'})

    def is_secret_field(key: object) -> bool:
        normalized = str(key or "").strip().lower().replace("-", "_")
        return any(marker in normalized for marker in ("password", "passwd", "secret", "token")) or normalized.endswith(("_pass", "pass"))

    def normalize_val(v):
        if isinstance(v, dict):
            return {key: normalize_val(value) for key, value in v.items()}
        if isinstance(v, list):
            return [normalize_val(value) for value in v]
        if v is None: 
            return ""
        s = str(v).strip().lower()
        if s == "true": 
            return "true"
        if s == "false": 
            return "false"
        if s == "none" or s == "null": 
            return ""
        # Xử lý số float: làm tròn về 9 chữ số thập phân để so sánh
        try:
            f = float(s)
            return round(f, 9) 
        except ValueError:
            return s

    if prefix == "Service":
        def is_disabled(value):
            return normalize_val(value) in ("false", "off", 0.0, "")

        # A disabled service may omit its connection settings from telemetry.
        # Still compare enable flags whenever the agent reports it running.
        enable_fields = {key for key in config2 if key == "rtcm_enabled" or (
            key.startswith("server") and key.endswith("_enabled")
            and key[6:-8].isdigit())}
        disabled_fields = {key for key in enable_fields
                           if is_disabled(config2[key]) and is_disabled(config1.get(key))}
        for flag in disabled_fields:
            ignore_fields.add(flag)
            if flag == "rtcm_enabled":
                ignore_fields.update({"rtcmserver1", "rtcmport1", "rtcmmountpoint1",
                                      "rtcmusername1", "rtcmntripversion1"})
            else:
                server_id = flag[6:-8]
                ignore_fields.update(f"{field}{server_id}" for field in
                                     ("serverhost", "port", "mountpoint", "username", "ntrip_version"))
                ignore_fields.add(f"server{server_id}_stream_on_demand")
        if enable_fields and disabled_fields == enable_fields:
            ignore_fields.update({"ncomport", "reconnectioninterval", "stream_on_demand"})

    for key, db_val in config2.items():
        if key in ignore_fields or is_secret_field(key):
            continue
            
        agent_val = config1.get(key)
        current_path = f"{prefix}.{key}" if prefix else key

        # Legacy fixed-position commands omitted this field and preserved the
        # receiver's geoid configuration. Missing metadata never proves ELLIPSOID.
        if (key == 'altitude_reference' and agent_val is None
                and config1.get('base_setup_method') == 'FIXED_LLA'
                and normalize_val(db_val) == 'legacy'):
            agent_val = 'LEGACY'
        
        if agent_val is None and db_val is not None:
            if is_relay:
                continue
            errors.append(f"[{current_path}] Agent=None")
            continue
            
        if isinstance(db_val, dict):
            if not isinstance(agent_val, dict):
                if not db_val and not agent_val: 
                    continue
                errors.append(f"[{current_path}] Type mismatch")
                continue
            
            is_match, sub_errors = _compare_configs(agent_val, db_val, current_path, is_relay=is_relay)
            errors.extend(sub_errors)
            continue

        norm_db = normalize_val(db_val)
        norm_agent = normalize_val(agent_val)
        
        if norm_db != norm_agent:
            if norm_db == "" and norm_agent == "":
                continue
            
            errors.append(f"[{current_path}] DB={db_val} vs Agent={agent_val}")

    return (len(errors) == 0), errors

# ==============================================================================
# START/STOP MQTT
# ==============================================================================

def start_mqtt_loop():
    global main_loop, IS_SHUTTING_DOWN
    try:
        IS_SHUTTING_DOWN = False
        main_loop = asyncio.get_running_loop()
        
        mqtt_client.on_connect = on_connect
        mqtt_client.on_disconnect = on_disconnect
        mqtt_client.on_message = on_message
        
        mqtt_client.connect_async(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=60)
        mqtt_client.loop_start()
        
        # 🔥 START BATCH SYNC CHECKER TASK
        schedule_background(batch_sync_checker())
        
        logging.info("✓ MQTT Loop + Batch Sync Checker Started.")
    except Exception as e:
        logging.error(f"MQTT Start Error: {e}")


def stop_mqtt_loop():
    global IS_SHUTTING_DOWN
    try:
        IS_SHUTTING_DOWN = True
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        logging.info("MQTT Stopped.")
    except:
        pass


def publish_message(topic: str, payload: str, qos: int = 1):
    if not mqtt_client.is_connected():
        raise ConnectionError("MQTT not connected, cannot publish")
    info = mqtt_client.publish(topic, payload, qos=qos)
    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        raise ConnectionError(f"MQTT command publish returned rc={info.rc}")
    return info


def set_external_command_handler(handler: Callable[[str, bytes], Awaitable[None]]):
    """Register a coroutine handler for non-device MQTT command topics."""
    global external_command_handler
    external_command_handler = handler


def set_status_broadcast_handler(handler: Callable[[dict], Awaitable[None]]):
    """Route station status through main's per-station coalescing queue."""
    global status_broadcast_handler
    status_broadcast_handler = handler

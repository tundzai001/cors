"""Station telemetry buffers, heartbeat workers and WebSocket transports."""

import asyncio
import logging
import time
from typing import Any, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, status
import os
import gc
import hmac
import json
from collections import deque

from . import auth, crud, models, schemas
from .database import auth_session_scope, device_session_scope, settings
from . import mqtt as mqtt_handler
from .monitoring import CircuitBreaker, health_monitor
from .websocket import manager as ui_manager
from .pi_websocket import pi_manager
from .status_reconciler import status_reconciler
from .control_plane_ack import build_control_plane_ack
from . import license_manager
from .station_config import _device_service_config
from .ntrip_control import _do_ntrip_stop, _do_ntrip_wake, _resolve_station_serial_by_mountpoint
from .caster_status import _on_device_status_update

logger = logging.getLogger("app.main")


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
    safe_payload = dict(payload)
    if isinstance(safe_payload.get("service_config"), (dict, list)):
        safe_payload["service_config"] = schemas.strip_status_config_secrets(safe_payload["service_config"])
    if not STATUS_PAYLOAD_COMPACT_ENABLED:
        return safe_payload

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
        "ntrip_stream_state",
        "nmea_data",
        "nmea_health",
        "version",
        "satellite_count",
        "hacc",
        "gnss_quality",
        "gnss_dop",
        "gnss_satellites",
        "gnss_rf",
        "gnss_security",
        "gnss_security_log",
        "gnss_rf_monitor",
        "gnss_spectrum",
        "gnss_receiver",
        "transport_status",
        "last_command_result",
        "reconnect_counters",
        "queue_metrics",
        "serial_health",
        "last_raw_gga",
        "last_raw_gga_age",
        "last_gga_fix_status",
        "rtk_fix_status",
    )
    compact = {key: safe_payload[key] for key in allowed_keys if key in safe_payload}

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
    timeout_seconds = timeout_threshold
    timeout_threshold = now - timeout_seconds

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
            device.offline_since = min(now, int(device.timestamp or now) + timeout_seconds)
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
                    device.offline_since = min(now, int(device.timestamp or now) + HEARTBEAT_TIMEOUT)
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


async def _safe_ws_close(websocket: WebSocket, code: int = 1000, reason: str = "") -> None:
    """Close websocket safely without raising RuntimeError if connection is already closed/disconnected."""
    try:
        await websocket.close(code=code, reason=reason)
    except Exception:
        pass


async def _authenticate_ui_websocket(websocket: WebSocket) -> Optional[dict]:
    try:
        await websocket.accept()
        auth_message = await asyncio.wait_for(websocket.receive_json(), timeout=5)
        token = auth_message.get("token") if isinstance(auth_message, dict) else None
        message_type = auth_message.get("type") if isinstance(auth_message, dict) else None
        if message_type != "auth" or not token:
            await _safe_ws_close(websocket, code=1008, reason="Missing authentication token")
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
    except WebSocketDisconnect:
        logger.debug(f"UI websocket disconnected during auth from {websocket.client}")
        return None
    except asyncio.TimeoutError:
        logger.warning(f"Rejected UI websocket from {websocket.client}: auth timeout")
        await _safe_ws_close(websocket, code=1008, reason="Authentication timeout")
        return None
    except Exception as exc:
        logger.warning(f"Rejected UI websocket from {websocket.client}: {exc}")
        await _safe_ws_close(websocket, code=1008, reason="Authentication failed")
        return None


ui_websocket_endpoint_router = APIRouter()

@ui_websocket_endpoint_router.websocket("/ws/updates")
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

                    if context.get("role") != auth.Role.ADMIN:
                        async with device_session_scope() as scope_db:
                            target_serial = await _resolve_station_serial_by_mountpoint(scope_db, str(mountpoint))
                        allowed_serials = context.get("allowed_serials") or set()
                        if not target_serial or str(target_serial).strip().upper() not in allowed_serials:
                            await websocket.send_json({"status": "error", "error": "station is outside your scope"})
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
                        if force and context.get("role") != auth.Role.ADMIN:
                            await websocket.send_json({"status": "error", "error": "force stop requires admin"})
                            continue
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


pi_websocket_endpoint_router = APIRouter()

@pi_websocket_endpoint_router.websocket("/ws/pi/{serial}")
async def pi_websocket_endpoint(websocket: WebSocket, serial: str, token: str = Query(None)):
    if crud.is_blocked_device_serial(serial):
        await _safe_ws_close(websocket, code=1008, reason="Blocked device serial")
        return
    # Never leave an unaccepted socket queued behind a saturated DB pool.  The
    # short-lived metadata cache removes the per-reconnect SELECT in steady state.
    try:
        metadata = await asyncio.wait_for(
            mqtt_handler.get_device_metadata(serial),
            timeout=float(os.getenv("AGENT_WS_AUTH_TIMEOUT_SECONDS", "3")),
        )
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning("Pi '%s' auth lookup timed out; asking agent to retry", serial)
        await _safe_ws_close(websocket, code=1013, reason="Server busy; retry")
        return
    except Exception as exc:
        logger.warning("Pi '%s' auth lookup failed; asking agent to retry: %s", serial, exc)
        await _safe_ws_close(websocket, code=1013, reason="Server busy; retry")
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

    if not metadata.get("exists"):
        logger.warning("Pi '%s' connection rejected: unknown device serial.", serial)
        await _safe_ws_close(websocket, code=status.WS_1008_POLICY_VIOLATION)
        return
    if not license_bypass and (
        not license_manager.is_compatible_device_token(expected_token, serial)
        or not hmac.compare_digest(provided_token, expected_token)
    ):
        logger.warning("Pi '%s' connection rejected: invalid or missing token.", serial)
        await _safe_ws_close(websocket, code=status.WS_1008_POLICY_VIOLATION)
        return
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
                if isinstance(payload.get("service_config"), (dict, list)):
                    payload["service_config"] = schemas.strip_status_config_secrets(payload["service_config"])
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
                saved_name = mqtt_handler.device_name_cache.get(serial)
                if saved_name:
                    payload["name"] = saved_name
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
                # WebSocket usually arrives before the duplicate MQTT heartbeat.
                # The reconciler accepts WebSocket and drops that MQTT duplicate,
                # so feed the same sync cache and online-device index here.
                mqtt_handler.cache_agent_sync_inputs(serial, payload)
                reported_status = str(compact_payload.get("status") or "").strip().lower()
                if reported_status:
                    mqtt_handler.device_status_cache[serial] = reported_status

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
                parsed_data = mqtt_handler.parse_nmea_sentence(serial, payload)
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
            "offline_since": now_ts,
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
                    device.offline_since = now_ts
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


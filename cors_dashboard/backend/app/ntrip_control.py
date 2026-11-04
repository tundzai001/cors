"""RTCM session control, NTRIP routing and keepalive workers."""

import logging
from typing import Dict, List, Optional, Set
from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
import asyncio
import json
import time
import uuid
from sqlalchemy import update, delete
from fastapi.responses import PlainTextResponse

from . import crud, models
from .mobile_base import is_4g_mountpoint
from .schemas import service_server_ids
from .database import auth_session_scope, device_session_scope, settings
from .database import DatabaseBusyError
from .command_dispatcher import send_command_to_pi
from .caster_cache import _get_caster_cache
from .caster_client import CasterClient
from .runtime_config import _get_env_preferring_dotenv, _read_caster_config_from_env
from .station_config import _device_service_config
from .rtcm_stream_state import (
    apply_stream_control_state,
    build_stream_control_payload,
    collect_reapable_server_streams,
    normalize_mountpoint as normalize_stream_mountpoint,
)

logger = logging.getLogger("app.main")
router = APIRouter()


rtcm_keepalive_miss_counts = {}  # {session_id: (last_snapshot_timestamp, miss_count)}
rtcm_last_auto_wake_times = {} # {serial: last_timestamp}
rtcm_session_lock = asyncio.Lock()
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

mountpoint_serial_cache: Dict[str, Optional[str]] = {}
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


def _sanitize_ttl(ttl_seconds: int) -> int:
    try:
        ttl = int(ttl_seconds)
    except (TypeError, ValueError):
        ttl = 90
    return max(RTCM_MIN_TTL, min(RTCM_MAX_TTL, ttl))


def _extract_station_mountpoints(device: models.Device) -> List[str]:
    cfg = _device_service_config(device)
    result: List[str] = []
    for sid in service_server_ids(cfg):
        if cfg.get(f"server{sid}_enabled") and cfg.get(f"mountpoint{sid}"):
            result.append(str(cfg[f"mountpoint{sid}"]))
    return result


def _normalize_mountpoint(value: Optional[str]) -> Optional[str]:
    return normalize_stream_mountpoint(value)


def _resolve_preferred_server_id_for_mountpoint(cfg: dict, mountpoint: Optional[str], requested_address: Optional[str] = None) -> Optional[int]:
    mountpoint_norm = _normalize_mountpoint(mountpoint)
    if not mountpoint_norm:
        return None

    candidates: List[int] = []
    for sid in service_server_ids(cfg):
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
                logger.info("[RTCM ROUTE] Explicit address match, selected_server=%d", sid)
                return sid

    # CASE 2: Fallback to .env preferred host (for older casters or missing address)
    if settings.ENABLE_PREFERRED_HOST and settings.PREFERRED_HOST_MARKER:
        marker = settings.PREFERRED_HOST_MARKER.strip().lower()
        for sid in candidates:
            host = str(cfg.get(f"serverhost{sid}", "") or "").strip().lower()
            if host and marker in host:
                logger.info("[RTCM ROUTE] Preferred host match, selected_server=%d", sid)
                return sid

    selected = sorted(candidates)[0]
    logger.info("[RTCM ROUTE] Selected fallback server %d", selected)
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
    if is_4g_mountpoint(mountpoint) or str(serial or '').upper().startswith('MONITOR_7'):
        return  # Includes old leases still waiting for expiry after the upgrade.
    requested_mp_norm = _normalize_mountpoint(mountpoint) if mountpoint else None
    try:
        target_server_id = int(server_id) if server_id is not None else None
    except (TypeError, ValueError):
        target_server_id = None
    if target_server_id is not None and target_server_id <= 0:
        target_server_id = None

    if target_server_id is None and requested_mp_norm and (bool(active) or requested_address):
        # Tự động phân giải server_idx dựa vào cấu hình trạm
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
        logger.warning("[RTCM] Failed to persist stream state (%s)", type(e).__name__)


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


async def _upsert_rtcm_session(
    db: AsyncSession,
    *,
    session_id: str,
    serial: str,
    mountpoint: str,
    client_id: str,
    expires_at: int,
    now: int,
    caster_host: Optional[str] = None,
) -> str:
    """Refresh one lease per station/client/mountpoint and collapse legacy duplicates."""
    match = and_(
        models.RTCMStreamSession.serial == serial,
        func.lower(models.RTCMStreamSession.mountpoint) == _normalize_mountpoint(mountpoint),
        models.RTCMStreamSession.client_id == client_id,
    )
    result = await db.execute(
        select(models.RTCMStreamSession)
        .where(match)
        .order_by(models.RTCMStreamSession.last_heartbeat_at.desc())
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing:
        session_id = existing.session_id
        existing.expires_at = expires_at
        existing.last_heartbeat_at = now
        if caster_host:
            existing.caster_host = caster_host
        await db.execute(
            delete(models.RTCMStreamSession).where(
                match,
                models.RTCMStreamSession.session_id != session_id,
            )
        )
    else:
        db.add(models.RTCMStreamSession(
            session_id=session_id,
            serial=serial,
            mountpoint=str(mountpoint),
            client_id=client_id,
            expires_at=expires_at,
            caster_host=caster_host,
            created_at=now,
            last_heartbeat_at=now,
        ))
    return session_id


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
        if _normalize_mountpoint(mountpoint) not in {_normalize_mountpoint(mp) for mp in mountpoints}:
            logger.warning("[NTRIP ROUTE] Rejected a mountpoint outside the enabled set")
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

            session_id = await _upsert_rtcm_session(
                db,
                session_id=session_id,
                serial=serial,
                mountpoint=str(selected_mountpoint),
                client_id=client_id,
                expires_at=expires_at,
                now=now,
                caster_host=caster_host or requested_address,
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
    return any(bool(cfg.get(f"server{sid}_enabled")) and
               _normalize_mountpoint(cfg.get(f"mountpoint{sid}")) == requested
               for sid in service_server_ids(cfg))


async def _resolve_station_serial_by_mountpoint(db: AsyncSession, mountpoint: str, *, force_refresh: bool = False) -> Optional[str]:
    global mountpoint_serial_cache, command_mountpoints_cache, mountpoint_cache_last_updated
    requested = _normalize_mountpoint(mountpoint)
    if not requested:
        raise ValueError("mountpoint is required")

    now = time.time()
    # Nếu cache còn hạn (60 giây), dùng luôn để phản hồi SIÊU TỐC
    async with mountpoint_cache_lock:
        if not force_refresh and requested in mountpoint_serial_cache and (now - mountpoint_cache_last_updated) < 60:
            return mountpoint_serial_cache[requested]

        # Cache hết hạn hoặc không có -> Quét DB 1 lần duy nhất để rebuild toàn bộ cache
        if force_refresh or (now - mountpoint_cache_last_updated) >= 60:
            logger.info("[CACHE] Rebuilding Mountpoint-to-Serial cache & Command Mountpoints cache...")
            new_cache = {}
            owner_priority = {}
            new_cmd_cache = set()
            rs = await db.execute(select(models.Device.serial, models.Device.service_config, models.Device.system_info))
            for row in sorted(rs.all(), key=lambda row: str(row[0])):
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

                for sid in service_server_ids(cfg):
                    mp = _normalize_mountpoint(cfg.get(f"mountpoint{sid}"))
                    if mp:
                        priority = 2 if cfg.get(f"server{sid}_enabled") else 1
                        if priority > owner_priority.get(mp, 0):
                            new_cache[mp] = serial
                            owner_priority[mp] = priority

                # A relay input is supplied by the Caster, not controlled by this relay.
                # None marks a known passive input; actual output owners take precedence.
                source = cfg.get('relay_source')
                if serial.upper().startswith('RELAY_') and isinstance(source, dict):
                    source_mp = _normalize_mountpoint(source.get('mountpoint'))
                    if source_mp:
                        new_cache.setdefault(source_mp, None)

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
            if resolved_serial is None:
                return

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


def _fresh_rtcm_clienttable(cache: Optional[dict], now: int) -> bool:
    if not cache or cache.get("error") or not isinstance(cache.get("clients"), list):
        return False
    # Polling may be faster than the monitor. Allow its normal scan interval,
    # but never use an old or failed snapshot as evidence that a rover left.
    max_age = max(
        RTCM_KEEPALIVE_FETCH_GRACE_SECONDS,
        2 * float(_get_env_preferring_dotenv("CASTER_MONITOR_INTERVAL", "30")),
    )
    sampled_at = int(cache.get("last_update") or 0)
    return sampled_at > 0 and 0 <= now - sampled_at <= max_age


def _record_rtcm_keepalive_miss(session_id: str, cache: Optional[dict], now: int, created_at: int) -> int:
    if (now - created_at < RTCM_KEEPALIVE_NEW_SESSION_GRACE_SECONDS
            or not _fresh_rtcm_clienttable(cache, now)
            or int(cache["last_update"]) <= created_at):
        rtcm_keepalive_miss_counts.pop(session_id, None)
        return 0

    sampled_at = int(cache["last_update"])
    previous_sample, misses = rtcm_keepalive_miss_counts.get(session_id, (0, 0))
    if sampled_at > previous_sample:
        misses += 1
        rtcm_keepalive_miss_counts[session_id] = (sampled_at, misses)
    return misses


async def _deactivate_idle_rtcm_mountpoint(serial: str, mountpoint: str):
    # Re-check mode and fresh leases under the same lock used by wake. A
    # candidate may have become Always-On or gained a rover since the scan.
    async with rtcm_session_lock:
        async with device_session_scope() as db:
            device = await crud.get_device_by_serial(db, serial)
            targets = [
                (server_id, mp)
                for server_id, mp in _extract_reapable_server_streams(device)
                if mp == mountpoint
            ]
            if not targets or await _count_active_mountpoint_sessions(db, serial, mountpoint, int(time.time())):
                return

        for server_id, mp in targets:
            await _set_station_stream(serial, active=False, mountpoint=mp, server_id=server_id)
            logger.info(f"[RTCM] Deactivated station {serial} S{server_id} (mp={mp}, no remaining sessions)")


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

            active_ids = {row[0] for row in active_rows}
            for session_id in list(rtcm_keepalive_miss_counts):
                if session_id not in active_ids:
                    rtcm_keepalive_miss_counts.pop(session_id, None)
            if not active_rows:
                continue

            # 2. Match each session against Global Caster Cache
            keepalive_to = now + 90
            updated_ids = []
            expired_ids = []
            candidates_for_deactivation = set()

            # Group sessions by host for easier lookup
            for sid, client_id, serial, mountpoint, host, expires_at, created_at in active_rows:
                # Resolve host to find cache entry
                target_host = host or next((h.strip() for h in settings.CASTER_HOST.split(",") if h.strip()), "localhost")
                port = int(settings.CASTER_PORT or 2101)

                hosts_to_check = [target_host]
                other_hosts = [h.strip() for h in settings.CASTER_HOST.split(",") if h.strip() and h.strip() != target_host]
                hosts_to_check.extend(other_hosts)

                found_on_host = None
                primary_cache = await _get_caster_cache(f"{target_host}:{port}")
                for h in hosts_to_check:
                    h_port = int(settings.CASTER_PORT or 2101)
                    h_key = f"{h}:{h_port}"
                    h_cache = await _get_caster_cache(h_key)
                    if not _fresh_rtcm_clienttable(h_cache, now): continue

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
                    rtcm_keepalive_miss_counts.pop(sid, None)
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
                    miss_count = _record_rtcm_keepalive_miss(sid, primary_cache, now, created_at)

                    if miss_count >= RTCM_KEEPALIVE_MISS_THRESHOLD:
                        expired_ids.append(sid)
                        candidates_for_deactivation.add((serial, _normalize_mountpoint(mountpoint)))
                        logger.warning(f"[RTCM] User '{client_id}' (@{mountpoint}) on station {serial} expired after {miss_count} misses.")
                    else:
                        # Keep the lease alive until enough independent, successful
                        # scans confirm absence, including during cache outages.
                        updated_ids.append(sid)
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
                if mp_norm:
                    await _deactivate_idle_rtcm_mountpoint(serial, mp_norm)

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

                    server_label = f" S{server_id}" if server_id is not None else ""
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
        return "Blocklist unavailable; request denied"
    return None


async def _do_ntrip_wake(mountpoint: str, client_id: str, ttl: int, requested_address: Optional[str] = None):
    """Xử lý wake trạm, trả về PlainTextResponse chuẩn cho Caster."""
    is_command_mp = mountpoint and _is_command_mountpoint(mountpoint)
    if is_command_mp:
        logger.debug("[NTRIP Wake] Passive wake requested")
    else:
        logger.info("[NTRIP Wake] Wake requested")

    # 1. Check Blocklist ngay lập tức
    reason = await _check_rover_blocked(client_id, mountpoint)
    if reason:
        msg = f"BLOCKED: {reason}"
        logger.warning("[NTRIP Wake] Blocklist denied wake request")
        return PlainTextResponse(f"WAKE_STATUS: blocked\n{msg}")

    # 1.5. Bypass cho command mountpoints
    if mountpoint and is_command_mp:
        logger.debug(f"[NTRIP Wake] Bypassing wake for command/test mountpoint '{mountpoint}'")
        return PlainTextResponse(f"WAKE_STATUS: ok\nSTREAM_HINT: streaming\nMOUNTPOINT: {mountpoint}\nOK")

    if is_4g_mountpoint(mountpoint):
        # Caster owns this source; decoder telemetry must never gate its input.
        # Pending keeps legacy casters accepting bytes without claiming RTCM exists.
        return PlainTextResponse(
            f"WAKE_STATUS: ok\nSTREAM_HINT: pending\nCONTROL_MODE: passive\nMOUNTPOINT: {mountpoint}\nOK"
        )

    # 2. Bật trạm nếu hợp lệ
    try:
        async with device_session_scope() as db:
            serial = await _resolve_station_serial_by_mountpoint(db, mountpoint)
            if serial is None:
                return PlainTextResponse(f"WAKE_STATUS: ok\nSTREAM_HINT: pending\nCONTROL_MODE: passive\nMOUNTPOINT: {mountpoint}\nOK")
            device = await crud.get_device_by_serial(db, serial)
            if device and not _device_has_enabled_mountpoint(device, mountpoint):
                # A config edit or duplicate disabled row can invalidate a cached owner.
                serial = await _resolve_station_serial_by_mountpoint(db, mountpoint, force_refresh=True)
                if serial is None:
                    return PlainTextResponse(f"WAKE_STATUS: ok\nSTREAM_HINT: pending\nCONTROL_MODE: passive\nMOUNTPOINT: {mountpoint}\nOK")
                device = await crud.get_device_by_serial(db, serial)
            if not device:
                return PlainTextResponse(f"WAKE_STATUS: error\nERROR: Trạm {serial} không tồn tại")

            # The system Rover (username=MP) only observes an ESP source already
            # pushing to the caster. It must not wake or gate its own source on
            # a delayed agent heartbeat/DB status.
            if str(serial).lower().startswith("esp_") and _normalize_mountpoint(client_id) == _normalize_mountpoint(mountpoint):
                return PlainTextResponse(
                    f"WAKE_STATUS: ok\nSTREAM_HINT: pending\nCONTROL_MODE: passive\nMOUNTPOINT: {mountpoint}\nOK"
                )

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
            if selected_server_id in service_server_ids(cfg):
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

            if selected_server_id in service_server_ids(cfg):
                logger.info("[NTRIP Wake] Selected server %s", selected_server_id)

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
    logger.info("[NTRIP Stop] Stop requested, force=%s", bool(force))
    if is_4g_mountpoint(mountpoint):
        return PlainTextResponse(f"STOP_STATUS: ok\nCONTROL_MODE: passive\nMOUNTPOINT: {mountpoint}\nOK")
    try:
        async with device_session_scope() as db:
            serial = await _resolve_station_serial_by_mountpoint(db, mountpoint)
            if serial is None:
                return PlainTextResponse(f"STOP_STATUS: ok\nCONTROL_MODE: passive\nMOUNTPOINT: {mountpoint}\nOK")
            device = await crud.get_device_by_serial(db, serial)
            if not device:
                return PlainTextResponse(f"STOP_STATUS: error\nERROR: Trạm {serial} không tồn tại")

            if not force and str(serial).lower().startswith("esp_") and _normalize_mountpoint(client_id) == _normalize_mountpoint(mountpoint):
                return PlainTextResponse(f"STOP_STATUS: ok\nCONTROL_MODE: passive\nMOUNTPOINT: {mountpoint}\nOK")

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
            if selected_server_id in service_server_ids(cfg):
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
                logger.info("[NTRIP Stop] Force stopped station stream")
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


async def get_caster_client(db: AsyncSession) -> CasterClient:
    """Helper để khởi tạo Caster Client an toàn từ .env hoặc settings"""
    hosts, env_cfg = _read_caster_config_from_env()

    # Ưu tiên .env -> settings -> default
    host = str(env_cfg["host"] or settings.CASTER_HOST or '').strip()
    port = int(env_cfg["port"] or settings.CASTER_PORT or 2101)
    user = str(env_cfg["user"] or "").strip()
    password = str(env_cfg["pass"] or "").strip()
    if not host or not user or not password:
        raise HTTPException(status_code=503, detail="Caster connection credentials are not configured")

    if RTCM_KEEPALIVE_DEBUG:
        logger.warning(f"[RTCM DEBUG] Using caster host={host} port={port} user={user}")

    return CasterClient(host, port, admin_user=user, admin_password=password)


"""ESP32 Base 4G automatic Rover account lifecycle and decoder synchronization.

When an ESP32 Base 4G station streams RTCM to NTRIP Caster at mountpoint 'mp':
1. Backend creates a Rover account with username=mp, password="12345", max_users=0 (unlimited),
   show_position=False on Caster and in DB.
2. Backend signals the decoder service (geotek_daithanh_aitogy) via MQTT topic `pi/decoder/esp_base`
   to pull the stream and decode satellites/RTCM for the ESP station's UI.
3. When the ESP32 station is disconnected/offline for >= 5 minutes (300s),
   the Rover account is automatically deleted from Caster and DB, and the decoder worker is stopped.
"""

import asyncio
import json
import logging
import re
import time
from typing import Optional, List, Dict, Any
from sqlalchemy import select, delete, func

from . import crud, models
from .database import device_session_scope, auth_session_scope, settings
from .runtime_config import _read_caster_config_from_env
from .caster_client import CasterClient
from .mobile_base import is_4g_mountpoint, is_readonly_base

logger = logging.getLogger("app.esp_base_manager")

OFFLINE_DELETE_THRESHOLD_SECONDS = 300  # 5 minutes
SYNC_INTERVAL_SECONDS = 15.0
DEFAULT_ROVER_PASSWORD = "12345"
MQTT_DECODER_TOPIC = "pi/decoder/esp_base"
MQTT_SYNC_REQUEST_TOPIC = "pi/decoder/esp_base/sync_request"


def _extract_esp_mountpoint(device: models.Device) -> Optional[str]:
    """Extract primary mountpoint configured on ESP32 device."""
    cfg = device.service_config
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except Exception:
            cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}

    mp = cfg.get("mountpoint1") or cfg.get("active_mountpoint") or cfg.get("mountpoint") or cfg.get("source_mountpoint")
    if not mp and cfg.get("server1_enabled"):
        mp = cfg.get("mountpoint1")
    if not mp and cfg.get("server2_enabled"):
        mp = cfg.get("mountpoint2")
    if not mp:
        mp = cfg.get("mountpoint2")

    if mp:
        mp_clean = str(mp).strip().lstrip("/")
        return mp_clean or None
    return None


async def _get_configured_casters() -> List[tuple[str, int]]:
    """Return list of (host, port) for all configured Casters."""
    env_hosts, env_cfg = _read_caster_config_from_env()
    port = int(env_cfg.get("port") or getattr(settings, "CASTER_PORT", 2101) or 2101)
    admin_user = env_cfg.get("user") or getattr(settings, "CASTER_ADMIN_USER", None)
    admin_password = env_cfg.get("pass") or getattr(settings, "CASTER_PASSWORD", None)

    casters = []
    for host in env_hosts:
        h = str(host).strip()
        if h:
            casters.append((h, port, admin_user, admin_password))
    return casters


async def auto_provision_esp_rover_account(device: models.Device, mountpoint: str) -> bool:
    """Create a temporary rover account on Caster & DB for an active ESP32 Base."""
    username = mountpoint
    password = DEFAULT_ROVER_PASSWORD
    now = int(time.time())

    # 1. Create on all configured Casters
    casters = await _get_configured_casters()
    caster_success = False
    for host, port, user, pw in casters:
        try:
            client = CasterClient(host, port, admin_user=user, admin_password=pw)
            ok = await client.create_user(
                creator="system",
                username=username,
                password=password,
                mountpoints=mountpoint,
                max_users=0,  # 0 = unlimited connections
                show_position=False,
                rtcm_cycle_enabled=False,
            )
            if ok:
                caster_success = True
                logger.info(f"[ESP ROVER] Created user '{username}' on Caster {host}:{port}")
            else:
                logger.warning(f"[ESP ROVER] Caster {host}:{port} returned non-success for '{username}'")
        except Exception as e:
            logger.error(f"[ESP ROVER] Error creating user '{username}' on Caster {host}:{port}: {e}")

    # 2. Persist in Auth DB
    async with auth_session_scope() as auth_db:
        rs = await auth_db.execute(select(models.RoverAccount).where(models.RoverAccount.username == username))
        existing = rs.scalars().first()
        if not existing:
            new_rover = models.RoverAccount(
                username=username,
                password=password,
                base_serial=device.serial,
                mountpoints=mountpoint,
                max_connections=0,  # 0 = không giới hạn kết nối
                show_position=False,  # TẮT: không hiển thị vị trí trên bản đồ
                auto_save_sessions=False,  # TẮT: không lưu phiên rover
                auto_save_fixed_points=False,  # TẮT: không lưu điểm cố định
                rtcm_cycle_enabled=False,  # TẮT: không chu kỳ ngắt RTCM
                is_shared_with_managers=False,  # TẮT: không chia sẻ chủ trạm
                expires_at=None,  # Vô hạn
                is_active=True,
                created_by_user_id=None,  # system-generated
                created_at=now,
            )
            auth_db.add(new_rover)
            await auth_db.commit()
            logger.info(f"[ESP ROVER] Saved system RoverAccount '{username}' in DB for base {device.serial} (all extra features disabled)")
        else:
            # Update base_serial and ensure features remain disabled
            existing.base_serial = device.serial
            existing.mountpoints = mountpoint
            existing.max_connections = 0
            existing.show_position = False
            existing.auto_save_sessions = False
            existing.auto_save_fixed_points = False
            existing.rtcm_cycle_enabled = False
            existing.is_shared_with_managers = False
            await auth_db.commit()

    # 3. Notify Decoder via MQTT
    lat, lon, alt = _extract_device_coords(device)
    await notify_decoder_worker("start", device.serial, mountpoint, username, password, lat=lat, lon=lon, alt=alt)
    return True


async def auto_deprovision_esp_rover_account(device_serial: str, username: str) -> bool:
    """Delete the temporary rover account from Caster & DB when ESP32 Base is offline >= 5m."""
    logger.info(f"[ESP ROVER] Deprovisioning temporary rover '{username}' for offline base {device_serial}")

    # 1. Delete on all configured Casters
    casters = await _get_configured_casters()
    for host, port, user, pw in casters:
        try:
            client = CasterClient(host, port, admin_user=user, admin_password=pw)
            await client.delete_user(identifier="system", username=username)
            logger.info(f"[ESP ROVER] Deleted user '{username}' from Caster {host}:{port}")
        except Exception as e:
            logger.error(f"[ESP ROVER] Error deleting user '{username}' on Caster {host}:{port}: {e}")

    # 2. Delete from Auth DB
    async with auth_session_scope() as auth_db:
        rs = await auth_db.execute(
            select(models.RoverAccount).where(
                models.RoverAccount.username == username,
                models.RoverAccount.base_serial == device_serial,
                models.RoverAccount.created_by_user_id.is_(None),
            )
        )
        rovers = rs.scalars().all()
        for r in rovers:
            await auth_db.delete(r)
        if rovers:
            await auth_db.commit()
            logger.info(f"[ESP ROVER] Removed {len(rovers)} RoverAccount rows for '{username}' from DB")

    # 3. Notify Decoder via MQTT to stop
    await notify_decoder_worker("stop", device_serial, username, username, DEFAULT_ROVER_PASSWORD)
    return True


def _extract_device_coords(device: models.Device | None) -> tuple[float, float, float]:
    """Extract coordinates from device base_config if available."""
    if not device:
        return 0.0, 0.0, 0.0
    coords = {}
    if getattr(device, "base_config", None) and isinstance(device.base_config, dict):
        coords = device.base_config.get("coords") or {}
    try:
        lat = float(coords.get("lat") or 0.0)
    except Exception:
        lat = 0.0
    try:
        lon = float(coords.get("lon") or 0.0)
    except Exception:
        lon = 0.0
    try:
        alt = float(coords.get("alt") or 0.0)
    except Exception:
        alt = 0.0
    return lat, lon, alt


async def notify_decoder_worker(
    action: str, serial: str, mountpoint: str, rover_user: str, rover_pass: str,
    lat: float = 0.0, lon: float = 0.0, alt: float = 0.0,
    host: str | None = None, port: int | None = None,
) -> None:
    """Publish MQTT control message to geotek_daithanh_aitogy decoder worker."""
    try:
        from . import mqtt as mqtt_module
        if mqtt_module.mqtt_client and mqtt_module.mqtt_client.is_connected():
            payload = json.dumps({
                "action": action,
                "serial": serial,
                "mountpoint": mountpoint,
                "rover_user": rover_user,
                "rover_pass": rover_pass,
                "lat": lat,
                "lon": lon,
                "alt": alt,
                "host": host,
                "port": port,
                "timestamp": int(time.time()),
            })
            mqtt_module.publish_message(MQTT_DECODER_TOPIC, payload, qos=1)
            logger.info(f"[ESP DECODER MQTT] Sent {action} for serial={serial}, mp={mountpoint}")
    except Exception as e:
        logger.error(f"[ESP DECODER MQTT] Failed to publish {action} to {MQTT_DECODER_TOPIC}: {e}")


async def sync_esp_base_rovers() -> None:
    """Main synchronization check:
    - Online ESP32 bases -> ensure RoverAccount exists and decoder worker is running.
    - Offline >= 5m ESP32 bases -> delete RoverAccount and stop decoder worker.
    """
    now = int(time.time())

    async with device_session_scope() as dev_db:
        result = await dev_db.execute(select(models.Device))
        all_devices = result.scalars().all()

        esp_devices = [
            d for d in all_devices
            if not is_readonly_base(d) and crud.is_mobile_base(d.serial)
        ]

    if not esp_devices:
        return

    async with auth_session_scope() as auth_db:
        rs = await auth_db.execute(select(models.RoverAccount))
        existing_rovers = rs.scalars().all()
        # Map: username -> RoverAccount
        rover_map = {r.username.lower(): r for r in existing_rovers}

    for dev in esp_devices:
        serial = dev.serial
        status = str(dev.status or "").lower()
        mp = _extract_esp_mountpoint(dev)

        is_online = status in ("online", "streaming", "starting", "sleep")

        if is_online and mp:
            mp_key = mp.lower()
            rover = rover_map.get(mp_key)
            if not rover:
                logger.info(f"[ESP ROVER] Online base {serial} with mountpoint '{mp}' lacks rover account -> creating")
                await auto_provision_esp_rover_account(dev, mp)
                rover_map[mp_key] = True
            else:
                # Account already exists; ensure decoder knows to keep running
                lat, lon, alt = _extract_device_coords(dev)
                await notify_decoder_worker("start", serial, mp, mp, DEFAULT_ROVER_PASSWORD, lat=lat, lon=lon, alt=alt)

            # Cleanup any stale system rover accounts for this base if its mountpoint changed
            for rover_user_key, rover_obj in list(rover_map.items()):
                if isinstance(rover_obj, models.RoverAccount):
                    if rover_obj.base_serial == serial and rover_obj.created_by_user_id is None and rover_obj.username.lower() != mp_key:
                        logger.info(f"[ESP ROVER] Base {serial} mountpoint changed from '{rover_obj.username}' to '{mp}' -> deleting old rover")
                        await auto_deprovision_esp_rover_account(serial, rover_obj.username)
                        rover_map.pop(rover_user_key, None)

        elif status == "offline":
            # Check offline duration
            offline_since = dev.offline_since
            if not offline_since:
                offline_since = int(dev.timestamp or now)

            offline_duration = now - offline_since
            if offline_duration >= OFFLINE_DELETE_THRESHOLD_SECONDS:
                # Find any system-created rover account for this serial
                for rover_user_key, rover_obj in list(rover_map.items()):
                    if isinstance(rover_obj, models.RoverAccount):
                        if rover_obj.base_serial == serial and rover_obj.created_by_user_id is None:
                            logger.info(
                                f"[ESP ROVER] Base {serial} has been offline for {offline_duration}s "
                                f"(>= {OFFLINE_DELETE_THRESHOLD_SECONDS}s) -> deleting rover '{rover_obj.username}'"
                            )
                            await auto_deprovision_esp_rover_account(serial, rover_obj.username)
                            rover_map.pop(rover_user_key, None)


async def sync_prefix7_bases() -> None:
    """Reuse Caster inventory and Rover credentials; never create/delete Rover users."""
    from .mqtt import cache_device_metadata
    now = int(time.time())
    casters = {(host, port) for host, port, *_ in await _get_configured_casters()}
    async with auth_session_scope() as auth_db:
        sources = (await auth_db.execute(select(models.CasterSource))).scalars().all()
        rovers = (await auth_db.execute(select(models.RoverAccount))).scalars().all()
        rover_map = {r.username: r for r in rovers}
    sources = sorted((s for s in sources if is_4g_mountpoint(s.mountpoint)
                      and (s.host, s.port) in casters),
                     key=lambda s: (s.host, s.port, s.mountpoint))
    notifications = []
    async with device_session_scope() as db:
        # Multiple backend processes share one inventory and one device identity.
        await db.execute(select(func.pg_advisory_xact_lock(713204701)))
        devices = (await db.execute(select(models.Device))).scalars().all()
        by_mp = {}
        for device in devices:
            mp = _extract_esp_mountpoint(device)
            if mp:
                by_mp.setdefault(mp, device)
        seen = set()
        for source in sources:
            mp = source.mountpoint
            if mp in seen or any(c in mp for c in '/+#\r\n'):
                continue
            rover = rover_map.get(mp)
            allowed_hosts = {h.strip() for h in (rover.target_casters or '').split(';')} if rover else set()
            if rover and rover.target_casters and source.host not in allowed_hosts:
                continue
            seen.add(mp)
            device = by_mp.get(mp)
            if not device:
                serial = f'MONITOR_{mp}'
                device = next((d for d in devices if d.serial == serial), None)
                if not device:
                    device = models.Device(serial=serial, name=mp, status='offline', timestamp=0,
                                           service_config={}, base_config={}, bps=0)
                    db.add(device)
                    devices.append(device)
                by_mp[mp] = device
            device.service_config = {**crud.normalize_service_config(device.service_config), 'mountpoint1': mp,
                                     'serverhost1': source.host, 'port1': source.port,
                                     'server1_enabled': True}
            allowed_mps = set(re.split(r'[;,\s]+', rover.mountpoints or '')) if rover else set()
            valid = bool(rover and rover.is_active and rover.password
                         and (not rover.mountpoints or '*' in allowed_mps or mp in allowed_mps)
                         and (not rover.expires_at or rover.expires_at > now))
            device.system_info = {**(device.system_info or {}), 'auth_license_bypass': True,
                                  'base_type': '4g', 'read_only': True,
                                  'decoder_status': 'ready' if valid else 'missing_rover_credentials'}
            if device.status in ('awaiting_license', 'unprovisioned', 'locked'):
                device.status = 'offline'
                device.bps = 0
                device.ntrip_connected = False
                device.offline_since = device.offline_since or now
            device.is_provisioned = True
            device.device_token = None
            device.is_locked = False
            device.server_config_lock = None
            # No token/license is generated; bypass is owned by the backend.
            notifications.append((device, mp, source, rover if valid else None))
        # Accounts disabled/deleted and removed inventory must stop existing decoders.
        for device in devices:
            if is_readonly_base(device) and _extract_esp_mountpoint(device) not in seen:
                notifications.append((device, _extract_esp_mountpoint(device) or '', None, None))
        await db.commit()
        for device, *_ in notifications:
            await cache_device_metadata(device)
    for device, mp, source, rover in notifications:
        lat, lon, alt = _extract_device_coords(device)
        await notify_decoder_worker('start' if rover else 'stop', device.serial, mp,
                                    mp, rover.password if rover else '', lat=lat, lon=lon, alt=alt,
                                    host=source.host if source else None,
                                    port=source.port if source else None)


async def esp_base_manager_task() -> None:
    """Background loop running every 15s."""
    logger.info("[ESP BASE MANAGER] Started background supervisor task")
    while True:
        try:
            await sync_prefix7_bases()
            await sync_esp_base_rovers()
        except Exception as e:
            logger.error(f"[ESP BASE MANAGER] Error in sync loop: {e}", exc_info=True)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)

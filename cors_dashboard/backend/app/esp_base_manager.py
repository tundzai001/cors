"""ESP32 Base 4G automatic Rover account lifecycle and decoder synchronization.

When an ESP32 Base 4G station streams RTCM to NTRIP Caster at mountpoint 'mp':
1. Backend creates a Rover account with username=mp, password="12345", max_users=0 (unlimited),
   show_position=False on Caster and in DB.
2. Backend signals the decoder service (geotek_daithanh_aitogy) via MQTT topic `pi/decoder/esp_base`
   to pull the stream and decode satellites/RTCM for the ESP station's UI.
3. Khi ESP32 offline, dừng decoder và giữ tài khoản để không mất mật khẩu.
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Optional, List, Dict, Any
from sqlalchemy import select, delete, func

from . import crud, models, schemas
from .database import device_session_scope, auth_session_scope, settings
from .runtime_config import _read_caster_config_from_env
from .auto_cors_source import (DEFAULT_ROVER_PASSWORD, caster_esp_rover, esp_rover_name, existing_esp_rover)
from .command_dispatcher import send_command_to_pi
from . import mqtt as mqtt_handler
from .caster_client import CasterClient
from .mobile_base import is_4g_mountpoint, is_readonly_base

logger = logging.getLogger("app.esp_base_manager")

SYNC_INTERVAL_SECONDS = 15.0
MQTT_DECODER_TOPIC = "pi/decoder/esp_base"
MQTT_SYNC_REQUEST_TOPIC = "pi/decoder/esp_base/sync_request"
_stopped_decoder_serials: set[str] = set()
_auto_base_started_boots: dict[str, str] = {}
_synced_esp_rover_passwords: dict[tuple, str] = {}


_esp_setup_status: dict[str, dict] = {}


def _set_esp_setup_status(serial, stage, state, **details):
    _esp_setup_status.setdefault(serial, {})[stage] = {"state": state, **details}


def get_esp_setup_status(serial, boot_id):
    stages = _esp_setup_status.get(serial, {})
    return {stage: value for stage, value in stages.items()
            if stage != "auto_nearest" or (boot_id and value.get("boot_id") == boot_id)}


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

    for slot in (1, 2):
        if str(cfg.get(f"server{slot}_enabled", '')).lower() in {'true', '1', 'on', 'yes'}:
            selected = str(cfg.get(f"mountpoint{slot}") or '').strip().lstrip('/')
            if selected:
                return selected

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


def _device_source_destinations(device, mountpoint):
    """Read enabled SOURCE destinations without any global-host fallback."""
    cfg = crud.normalize_service_config(device.service_config)
    targets = []
    for slot in (1, 2):
        host = str(cfg.get(f"serverhost{slot}") or "").strip()
        flag = cfg.get(f"server{slot}_enabled")
        enabled = bool(host) if flag is None else str(flag).lower() in {"true", "1", "yes", "on"}
        mp = str(cfg.get(f"mountpoint{slot}") or mountpoint).strip().lstrip('/')
        if not enabled or not host or mp != mountpoint:
            continue
        try:
            port = int(cfg.get(f"port{slot}") or 2101)
        except (TypeError, ValueError):
            continue
        if not 1 <= port <= 65535:
            continue
        target = (host.rstrip('.').casefold(), port)
        if target not in targets:
            targets.append(target)
    return targets


async def _device_casters(device, mountpoint):
    configured = await _get_configured_casters()
    allowed = {host.rstrip('.').casefold(): (host, user, password) for host, _, user, password in configured}
    return [(allowed[host][0], port, allowed[host][1], allowed[host][2])
            for host, port in _device_source_destinations(device, mountpoint) if host in allowed]


async def sync_esp_mountpoint(device, username):
    """Provision the new SOURCE on each enabled caster before changing ESP32 config."""
    config = crud.normalize_service_config(device.service_config)
    slots = [slot for slot in (1, 2) if config.get(f"serverhost{slot}")
             and str(config.get(f"server{slot}_enabled", True)).lower() in {"true", "1", "yes", "on"}]
    changed = [slot for slot in slots if config.get(f"mountpoint{slot}") != username]
    if not changed:
        _set_esp_setup_status(device.serial, "source", "ready" if slots else "waiting_config",
                              mountpoint=username)
        return False
    _set_esp_setup_status(device.serial, "source", "registering", mountpoint=username)

    configured = {host.rstrip(".").casefold(): (host, port, user, password)
                  for host, port, user, password in await _get_configured_casters()}
    lat, lon, _ = _extract_device_coords(device)
    identifier = f"ESP32_{device.serial}"
    for slot in changed:
        host_key = str(config.get(f"serverhost{slot}") or "").strip().rstrip(".").casefold()
        destination = configured.get(host_key)
        if not destination:
            raise ValueError(f"Caster {host_key} chưa được cấu hình để đồng bộ mountpoint.")
        host, default_port, admin, admin_password = destination
        port = int(config.get(f"port{slot}") or default_port)
        source_password = str(config.get(f"password{slot}") or "")
        if not source_password:
            raise ValueError(f"Chưa có mật khẩu SOURCE cho cổng phát S{slot}.")
        caster = CasterClient(host, port, admin_user=admin, admin_password=admin_password)
        sources = await caster.get_sources()
        if sources is None:
            raise ValueError(f"Không đọc được danh sách mountpoint trên {host}:{port}.")
        existing = next((source for source in sources
                         if str(source.get("mountpoint") or "").casefold() == username.casefold()), None)
        if existing:
            if existing.get("identifier") != identifier or existing.get("password") != source_password:
                raise ValueError(f"Mountpoint {username} đã tồn tại trên {host}:{port}.")
        elif not await caster.create_source(identifier, username, source_password, latitude=lat, longitude=lon):
            raise ValueError(f"Caster {host}:{port} chưa xác nhận tạo mountpoint {username}.")

    patch = {f"mountpoint{slot}": username for slot in changed}
    payload = schemas.service_config_deployment_payload(device.version, {**config, **patch}, patch)
    await send_command_to_pi(device.serial, {"command": "DEPLOY_SERVICE_CONFIG", "payload": payload})
    # Keep the desired DB config aligned with the command. Otherwise the next
    # health report is marked unsynced and cannot replace the old mountpoint.
    async with mqtt_handler.mqtt_db_write_lock:
        async with device_session_scope() as db:
            current = await crud.get_device_for_update(db, device.serial)
            if current is None:
                return True
            current.service_config = {**crud.normalize_service_config(current.service_config), **patch}
            current.is_synced = False
            await db.commit()
            await mqtt_handler.cache_device_metadata(current)
    _set_esp_setup_status(device.serial, "source", "waiting_health", mountpoint=username)
    # Health tiếp theo xác nhận cấu hình trước khi đồng bộ tài khoản theo nguồn mới.
    return True


async def auto_provision_esp_rover_account(device: models.Device, mountpoint: str) -> bool:
    """Merge SOURCE access into the station account, preserving user settings."""
    casters = await _device_casters(device, mountpoint)
    if not casters:
        return False
    async with auth_session_scope() as auth_db:
        current, previous = await existing_esp_rover(auth_db, device, mountpoint)
        now = int(time.time())
        if previous and (not previous.is_active or (previous.expires_at is not None and previous.expires_at <= now)):
            return False
        password = previous.password if previous and previous.password else DEFAULT_ROVER_PASSWORD
        saved_hosts = [host for host in (current.target_casters or "").split(";") if host] if current else []
        saved_mounts = [mp for mp in (current.mountpoints or "").split(";") if mp] if current else []
        merged = list(dict.fromkeys([mountpoint, *saved_mounts]))
        successful_hosts = []
        for host, port, admin, secret in casters:
            sync_key = (device.serial, mountpoint, host, port)
            if current and mountpoint in saved_mounts and host in saved_hosts \
                    and _synced_esp_rover_passwords.get(sync_key) == password:
                successful_hosts.append(host)
                continue
            caster = CasterClient(host, port, admin_user=admin, admin_password=secret)
            remote = await caster_esp_rover(caster, mountpoint, current)
            remote_mounts = remote["mountpoints"] if remote else []
            expiry = int(previous.expires_at) - now if previous and previous.expires_at is not None else 31536000
            already_synced = remote and remote["password"] == password and mountpoint in remote_mounts
            if already_synced or await caster.create_user(
                "system", mountpoint, password, ";".join(dict.fromkeys([mountpoint, *remote_mounts])),
                expiry_seconds=expiry,
                max_users=previous.max_connections if previous else 0,
                show_position=previous.show_position if previous else False,
                rtcm_cycle_enabled=bool(previous and previous.rtcm_cycle_enabled),
                rtcm_disconnect_interval_seconds=previous.rtcm_disconnect_interval_seconds if previous else 300,
                rtcm_disconnect_duration_seconds=previous.rtcm_disconnect_duration_seconds if previous else 10,
            ):
                _synced_esp_rover_passwords[sync_key] = password
                successful_hosts.append(host)
        if not successful_hosts:
            return False
        if current is None:
            current = models.RoverAccount(
                username=mountpoint, password=password, base_serial=device.serial,
                created_by_user_id=None, created_at=now,
                max_connections=previous.max_connections if previous else 0,
                show_position=previous.show_position if previous else False,
                expires_at=previous.expires_at if previous else None, is_active=True,
                auto_save_sessions=False, auto_save_fixed_points=False,
            )
            auth_db.add(current)
        current.mountpoints = ";".join(merged)
        current.target_casters = ";".join(dict.fromkeys([*saved_hosts, *successful_hosts]))
        await auth_db.commit()

    _set_esp_setup_status(device.serial, "rover", "ready" if len(successful_hosts) == len(casters) else "partial",
                          username=mountpoint)
    lat, lon, alt = _extract_device_coords(device)
    await notify_decoder_worker("start", device.serial, mountpoint, mountpoint, password,
                                lat=lat, lon=lon, alt=alt, host=successful_hosts[0],
                                port=next(port for host, port, *_ in casters if host == successful_hosts[0]))
    return len(successful_hosts) == len(casters)


async def auto_deprovision_esp_rover_account(device_serial: str, username: str) -> bool:
    """Delete the temporary rover account from Caster & DB when ESP32 Base is confirmed offline."""
    logger.info(f"[ESP ROVER] Deprovisioning temporary rover '{username}' for offline base {device_serial}")

    # Verify ownership before deleting a Caster login, which may be manually managed.
    async with auth_session_scope() as auth_db:
        rs = await auth_db.execute(
            select(models.RoverAccount).where(
                models.RoverAccount.username == username,
                models.RoverAccount.base_serial == device_serial,
                models.RoverAccount.created_by_user_id.is_(None),
            )
        )
        owned = rs.scalars().all()
        if not owned:
            return False
        target_hosts = {host.strip().rstrip('.').casefold() for rover in owned
                        for host in (rover.target_casters or '').split(';') if host.strip()}

    await notify_decoder_worker("stop", device_serial, username, username, DEFAULT_ROVER_PASSWORD)

    # Only touch the destinations recorded for this system-owned account.
    casters = await _get_configured_casters()
    for host, port, user, pw in casters:
        if host.rstrip('.').casefold() not in target_hosts:
            continue
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

    for key in list(_synced_esp_rover_passwords):
        if key[:2] == (device_serial, username):
            _synced_esp_rover_passwords.pop(key, None)
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
            if action == "stop" and serial in _stopped_decoder_serials:
                return
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
            if action == "stop":
                _stopped_decoder_serials.add(serial)
            elif action == "start":
                _stopped_decoder_serials.discard(serial)
            log = logger.debug if is_4g_mountpoint(mountpoint) else logger.info
            log(f"[ESP DECODER MQTT] Sent {action} for serial={serial}, mp={mountpoint}")
    except Exception as e:
        logger.error(f"[ESP DECODER MQTT] Failed to publish {action} to {MQTT_DECODER_TOPIC}: {e}")


async def sync_esp_base_rovers() -> None:
    """Main synchronization check:
    - ESP32 sources with fresh Caster data -> ensure RoverAccount and decoder.
    - Missing source data -> stop the decoder; agent status can lag the caster.
    - Offline ESP32 bases -> stop decoder and retain the RoverAccount.
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
        sources = (await auth_db.execute(select(models.CasterSource))).scalars().all()
        # Map: username -> RoverAccount
        rover_map = {r.username.lower(): r for r in existing_rovers}

    for dev in esp_devices:
        serial = dev.serial
        status = str(dev.status or "").lower()
        mp = _extract_esp_mountpoint(dev)

        destinations = _device_source_destinations(dev, mp)
        active_source = next((s for s in sources if s.mountpoint == mp
                              and (s.host.rstrip('.').casefold(), int(s.port)) in destinations
                              and _source_has_data(s, now)), None)
        source_active = active_source is not None
        host = active_source.host if active_source else None
        port = active_source.port if active_source else None
        if mp and not source_active:
            await notify_decoder_worker("stop", serial, mp, mp, DEFAULT_ROVER_PASSWORD)

        if status not in {"online", "streaming"} or dev.is_locked or not dev.is_provisioned:
            continue
        system = dev.system_info if isinstance(dev.system_info, dict) else {}
        startup = system.get("base_startup") or {}
        if isinstance(startup, dict) and startup.get("state") == "auto_base_running":
            continue
        try:
            username = esp_rover_name(dev)
            collision = rover_map.get(username.casefold())
            if collision and (collision.base_serial != serial or collision.created_by_user_id is not None):
                raise ValueError(f"Tài khoản {username} đang thuộc trạm khác hoặc được quản lý thủ công.")
            if await sync_esp_mountpoint(dev, username):
                continue
            if not source_active or mp != username:
                continue
            if not await auto_provision_esp_rover_account(dev, mp):
                if not _esp_setup_status.get(serial, {}).get("rover", {}).get("state") == "partial":
                    _set_esp_setup_status(serial, "rover", "retrying", username=username)
                continue
            for old in existing_rovers:
                if old.base_serial == serial and old.created_by_user_id is None and old.username.casefold() != username.casefold():
                    await auto_deprovision_esp_rover_account(serial, old.username)
        except Exception as exc:
            source = _esp_setup_status.get(serial, {}).get("source", {})
            failed_stage = "rover" if source.get("state") == "ready" else "source"
            _set_esp_setup_status(serial, failed_stage, "retrying",
                                  detail=str(exc) if isinstance(exc, ValueError) else "Chưa đồng bộ được với caster.")
            logger.warning("[ESP ROVER] Không đồng bộ được định danh %s: %s", serial, exc)


def _source_has_data(source, now: int) -> bool:
    """Use the existing Caster poll; never open a Rover socket to probe an idle MP."""
    max_age = max(2 * float(os.getenv('CASTER_MONITOR_INTERVAL', '30')), 2 * SYNC_INTERVAL_SECONDS)
    data = source.source_data if isinstance(source.source_data, dict) else {}
    try:
        count = int(data.get('datacount') or 0)
        observed_at = int(source.last_seen_at or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    return count > 0 and observed_at > 0 and 0 <= now - observed_at <= max_age


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
                     key=lambda s: (not _source_has_data(s, now), s.host, s.port, s.mountpoint))
    notifications = []
    async with device_session_scope() as db:
        # Multiple backend processes share one inventory and one device identity.
        await db.execute(select(func.pg_advisory_xact_lock(713204701)))
        devices = (await db.execute(select(models.Device))).scalars().all()
        # Không tạo bản ghi giám sát từ nguồn mà ESP32 quản lý đang cấu hình/phát.
        managed = set()
        managed_identifiers = set()
        for device in devices:
            if not str(device.serial or "").strip().lower().startswith("esp_"):
                continue
            managed_identifiers.add(f"ESP32_{device.serial}".casefold())
            config = crud.normalize_service_config(device.service_config)
            managed.update(str(config.get(f"mountpoint{slot}") or "").strip().lstrip("/").casefold()
                           for slot in (1, 2))
        by_mp = {}
        for device in devices:
            mp = _extract_esp_mountpoint(device)
            if mp:
                by_mp.setdefault(mp, device)
        seen = set()
        for source in sources:
            mp = source.mountpoint
            metadata = source.source_data if isinstance(source.source_data, dict) else {}
            if mp.casefold() in managed or str(metadata.get("identifier") or "").casefold() in managed_identifiers:
                continue
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
                                     'server1_enabled': True, 'stream_on_demand': False,
                                     'server1_stream_on_demand': False}
            allowed_mps = set(re.split(r'[;,\s]+', rover.mountpoints or '')) if rover else set()
            valid = bool(rover and rover.is_active and rover.password
                         and (not rover.mountpoints or '*' in allowed_mps or mp in allowed_mps)
                         and (not rover.expires_at or rover.expires_at > now))
            source_active = _source_has_data(source, now)
            can_start = valid and source_active
            device.system_info = {**(device.system_info or {}), 'auth_license_bypass': True,
                                  'base_type': '4g', 'read_only': True,
                                  'decoder_status': ('ready' if source_active else 'source_offline') if valid else 'missing_rover_credentials'}
            if not can_start or device.status in ('awaiting_license', 'unprovisioned', 'locked', 'sleep'):
                device.status = 'offline'
                device.bps = 0
                device.ntrip_connected = False
                device.offline_since = device.offline_since or now
            device.is_provisioned = True
            device.device_token = None
            device.is_locked = False
            device.server_config_lock = None
            # No token/license is generated; bypass is owned by the backend.
            notifications.append((device, mp, source, rover if can_start else None))
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


async def resync_decoder_workers() -> None:
    """Replay desired state after the decoder reconnects, including offline bases."""
    _stopped_decoder_serials.clear()
    await sync_prefix7_bases()
    await sync_esp_base_rovers()


async def start_pending_auto_base_workflows() -> None:
    """Provision AutoNearest after a fresh ESP boot status, before the rover connects."""
    async with device_session_scope() as devices_db:
        result = await devices_db.execute(select(models.Device).where(models.Device.serial.ilike('esp_%')))
        devices = result.scalars().all()
        for device in devices:
            system = device.system_info if isinstance(device.system_info, dict) else {}
            startup = system.get('base_startup') if isinstance(system.get('base_startup'), dict) else {}
            if startup.get('state') != 'awaiting_auto_base' or device.is_locked or not device.is_provisioned:
                continue
            device_status = str(device.status or '').casefold()
            if (device_status not in {'online', 'streaming'} and not device_status.startswith('auto_setup')) \
                    or int(device.timestamp or 0) < int(time.time()) - 90:
                continue
            from .station_runtime import latest_device_telemetry_cache
            snapshots = (mqtt_handler.device_telemetry_cache.get(device.serial) or {},
                         latest_device_telemetry_cache.get(device.serial) or {})
            live = max(snapshots, key=lambda item: float(item.get("server_received_at") or item.get("timestamp") or 0))
            boot_id = str(live.get('status_boot_id') or '').strip()
            if not boot_id:
                continue
            config = device.base_config if isinstance(device.base_config, dict) else {}
            request_id = str(config.get('auto_base_request_id') or '')
            start_key = f'{boot_id}:{request_id}'
            if _auto_base_started_boots.get(device.serial) == start_key:
                continue
            if (str(config.get('auto_base_started_boot_id') or '') == boot_id
                    and str(config.get('auto_base_started_request_id') or '') == request_id):
                _auto_base_started_boots[device.serial] = start_key
                continue
            sensor_type = str(device.detected_chip_type or '').strip()
            if sensor_type not in {'Ublox', 'Unicorecomm'}:
                continue
            if not device.user_id:
                continue
            if system.get("auto_base_firmware_profile") is not True:
                _set_esp_setup_status(device.serial, "auto_nearest", "retrying", boot_id=boot_id,
                                      detail="Cần cập nhật firmware hỗ trợ cấu hình caster tích hợp.")
                continue
            auto_base_config = {
                **config,
                'base_setup_method': 'AUTO_CORS',
                'auto_cors_source_mode': 'nearest',
                'mode': 'BASE',
            }
            command = {
                'command': 'TRIGGER_AUTO_BASE',
                'payload': {
                    'source_mode': 'nearest',
                    'auto_apply': True,
                    'startup_auto': True,
                    'startup_boot_id': boot_id,
                    'startup_request_id': request_id,
                    'auto_base_config': auto_base_config,
                    'enable_itrf_vn2000_transform': False,
                    'timeout': 3600,
                    'samples': 60,
                    'sensor_type': sensor_type,
                },
            }
            try:
                await send_command_to_pi(device.serial, command)
                _auto_base_started_boots[device.serial] = start_key
                _set_esp_setup_status(device.serial, "auto_nearest", "sent", boot_id=boot_id)
                logger.info('[AUTO BASE] Started AutoNearest for %s using firmware configuration', device.serial)
            except Exception as exc:
                _set_esp_setup_status(device.serial, "auto_nearest", "delivery_retry", boot_id=boot_id)
                logger.warning('[AUTO BASE] Could not start workflow for %s: %s', device.serial, exc)


async def esp_base_manager_task() -> None:
    """Background loop running every 15s."""
    logger.info("[ESP BASE MANAGER] Started background supervisor task")
    while True:
        try:
            await sync_esp_base_rovers()
            await start_pending_auto_base_workflows()
            await sync_prefix7_bases()
        except Exception as e:
            logger.error(f"[ESP BASE MANAGER] Error in sync loop: {e}", exc_info=True)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)

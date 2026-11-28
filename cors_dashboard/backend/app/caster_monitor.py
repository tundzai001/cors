"""Caster polling and rover history snapshot persistence."""

import asyncio
import logging
import time
from sqlalchemy import select, func
from fastapi import APIRouter
import os
import math

from . import crud, models
from .database import auth_session_scope, device_session_scope, settings
from .runtime_config import _read_caster_config_from_env, ROVER_FIXED_POINT_INTERVAL_SECONDS
from .ntrip_control import RTCM_KEEPALIVE_DEBUG
from .caster_source_store import sync_caster_sources
from .caster_cache import _get_caster_cache, _set_caster_cache, global_caster_cache_lock

logger = logging.getLogger("app.main")
router = APIRouter()


def _add_caster_host_values(unique_hosts, raw_value):
    if not raw_value:
        return
    for host in str(raw_value).split(','):
        host = host.strip()
        if host:
            unique_hosts.add(host)


async def _add_session_caster_hosts(db, unique_hosts):
    try:
        sessions = await db.execute(select(models.RTCMStreamSession.caster_host).distinct())
        for row in sessions.all():
            if row[0]:
                unique_hosts.add(str(row[0]).strip())
    except Exception:
        pass


async def _sync_configured_casters(db):
    env_hosts, env_cfg = _read_caster_config_from_env()
    system_settings = {}
    try:
        system_settings = await crud.get_system_settings(db)
    except Exception:
        pass
    unique_hosts = set(env_hosts)
    for raw_value in (getattr(settings, "CASTER_HOST", ""), system_settings.get('caster_host', '')):
        _add_caster_host_values(unique_hosts, raw_value)
    await _add_session_caster_hosts(db, unique_hosts)

    port = int(getattr(settings, "CASTER_PORT", 2101) or system_settings.get('caster_port', 2101))
    tasks = [
        _sync_single_caster(host, port, env_cfg.get("user"), env_cfg.get("pass"))
        for host in unique_hosts
    ]
    if tasks:
        await asyncio.gather(*tasks)


async def caster_monitor_task():
    """Background task to periodically sync data from all configured casters."""
    logger.info("[CASTER MONITOR] Starting background sync task")
    while True:
        try:
            async with auth_session_scope() as db:
                await _sync_configured_casters(db)
        except Exception as error:
            logger.exception(f"[CASTER MONITOR] Sync loop error: {error}")
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

        (users, users_sampled_at), clients, source_sync = await asyncio.gather(
            fetch_users_snapshot(),
            caster.get_clienttable(mountpoint="all"),
            sync_caster_sources(host, port, user, password),
        )

        # A decoder can share credentials with a real rover; filter by connection identity.
        if clients is not None:
            decoder_counts = {}
            rover_clients = []
            for client in clients:
                if any(str(client.get(key) or '').strip().upper().startswith('AITOGY_DECODER_')
                       for key in ('receiver_id', 'client_id')):
                    username = str(client.get('username') or '').strip().casefold()
                    decoder_counts[username] = decoder_counts.get(username, 0) + 1
                else:
                    rover_clients.append(client)
            clients = rover_clients
            users = [{**account, 'nclient': max(0, int(account.get('nclient') or 0) -
                     decoder_counts.get(str(account.get('username') or '').strip().casefold(), 0))}
                     for account in (users or [])]

        scan_timestamp = int(time.time())
        async with global_caster_cache_lock:
            await _set_caster_cache(key, {
                "last_update": scan_timestamp,
                "users_last_update": users_sampled_at,
                "sources_last_update": source_sync["synced_at"],
                "sources_error": source_sync["error"],
                "users": users or [],
                "clients": clients if clients is not None else [],
                "error": None if clients is not None else "clienttable query failed",
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
        # GGA quality 0 means no valid position; retain other reported qualities.
        if int(client.get("status") or 0) <= 0:
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


def _rover_snapshot_usernames(clients):
    return {
        str(client.get("username") or "").strip().lower()
        for client in clients
        if isinstance(client, dict) and str(client.get("username") or "").strip()
    }


async def _rover_save_settings(db, client_usernames):
    if not client_usernames:
        return {}
    result = await db.execute(
        select(
            func.lower(models.RoverAccount.username),
            models.RoverAccount.auto_save_sessions,
            models.RoverAccount.auto_save_fixed_points,
            models.RoverAccount.base_serial,
        ).where(func.lower(models.RoverAccount.username).in_(client_usernames))
    )
    return {
        username: (bool(save_sessions), bool(save_points), base_serial)
        for username, save_sessions, save_points, base_serial in result.all()
    }


def _rover_base_lookup_scope(rover_settings, client_usernames):
    base_serials = set()
    lookup_all_stations = bool(client_usernames - rover_settings.keys())
    for save_sessions, _, raw_serials in rover_settings.values():
        if not save_sessions:
            continue
        serial_values = str(raw_serials or "").replace(";", ",").split(",")
        if not str(raw_serials or "").strip():
            lookup_all_stations = True
        for serial in serial_values:
            if serial.strip():
                base_serials.add(serial.strip().upper())
    return base_serials, lookup_all_stations


def _add_station_mountpoints(station, names_by_serial_mountpoint, names_by_mountpoint):
    serial = str(station.serial or "").strip().upper()
    base_name = str(station.name or station.serial or "").strip()
    if not serial or not base_name:
        return
    service_config = crud.normalize_service_config(station.service_config)
    for index in (1, 2):
        mountpoint = str(service_config.get(f"mountpoint{index}") or "").strip().casefold()
        if mountpoint:
            names_by_serial_mountpoint[(serial, mountpoint)] = base_name
            names_by_mountpoint.setdefault(mountpoint, set()).add(base_name)


async def _rover_base_name_maps(host, base_serials, lookup_all_stations):
    names_by_serial_mountpoint = {}
    names_by_mountpoint = {}
    if not base_serials and not lookup_all_stations:
        return names_by_serial_mountpoint, names_by_mountpoint
    try:
        async with device_session_scope() as devices_db:
            base_query = select(models.Device)
            if base_serials and not lookup_all_stations:
                base_query = base_query.where(func.upper(models.Device.serial).in_(base_serials))
            result = await devices_db.execute(base_query)
            for station in result.scalars().all():
                _add_station_mountpoints(station, names_by_serial_mountpoint, names_by_mountpoint)
    except Exception as error:
        logger.warning("[CASTER HISTORY] Could not resolve Base names for %s: %s", host, error)
    return names_by_serial_mountpoint, names_by_mountpoint


def _rover_snapshot_base_name(mountpoint, raw_base_serials, names_by_serial_mountpoint, names_by_mountpoint):
    normalized_mountpoint = mountpoint.casefold()
    account_serials = [
        serial.strip().upper()
        for serial in str(raw_base_serials or "").replace(";", ",").split(",")
        if serial.strip()
    ]
    matching_names = {
        names_by_serial_mountpoint[(serial, normalized_mountpoint)]
        for serial in account_serials
        if (serial, normalized_mountpoint) in names_by_serial_mountpoint
    }
    base_name = next(iter(matching_names)) if len(matching_names) == 1 else None
    if base_name is None and not account_serials:
        matching_names = names_by_mountpoint.get(normalized_mountpoint, set())
        base_name = next(iter(matching_names)) if len(matching_names) == 1 else None
    return base_name


def _rover_snapshot_metrics(client):
    try:
        raw_uptime = client.get("uptime")
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
    return uptime_seconds, latest_fix_quality, satellite_count


def _upsert_rover_snapshot_row(
    db, active_by_key, session_key, username, host, mountpoint, base_name,
    client_id, receiver_id, observed_at, uptime_seconds, latest_fix_quality, satellite_count,
):
    row = active_by_key.pop(session_key, None)
    if (
        row is not None
        and uptime_seconds is not None
        and row.uptime_seconds is not None
        and uptime_seconds + 10 < row.uptime_seconds
    ):
        row.ended_at = observed_at
        row = None
    if row is None:
        row = models.RoverSessionHistory(
            username=username,
            server=host,
            mountpoint=mountpoint,
            base_name=base_name,
            client_id=client_id,
            receiver_id=receiver_id,
            started_at=observed_at - uptime_seconds if uptime_seconds else observed_at,
            last_seen_at=observed_at,
            uptime_seconds=uptime_seconds,
            latest_fix_quality=latest_fix_quality,
            satellite_count=satellite_count,
        )
        db.add(row)
        return row
    row.username = username
    row.mountpoint = mountpoint
    if base_name and not row.base_name:
        row.base_name = base_name
    row.client_id = client_id
    row.receiver_id = receiver_id
    row.last_seen_at = observed_at
    row.latest_fix_quality = latest_fix_quality
    row.satellite_count = satellite_count
    if uptime_seconds is not None:
        if row.uptime_seconds is None or row.started_at is None:
            row.started_at = observed_at - uptime_seconds
        row.uptime_seconds = uptime_seconds
    return row


async def _save_rover_snapshot_position(
    db, row, client, latest_points_by_session, observed_at, latest_fix_quality, satellite_count, save_fixed_points,
):
    position = _rover_history_position(client)
    if position is None:
        return
    row.latitude, row.longitude = position
    row.position_fix_quality = latest_fix_quality
    row.position_updated_at = observed_at
    row.position_satellite_count = satellite_count
    if not save_fixed_points:
        return
    last_point_at = latest_points_by_session.get(row.id)
    if last_point_at is not None and observed_at - last_point_at < ROVER_FIXED_POINT_INTERVAL_SECONDS:
        return
    if row.id is None:
        await db.flush()
    db.add(models.RoverFixedPoint(
        session_id=row.id,
        label="Vị trí tự động",
        latitude=position[0],
        longitude=position[1],
        fix_quality=latest_fix_quality or 0,
        satellite_count=satellite_count,
        recorded_at=observed_at,
        saved_at=observed_at,
        created_by="Tự động",
    ))
    latest_points_by_session[row.id] = observed_at


async def _record_rover_snapshot_client(
    db, host, client, username, save_fixed_points, raw_base_serials,
    names_by_serial_mountpoint, names_by_mountpoint, active_by_key, observed_keys,
    latest_points_by_session, observed_at,
):
    mountpoint = str(client.get("mountpoint") or "").strip()
    base_name = _rover_snapshot_base_name(
        mountpoint, raw_base_serials, names_by_serial_mountpoint, names_by_mountpoint
    )
    client_id = str(client.get("client_id") or "").strip()
    receiver_id = str(client.get("receiver_id") or "").strip()
    session_key = _rover_history_session_key(host, username, mountpoint, client_id, receiver_id)
    if session_key in observed_keys:
        return
    observed_keys.add(session_key)
    uptime_seconds, latest_fix_quality, satellite_count = _rover_snapshot_metrics(client)
    row = _upsert_rover_snapshot_row(
        db, active_by_key, session_key, username, host, mountpoint, base_name,
        client_id, receiver_id, observed_at, uptime_seconds, latest_fix_quality, satellite_count,
    )
    await _save_rover_snapshot_position(
        db, row, client, latest_points_by_session, observed_at,
        latest_fix_quality, satellite_count, save_fixed_points,
    )


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
        client_usernames = _rover_snapshot_usernames(clients)
        rover_settings = await _rover_save_settings(db, client_usernames)
        base_serials, lookup_all_stations = _rover_base_lookup_scope(rover_settings, client_usernames)
        names_by_serial_mountpoint, names_by_mountpoint = await _rover_base_name_maps(
            host, base_serials, lookup_all_stations
        )
        active_session_ids = [row.id for row in active_rows if row.id is not None]
        latest_points_by_session = {}
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
            save_sessions, save_fixed_points, raw_base_serials = rover_settings.get(
                username.lower(), (True, False, None)
            )
            if not save_sessions:
                continue
            await _record_rover_snapshot_client(
                db, host, client, username, save_fixed_points, raw_base_serials,
                names_by_serial_mountpoint, names_by_mountpoint, active_by_key,
                observed_keys, latest_points_by_session, observed_at,
            )
        for row in active_by_key.values():
            row.ended_at = observed_at
        await db.commit()


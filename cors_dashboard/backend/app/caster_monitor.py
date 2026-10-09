"""Caster polling and rover history snapshot persistence."""

import asyncio
import logging
import time
from sqlalchemy import select, func
from fastapi import APIRouter
import os
import math

from . import crud, models
from .database import auth_session_scope, settings
from .runtime_config import _read_caster_config_from_env, ROVER_FIXED_POINT_INTERVAL_SECONDS
from .ntrip_control import RTCM_KEEPALIVE_DEBUG
from .caster_cache import _get_caster_cache, _set_caster_cache, global_caster_cache_lock

logger = logging.getLogger("app.main")
router = APIRouter()


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
        rover_save_settings_by_username = {}
        if client_usernames:
            auto_save_result = await db.execute(
                select(
                    func.lower(models.RoverAccount.username),
                    models.RoverAccount.auto_save_sessions,
                    models.RoverAccount.auto_save_fixed_points,
                ).where(func.lower(models.RoverAccount.username).in_(client_usernames))
            )
            rover_save_settings_by_username = {
                username: (bool(save_sessions), bool(save_points))
                for username, save_sessions, save_points in auto_save_result.all()
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
            save_sessions, save_fixed_points = rover_save_settings_by_username.get(username.lower(), (True, False))
            if not save_sessions:
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
                row.position_fix_quality = latest_fix_quality
                row.position_updated_at = observed_at
                row.position_satellite_count = satellite_count
                if save_fixed_points:
                    last_point_at = latest_points_by_session.get(row.id)
                    if (
                        last_point_at is None
                        or observed_at - last_point_at >= ROVER_FIXED_POINT_INTERVAL_SECONDS
                    ):
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

        for row in active_by_key.values():
            row.ended_at = observed_at

        await db.commit()


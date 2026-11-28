"""Activity, operations summaries, alerts and event APIs."""

import logging
from typing import List, Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
import re
import time

from . import auth, crud, models, schemas
from .database import get_auth_db, get_db
from .station_scope import _rtcm_visible_serials
from .caster_cache import _get_all_caster_cache, global_caster_cache_lock

logger = logging.getLogger("app.main")
router = APIRouter()


@router.get("/api/activity/feed", response_model=List[schemas.ActivityLogResponse])
async def get_activity_feed(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    auth_db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    serials = None if current_user.role == auth.Role.ADMIN else await _rtcm_visible_serials(auth_db, db, current_user)
    return await crud.get_activity_logs(
        db,
        limit=limit,
        user_id=None if current_user.role == auth.Role.ADMIN else current_user.id,
        device_serials=serials,
    )

@router.get("/api/activity/heatmap", response_model=schemas.HeatmapData)
async def get_heatmap_data(
    db: AsyncSession = Depends(get_db),
    auth_db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    serials = None if current_user.role == auth.Role.ADMIN else await _rtcm_visible_serials(auth_db, db, current_user)
    data = await crud.get_activity_heatmap(
        db,
        user_id=None if current_user.role == auth.Role.ADMIN else current_user.id,
        device_serials=serials,
    )
    return {"data": data}

# === OPERATIONS CENTER / V1 ADDITIVE OPS APIS ===
def _split_ops_tokens(value: str | None) -> list[str]:
    return [token.strip() for token in re.split(r"[;,\s]+", str(value or "")) if token.strip()]


def _device_total_bps(device: models.Device) -> int:
    status_data = device.ntrip_status if isinstance(device.ntrip_status, dict) else {}
    total = 0
    has_server_bps = False
    for server_id in (1, 2):
        server = status_data.get(f"server{server_id}")
        value = server.get("bps") if isinstance(server, dict) else status_data.get(f"server{server_id}_bps")
        if value is not None:
            has_server_bps = True
            try:
                total += max(0, int(float(value or 0)))
            except (TypeError, ValueError, OverflowError):
                continue
    return total if has_server_bps else int(getattr(device, "bps", 0) or 0)


def _device_status_alerts(device: models.Device, now_ts: int, raw_status: str) -> list[dict]:
    alerts = []
    stale_seconds = now_ts - int(device.timestamp or 0)
    if raw_status == "offline":
        alerts.append({
            "id": f"device-offline-{device.serial}",
            "severity": "critical",
            "type": "device_offline",
            "title": "Station offline",
            "message": f"{device.name or device.serial} is offline",
            "device_serial": device.serial,
            "created_at": int(device.offline_since or device.timestamp or now_ts),
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
    return alerts


def _silent_stream_alerts(device: models.Device, now_ts: int, raw_status: str, service: dict, ntrip: dict) -> list[dict]:
    alerts = []
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
    return alerts


def _device_antenna_alerts(device: models.Device, now_ts: int, raw_status: str, ntrip: dict) -> list[dict]:
    if raw_status not in ("online", "streaming", "sleep", "starting"):
        return []
    system_info = device.system_info if isinstance(device.system_info, dict) else {}
    antenna_status = system_info.get("antenna_status") or ntrip.get("antenna_status")
    if isinstance(antenna_status, dict) and antenna_status.get("state") == "FAULT":
        return [{
            "id": f"device-antenna-fault-{device.serial}",
            "severity": "warning",
            "type": "antenna_fault",
            "title": "Antenna Fault",
            "message": antenna_status.get("message") or f"{device.name or device.serial} has an antenna fault.",
            "device_serial": device.serial,
            "created_at": now_ts,
        }]
    rtcm_in_bps = int(ntrip.get("rtcm_input_bps") or 0)
    if 0 < rtcm_in_bps < 100:
        return [{
            "id": f"device-antenna-low-bps-{device.serial}",
            "severity": "warning",
            "type": "antenna_fault",
            "title": "Low RTCM Input Rate",
            "message": f"{device.name or device.serial} has extremely low RTCM input rate ({rtcm_in_bps} B/s). Possible antenna fault.",
            "device_serial": device.serial,
            "created_at": now_ts,
        }]
    return []


def _device_stream_alerts(device: models.Device, now_ts: int) -> list[dict]:
    service = device.service_config if isinstance(device.service_config, dict) else {}
    ntrip = device.ntrip_status if isinstance(device.ntrip_status, dict) else {}
    raw_status = str(device.status or "offline").lower()
    alerts = _device_status_alerts(device, now_ts, raw_status)
    alerts.extend(_silent_stream_alerts(device, now_ts, raw_status, service, ntrip))
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
    alerts.extend(_device_antenna_alerts(device, now_ts, raw_status, ntrip))
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
    allowed_serials: set[str] | None = None,
    actor_user_id: int | None = None,
) -> list[dict]:
    stmt = select(models.ActivityLog)
    if allowed_serials is not None:
        scope = []
        if allowed_serials:
            scope.append(func.upper(models.ActivityLog.device_serial).in_(allowed_serials))
        if actor_user_id is not None:
            scope.append(and_(models.ActivityLog.user_id == actor_user_id, models.ActivityLog.device_serial.is_(None)))
        stmt = stmt.where(or_(*scope)) if scope else stmt.where(False)
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


@router.get("/api/ops/summary")
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
    allowed_serials = None if current_user.role == auth.Role.ADMIN else {
        str(device.serial).strip().upper() for device in devices if device.serial
    }
    events = await _ops_events_query(
        devices_db,
        limit=8,
        allowed_serials=allowed_serials,
        actor_user_id=None if current_user.role == auth.Role.ADMIN else current_user.id,
    )
    caster_hosts = []
    if current_user.role == auth.Role.ADMIN:
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
        alert = _rover_expiration_alert(rover, now_ts)
        if alert:
            alerts.append(alert)

    if current_user.role == auth.Role.ADMIN:
        alerts.extend(await _admin_rover_block_alerts(auth_db))
        alerts.extend(await _admin_caster_alerts(now_ts))

    states = await _ops_alert_states(auth_db)
    alerts = _apply_ops_alert_states(alerts, states)
    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    return sorted(alerts, key=lambda a: (severity_rank.get(a.get("severity"), 3), -int(a.get("created_at") or 0)))


def _rover_expiration_alert(rover, now_ts):
    if not rover.expires_at:
        return None
    expires_at = int(rover.expires_at)
    if expires_at <= now_ts:
        severity, alert_type, title = "critical", "rover_expired", "Rover expired"
    elif expires_at <= now_ts + 14 * 86400:
        severity, alert_type, title = "warning", "rover_expiring", "Rover expiring"
    else:
        return None
    return {
        "id": f"{alert_type}-{rover.username}",
        "severity": severity,
        "type": alert_type,
        "title": title,
        "message": f"{rover.username} expires at {time.strftime('%Y-%m-%d %H:%M', time.localtime(expires_at))}",
        "rover_username": rover.username,
        "created_at": now_ts,
    }


async def _admin_rover_block_alerts(auth_db: AsyncSession) -> list[dict]:
    result = await auth_db.execute(
        select(models.RoverBlocklist).order_by(models.RoverBlocklist.blocked_at.desc()).limit(50)
    )
    return [{
        "id": f"rover-blocked-{item.id}",
        "severity": "warning",
        "type": "rover_blocked",
        "title": "Rover blocked",
        "message": f"{item.username} blocked on {item.mountpoint or 'all mountpoints'}",
        "rover_username": item.username,
        "mountpoint": item.mountpoint,
        "created_at": item.blocked_at,
    } for item in result.scalars().all()]


async def _admin_caster_alerts(now_ts: int) -> list[dict]:
    alerts = []
    async with global_caster_cache_lock:
        cache_data_by_host = await _get_all_caster_cache()
        for cache_data in cache_data_by_host.values():
            last_update = int(cache_data.get("last_update") or 0)
            has_error = bool(cache_data.get("error"))
            stale = last_update and now_ts - last_update > 180
            if not (has_error or stale):
                continue
            alerts.append({
                "id": f"caster-{cache_data.get('host', 'unknown')}",
                "severity": "critical" if has_error else "warning",
                "type": "caster_error" if has_error else "caster_stale",
                "title": "Caster needs attention",
                "message": cache_data.get("error") or f"Caster cache stale for {(now_ts - last_update) // 60} minutes",
                "caster_host": cache_data.get("host", "unknown"),
                "created_at": last_update or now_ts,
            })
    return alerts


@router.get("/api/ops/alerts")
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


@router.post("/api/ops/alerts/{alert_id}/ack", responses={404: {"description": "Not Found"}})
async def acknowledge_ops_alert(
    alert_id: str,
    payload: dict = Body(default={}),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    return await _set_ops_alert_workflow_state(alert_id, "acknowledged", payload, auth_db, devices_db, current_user)


@router.post("/api/ops/alerts/{alert_id}/resolve", responses={404: {"description": "Not Found"}})
async def resolve_ops_alert(
    alert_id: str,
    payload: dict = Body(default={}),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    return await _set_ops_alert_workflow_state(alert_id, "resolved", payload, auth_db, devices_db, current_user)


@router.get("/api/ops/events")
async def get_ops_events(
    limit: int = Query(50, ge=1, le=200),
    severity: Optional[str] = Query(None),
    device_serial: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    auth_db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    visible_devices = await _ops_visible_devices(db, auth_db, current_user)
    allowed_serials = None if current_user.role == auth.Role.ADMIN else {
        str(device.serial).strip().upper() for device in visible_devices if device.serial
    }
    return {"items": await _ops_events_query(
        db,
        limit=limit,
        severity=severity,
        device_serial=device_serial,
        q=q,
        allowed_serials=allowed_serials,
        actor_user_id=None if current_user.role == auth.Role.ADMIN else current_user.id,
    )}


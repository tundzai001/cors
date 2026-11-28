"""Rover session history, daily activity and track APIs."""

import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import json
import time
from datetime import date, datetime, timedelta, timezone

from . import auth, crud, models, schemas
from .database import get_auth_db, get_db
from .runtime_config import ROVER_FIXED_POINT_INTERVAL_SECONDS
from .operations_routes import (
    _ops_visible_devices,
    _ops_visible_rovers,
    _ops_events_query,
    _ops_cached_rover_status,
)

logger = logging.getLogger("app.main")
router = APIRouter()


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


def _daily_rover_history_query(range_start, range_end, visible_usernames):
    stmt = select(
        models.RoverSessionHistory.username,
        models.RoverSessionHistory.receiver_id,
        models.RoverSessionHistory.started_at,
        models.RoverSessionHistory.last_seen_at,
        models.RoverSessionHistory.ended_at,
    ).where(
        *_rover_history_overlap_filters(range_start, range_end),
        models.RoverSessionHistory.username.is_not(None),
        func.trim(models.RoverSessionHistory.username) != "",
        models.RoverSessionHistory.receiver_id.is_not(None),
        func.trim(models.RoverSessionHistory.receiver_id) != "",
    )
    if visible_usernames is not None:
        stmt = stmt.where(func.lower(func.trim(models.RoverSessionHistory.username)).in_(visible_usernames))
    return stmt


def _add_daily_receiver_activity(daily_machines, date_from, date_to, row):
    username, receiver_id, started_at, last_seen_at, ended_at = row
    receiver_key = str(receiver_id or "").strip().casefold()
    username_key = str(username or "").strip().casefold()
    if not username_key or not receiver_key:
        return
    try:
        first_active_at = int(started_at if started_at is not None else last_seen_at)
        last_active_at = int(ended_at if ended_at is not None else last_seen_at)
        first_active_day = datetime.fromtimestamp(first_active_at, ROVER_HISTORY_LOCAL_TIMEZONE).date()
        last_active_day = datetime.fromtimestamp(
            max(first_active_at, last_active_at - 1), ROVER_HISTORY_LOCAL_TIMEZONE
        ).date()
    except (TypeError, ValueError, OverflowError, OSError):
        return
    cursor = max(date_from, first_active_day)
    last_day = min(date_to, last_active_day)
    while cursor <= last_day:
        daily_machines[cursor].add((username_key, receiver_key))
        cursor += timedelta(days=1)


async def _populate_daily_rover_activity(auth_db, date_from, date_to, visible_usernames, daily_machines):
    if visible_usernames is not None and not visible_usernames:
        return
    range_start, _ = _rover_history_day_bounds(date_from)
    _, range_end = _rover_history_day_bounds(date_to)
    result = await auth_db.execute(_daily_rover_history_query(range_start, range_end, visible_usernames))
    for row in result.all():
        _add_daily_receiver_activity(daily_machines, date_from, date_to, row)


@router.get("/api/rovers/activity/daily", responses={400: {"description": "Bad Request"}})
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
    daily_machines = {
        date_from + timedelta(days=offset): set()
        for offset in range((date_to - date_from).days + 1)
    }
    await _populate_daily_rover_activity(auth_db, date_from, date_to, visible_usernames, daily_machines)

    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "days": [
            {"date": day.isoformat(), "machine_count": len(machines)}
            for day, machines in daily_machines.items()
        ],
    }


@router.get("/api/rovers/activity/day")
def _group_rover_history_sessions(rows):
    items: dict[tuple[str, str], dict] = {}
    machine_by_session: dict[int, tuple[str, str]] = {}
    for session_id, username, mountpoint, receiver_id in rows:
        username_value = str(username or "").strip()
        username_key = username_value.casefold()
        receiver_key = str(receiver_id or "").strip().casefold()
        if not username_key or not receiver_key:
            continue
        machine_key = (username_key, receiver_key)
        item = items.setdefault(machine_key, {
            "machine_key": json.dumps(machine_key, ensure_ascii=False, separators=(",", ":")),
            "username": username_value,
            "receiver_id": str(receiver_id).strip(),
            "usernames": set(),
            "session_count": 0,
            "point_count": 0,
        })
        item["usernames"].add(username_value)
        item["session_count"] += 1
        machine_by_session[session_id] = machine_key
    return items, machine_by_session


async def _add_rover_history_point_counts(auth_db, machine_by_session, start_at, end_at, items):
    if not machine_by_session:
        return
    point_stmt = select(
        models.RoverFixedPoint.session_id,
        func.count(models.RoverFixedPoint.id),
    ).where(
        models.RoverFixedPoint.session_id.in_(list(machine_by_session)),
        models.RoverFixedPoint.recorded_at >= start_at,
        models.RoverFixedPoint.recorded_at < end_at,
    ).group_by(models.RoverFixedPoint.session_id)
    point_result = await auth_db.execute(point_stmt)
    for session_id, point_count in point_result.all():
        item = items.get(machine_by_session.get(session_id, ("", "")))
        if item is not None:
            item["point_count"] += int(point_count or 0)


def _rover_activity_machines(items):
    return [
        {
            **item,
            "usernames": sorted(item["usernames"], key=str.casefold),
        }
        for item in sorted(items.values(), key=lambda value: (value["receiver_id"].casefold(), value["username"].casefold()))
    ]


async def get_rover_activity_day(
    activity_date: date = Query(..., alias="date"),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_ROVERS)),
):
    visible_usernames = await _rover_history_user_scope(auth_db, devices_db, current_user)
    start_at, end_at = _rover_history_day_bounds(activity_date)
    if visible_usernames is not None and not visible_usernames:
        return {"date": activity_date.isoformat(), "machine_count": 0, "machines": []}

    session = models.RoverSessionHistory
    stmt = select(
        session.id,
        session.username, session.mountpoint, session.receiver_id,
    ).where(
        *_rover_history_overlap_filters(start_at, end_at),
        session.username.is_not(None),
        func.trim(session.username) != "",
        session.receiver_id.is_not(None),
        func.trim(session.receiver_id) != "",
    )
    if visible_usernames is not None:
        stmt = stmt.where(func.lower(func.trim(session.username)).in_(visible_usernames))
    result = await auth_db.execute(stmt)
    items, machine_by_session = _group_rover_history_sessions(result.all())
    await _add_rover_history_point_counts(auth_db, machine_by_session, start_at, end_at, items)
    machines = _rover_activity_machines(items)
    return {"date": activity_date.isoformat(), "machine_count": len(machines), "machines": machines}


@router.get("/api/rovers/activity/track", responses={400: {"description": "Bad Request"}})
async def get_rover_activity_track(
    activity_date: date = Query(..., alias="date"),
    receiver_id: str = Query(..., min_length=1, max_length=256),
    username: str = Query(..., min_length=1, max_length=256),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_ROVERS)),
):
    if not receiver_id.strip() or not username.strip():
        raise HTTPException(status_code=400, detail="Tài khoản và receiver không được để trống")
    visible_usernames = await _rover_history_user_scope(auth_db, devices_db, current_user)
    if visible_usernames is not None and not visible_usernames:
        return {"date": activity_date.isoformat(), "username": username, "receiver_id": receiver_id, "sessions": [], "points": []}

    start_at, end_at = _rover_history_day_bounds(activity_date)
    session = models.RoverSessionHistory
    session_query = select(
        session.id,
        session.username,
        session.mountpoint,
        session.base_name,
        session.started_at,
        session.last_seen_at,
        session.ended_at,
    ).where(
        *_rover_history_overlap_filters(start_at, end_at),
        func.lower(func.trim(session.receiver_id)) == receiver_id.strip().casefold(),
        func.lower(func.trim(session.username)) == username.strip().casefold(),
    )
    if visible_usernames is not None:
        session_query = session_query.where(func.lower(func.trim(session.username)).in_(visible_usernames))
    session_result = await auth_db.execute(session_query.order_by(func.coalesce(session.started_at, session.last_seen_at), session.id))
    session_info = {
        session_id: {
            "username": username,
            "mountpoint": mountpoint,
            "base_name": base_name,
            "started_at": started_at,
            "last_seen_at": last_seen_at,
            "ended_at": ended_at,
        }
        for session_id, username, mountpoint, base_name, started_at, last_seen_at, ended_at in session_result.all()
    }
    if not session_info:
        return {"date": activity_date.isoformat(), "username": username, "receiver_id": receiver_id, "sessions": [], "points": []}

    point_result = await auth_db.execute(
        select(
            models.RoverFixedPoint.id,
            models.RoverFixedPoint.latitude,
            models.RoverFixedPoint.longitude,
            models.RoverFixedPoint.recorded_at,
            models.RoverFixedPoint.fix_quality,
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
            "fix_quality": fix_quality,
            "satellites": satellite_count,
            "username": session_info[session_id]["username"],
            "mountpoint": session_info[session_id]["mountpoint"],
            "session_id": session_id,
        }
        for point_id, latitude, longitude, recorded_at, fix_quality, satellite_count, session_id in point_result.all()
    ]
    sessions = [
        {"id": session_id, **info}
        for session_id, info in session_info.items()
    ]
    return {"date": activity_date.isoformat(), "username": username, "receiver_id": receiver_id, "sessions": sessions, "points": points}


async def _rover_history_events(devices_db, auth_db, current_user, username, date_start, date_end):
    is_admin = current_user.role == auth.Role.ADMIN
    allowed_serials = None
    if not is_admin:
        visible_devices = await _ops_visible_devices(devices_db, auth_db, current_user)
        allowed_serials = {
            str(device.serial).strip().upper() for device in visible_devices if device.serial
        }
    return await _ops_events_query(
        devices_db,
        limit=100,
        q=username,
        timestamp_from=date_start,
        timestamp_to=date_end,
        allowed_serials=allowed_serials,
        actor_user_id=None if is_admin else current_user.id,
    )


async def _rover_history_session_page(auth_db, username, date_start, date_end, page):
    normalized_username = username.strip().lower()
    session_query = select(models.RoverSessionHistory).where(
        func.lower(models.RoverSessionHistory.username) == normalized_username
    )
    if date_start is not None and date_end is not None:
        session_query = session_query.where(*_rover_history_overlap_filters(date_start, date_end))
    total_result = await auth_db.execute(
        select(func.count()).select_from(session_query.order_by(None).subquery())
    )
    total_sessions = int(total_result.scalar_one() or 0)
    date_filters = _rover_history_overlap_filters(date_start, date_end) if date_start is not None else ()
    receiver_count_result = await auth_db.execute(
        select(func.count(func.distinct(func.lower(func.trim(models.RoverSessionHistory.receiver_id)))))
        .where(
            func.lower(models.RoverSessionHistory.username) == normalized_username,
            models.RoverSessionHistory.receiver_id.is_not(None),
            func.trim(models.RoverSessionHistory.receiver_id) != "",
            *date_filters,
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
    return page, total_pages, total_sessions, receiver_count, sessions_result.scalars().all()


async def _rover_history_points(auth_db, session_rows, date_start, date_end):
    session_ids = [session.id for session in session_rows]
    points_by_session: dict[int, list[dict]] = {session_id: [] for session_id in session_ids}
    if not session_ids:
        return points_by_session
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
            "id": point.id, "label": point.label, "lat": point.latitude, "lon": point.longitude,
            "fix_quality": point.fix_quality, "satellites": point.satellite_count,
            "recorded_at": point.recorded_at, "saved_at": point.saved_at,
            "created_by": point.created_by,
        })
    return points_by_session


def _rover_history_session_payload(session, points, date_start, date_end):
    has_position = (
        session.latitude is not None
        and session.longitude is not None
        and (date_start is None or date_start <= int(session.position_updated_at or 0) < date_end)
    )
    return {
        "id": session.id,
        "server": session.server,
        "mountpoint": session.mountpoint,
        "base_name": session.base_name,
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
        } if has_position else None,
        "points": points[session.id],
    }


@router.get("/api/rovers/{username}/history", responses={403: {"description": "Forbidden"}})
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
    scoped_names = {str(item.username).strip().lower() for item in scoped}
    if current_user.role != auth.Role.ADMIN and username.strip().lower() not in scoped_names:
        raise HTTPException(status_code=403, detail="Rover is outside your scope")
    date_start, date_end = _rover_history_day_bounds(activity_date) if activity_date else (None, None)
    events = await _rover_history_events(
        devices_db, auth_db, current_user, username, date_start, date_end
    )
    statuses = await _ops_cached_rover_status({username.strip().lower()})
    page, total_pages, total_sessions, receiver_count, session_rows = await _rover_history_session_page(
        auth_db, username, date_start, date_end, page
    )
    points = await _rover_history_points(auth_db, session_rows, date_start, date_end)
    sessions = [
        _rover_history_session_payload(session, points, date_start, date_end)
        for session in session_rows
    ]
    return {
        "username": username,
        "in_db": bool(rover),
        "account": {"username": rover.username} if rover else None,
        "auto_save_fixed_points": bool(getattr(rover, "auto_save_fixed_points", False)) if rover else False,
        "auto_save_sessions": bool(getattr(rover, "auto_save_sessions", True)) if rover else True,
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


@router.delete("/api/rovers/{username}/history/{session_id}", responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 409: {"description": "Conflict"}})
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


@router.delete("/api/rovers/{username}/history/{session_id}/points/{point_id}", responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
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


@router.post("/api/rovers/sync-status")
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

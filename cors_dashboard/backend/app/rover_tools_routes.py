"""Rover validation, renewal and CSV exports."""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
import csv
import io
import re
import time
from fastapi.responses import Response, StreamingResponse

from . import auth, crud, models, schemas
from .database import get_auth_db, get_db, auth_session_scope
from .operations_routes import _ops_visible_rovers, _ops_cached_rover_status, _split_ops_tokens

logger = logging.getLogger("app.main")
router = APIRouter()


@router.get("/api/rovers/validate", responses={403: {"description": "Forbidden"}})
async def validate_rover_username(
    username: str = Query(..., min_length=1),
    mountpoints: Optional[str] = Query(None),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_ROVERS)),
):
    clean_username = username.strip()
    existing = await crud.get_rover_by_username(auth_db, clean_username)
    requested_mps = {mp.lower() for mp in _split_ops_tokens(mountpoints)}
    scoped_rovers = await _ops_visible_rovers(auth_db, devices_db, current_user)
    scoped_names = {str(r.username).strip().lower() for r in scoped_rovers}
    if current_user.role != auth.Role.ADMIN and existing and clean_username.lower() not in scoped_names:
        raise HTTPException(status_code=403, detail="Rover is outside your scope")

    caster_statuses = await _ops_cached_rover_status({clean_username.lower()})
    mountpoint_conflicts = []
    if requested_mps:
        for rover in scoped_rovers:
            if existing and rover.id == existing.id:
                continue
            rover_mps = {mp.lower() for mp in _split_ops_tokens(rover.mountpoints)}
            overlap = sorted(requested_mps & rover_mps)
            if overlap:
                mountpoint_conflicts.append({"username": rover.username, "mountpoints": overlap})

    return {
        "username": clean_username,
        "available": not existing and not caster_statuses,
        "exists_db": bool(existing),
        "exists_caster": bool(caster_statuses),
        "caster_records": caster_statuses,
        "mountpoint_conflicts": mountpoint_conflicts,
    }


@router.post("/api/rovers/bulk-renew", responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}})
async def bulk_renew_rovers(
    request: schemas.RoverBulkRenewRequest,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    if not auth.can_edit_rovers(current_user):
        raise HTTPException(status_code=403, detail="You cannot renew rover accounts")
    names = {name.strip().lower() for name in request.usernames if name.strip()}
    if not names:
        raise HTTPException(status_code=400, detail="usernames is required")
    scoped_rovers = await _ops_visible_rovers(auth_db, devices_db, current_user)
    matched = [rover for rover in scoped_rovers if str(rover.username).strip().lower() in names]
    new_expiry = int(time.time()) + int(request.expiry_days) * 86400
    updated = []
    skipped = sorted(names - {str(r.username).strip().lower() for r in matched})

    for rover in matched:
        rover.expires_at = new_expiry
        rover.is_active = True
        updated.append(rover.username)
    await auth_db.commit()

    await crud.log_system_event(
        db=devices_db,
        type="success",
        message=f"{current_user.username} renewed {len(updated)} rover account(s) for {request.expiry_days} days",
        user_id=current_user.id,
    )
    return {
        "status": "ok",
        "expiry_days": request.expiry_days,
        "expires_at": new_expiry,
        "updated": updated,
        "skipped": skipped,
        "sync_caster": False,
    }


@router.get("/api/rovers/export/csv")
async def export_rovers_csv(
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_ROVERS)),
):
    rovers = await _ops_visible_rovers(auth_db, devices_db, current_user)
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["username", "base_serial", "mountpoints", "target_casters", "active", "max_connections", "expires_at", "created_at"])

    def safe(value):
        if value is None:
            return ""
        if isinstance(value, str) and re.match(r"^\s*[=+\-@]", value):
            return "'" + value
        return value

    for rover in rovers:
        writer.writerow([
            safe(rover.username),
            safe(rover.base_serial),
            safe(rover.mountpoints),
            safe(rover.target_casters),
            "yes" if rover.is_active else "no",
            rover.max_connections,
            rover.expires_at or "",
            rover.created_at or "",
        ])
    return Response(
        content=stream.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=rovers-export.csv"},
    )


def _safe_rover_csv_value(value):
    if value is None:
        return ""
    if isinstance(value, str) and re.match(r"^\s*[=+\-@]", value):
        return "'" + value
    return value


def _rover_session_history_csv_row(session):
    has_position = (
        session.position_fix_quality == 4
        and session.latitude is not None
        and session.longitude is not None
    )
    maps_url = (
        f"https://www.google.com/maps/search/?api=1&query={session.latitude},{session.longitude}"
        if has_position else ""
    )
    return [
        _safe_rover_csv_value(session.username), _safe_rover_csv_value(session.server),
        _safe_rover_csv_value(session.mountpoint), _safe_rover_csv_value(session.receiver_id),
        _safe_rover_csv_value(session.client_id), _safe_rover_csv_value(session.started_at),
        _safe_rover_csv_value(session.last_seen_at), _safe_rover_csv_value(session.ended_at),
        session.latitude if has_position else "", session.longitude if has_position else "",
        _safe_rover_csv_value(session.position_updated_at) if has_position else "", maps_url,
        session.position_fix_quality if has_position else "",
    ]


async def _rover_session_history_csv_stream(query):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "username", "server", "mountpoint", "receiver_id", "client_id",
        "started_at_unix", "last_seen_at_unix", "ended_at_unix",
        "latitude", "longitude", "position_updated_at_unix", "google_maps_url",
        "position_fix_quality",
    ])
    yield "\ufeff" + buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    async with auth_session_scope() as db:
        result = await db.stream_scalars(query)
        count = 0
        async for session in result:
            writer.writerow(_rover_session_history_csv_row(session))
            count += 1
            if count % 256 == 0:
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)
        if buffer.tell():
            yield buffer.getvalue()


@router.get("/api/rovers/history/export/csv")
async def export_rover_session_history_csv(
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_ROVERS)),
):
    """Export every saved session in the viewer's Rover scope."""
    query = select(models.RoverSessionHistory)
    if current_user.role != auth.Role.ADMIN:
        rovers = await _ops_visible_rovers(auth_db, devices_db, current_user)
        usernames = {str(rover.username).strip().lower() for rover in rovers}
        query = query.where(func.lower(models.RoverSessionHistory.username).in_(usernames))
    query = query.order_by(models.RoverSessionHistory.last_seen_at.desc())

    return StreamingResponse(
        _rover_session_history_csv_stream(query),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=rover-session-history.csv"},
    )


"""Caster source, account and blocklist APIs."""

import logging
import math
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import time
from sqlalchemy import delete
from fastapi.responses import PlainTextResponse

from . import auth, crud, models, schemas
from .database import get_auth_db, get_db, auth_session_scope, device_session_scope, settings
from .caster_cache import _get_all_caster_cache, global_caster_cache_lock
from .runtime_config import _read_caster_config_from_env
from .caster_helpers import validate_caster_hosts
from .caster_source_store import inventory_response, sync_configured_sources, lock_source_host, save_sources

logger = logging.getLogger("app.main")
router = APIRouter()
CASTER_NOT_CONFIGURED = "Chưa cấu hình Caster Server trong hệ thống"


def _finite_coordinate(value):
    try:
        coordinate = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return coordinate if math.isfinite(coordinate) else None


@router.get("/api/caster/clienttable", responses={403: {"description": "Forbidden"}})
async def get_caster_clienttable(
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Aggregate clienttable from all scanned casters to show on map."""
    has_rover_view_permission = auth.has_permission(current_user, auth.Permission.VIEW_ROVERS)
    if not has_rover_view_permission:
        raise HTTPException(status_code=403, detail="Bạn không có quyền xem rover trên bản đồ.")
    all_clients = []

    async with global_caster_cache_lock:
        for cache_data in (await _get_all_caster_cache()).values():
            host = cache_data.get("host", "unknown")
            users = cache_data.get("users", [])
            user_visibility_map = {u.get("username", "").strip().lower(): u.get("show_position", True) for u in users}

            clients = cache_data.get("clients", [])
            for c in clients:
                u_lower = str(c.get("username", "")).strip().lower()
                # Add server info and visibility for UI
                c_copy = dict(c)
                c_copy["server"] = host
                c_copy["show_position"] = user_visibility_map.get(u_lower, True)
                # One non-finite coordinate makes Starlette reject the entire JSON response.
                # Keep the client row, but let the map skip that unusable position.
                c_copy["latitude"] = _finite_coordinate(c_copy.get("latitude", c_copy.get("lat")))
                c_copy["longitude"] = _finite_coordinate(c_copy.get("longitude", c_copy.get("lon")))
                if "lat" in c_copy:
                    c_copy["lat"] = _finite_coordinate(c_copy["lat"])
                if "lon" in c_copy:
                    c_copy["lon"] = _finite_coordinate(c_copy["lon"])
                all_clients.append(c_copy)

    # Keep all clients; frontend will decide visibility by valid coordinates.
    filtered_clients = list(all_clients)

    # --- PHÂN QUYỀN HIỂN THỊ TRÊN MAP ---
    if current_user.role != auth.Role.ADMIN:
        # get_visible_rovers_for_user is the single source of truth for rover scope.
        my_rovers = await crud.get_visible_rovers_for_user(db, devices_db, current_user)
        allowed_usernames = {
            str(r.username).strip().lower() for r in my_rovers if r.username
        }
        filtered_clients = [c for c in filtered_clients if str(c.get("username", "")).strip().lower() in allowed_usernames]

    return {
        "count": len(filtered_clients),
        "items": filtered_clients,
    }


async def _collect_caster_users():
    users = []
    async with global_caster_cache_lock:
        for cache_data in (await _get_all_caster_cache()).values():
            host = cache_data.get("host", "unknown")
            for user in cache_data.get("users", []):
                item = dict(user)
                item["server"] = host
                item["last_update"] = int(
                    cache_data.get("users_last_update") or cache_data.get("last_update") or 0
                )
                users.append(item)
    return users


async def _visible_caster_users(users, auth_db, devices_db, current_user):
    if current_user.role == auth.Role.ADMIN:
        return users
    rovers = await crud.get_visible_rovers_for_user(auth_db, devices_db, current_user)
    allowed = {str(rover.username).strip().lower() for rover in rovers if rover.username}
    return [
        user for user in users
        if str(user.get("username", "")).strip().lower() in allowed
    ]


async def _caster_user_lookup_maps(auth_db):
    rover_result = await auth_db.execute(select(models.RoverAccount))
    rover_map = {
        str(rover.username).strip().lower(): rover
        for rover in rover_result.scalars().all()
    }
    block_result = await auth_db.execute(select(models.RoverBlocklist))
    block_map: Dict[str, List[models.RoverBlocklist]] = {}
    for block in block_result.scalars().all():
        block_map.setdefault(str(block.username).lower(), []).append(block)
    return rover_map, block_map


async def _active_caster_sessions():
    sessions = {}
    async with global_caster_cache_lock:
        for cache_data in (await _get_all_caster_cache()).values():
            host = cache_data.get("host", "unknown")
            last_update = int(cache_data.get("last_update") or time.time())
            for client in cache_data.get("clients", []):
                key = (host, str(client.get("username", "")).strip().lower())
                sessions.setdefault(key, []).append({
                    "mountpoint": client.get("mountpoint"),
                    "requested_mountpoint": client.get("requested_mountpoint"),
                    "selected_mountpoint": client.get("selected_mountpoint"),
                    "uptime": int(client.get("uptime", 0) or 0),
                    "client_id": str(client.get("client_id", "") or "").strip(),
                    "receiver_id": str(client.get("receiver_id", "") or "").strip(),
                    "last_update": last_update,
                })
    return sessions


def _caster_user_block_status(username_lower, user, blocklist_map):
    for entry in blocklist_map.get(username_lower, []):
        if entry.mountpoint is None:
            return True, entry.reason, entry.blocked_at
        user_mountpoints = [mp.lower().strip() for mp in user.get("mountpoints", [])]
        if entry.mountpoint.lower().strip() in user_mountpoints:
            return True, entry.reason, entry.blocked_at
    return False, None, None


def _active_connection_items(raw_connections):
    items = []
    for connection in raw_connections:
        uptime = int(connection.get("uptime", 0) or 0)
        last_update = int(connection.get("last_update") or time.time())
        items.append({
            "mountpoint": connection.get("mountpoint"),
            "requested_mountpoint": connection.get("requested_mountpoint"),
            "selected_mountpoint": connection.get("selected_mountpoint"),
            "uptime": uptime,
            "duration_seconds": uptime,
            "client_id": connection.get("client_id", ""),
            "receiver_id": connection.get("receiver_id", ""),
            "started_at": last_update - uptime if uptime > 0 else None,
            "last_seen_at": last_update,
        })
    return items


def _build_caster_rover_item(user, rover_map, blocklist_map, version_map, sessions):
    username = str(user.get("username", "")).strip()
    username_lower = username.lower()
    server_host = user.get("server")
    rover = rover_map.get(username_lower)
    in_db = rover is not None
    is_blocked, blocked_reason, blocked_at = _caster_user_block_status(
        username_lower, user, blocklist_map
    )
    active_connections = _active_connection_items(sessions.get((server_host, username_lower), []))
    serial = str(rover.base_serial or "").strip().upper() if rover else ""
    item = schemas.CasterRoverItem(
        username=username,
        password=str(user.get("password", "")),
        mountpoints=user.get("mountpoints", []),
        datacount=user.get("datacount", 0),
        status=user.get("status", 0),
        last_update=user.get("last_update", 0),
        identifier=str(user.get("identifier", "")),
        nclient=user.get("nclient", 0),
        maxclient=user.get("maxclient", 0),
        in_db=in_db,
        is_blocked=is_blocked,
        blocked_reason=blocked_reason,
        blocked_at=blocked_at,
        server=server_host,
        version=version_map.get(serial) if serial else None,
        active_connections=active_connections,
        show_position=user.get("show_position", True),
    )
    return item, in_db, is_blocked


@router.get("/api/caster/users", response_model=schemas.CasterUsersResponse)
async def get_caster_users(
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_ROVERS)),
):
    """Aggregate total users from all scanned casters with enrichment."""
    users = await _collect_caster_users()
    users = await _visible_caster_users(users, auth_db, devices_db, current_user)
    rover_map, blocklist_map = await _caster_user_lookup_maps(auth_db)
    async with device_session_scope() as db:
        result = await db.execute(select(models.Device.serial, models.Device.version))
        version_map = {str(device.serial).upper(): device.version for device in result.all()}
    sessions = await _active_caster_sessions()

    items = []
    in_db_count = 0
    blocked_count = 0
    for user in users:
        item, in_db, is_blocked = _build_caster_rover_item(
            user, rover_map, blocklist_map, version_map, sessions
        )
        items.append(item)
        in_db_count += in_db
        blocked_count += is_blocked
    return schemas.CasterUsersResponse(
        server_time=int(time.time()),
        total=len(items),
        in_db_count=in_db_count,
        unknown_count=len(items) - in_db_count,
        blocked_count=blocked_count,
        items=items,
    )


@router.get("/api/admin/caster-sources", responses={403: {"description": "Forbidden"}})
async def get_admin_caster_sources(
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    """Read the durable inventory without querying caster sockets."""
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ ADMIN mới có quyền xem toàn bộ mountpoints từ Caster")
    result = await db.execute(select(models.CasterSource).order_by(models.CasterSource.host, models.CasterSource.mountpoint))
    hosts, _ = _read_caster_config_from_env()
    async with global_caster_cache_lock:
        observations = {str(item.get('host') or '').lower(): int(item['sources_last_update'])
                        for item in (await _get_all_caster_cache()).values()
                        if not item.get('sources_error') and item.get('sources_last_update')
                        and time.time() - int(item['sources_last_update']) <= 120}
    return inventory_response(result.scalars().all(), hosts, observations)


@router.post("/api/admin/caster-sources/sync", responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}})
async def sync_admin_caster_sources(
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ ADMIN mới có quyền đồng bộ mountpoint")
    hosts, config = _read_caster_config_from_env()
    if not hosts:
        raise HTTPException(status_code=400, detail=CASTER_NOT_CONFIGURED)
    results = await sync_configured_sources(hosts, config)
    return {'results': results, 'synced_hosts': sum(result['error'] is None for result in results)}


@router.post("/api/admin/caster-sources/restore", responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 409: {"description": "Conflict"}, 502: {"description": "Bad Gateway"}})
async def restore_admin_caster_source(
    req: schemas.CasterSourceRestoreRequest,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    """Restore a missing source from saved settings; never overwrite a live source."""
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ ADMIN được khôi phục mountpoint")
    try:
        host = validate_caster_hosts([req.host.strip()])[0]
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    _, config = _read_caster_config_from_env()
    port = int(config.get("port") or settings.CASTER_PORT or 2101)
    await lock_source_host(db, host, port)
    result = await db.execute(select(models.CasterSource).where(
        models.CasterSource.host == host.lower(),
        models.CasterSource.port == port,
        models.CasterSource.mountpoint == req.mountpoint.strip(),
    ))
    saved = result.scalars().first()
    if saved is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy cấu hình mountpoint đã lưu")
    from .caster_client import CasterClient
    caster = CasterClient(host, port)
    live = await caster.get_sources()
    if live is None:
        raise HTTPException(status_code=502, detail="Không đọc được caster; cấu hình đã lưu được giữ nguyên. Hãy thử lại.")
    if any(item.get("mountpoint") == saved.mountpoint for item in live):
        return {"status": "already_present", "host": host, "mountpoint": saved.mountpoint}
    if not saved.password:
        raise HTTPException(status_code=409, detail="Thiếu mật khẩu mountpoint đã lưu; cần nhập lại mật khẩu khi tạo mountpoint.")
    fields = schemas.CasterSourceCreateRequest.model_fields
    values = {key: value for key, value in (saved.source_data or {}).items()
              if key in fields and key not in {"host", "mountpoint", "password", "identifier"}}
    source = schemas.CasterSourceCreateRequest(
        host=host, mountpoint=saved.mountpoint, password=saved.password,
        identifier=(saved.source_data or {}).get("identifier") or current_user.username,
        **values,
    )
    if not await caster.create_source(**source.model_dump(exclude={"host"})):
        raise HTTPException(status_code=502, detail="Caster chưa xác nhận khôi phục mountpoint; cấu hình đã lưu được giữ nguyên.")
    return {"status": "restored", "host": host, "mountpoint": saved.mountpoint}


@router.put("/api/admin/caster-sources", responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 409: {"description": "Conflict"}, 502: {"description": "Bad Gateway"}})
async def update_admin_caster_source(
    req: schemas.CasterSourceUpdateRequest,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ ADMIN được sửa mountpoint")
    try:
        host = validate_caster_hosts([req.host or ""])[0]
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    _, config = _read_caster_config_from_env()
    port = int(config.get("port") or settings.CASTER_PORT or 2101)
    await lock_source_host(db, host, port)
    saved = (await db.execute(select(models.CasterSource).where(
        models.CasterSource.host == host.lower(), models.CasterSource.port == port,
        models.CasterSource.mountpoint == req.mountpoint.strip(),
    ))).scalars().first()
    if saved is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy mountpoint đã lưu")
    fields = schemas.CasterSourceCreateRequest.model_fields
    old = {k: v for k, v in (saved.source_data or {}).items() if k in fields}
    old.update(mountpoint=saved.mountpoint, password=saved.password,
               identifier=(saved.source_data or {}).get('identifier') or '')
    values = {**old, **req.model_dump(exclude_unset=True, exclude={'host', 'mountpoint', 'password'})}
    values['password'] = req.password or saved.password
    if not values['password'] or not saved.password:
        raise HTTPException(status_code=409, detail="Thiếu mật khẩu đã lưu; không thể sửa an toàn. Hãy tạo lại mountpoint với mật khẩu đầy đủ.")
    desired = schemas.CasterSourceCreateRequest(**values).model_dump(exclude={'host'})
    from .caster_client import CasterClient
    caster = CasterClient(host, port)
    live = await caster.get_sources()
    if live is None:
        raise HTTPException(status_code=502, detail="Không đọc được caster; chưa thay đổi cấu hình.")
    existing = next((item for item in live if item.get('mountpoint') == saved.mountpoint), None)
    if existing is not None and not await caster.delete_source(existing.get('identifier') or '', saved.mountpoint):
        raise HTTPException(status_code=502, detail="Caster chưa xác nhận cập nhật; cấu hình đã lưu được giữ nguyên.")
    if not await caster.create_source(**desired):
        rollback = schemas.CasterSourceCreateRequest(**old).model_dump(exclude={'host'})
        restored = await caster.create_source(**rollback)
        detail = "Lưu thất bại; đã gửi lại cấu hình cũ." if restored else "Lưu thất bại; cấu hình cũ vẫn được giữ. Cần khôi phục mountpoint trên caster."
        raise HTTPException(status_code=502, detail=detail)
    await save_sources(db, host, port, [desired], int(time.time()))
    await db.commit()
    return {'status': 'updated', 'host': host, 'mountpoint': saved.mountpoint}


@router.post("/api/admin/caster-sources", responses={403: {"description": "Forbidden"}, 500: {"description": "Internal Server Error"}, 502: {"description": "Bad Gateway"}})
async def create_admin_caster_source(
    req: schemas.CasterSourceCreateRequest,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ ADMIN mới có quyền tạo mountpoint trên Caster")

    from app.caster_client import CasterClient

    hosts, env_cfg = _read_caster_config_from_env()
    target_host = (req.host or (hosts[0] if hosts else None) or env_cfg.get("host") or settings.CASTER_HOST or "").strip()
    if not target_host:
        raise HTTPException(status_code=500, detail=CASTER_NOT_CONFIGURED)
    try:
        target_host = validate_caster_hosts([target_host])[0]
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None

    port = env_cfg.get("port") or int(settings.CASTER_PORT or 2101)
    await lock_source_host(db, target_host, port)
    caster = CasterClient(target_host, port)
    ok = await caster.create_source(
        req.identifier,
        req.mountpoint,
        req.password,
        req.counter,
        req.index,
        req.latitude,
        req.longitude,
        req.auth_required,
        req.gga_required,
        req.show_position,
        req.overlap_data,
        req.re_positioning,
        req.storing_data,
    )
    if not ok:
        raise HTTPException(status_code=502, detail=f"Không thể tạo mountpoint '{req.mountpoint}' trên caster {target_host}")

    await save_sources(db, target_host, port, [req.model_dump(exclude={'host'})], int(time.time()))
    await db.commit()

    return {"status": "ok", "host": target_host, "mountpoint": req.mountpoint, "identifier": req.identifier}


@router.delete("/api/admin/caster-sources", responses={403: {"description": "Forbidden"}, 500: {"description": "Internal Server Error"}, 502: {"description": "Bad Gateway"}})
async def delete_admin_caster_source(
    req: schemas.CasterSourceDeleteRequest,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ ADMIN mới có quyền xóa mountpoint trên Caster")

    from app.caster_client import CasterClient

    hosts, env_cfg = _read_caster_config_from_env()
    target_host = (req.host or (hosts[0] if hosts else None) or env_cfg.get("host") or settings.CASTER_HOST or "").strip()
    if not target_host:
        raise HTTPException(status_code=500, detail=CASTER_NOT_CONFIGURED)
    try:
        target_host = validate_caster_hosts([target_host])[0]
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None

    port = env_cfg.get("port") or int(settings.CASTER_PORT or 2101)
    await lock_source_host(db, target_host, port)
    caster = CasterClient(target_host, port)
    ok = await caster.delete_source(req.identifier, req.mountpoint)
    if not ok:
        # Retrying after a partial delete is safe once absence is confirmed.
        sources = await caster.get_sources()
        if sources is None or any(str(source.get('mountpoint') or '').strip() == req.mountpoint.strip() for source in sources):
            raise HTTPException(status_code=502, detail=f"Không thể xóa mountpoint '{req.mountpoint}' trên caster {target_host}")

    await db.execute(delete(models.CasterSource).where(
        models.CasterSource.host == target_host.lower(),
        models.CasterSource.port == int(port),
        models.CasterSource.mountpoint == req.mountpoint.strip(),
    ))
    await db.commit()

    return {"status": "ok", "host": target_host, "mountpoint": req.mountpoint, "identifier": req.identifier}


async def _create_caster_blocklist_entries(auth_db, username: str, mountpoints, blocked_by: str, reason: str) -> int:
    blocks_created = 0
    blocked_at = int(time.time())
    for mountpoint_value in mountpoints or [None]:
        mountpoint = mountpoint_value.strip() if mountpoint_value else None
        query = select(models.RoverBlocklist).where(models.RoverBlocklist.username == username)
        if mountpoint:
            query = query.where(models.RoverBlocklist.mountpoint == mountpoint)
        else:
            query = query.where(models.RoverBlocklist.mountpoint.is_(None))
        existing = await auth_db.execute(query)
        if existing.scalars().first():
            continue
        auth_db.add(models.RoverBlocklist(
            username=username,
            mountpoint=mountpoint,
            blocked_at=blocked_at,
            blocked_by=blocked_by,
            reason=reason,
        ))
        blocks_created += 1
    if blocks_created:
        await auth_db.commit()
    return blocks_created


async def _block_caster_user_on_hosts(username: str, blocked_by: str, reason: str) -> int:
    hosts, _ = _read_caster_config_from_env()
    target_hosts = hosts if hosts else [str(settings.CASTER_HOST or "localhost")]
    port = int(settings.CASTER_PORT or 2101)
    affected = 0
    for host in target_hosts:
        try:
            from app.caster_client import CasterClient
            caster = CasterClient(host, port)
            if await caster.block_user(identifier=blocked_by, username=username, reason=reason):
                affected += 1
            logger.info(f"[CASTER MGMT] Blocked user '{username}' on caster {host}")
        except Exception as error:
            logger.warning(f"[CASTER MGMT] Failed to block user on caster {host}: {error}")
    return affected


@router.post("/api/caster/block-user", responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def block_caster_user(
    req: schemas.BlockRoverRequest,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    """
    Chặn rover trên hệ thống:
    1. Lưu vào blocklist DB (source of truth)
    2. Gọi caster TCP block_user để kick và chặn ngay lập tức
    3. Cập nhật RoverAccount.is_active = False (nếu user có trong DB)
    """
    username = req.username.strip()
    rover = await crud.get_rover_by_username(auth_db, username)
    if not rover and current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=404, detail="Không tìm thấy Rover")
    if rover:
        has_perm = await crud.check_rover_management_permission(rover, current_user, devices_db, auth_db)
        if not has_perm:
            raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý Rover này.")

    reason = req.reason or "Blocked by admin"
    blocks_created = await _create_caster_blocklist_entries(
        auth_db, username, req.mountpoints, current_user.username, reason
    )
    deleted_count_caster = await _block_caster_user_on_hosts(
        username, current_user.username, reason
    )

    return {
        "status": "blocked",
        "username": username,
        "blocks_created": blocks_created,
        "casters_affected": deleted_count_caster,
        "message": f"Đã chặn user '{username}' ({blocks_created} mountpoints) trên toàn hệ thống ({deleted_count_caster} server affected)."
    }


@router.get("/api/caster/blocklist")
async def get_caster_blocklist(
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    """Fetch all active block rules."""
    rs = await db.execute(select(models.RoverBlocklist).order_by(models.RoverBlocklist.blocked_at.desc()))
    return rs.scalars().all()


async def _require_rover_management_access(auth_db, devices_db, username: str, current_user):
    rover = await crud.get_rover_by_username(auth_db, username)
    if not rover and current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=404, detail="Không tìm thấy Rover")
    if rover:
        has_permission = await crud.check_rover_management_permission(
            rover, current_user, devices_db, auth_db
        )
        if not has_permission:
            raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý Rover này.")
    return rover


async def _delete_rover_block_entries(auth_db, username: str, mountpoint: Optional[str]) -> None:
    query = delete(models.RoverBlocklist).where(models.RoverBlocklist.username == username)
    if mountpoint:
        query = query.where(models.RoverBlocklist.mountpoint == mountpoint)
    result = await auth_db.execute(query)
    _ = result.rowcount
    await auth_db.commit()


async def _recreate_rover_on_casters(username: str, rover, current_user) -> bool:
    hosts, _ = _read_caster_config_from_env()
    target_hosts = hosts if hosts else [str(settings.CASTER_HOST or "localhost")]
    port = int(settings.CASTER_PORT or 2101)
    recreated_count = 0
    for host in target_hosts:
        try:
            from app.caster_client import CasterClient
            caster = CasterClient(host, port)
            await caster.unblock_user(identifier=current_user.username, username=rover.username)
            recreated_count += 1
            logger.info(f"[CASTER MGMT] Unblocked user '{username}' on caster {host}")
        except Exception as error:
            logger.warning(f"[CASTER MGMT] Failed to unblock user on caster {host}: {error}")
    return recreated_count > 0


@router.post("/api/caster/unblock-user", responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def unblock_caster_user(
    req: schemas.UnblockRoverRequest,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    """
    Mở khóa rover:
    1. Xóa khỏi blocklist DB
    2. Tạo lại user trên caster (nếu có thông tin trong DB)
    3. Cập nhật RoverAccount.is_active = True (nếu có)
    """
    username = req.username.strip()
    await _require_rover_management_access(auth_db, devices_db, username, current_user)
    mountpoint = req.mountpoint.strip() if req.mountpoint else None
    await _delete_rover_block_entries(auth_db, username, mountpoint)

    caster_recreated = False
    rover = await crud.get_rover_by_username(auth_db, username)

    if rover:
        rover.is_active = True
        await auth_db.commit()
        caster_recreated = await _recreate_rover_on_casters(username, rover, current_user)
    else:
        await auth_db.commit()

    return {
        "status": "unblocked",
        "username": username,
        "mountpoint": mountpoint,
        "caster_recreated": caster_recreated,
        "in_db": rover is not None,
        "message": f"Đã mở khóa user '{username}'." + (" User đã được tạo lại trên caster." if caster_recreated else " (User không có trong DB, cần tạo thủ công trên caster)")
    }


@router.get("/api/ntrip/auth-check", response_class=PlainTextResponse)
async def ntrip_auth_check(
    user: str = Query(...),
    mountpoint: Optional[str] = Query(None),
):
    """
    Endpoint cho NTRIP Caster gọi để kiểm tra xem user có bị chặn không.
    Caster gọi: GET /api/ntrip/auth-check?user=xxx&mountpoint=yyy
    
    Response:
    - 200 "OK" → Caster cho phép kết nối (không bị chặn)
    - 403 "BLOCKED" → Caster từ chối kết nối (bị chặn)
    
    Endpoint này KHÔNG yêu cầu auth token vì được gọi bởi caster process.
    """
    username = user.strip()
    mountpoint_clean = mountpoint.strip() if mountpoint else None

    try:
        async with auth_session_scope() as auth_db:
            # Check blocklist
            block_q = select(models.RoverBlocklist).where(
                models.RoverBlocklist.username == username,
            )
            block_rs = await auth_db.execute(block_q)
            block_entries = block_rs.scalars().all()

            for entry in block_entries:
                # Block all mountpoints
                if entry.mountpoint is None:
                    logger.info("[NTRIP AUTH] BLOCKED user=%r reason=%r (all mountpoints)", username, entry.reason)
                    return PlainTextResponse(
                        f"BLOCKED: {entry.reason or 'User is blocked'}",
                        status_code=403,
                    )
                # Block specific mountpoint
                if mountpoint_clean and entry.mountpoint.lower().strip() == mountpoint_clean.lower():
                    logger.info("[NTRIP AUTH] BLOCKED user=%r mountpoint=%r reason=%r", username, mountpoint_clean, entry.reason)
                    return PlainTextResponse(
                        f"BLOCKED: {entry.reason or 'User is blocked on this mountpoint'}",
                        status_code=403,
                    )

            # Không bị chặn → cho phép
            return PlainTextResponse("OK", status_code=200)

    except Exception as e:
        logger.exception("[NTRIP AUTH] Blocklist lookup failed (%s)", type(e).__name__)
        return PlainTextResponse("UNAVAILABLE", status_code=503)


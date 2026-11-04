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


def _finite_coordinate(value):
    try:
        coordinate = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return coordinate if math.isfinite(coordinate) else None


@router.get("/api/caster/clienttable")
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


@router.get("/api/caster/users", response_model=schemas.CasterUsersResponse)
async def get_caster_users(
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_ROVERS)),
):
    """Aggregate total users from all scanned casters with enrichment."""
    all_caster_users = []

    async with global_caster_cache_lock:
        for cache_data in (await _get_all_caster_cache()).values():
            host = cache_data.get("host", "unknown")
            users = cache_data.get("users", [])
            for u in users:
                u_copy = dict(u)
                u_copy["server"] = host # Tag with server
                u_copy["last_update"] = int(cache_data.get("users_last_update") or cache_data.get("last_update") or 0)
                all_caster_users.append(u_copy)

    # --- PHÂN QUYỀN HIỂN THỊ (ĐỒNG BỘ VỚI MAP) ---
    if current_user.role != auth.Role.ADMIN:
        # Use the same resolved rover scope as the DB list and map endpoint.
        my_rovers = await crud.get_visible_rovers_for_user(auth_db, devices_db, current_user)
        allowed_usernames = {
            str(r.username).strip().lower() for r in my_rovers if r.username
        }
        all_caster_users = [u for u in all_caster_users if str(u.get("username", "")).strip().lower() in allowed_usernames]


    # Local DB Lookups
    db_rovers_rs = await auth_db.execute(select(models.RoverAccount))
    db_rovers_map = {str(r.username).strip().lower(): r for r in db_rovers_rs.scalars().all()}

    blocklist_rs = await auth_db.execute(select(models.RoverBlocklist))
    blocklist_items = blocklist_rs.scalars().all()
    blocklist_map: Dict[str, List[models.RoverBlocklist]] = {}
    for bl in blocklist_items:
        key = str(bl.username).lower()
        if key not in blocklist_map:
            blocklist_map[key] = []
        blocklist_map[key].append(bl)

    # Device lookup for version matching
    async with device_session_scope() as db:
        res_devices = await db.execute(select(models.Device.serial, models.Device.version))
        device_versions = {str(d.serial).upper(): d.version for d in res_devices.all()}

    # 1. Active Sessions Lookup Map (from Caster Cache)
    active_sessions_map = {}  # (server, username) -> [ {mp, uptime} ]

    async with global_caster_cache_lock:
        for cache_data in (await _get_all_caster_cache()).values():
            host = cache_data.get("host", "unknown")
            last_update = int(cache_data.get("last_update") or time.time())
            clients = cache_data.get("clients", [])
            for c in clients:
                user_key = (host, str(c.get("username", "")).strip().lower())
                if user_key not in active_sessions_map:
                    active_sessions_map[user_key] = []
                active_sessions_map[user_key].append({
                    "mountpoint": c.get("mountpoint"),
                    "uptime": int(c.get("uptime", 0) or 0),
                    "client_id": str(c.get("client_id", "") or "").strip(),
                    "last_update": last_update,
                })

    enriched_items = []
    in_db_count = 0
    blocked_count = 0

    for cu in all_caster_users:
        username = str(cu.get("username", "")).strip()
        username_lower = username.lower()
        server_host = cu.get("server")

        in_db = username_lower in db_rovers_map
        is_blocked = False
        blocked_reason = None
        blocked_at = None

        if username_lower in blocklist_map:
            for bl_entry in blocklist_map[username_lower]:
                if bl_entry.mountpoint is None:
                    is_blocked = True
                    blocked_reason = bl_entry.reason
                    blocked_at = bl_entry.blocked_at
                    break
                else:
                    user_mps = [mp.lower().strip() for mp in cu.get("mountpoints", [])]
                    if bl_entry.mountpoint.lower().strip() in user_mps:
                        is_blocked = True
                        blocked_reason = bl_entry.reason
                        blocked_at = bl_entry.blocked_at
                        break

        if in_db: in_db_count += 1
        if is_blocked: blocked_count += 1

        # Match active connections
        active_key = (server_host, username_lower)
        active_conns_raw = active_sessions_map.get(active_key, [])
        active_conns = []
        for ac in active_conns_raw:
            uptime = int(ac.get("uptime", 0) or 0)
            last_update = int(ac.get("last_update") or time.time())
            started_at = last_update - uptime if uptime > 0 else None
            active_conns.append({
                "mountpoint": ac.get("mountpoint"),
                "uptime": uptime,
                "duration_seconds": uptime,
                "client_id": ac.get("client_id", ""),
                "started_at": started_at,
                "last_seen_at": last_update,
            })

        enriched_items.append(schemas.CasterRoverItem(
            username=username,
            password=str(cu.get("password", "")),
            mountpoints=cu.get("mountpoints", []),
            datacount=cu.get("datacount", 0),
            status=cu.get("status", 0),
            last_update=cu.get("last_update", 0),
            identifier=str(cu.get("identifier", "")),
            nclient=cu.get("nclient", 0),
            maxclient=cu.get("maxclient", 0),
            in_db=in_db,
            is_blocked=is_blocked,
            blocked_reason=blocked_reason,
            blocked_at=blocked_at,
            server=server_host,
            version=device_versions.get(str(db_rovers_map[username_lower].base_serial or "").strip().upper()) if in_db and db_rovers_map[username_lower].base_serial else None,
            active_connections=active_conns,
            show_position=cu.get("show_position", True)
        ))

    return schemas.CasterUsersResponse(
        server_time=int(time.time()),
        total=len(enriched_items),
        in_db_count=in_db_count,
        unknown_count=len(enriched_items) - in_db_count,
        blocked_count=blocked_count,
        items=enriched_items,
    )


@router.get("/api/admin/caster-sources")
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


@router.post("/api/admin/caster-sources/sync")
async def sync_admin_caster_sources(
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_CASTER)),
):
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ ADMIN mới có quyền đồng bộ mountpoint")
    hosts, config = _read_caster_config_from_env()
    if not hosts:
        raise HTTPException(status_code=400, detail="Chưa cấu hình Caster Server trong hệ thống")
    results = await sync_configured_sources(hosts, config)
    return {'results': results, 'synced_hosts': sum(result['error'] is None for result in results)}


@router.post("/api/admin/caster-sources/restore")
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


@router.put("/api/admin/caster-sources")
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


@router.post("/api/admin/caster-sources")
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
        raise HTTPException(status_code=500, detail="Chưa cấu hình Caster Server trong hệ thống")
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


@router.delete("/api/admin/caster-sources")
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
        raise HTTPException(status_code=500, detail="Chưa cấu hình Caster Server trong hệ thống")
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


@router.post("/api/caster/block-user")
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

    # Permission check if rover exists in DB
    rover = await crud.get_rover_by_username(auth_db, username)
    if not rover and current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=404, detail="Không tìm thấy Rover")
    if rover:
        has_perm = await crud.check_rover_management_permission(rover, current_user, devices_db, auth_db)
        if not has_perm:
            raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý Rover này.")

    username = req.username.strip()
    target_mps = req.mountpoints if req.mountpoints else [None]
    now = int(time.time())

    blocks_created = 0
    for mp in target_mps:
        mountpoint = mp.strip() if mp else None

        # Check duplicate
        existing_block_q = select(models.RoverBlocklist).where(models.RoverBlocklist.username == username)
        if mountpoint:
            existing_block_q = existing_block_q.where(models.RoverBlocklist.mountpoint == mountpoint)
        else:
            existing_block_q = existing_block_q.where(models.RoverBlocklist.mountpoint.is_(None))

        existing_block_rs = await auth_db.execute(existing_block_q)
        if existing_block_rs.scalars().first():
            continue

        new_block = models.RoverBlocklist(
            username=username,
            mountpoint=mountpoint,
            blocked_at=now,
            blocked_by=current_user.username,
            reason=req.reason or "Blocked by admin",
        )
        auth_db.add(new_block)
        blocks_created += 1

    if blocks_created > 0:
        await auth_db.commit()

    # 3. Gọi các caster TCP để xóa user (kick ngay lập tức trên tất cả server)
    hosts, env_cfg = _read_caster_config_from_env()
    target_hosts = hosts if hosts else [str(settings.CASTER_HOST or "localhost")]
    port = int(settings.CASTER_PORT or 2101)

    deleted_count_caster = 0
    for host in target_hosts:
        try:
            from app.caster_client import CasterClient
            caster = CasterClient(host, port)
            ok = await caster.block_user(
                identifier=current_user.username,
                username=username,
                reason=req.reason or "Blocked by admin",
            )
            if ok: deleted_count_caster += 1
            logger.info(f"[CASTER MGMT] Blocked user '{username}' on caster {host}")
        except Exception as e:
            logger.warning(f"[CASTER MGMT] Failed to block user on caster {host}: {e}")

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


@router.post("/api/caster/unblock-user")
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

    # Permission check if rover exists in DB
    rover = await crud.get_rover_by_username(auth_db, username)
    if not rover and current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=404, detail="Không tìm thấy Rover")
    if rover:
        has_perm = await crud.check_rover_management_permission(rover, current_user, devices_db, auth_db)
        if not has_perm:
            raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý Rover này.")

    username = req.username.strip()
    mountpoint = req.mountpoint.strip() if req.mountpoint else None

    # 1. Xóa khỏi blocklist
    del_q = delete(models.RoverBlocklist).where(
        models.RoverBlocklist.username == username,
    )
    if mountpoint:
        del_q = del_q.where(models.RoverBlocklist.mountpoint == mountpoint)
    else:
        # Xóa tất cả block entries cho user này
        pass

    result = await auth_db.execute(del_q)
    deleted_count = result.rowcount
    await auth_db.commit()

    caster_recreated = False
    rover = await crud.get_rover_by_username(auth_db, username)

    if rover:
        rover.is_active = True
        await auth_db.commit()

        # 3. Tạo lại user trên TẤT CẢ caster
        hosts, env_cfg = _read_caster_config_from_env()
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
            except Exception as e:
                logger.warning(f"[CASTER MGMT] Failed to unblock user on caster {host}: {e}")

        caster_recreated = (recreated_count > 0)
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
        logger.error("[NTRIP AUTH] Error checking auth for user=%r: %r", username, e)
        return PlainTextResponse("UNAVAILABLE", status_code=503)


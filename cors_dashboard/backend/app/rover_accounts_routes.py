"""Rover account creation and caster synchronization."""

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import re
import time

from . import auth, crud, models, schemas
from .database import get_auth_db, get_db, settings
from .station_config import _device_service_config
from .runtime_config import _read_caster_config_from_env
from .caster_helpers import validate_caster_hosts, saved_rover_duration

logger = logging.getLogger("app.main")
router = APIRouter()


@router.delete("/api/caster/users/{username}", responses={403: {"description": "Forbidden"}, 500: {"description": "Internal Server Error"}, 502: {"description": "Bad Gateway"}})
async def delete_caster_user_endpoint(
    username: str,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # 1. Kiểm tra quyền ADMIN
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(
            status_code=403,
            detail="Chỉ Administrator mới có quyền xóa tài khoản ngoại lai trực tiếp trên Caster."
        )

    # 2. Xóa trên toàn bộ Caster được cấu hình
    hosts, config = _read_caster_config_from_env()
    if not hosts:
        raise HTTPException(status_code=500, detail="Chưa cấu hình Caster Server trong hệ thống.")

    results_log = []
    errors = []
    port = config.get("port") or 2101

    for host in hosts:
        try:
            from app.caster_client import CasterClient
            caster = CasterClient(host, port)
            # Dùng 'admin' hoặc current_user.username làm identifier cho lệnh deluser
            await caster.delete_user(identifier='admin', username=username)
            results_log.append(host)
        except Exception as e:
            logger.exception("Failed to delete rover on caster (%s)", type(e).__name__)
            errors.append(f"{host}: {str(e)}")

    if errors and not results_log:
        raise HTTPException(status_code=502, detail=f"Lỗi Caster: {'; '.join(errors)}")

    # Track the event
    try:
        from .crud import log_system_event
        await crud.log_system_event(
            db=db,
            type="warning",
            message=f"Xóa tài khoản ngoại lai '{username}' khỏi Caster bởi {current_user.username}",
            user_id=current_user.id
        )
    except Exception:
        pass

    return {
        "status": "deleted",
        "username": username,
        "casters": results_log,
        "errors": errors
    }


@router.get("/api/rovers/all", response_model=list[schemas.RoverAccountResponse], responses={403: {"description": "Forbidden"}})
async def get_all_rovers_global(
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):

    # 1. Kiểm tra quyền cơ bản
    if not auth.has_permission(current_user, auth.Permission.VIEW_ROVERS):
        raise HTTPException(
            status_code=403,
            detail="Bạn chưa được cấp quyền quản lý Rover."
        )

    rovers = await crud.get_visible_rovers_for_user(db, devices_db, current_user)

    # 3. Làm giàu dữ liệu (XỬ LÝ HIỂN THỊ NHIỀU TRẠM)
    enriched_rovers = []

    # Cache devices để tránh query DB quá nhiều lần trong vòng lặp
    all_devices_result = await devices_db.execute(select(models.Device))
    all_devices = {d.serial.strip().upper(): d.name for d in all_devices_result.scalars().all() if d.serial}

    for rover in rovers:
        rover_dict = schemas.RoverAccountResponse.from_orm(rover).model_dump()

        # Xử lý base_serial có thể là "SN1,SN2"
        serials = (rover.base_serial or "").split(',')
        station_names = []

        for sn in serials:
            s_name = all_devices.get(sn.strip().upper())
            if s_name:
                station_names.append(s_name)
            else:
                station_names.append(sn) # Nếu không tìm thấy tên thì hiện serial

        # Nối tên các trạm lại: "Trạm A, Trạm B"
        rover_dict['base_name'] = ", ".join(station_names)

        enriched_rovers.append(rover_dict)

    return enriched_rovers


def _authorize_rover_sync(update_req, current_user):
    if not auth.can_edit_rovers(current_user):
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý Rover.")
    if update_req.auto_save_sessions is not None and current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được cấu hình lưu phiên Rover.")
    if update_req.auto_save_fixed_points is not None and current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được cấu hình tự lưu điểm Fixed.")


def _rover_sync_hosts(update_req, rover):
    configured_hosts, config = _read_caster_config_from_env()
    requested_hosts = [str(host).strip() for host in (update_req.target_casters or []) if str(host).strip()]
    if not requested_hosts:
        requested_hosts = (
            [host.strip() for host in (rover.target_casters or "").split(";") if host.strip()]
            if rover and rover.target_casters
            else [host.strip() for host in configured_hosts if host.strip()]
        )
    if not requested_hosts:
        fallback_host = str(config.get("host") or settings.CASTER_HOST or "").strip()
        requested_hosts = [fallback_host] if fallback_host else []
    try:
        target_hosts = validate_caster_hosts(requested_hosts)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    return target_hosts, config


async def _validate_selected_rover_serials(update_req, current_user, devices_db, db):
    if not update_req.selected_serials or current_user.role == auth.Role.ADMIN:
        return
    manageable_serials = await crud.get_rover_manageable_base_serials(devices_db, current_user, db)
    selected_serials = {
        str(serial).strip().upper()
        for serial in update_req.selected_serials
        if str(serial).strip()
    }
    if not selected_serials.issubset(manageable_serials):
        raise HTTPException(status_code=403, detail="Rover chỉ được gán vào trạm trong phạm vi của bạn.")


async def _validate_rover_mountpoint_scope(update_req, rover, current_user, devices_db):
    if current_user.role == auth.Role.ADMIN or update_req.mountpoints is None:
        return
    serials = update_req.selected_serials or ((rover.base_serial or "").split(",") if rover else [])
    within_scope = await crud.rover_mountpoints_within_scope(
        devices_db, current_user, serials, update_req.mountpoints
    )
    if not within_scope:
        raise HTTPException(status_code=403, detail="Mountpoint không thuộc trạm được cấp cho bạn.")


async def _validate_rover_management_permission(rover, current_user, devices_db, db):
    if not rover:
        return
    has_perm = await crud.check_rover_management_permission(rover, current_user, devices_db, db)
    if not has_perm:
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý Rover này.")


async def _validate_rover_sync_scope(update_req, rover, current_user, devices_db, db):
    if current_user.role == auth.Role.VIEWER and not rover and not update_req.selected_serials:
        raise HTTPException(status_code=403, detail="Viewer phải chọn ít nhất một trạm trong Rover Access.")
    await _validate_selected_rover_serials(update_req, current_user, devices_db, db)
    await _validate_rover_mountpoint_scope(update_req, rover, current_user, devices_db)
    await _validate_rover_management_permission(rover, current_user, devices_db, db)


async def _create_synced_rover(username, update_req, current_user, db):
    if not update_req.selected_serials and not update_req.mountpoints:
        raise HTTPException(status_code=400, detail="Vui lòng chọn trạm hoặc nhập Allowed Mountpoints để đồng bộ tài khoản này vào hệ thống.")
    if not update_req.password:
        raise HTTPException(status_code=400, detail="Password is required to import a caster account")
    combined_serials = ",".join(update_req.selected_serials) if update_req.selected_serials else ""
    rover = await crud.create_rover_account(
        db,
        username=username,
        password=update_req.password,
        base_serial=combined_serials,
        mountpoints=update_req.mountpoints,
        creator_id=current_user.id,
        max_connections=update_req.max_connections if update_req.max_connections is not None else 1,
        show_position=update_req.show_position if update_req.show_position is not None else True,
        rtcm_cycle_enabled=getattr(update_req, 'rtcm_cycle_enabled', False),
        rtcm_disconnect_interval_seconds=getattr(update_req, 'rtcm_disconnect_interval_seconds', 300),
        auto_save_sessions=update_req.auto_save_sessions if update_req.auto_save_sessions is not None else True,
        auto_save_fixed_points=update_req.auto_save_fixed_points if update_req.auto_save_fixed_points is not None else False,
        is_shared_with_managers=getattr(update_req, 'is_shared_with_managers', False)
    )
    logger.info("Adopted unknown rover into DB")
    return rover


async def _update_synced_rover(rover, update_req, db):
    update_data = {}
    if update_req.password:
        update_data["password"] = update_req.password
    if update_req.mountpoints:
        update_data["mountpoints"] = update_req.mountpoints
    if update_req.max_connections is not None:
        update_data["max_connections"] = update_req.max_connections
    if update_req.show_position is not None:
        update_data["show_position"] = update_req.show_position
    if update_req.rtcm_cycle_enabled is not None:
        update_data["rtcm_cycle_enabled"] = update_req.rtcm_cycle_enabled
    if update_req.rtcm_disconnect_interval_seconds is not None:
        update_data["rtcm_disconnect_interval_seconds"] = update_req.rtcm_disconnect_interval_seconds
    if update_req.auto_save_sessions is not None:
        update_data["auto_save_sessions"] = update_req.auto_save_sessions
    if update_req.auto_save_fixed_points is not None:
        update_data["auto_save_fixed_points"] = update_req.auto_save_fixed_points
    if update_data:
        if update_req.selected_serials:
            update_data["base_serial"] = ",".join(update_req.selected_serials)
        rover = await crud.update_rover_account(db, rover.id, update_data)
    return rover


async def _toggle_synced_rover_position(caster, username, update_req, current_user):
    if update_req.show_position is None:
        return
    toggle_ids = []
    for ident in (current_user.username, "admin", "id"):
        ident_s = str(ident or "").strip()
        if ident_s and ident_s not in toggle_ids:
            toggle_ids.append(ident_s)
    for ident in toggle_ids:
        if await caster.toggle_show_pos(ident, username, update_req.show_position):
            return
    logger.warning("Sync-by-username could not confirm show_position toggle")


async def _sync_rover_to_caster(host, port, username, update_req, rover, current_user):
    from app.caster_client import CasterClient

    caster = CasterClient(host, port)
    if update_req.expiry_days is not None:
        duration_seconds = update_req.expiry_days * 86400
    else:
        duration_seconds = await caster.get_user_remaining_seconds(username)
        if duration_seconds is None:
            duration_seconds = saved_rover_duration(rover)
    created_ok = await caster.create_user(
        creator=current_user.username,
        username=username,
        password=update_req.password or rover.password,
        mountpoints=update_req.mountpoints or rover.mountpoints,
        expiry_seconds=duration_seconds,
        max_users=update_req.max_connections if update_req.max_connections is not None else rover.max_connections,
        show_position=update_req.show_position if update_req.show_position is not None else rover.show_position,
        rtcm_cycle_enabled=bool(getattr(rover, "rtcm_cycle_enabled", False) if update_req.rtcm_cycle_enabled is None else update_req.rtcm_cycle_enabled),
        rtcm_disconnect_interval_seconds=int((getattr(rover, "rtcm_disconnect_interval_seconds", None) or 300) if update_req.rtcm_disconnect_interval_seconds is None else update_req.rtcm_disconnect_interval_seconds),
    )
    if not created_ok:
        raise RuntimeError("Caster did not confirm account restore/update")
    await _toggle_synced_rover_position(caster, username, update_req, current_user)


async def _sync_rover_casters(username, update_req, rover, current_user, target_hosts, config, db):
    try:
        port = int(config.get("port") or settings.CASTER_PORT or 2101)
        failed_hosts = []
        for host in target_hosts:
            try:
                await _sync_rover_to_caster(host, port, username, update_req, rover, current_user)
            except Exception as host_err:
                logger.exception("Failed to sync rover to caster (%s)", type(host_err).__name__)
                failed_hosts.append(host)
        if failed_hosts:
            raise HTTPException(status_code=502, detail=f"Caster chưa xác nhận lưu Rover: {', '.join(failed_hosts)}. Có thể thử lại.")
        if target_hosts:
            db_target_casters = ";".join(target_hosts)
            if db_target_casters != rover.target_casters:
                rover = await crud.update_rover_account(db, rover.id, {"target_casters": db_target_casters})
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Sync Caster Error (%s)", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Chưa thể đồng bộ Rover sang caster; hãy thử lại.") from None
    return rover


@router.put("/api/rovers/sync/by-username/{username}", response_model=schemas.RoverAccountResponse, responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 502: {"description": "Bad Gateway"}})
async def sync_rover_by_username(
    username: str,
    update_req: schemas.RoverUpdate,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    """Import or update a rover account and synchronize it with configured casters."""
    _authorize_rover_sync(update_req, current_user)
    rover = await crud.get_rover_by_username(db, username)
    target_hosts, config = _rover_sync_hosts(update_req, rover)
    await _validate_rover_sync_scope(update_req, rover, current_user, devices_db, db)
    if rover:
        rover = await _update_synced_rover(rover, update_req, db)
    else:
        rover = await _create_synced_rover(username, update_req, current_user, db)
    return await _sync_rover_casters(username, update_req, rover, current_user, target_hosts, config, db)


async def _get_coordinator_visible_serials(request, devices_db, current_user):
    visible_serials = await crud.get_coordinator_visible_base_serials(devices_db, current_user)
    for serial in request.selected_serials:
        if str(serial).strip().upper() not in visible_serials:
            raise HTTPException(
                status_code=403,
                detail=f"Bạn không có quyền quản lý trạm '{serial}'."
            )
    return visible_serials


async def _validate_viewer_multi_rover_scope(
    devices_db, db, current_user, selected_serials, target_mountpoints,
):
    manageable_serials = await crud.get_rover_manageable_base_serials(devices_db, current_user, db)
    selected_serials_upper = {
        str(serial).strip().upper() for serial in selected_serials if str(serial).strip()
    }
    if not selected_serials_upper.issubset(manageable_serials):
        raise HTTPException(status_code=403, detail="Rover chỉ được gán vào trạm trong phạm vi Rover Access của bạn.")
    if not await crud.rover_mountpoints_within_scope(
        devices_db, current_user, selected_serials, target_mountpoints
    ):
        raise HTTPException(status_code=403, detail="Mountpoint không thuộc trạm được cấp cho bạn.")


def _add_enabled_server_mountpoint(mountpoints, config, server_number):
    if config.get(f'server{server_number}_enabled'):
        mountpoint = config.get(f'mountpoint{server_number}')
        if mountpoint:
            mountpoints.add(mountpoint)


async def _coordinator_device_mountpoints(request, devices_db, visible_serials):
    mountpoints = set()
    for serial in request.selected_serials or []:
        device = await crud.get_device_by_serial(devices_db, serial)
        if not device or str(device.serial).strip().upper() not in visible_serials:
            continue
        config = _device_service_config(device)
        _add_enabled_server_mountpoint(mountpoints, config, 1)
        _add_enabled_server_mountpoint(mountpoints, config, 2)
    return mountpoints


def _coordinator_extra_mountpoints(current_user):
    if not current_user.extra_mountpoints:
        return set()
    return {
        mountpoint.strip()
        for mountpoint in re.split(r'[;,\s]+', current_user.extra_mountpoints)
        if mountpoint.strip()
    }


async def _collect_coordinator_mountpoints(request, devices_db, current_user, visible_serials):
    valid_mountpoints = await _coordinator_device_mountpoints(request, devices_db, visible_serials)
    valid_mountpoints.update(_coordinator_extra_mountpoints(current_user))
    return valid_mountpoints


async def _validate_coordinator_mountpoint_scope(request, devices_db, current_user, visible_serials):
    valid_mountpoints = await _collect_coordinator_mountpoints(
        request, devices_db, current_user, visible_serials
    )
    requested = [
        mountpoint.strip()
        for mountpoint in re.split(r'[;,\s]+', request.mountpoint_override)
        if mountpoint.strip()
    ]
    if not requested:
        raise HTTPException(status_code=400, detail="Mountpoint không được để trống.")
    for mountpoint in requested:
        if mountpoint not in valid_mountpoints:
            raise HTTPException(
                status_code=403,
                detail=f"Mountpoint '{mountpoint}' không thuộc quyền quản lý của bạn."
            )


async def _validate_multi_rover_scope(
    request, db, devices_db, current_user, is_admin, selected_serials, target_mountpoints,
):
    if current_user.role == auth.Role.COORDINATOR:
        visible_serials = await _get_coordinator_visible_serials(request, devices_db, current_user)
        await _validate_coordinator_mountpoint_scope(
            request, devices_db, current_user, visible_serials
        )
    if current_user.role == auth.Role.VIEWER:
        await _validate_viewer_multi_rover_scope(
            devices_db, db, current_user, selected_serials, target_mountpoints
        )
    if not selected_serials and not is_admin:
        if current_user.role != auth.Role.COORDINATOR or not target_mountpoints:
            raise HTTPException(status_code=400, detail="Phải chọn ít nhất 1 trạm hoặc Mountpoint bổ sung.")
    return ",".join(selected_serials) if selected_serials else ""


def _multi_rover_target_hosts(request):
    target_hosts = request.target_casters or []
    if not target_hosts:
        env_hosts, env_config = _read_caster_config_from_env()
        if env_hosts:
            target_hosts = [str(host).strip() for host in env_hosts if host.strip()]
        else:
            default_host = str(env_config.get("host") or settings.CASTER_HOST or "")
            if default_host:
                target_hosts = [default_host]
    if not target_hosts:
        raise ValueError("No target caster configured.")
    return validate_caster_hosts(target_hosts)


async def _create_multi_rover_on_caster(host, port, request, creator, duration_seconds, target_mountpoints):
    from app.caster_client import CasterClient

    caster = CasterClient(host, port)
    await caster.create_user(
        creator=creator,
        username=request.username,
        password=request.password,
        mountpoints=target_mountpoints,
        expiry_seconds=duration_seconds,
        max_users=request.max_connections,
        show_position=request.show_position,
        rtcm_cycle_enabled=bool(getattr(request, "rtcm_cycle_enabled", False)),
        rtcm_disconnect_interval_seconds=int(getattr(request, "rtcm_disconnect_interval_seconds", 300) or 300),
        rtcm_disconnect_duration_seconds=int(getattr(request, "rtcm_disconnect_duration_seconds", 10) or 10),
    )


async def _create_multi_rover_on_casters(request, current_user, duration_seconds, target_mountpoints):
    try:
        creator_identifier = current_user.username
        target_hosts = _multi_rover_target_hosts(request)
        results_log = []
        errors = []
        port = int(settings.CASTER_PORT or 2101)
        for host in target_hosts:
            try:
                await _create_multi_rover_on_caster(
                    host, port, request, creator_identifier, duration_seconds, target_mountpoints
                )
                results_log.append(host)
            except Exception as error:
                logger.exception(f"Failed to create user on caster {host}: {error}")
                errors.append(f"{host}: {str(error)}")

        if errors and not results_log:
            raise Exception("; ".join(errors))
        logger.info(f"✓ Created Multi-Rover '{request.username}' on: {', '.join(results_log)}")
        return results_log
    except Exception as error:
        logger.exception(f"Caster Multi-Create error: {error}")
        raise HTTPException(status_code=502, detail=f"Lỗi Caster: {str(error)}")


@router.post("/api/rovers/create-multi", response_model=schemas.RoverAccountResponse, responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 502: {"description": "Bad Gateway"}})
async def create_multi_base_rover(
    request: schemas.RoverMultiCreate,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db), # Add devices_db here
    current_user: models.User = Depends(auth.get_current_user)
):
    is_admin = current_user.role == auth.Role.ADMIN
    if not auth.can_edit_rovers(current_user):
        raise HTTPException(status_code=403, detail="Bạn không có quyền tạo tài khoản Rover.")

    existing = await crud.get_rover_by_username(db, request.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username đã tồn tại.")

    duration_seconds = request.expiry_days * 86400
    target_mountpoints = request.mountpoint_override.strip()
    selected_serials = list(request.selected_serials or [])
    combined_serials = await _validate_multi_rover_scope(
        request, db, devices_db, current_user, is_admin, selected_serials, target_mountpoints
    )
    results_log = await _create_multi_rover_on_casters(
        request, current_user, duration_seconds, target_mountpoints
    )

    new_rover = await crud.create_rover_account(
        db,
        username=request.username,
        password=request.password,
        base_serial=combined_serials,
        mountpoints=target_mountpoints,
        creator_id=current_user.id,
        max_connections=request.max_connections,
        show_position=request.show_position,
        target_casters=";".join(results_log),
        rtcm_cycle_enabled=getattr(request, 'rtcm_cycle_enabled', False),
        rtcm_disconnect_interval_seconds=getattr(request, 'rtcm_disconnect_interval_seconds', 300),
        rtcm_disconnect_duration_seconds=getattr(request, 'rtcm_disconnect_duration_seconds', 10),
        expires_at=int(time.time()) + duration_seconds,
        is_shared_with_managers=getattr(request, 'is_shared_with_managers', False),
        auto_save_sessions=getattr(request, 'auto_save_sessions', True) if is_admin else True,
        auto_save_fixed_points=getattr(request, 'auto_save_fixed_points', False) if is_admin else False
    )
    return new_rover


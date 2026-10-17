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
from .caster_helpers import validate_caster_hosts

logger = logging.getLogger("app.main")
router = APIRouter()


@router.delete("/api/caster/users/{username}")
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
            logger.error(f"Failed to delete user '{username}' on caster {host}: {e}")
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


@router.get("/api/rovers/all", response_model=list[schemas.RoverAccountResponse])
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

    # 3. LÃ m giÃ u dữ liệu (XỬ LÝ HIỂN THỊ NHIỀU TRẠM)
    enriched_rovers = []

    # Cache devices để tránh query DB quá nhiều lần trong vòng lặp
    all_devices_result = await devices_db.execute(select(models.Device))
    all_devices = {d.serial.strip().upper(): d.name for d in all_devices_result.scalars().all() if d.serial}

    for rover in rovers:
        rover_dict = schemas.RoverAccountResponse.from_orm(rover).model_dump()

        # Xử lý base_serial có thể lÃ  "SN1,SN2"
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


@router.put("/api/rovers/sync/by-username/{username}", response_model=schemas.RoverAccountResponse)
async def sync_rover_by_username(
    username: str,
    update_req: schemas.RoverUpdate,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    """
    Thông minh: Đồng bộ rover bằng username. 
    Nếu rover chưa có trong DB, nó sẽ tự động tạo mới (Link/Adopt).
    Nếu đã có, nó cập nhật thông tin.
    """
    # 1. Kiểm tra Admin/Creator
    if not auth.can_edit_rovers(current_user):
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý Rover.")
    if update_req.auto_save_sessions is not None and current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được cấu hình lưu phiên Rover.")
    if update_req.auto_save_fixed_points is not None and current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được cấu hình tự lưu điểm Fixed.")

    # 2. Tìm trong DB
    rover = await crud.get_rover_by_username(db, username)
    configured_hosts, config = _read_caster_config_from_env()
    requested_hosts = [str(h).strip() for h in (update_req.target_casters or []) if str(h).strip()]
    if not requested_hosts:
        requested_hosts = (
            [h.strip() for h in (rover.target_casters or "").split(";") if h.strip()]
            if rover and rover.target_casters
            else [h.strip() for h in configured_hosts if h.strip()]
        )
    if not requested_hosts:
        fallback_host = str(config.get("host") or settings.CASTER_HOST or "").strip()
        requested_hosts = [fallback_host] if fallback_host else []
    try:
        target_hosts = validate_caster_hosts(requested_hosts)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    if current_user.role == auth.Role.VIEWER and not rover and not update_req.selected_serials:
        raise HTTPException(status_code=403, detail="Viewer phải chọn ít nhất một trạm trong Rover Access.")
    if update_req.selected_serials and current_user.role != auth.Role.ADMIN:
        manageable_serials = await crud.get_rover_manageable_base_serials(devices_db, current_user, db)
        selected_serials = {
            str(serial).strip().upper()
            for serial in update_req.selected_serials
            if str(serial).strip()
        }
        if not selected_serials.issubset(manageable_serials):
            raise HTTPException(status_code=403, detail="Rover chỉ được gán vào trạm trong phạm vi của bạn.")
    if current_user.role != auth.Role.ADMIN and update_req.mountpoints is not None:
        serials_for_mps = update_req.selected_serials or ((rover.base_serial or "").split(",") if rover else [])
        if not await crud.rover_mountpoints_within_scope(devices_db, current_user, serials_for_mps, update_req.mountpoints):
            raise HTTPException(status_code=403, detail="Mountpoint không thuộc trạm được cấp cho bạn.")

    if rover:
        has_perm = await crud.check_rover_management_permission(rover, current_user, devices_db, db)
        if not has_perm:
            raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý Rover này.")

    # 3. TRƯỜNG HỢP: USER CHƯA CÓ TRONG DB (Unknown)
    if not rover:
        if not update_req.selected_serials and not update_req.mountpoints:
            raise HTTPException(status_code=400, detail="Vui lòng chọn trạm hoặc nhập Allowed Mountpoints để đồng bộ tài khoản này vào hệ thống.")
        if not update_req.password:
            raise HTTPException(status_code=400, detail="Password is required to import a caster account")

        # Tạo mới trong DB
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
        logger.info(f"âœ“ Adopted unknown rover '{username}' into DB")

    # 4. TRƯỜNG HỢP: USER ĐÃ CÓ - CẬP NHẬT
    else:
        # Cập nhật DB
        update_data = {}
        if update_req.password: update_data["password"] = update_req.password
        if update_req.mountpoints: update_data["mountpoints"] = update_req.mountpoints
        if update_req.max_connections is not None: update_data["max_connections"] = update_req.max_connections
        if update_req.show_position is not None: update_data["show_position"] = update_req.show_position
        if update_req.rtcm_cycle_enabled is not None: update_data["rtcm_cycle_enabled"] = update_req.rtcm_cycle_enabled
        if update_req.rtcm_disconnect_interval_seconds is not None: update_data["rtcm_disconnect_interval_seconds"] = update_req.rtcm_disconnect_interval_seconds
        if update_req.auto_save_sessions is not None: update_data["auto_save_sessions"] = update_req.auto_save_sessions
        if update_req.auto_save_fixed_points is not None: update_data["auto_save_fixed_points"] = update_req.auto_save_fixed_points

        if update_data:
            # 🔥 FIX: Cập nhật base_serial nếu có gửi trạm đích
            if update_req.selected_serials:
                update_data["base_serial"] = ",".join(update_req.selected_serials)

            rover = await crud.update_rover_account(db, rover.id, update_data)

    # 5. ĐỒNG BỘ SANG CASTER (Đồng bộ trên toàn bộ Caster được cấu hình)
    try:
        port = int(config.get("port") or settings.CASTER_PORT or 2101)
        for host in target_hosts:
            try:
                from app.caster_client import CasterClient
                caster = CasterClient(host, port)
                if update_req.expiry_days is not None:
                    duration_seconds = update_req.expiry_days * 86400
                else:
                    duration_seconds = await caster.get_user_remaining_seconds(username)
                    if duration_seconds is None:
                        if host in (rover.target_casters or "").split(";"):
                            raise ValueError(f"Không đọc được thời hạn Rover '{username}' trên Caster {host}")
                        duration_seconds = max(0, int(rover.expires_at - time.time())) if rover.expires_at else 315360000
                created_ok = await caster.create_user(
                    creator=current_user.username,
                    username=username,
                    password=update_req.password or rover.password,
                    mountpoints=update_req.mountpoints or rover.mountpoints,
                    expiry_seconds=duration_seconds,
                    max_users=update_req.max_connections if update_req.max_connections is not None else rover.max_connections,
                    show_position=update_req.show_position if update_req.show_position is not None else rover.show_position,
                    rtcm_cycle_enabled=bool(getattr(rover, "rtcm_cycle_enabled", False) if update_req.rtcm_cycle_enabled is None else update_req.rtcm_cycle_enabled),
                    rtcm_disconnect_interval_seconds=int(getattr(rover, "rtcm_disconnect_interval_seconds", 300) if update_req.rtcm_disconnect_interval_seconds is None else update_req.rtcm_disconnect_interval_seconds),
                )
                if not created_ok:
                    logger.warning(f"Sync-by-username did not get caster confirmation for '{username}' on host {host}")

                # Sync show_position again specifically to be safe
                if update_req.show_position is not None:
                    toggle_id_candidates = []
                    for ident in (current_user.username, "admin", "id"):
                        ident_s = str(ident or "").strip()
                        if ident_s and ident_s not in toggle_id_candidates:
                            toggle_id_candidates.append(ident_s)

                    toggle_ok = False
                    for ident in toggle_id_candidates:
                        if await caster.toggle_show_pos(ident, username, update_req.show_position):
                            toggle_ok = True
                            break
                    if not toggle_ok:
                        logger.warning(f"Sync-by-username could not confirm show_position toggle for '{username}' on host {host}")
            except Exception as host_err:
                logger.error(f"Failed to sync '{username}' to caster host {host}: {host_err}")

        # Cập nhật target_casters trong DB
        if target_hosts:
            db_target_casters = ";".join(target_hosts)
            if db_target_casters != rover.target_casters:
                rover = await crud.update_rover_account(db, rover.id, {"target_casters": db_target_casters})
    except Exception as e:
        logger.error(f"Sync Caster Error for '{username}': {e}")

    return rover


@router.post("/api/rovers/create-multi", response_model=schemas.RoverAccountResponse)
async def create_multi_base_rover(
    request: schemas.RoverMultiCreate,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db), # Add devices_db here
    current_user: models.User = Depends(auth.get_current_user)
):
    # 1. Kiểm tra quyền (như cũ)
    is_admin = current_user.role == auth.Role.ADMIN
    if not auth.can_edit_rovers(current_user):
        raise HTTPException(status_code=403, detail="Bạn không có quyền tạo tÃ i khoản Rover.")

    # 2. Kiểm tra trùng (như cũ)
    existing = await crud.get_rover_by_username(db, request.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username đã tồn tại.")

    # 2.1 Coordinator may create Rover for editable or view-only stations.
    if current_user.role == auth.Role.COORDINATOR:
        coord_visible_serials = await crud.get_coordinator_visible_base_serials(devices_db, current_user)

        # Every requested station must be in the coordinator's visible scope.
        for serial in request.selected_serials:
            if str(serial).strip().upper() not in coord_visible_serials:
                raise HTTPException(
                    status_code=403,
                    detail=f"Bạn không có quyền quản lý trạm '{serial}'."
                )


    # 3. Chuẩn bị dữ liệu
    duration_seconds = request.expiry_days * 86400
    target_mountpoints = request.mountpoint_override.strip()

    selected_serials = list(request.selected_serials or [])

    if current_user.role == auth.Role.VIEWER:
        manageable_serials = await crud.get_rover_manageable_base_serials(devices_db, current_user, db)
        selected_serials_upper = {
            str(serial).strip().upper()
            for serial in selected_serials
            if str(serial).strip()
        }
        if not selected_serials_upper.issubset(manageable_serials):
            raise HTTPException(status_code=403, detail="Rover chỉ được gán vào trạm trong phạm vi Rover Access của bạn.")
        if not await crud.rover_mountpoints_within_scope(devices_db, current_user, selected_serials, target_mountpoints):
            raise HTTPException(status_code=403, detail="Mountpoint không thuộc trạm được cấp cho bạn.")

    # 3. Xác định danh sÃ¡ch Mountpoint hợp lệ (Đặc biệt cho Coordinator)
    if current_user.role == auth.Role.COORDINATOR:
        valid_mps = set()

        # 1. MP từ cÃ¡c trạm vật lÃ½ được chọn
        if request.selected_serials:
            for serial in request.selected_serials:
                dev = await crud.get_device_by_serial(devices_db, serial)
                if dev and str(dev.serial).strip().upper() in coord_visible_serials:
                    sc = _device_service_config(dev)
                    if sc.get('server1_enabled') and sc.get('mountpoint1'):
                        valid_mps.add(sc.get('mountpoint1'))
                    if sc.get('server2_enabled') and sc.get('mountpoint2'):
                        valid_mps.add(sc.get('mountpoint2'))

        # 2. MP bổ sung được gÃ¡n rÃªng
        if current_user.extra_mountpoints:
            extras = [mp.strip() for mp in re.split(r'[;,\s\n]+', current_user.extra_mountpoints) if mp.strip()]
            valid_mps.update(extras)

        # 3. Kiểm tra Mountpoint yÃªu cầu
        requested = [mp.strip() for mp in re.split(r'[;,\s\n]+', request.mountpoint_override) if mp.strip()]
        if not requested:
             raise HTTPException(status_code=400, detail="Mountpoint không được để trống.")

        for mp in requested:
            if mp not in valid_mps:
                raise HTTPException(status_code=403, detail=f"Mountpoint '{mp}' không thuộc quyền quản lý của bạn.")

    # 4. Nới lỏng yêu cầu chọn trạm:
    # Nếu là Coordinator vÃ  đã có mountpoint_override (hợp lệ) thì khÃ´ng bắt buộc chọn trạm vật lÃ½.
    if not selected_serials and not is_admin:
        if current_user.role != auth.Role.COORDINATOR or not target_mountpoints:
            raise HTTPException(status_code=400, detail="Phải chọn ít nhất 1 trạm hoặc Mountpoint bổ sung.")

    combined_serials = ",".join(selected_serials) if selected_serials else ""

    # 4. GỌI CASTER (Tạo user thật)
    try:
        creator_identifier = current_user.username

        # Determine which casters to send to
        target_hosts = request.target_casters or []
        if not target_hosts:
            # Fallback to all configured hosts to ensure complete sync
            env_hosts, env_cfg = _read_caster_config_from_env()
            if env_hosts:
                target_hosts = [str(h).strip() for h in env_hosts if h.strip()]
            else:
                default_host = str(env_cfg.get("host") or settings.CASTER_HOST or "")
                if default_host:
                    target_hosts = [default_host]

        if not target_hosts:
            raise ValueError("No target caster configured.")
        target_hosts = validate_caster_hosts(target_hosts)

        # Create user on each target caster
        results_log = []
        errors = []
        port = int(settings.CASTER_PORT or 2101)

        for host in target_hosts:
            try:
                from app.caster_client import CasterClient
                caster = CasterClient(host, port)
                await caster.create_user(
                    creator=creator_identifier,
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
                results_log.append(host)
            except Exception as e:
                logger.error(f"Failed to create user on caster {host}: {e}")
                errors.append(f"{host}: {str(e)}")

        if errors and not results_log:
            # Failed on all targets
            raise Exception("; ".join(errors))

        logger.info(f"✓ Created Multi-Rover '{request.username}' on: {', '.join(results_log)}")

    except Exception as e:
        logger.error(f"Caster Multi-Create error: {e}")
        raise HTTPException(status_code=502, detail=f"Lỗi Caster: {str(e)}")

    # 5. LƯU VÀO DB (Ghi rõ mountpoint)
    new_rover = await crud.create_rover_account(
        db,
        username=request.username,
        password=request.password,
        base_serial=combined_serials,   # Lưu: "SN1,SN2"
        mountpoints=target_mountpoints, # Lưu: "MP1;MP2" (Cột mới)
        creator_id=current_user.id,
        max_connections=request.max_connections,
        show_position=request.show_position,
        target_casters=";".join(results_log), # Lưu chính xác những server đã tạo thành công
        rtcm_cycle_enabled=getattr(request, 'rtcm_cycle_enabled', False),
        rtcm_disconnect_interval_seconds=getattr(request, 'rtcm_disconnect_interval_seconds', 300),
        rtcm_disconnect_duration_seconds=getattr(request, 'rtcm_disconnect_duration_seconds', 10),
        expires_at=int(time.time()) + duration_seconds,
        is_shared_with_managers=getattr(request, 'is_shared_with_managers', False),
        auto_save_sessions=getattr(request, 'auto_save_sessions', True) if is_admin else True,
        auto_save_fixed_points=getattr(request, 'auto_save_fixed_points', False) if is_admin else False
    )

    return new_rover


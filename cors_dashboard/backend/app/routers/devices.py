from ..redis_client import redis_client
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from sqlalchemy.exc import IntegrityError
from ..database import get_auth_db, get_db
from .. import crud, models, schemas, auth, license_manager, command_builder
from .. import mqtt as mqtt_handler
from ..command_dispatcher import send_command_to_pi
from ..caster_helpers import _read_caster_config_from_env, get_caster_client, global_caster_cache_lock
from ..caster_client import CasterClient
from ..database import settings
from ..websocket import manager as ui_manager
import base64
import logging
import os
import re

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Devices"])


def _normalize_auto_cors_config(payload: dict) -> dict:
    raw_config = payload.get("auto_cors") if isinstance(payload, dict) else None
    if not isinstance(raw_config, dict):
        raise ValueError("AUTO_CORS requires the CORS connection fields")

    host = str(raw_config.get("ip") or "").strip()
    user = str(raw_config.get("user") or "").strip()
    password = str(raw_config.get("password") or "")
    mountpoint = str(raw_config.get("mountpoint") or "").strip()
    if not host or not user or not password or not mountpoint:
        raise ValueError("CORS IP, port, username, password, and mountpoint are required")
    if any(char in "\r\n" for char in f"{host}{user}{password}{mountpoint}"):
        raise ValueError("CORS connection fields contain invalid line breaks")

    try:
        port = int(raw_config.get("port"))
    except (TypeError, ValueError):
        raise ValueError("CORS port must be a valid number") from None
    if not 1 <= port <= 65535:
        raise ValueError("CORS port must be between 1 and 65535")

    return {
        "ip": host,
        "port": port,
        "user": user,
        "password": password,
        "mountpoint": mountpoint,
    }


def _with_runtime_sync_flags(devices: list[models.Device]) -> list[models.Device]:
    expected = str(settings.APP_VERSION or "").strip().upper()
    for device in devices:
        agent_version = str(getattr(device, "version", "") or "").strip().upper()
        sync_errors = []
        if expected and agent_version != expected:
            device.is_synced = False
            sync_errors.append(f"Agent version {getattr(device, 'version', None) or '??'} != server {settings.APP_VERSION}")
        setattr(device, "sync_errors", sync_errors)
    return devices


# === DEVICE ENDPOINTS ===
@router.get("/api/devices", response_model=list[schemas.Device])
async def get_initial_devices(
    user_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    auth_db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)) 
):
    target_user_id = current_user.id
    target_role = current_user.role

    if user_id is not None:
        user_info = await crud.get_user_by_id(auth_db, user_id)
        is_child = user_info and getattr(user_info, 'parent_id', None) == current_user.id
        
        if current_user.role != auth.Role.ADMIN and user_id != current_user.id and not (current_user.role == auth.Role.COORDINATOR and is_child):
            logging.warning(
                f"IDOR ATTEMPT: User '{current_user.username}' (role={current_user.role}) "
                f"tried to filter by user_id={user_id}"
            )
            raise HTTPException(
                status_code=403, 
                detail="Only admins can filter devices by user_id"
            )
        target_user_id = user_id
        if user_info:
            target_role = user_info.role

    if target_role == auth.Role.COORDINATOR:
        coordinator = current_user if current_user.id == target_user_id else await crud.get_user_by_id(auth_db, target_user_id)
        if not coordinator:
            return []
        visible_serials = await crud.get_coordinator_visible_base_serials(db, coordinator)
        result = await db.execute(select(models.Device).where(func.upper(models.Device.serial).in_(visible_serials)).order_by(models.Device.name))
        devices = result.scalars().all()
        logger.info(f"[DEVICES] {current_user.username} (role={target_role}) → {len(devices)} devices by user_id={target_user_id}")
        return _with_runtime_sync_flags(devices)
    elif target_role == auth.Role.VIEWER:
        from sqlalchemy import select as _select
        from sqlalchemy import func as _func
        from .. import models as _models
        user_info = current_user if current_user.id == target_user_id else await crud.get_user_by_id(auth_db, target_user_id)
        if not user_info:
            return []
        viewer_serials = await crud.get_viewer_visible_base_serials(auth_db, db, user_info)
        if not viewer_serials:
            return []
        
        result = await db.execute(_select(_models.Device).where(_func.upper(_models.Device.serial).in_(viewer_serials)).order_by(_models.Device.name))
        devices = result.scalars().all()
        logger.info(f"[DEVICES] Viewer '{current_user.username}' → returning {len(devices)} devices")
        return _with_runtime_sync_flags(devices)
    
    # Fallback: Admin sees all
    logger.info(f"[DEVICES] {current_user.username} (role={target_role}) → returning ALL devices")
    return _with_runtime_sync_flags(await crud.get_all_devices(db))

@router.post("/api/devices/{serial}/command")
async def send_generic_command(
    serial: str, 
    command: schemas.Command, 
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)):
    if current_user.role == auth.Role.COORDINATOR:
        raise HTTPException(status_code=403, detail="Coordinators can only send specific commands.")
    if not auth.has_permission(current_user, auth.Permission.EDIT_CHIP_CONFIG):
         raise HTTPException(status_code=403, detail="Permission denied.")
    
    # 1. Lấy thiết bị
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if current_user.role == auth.Role.COORDINATOR and device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Trạm này chỉ được xem")
    await crud.log_system_event(
        db=db,
        type="info",
        message=f"Command '{command.command}' sent to '{device.name}' by {current_user.username}",
        user_id=current_user.id,
        serial=serial
    )
    return await send_command_to_pi(serial, command.model_dump())


@router.post("/api/devices/{serial}/reference-check")
async def start_base_reference_check(
    serial: str,
    request: schemas.BaseReferenceCheckRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_COORDINATES)),
):
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if current_user.role == auth.Role.COORDINATOR and device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Trạm này chỉ được xem")
    command = {
        "command": "TRIGGER_BASE_REFERENCE_CHECK",
        "payload": {
            "ip": request.ntrip_host,
            "port": request.ntrip_port,
            "user": request.ntrip_username,
            "password": request.ntrip_password,
            "mountpoint": request.mountpoint,
            "timeout": request.timeout,
            "samples": request.samples,
            "sensor_type": "Ublox",
            "reference_coords": {
                "lat": request.reference_lat,
                "lon": request.reference_lon,
                "alt": request.reference_alt,
            },
        },
    }
    await crud.log_system_event(
        db=db,
        type="info",
        message=f"Base reference check requested for '{device.name}' via {request.mountpoint}",
        user_id=current_user.id,
        serial=serial,
    )
    return await send_command_to_pi(serial, command)

@router.post("/api/devices/{serial}/cors-mountpoints")
async def get_device_cors_mountpoints(
    serial: str,
    request: schemas.CasterMountpointLookupRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_COORDINATES)),
):
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    host = request.host.strip()
    username = request.username.strip()
    if any(char in "\r\n/" for char in host):
        raise HTTPException(status_code=400, detail="CORS host must be a hostname or IP address")
    if not host or not username or not request.password:
        raise HTTPException(status_code=400, detail="CORS host, username, and password are required")

    try:
        caster = CasterClient(
            host,
            request.port,
            admin_user=username,
            admin_password=request.password,
        )
        sources = await caster.get_sources()
    except Exception as exc:
        logger.warning("CORS sourcetable lookup failed for %s:%s: %s", host, request.port, exc)
        raise HTTPException(status_code=502, detail="Không thể kết nối hoặc đọc sourcetable từ CORS") from exc

    if sources is None:
        raise HTTPException(status_code=502, detail="CORS không trả về sourcetable hợp lệ")

    # Do not send source-table passwords or other caster-side secrets to the browser.
    safe_sources = []
    seen_mountpoints = set()
    for source in sources[:500]:
        mountpoint = str(source.get("mountpoint") or "").strip()
        if not mountpoint or mountpoint in seen_mountpoints:
            continue
        seen_mountpoints.add(mountpoint)
        safe_sources.append({
            "identifier": str(source.get("identifier") or "").strip(),
            "mountpoint": mountpoint,
            "latitude": source.get("latitude"),
            "longitude": source.get("longitude"),
            "auth_required": bool(source.get("auth_required", True)),
            "gga_required": bool(source.get("gga_required", False)),
        })

    return {"host": host, "port": request.port, "sources": safe_sources}


@router.post("/api/devices/{serial}/configure-chip")
async def configure_chip_endpoint(
    serial: str,
    config_request: schemas.Command,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_COORDINATES))
):
    device_in_db = await crud.get_device_by_serial(db, serial)
    if not device_in_db:
        raise HTTPException(status_code=404, detail="Device not found in database")
    if current_user.role == auth.Role.COORDINATOR and device_in_db.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Trạm này chỉ được xem")

    payload = config_request.payload or {}
    sensor_type = payload.get("sensor_type")
    params = payload.get("params", {})
    mode = payload.get("mode")
    method = params.get("base_setup_method") if isinstance(params, dict) else None
    gnss_options = params.get("gnss_options") if isinstance(params, dict) else None

    if not sensor_type or not mode:
        raise HTTPException(status_code=400, detail="Missing required fields: sensor_type or mode")

    auto_cors_config = None
    if mode == "BASE" and method == "AUTO_CORS":
        try:
            auto_cors_config = _normalize_auto_cors_config(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        device_in_db.detected_chip_type = sensor_type
        if isinstance(params, dict) and params:
            normalized_params = crud.normalize_base_config(params)
            if normalized_params:
                device_in_db.base_config = normalized_params
            else:
                current_base_config = device_in_db.base_config if isinstance(device_in_db.base_config, dict) else {}
                merged_base_config = dict(current_base_config)
                for key, value in params.items():
                    if key != "coords":
                        merged_base_config[key] = value
                if merged_base_config:
                    device_in_db.base_config = merged_base_config
        device_in_db.is_synced = False

        await db.commit()
        await db.refresh(device_in_db)
        mqtt_handler.invalidate_device_cache(serial)

        await ui_manager.broadcast({
            "type": "status_update",
            "data": schemas.Device.from_orm(device_in_db).model_dump()
        })
    except Exception as e:
        await db.rollback()
        logger.error("Failed to save chip config for %s: %s", serial, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save configuration to database: {str(e)}"
        )

    try:
        commands_to_send = []
        pi_command = None

        if mode == "BASE":
            if method == "AUTO_CORS":
                pi_command = {
                    "command": "TRIGGER_AUTO_BASE",
                    "payload": {
                        **auto_cors_config,
                        "timeout": 3600,
                        "samples": 60,
                        "sensor_type": sensor_type,
                    },
                    "original_config": {
                        "sensor_type": sensor_type,
                        "mode": mode,
                        "params": params,
                    },
                }
            elif method == "SURVEY_IN":
                survey_duration = params.get("survey_in_duration")
                survey_accuracy = params.get("survey_in_accuracy")
                if not survey_duration or not survey_accuracy:
                    raise ValueError("Missing survey_in parameters")

                commands_to_send = command_builder.build_base_survey_in_command(
                    sensor_type, survey_duration, survey_accuracy, gnss_options
                )
            elif method == "FIXED_LLA":
                coords = params.get("coords")
                if not coords or not all(k in coords for k in ["lat", "lon", "alt"]):
                    raise ValueError("Missing or incomplete coordinates")

                accuracy = params.get("accuracy", 10.0)
                commands_to_send = command_builder.build_base_fixed_lla_command(
                    sensor_type, coords["lat"], coords["lon"], coords["alt"], accuracy, gnss_options
                )
            else:
                raise ValueError(f"Unknown base setup method: {method}")
        else:
            raise ValueError(f"Unsupported mode: {mode}")

        if not pi_command:
            encoded_commands = [base64.b64encode(cmd).decode("ascii") for cmd in commands_to_send if cmd]
            if not encoded_commands:
                raise ValueError("No commands generated")
            pi_command = {
                "command": "EXECUTE_RAW_COMMANDS",
                "payload": {"commands_b64": encoded_commands},
                "original_config": config_request.payload,
            }
    except Exception as e:
        logger.error("Failed to build chip config commands for %s: %s", serial, e)
        raise HTTPException(
            status_code=400,
            detail=f"Failed to build configuration commands: {str(e)}"
        )

    try:
        response = await send_command_to_pi(serial, pi_command)
        await crud.log_system_event(
            db=db,
            type="info",
            message=f"GNSS config updated for '{device_in_db.name}' by {current_user.username}",
            user_id=current_user.id,
            serial=serial
        )
        return {
            "status": "command_sent_waiting_sync",
            "channel": response["channel"],
            "commands_sent": len(commands_to_send) if not pi_command else 1,
            "message": "Configuration saved to database and sent to device. Waiting for device to apply and confirm sync status...",
            "is_synced": False,
        }
    except HTTPException as http_exc:
        logger.warning("Failed to send chip config command to %s: %s", serial, http_exc.detail)
        raise HTTPException(
            status_code=503,
            detail=f"Configuration saved to database successfully, but device is offline ({http_exc.detail}). Config will auto-sync when device comes back online."
        )
    except Exception as e:
        logger.error("Unexpected error sending chip config command to %s: %s", serial, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error while sending command: {str(e)}")

# --- CÁC ENDPOINT CHO QUẢN LÝ ROVER ---
@router.get("/api/bases/{serial}/rovers", response_model=list[schemas.RoverAccountResponse])
async def get_rovers_for_base(
    serial: str,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db), # Thêm vào để kiểm tra quyền
    current_user: models.User = Depends(auth.get_current_user)
):
    # Admin có thể xem Rover của bất kỳ trạm nào
    if current_user.role == auth.Role.ADMIN:
        return await crud.get_rovers_by_base_serial(db, base_serial=serial)

    # Coordinator chỉ có thể xem Rover của các trạm họ quản lý
    if current_user.role == auth.Role.COORDINATOR:
        if not auth.can_view_rover_page(current_user):
            raise HTTPException(status_code=403, detail="Bạn chưa được cấp quyền xem trang Rover.")
        device = await crud.get_device_by_serial(devices_db, serial)
        if device and (device.user_id == current_user.id or str(serial).strip().upper() in crud.get_coordinator_view_base_serials(current_user)):
            return await crud.get_rovers_by_base_serial(db, base_serial=serial)

    if current_user.role == auth.Role.VIEWER:
        if not auth.can_view_rover_page(current_user):
            raise HTTPException(status_code=403, detail="Bạn không có quyền xem trang Rover.")
        if str(serial).strip().upper() not in await crud.get_viewer_visible_base_serials(db, devices_db, current_user):
            raise HTTPException(status_code=403, detail="Bạn không có quyền xem Rover của trạm này.")
        allowed_rovers = crud.get_allowed_rover_usernames(current_user)
        rovers = await crud.get_rovers_by_base_serial(db, base_serial=serial)
        return [
            rover for rover in rovers
            if rover.created_by_user_id == current_user.id
            or str(rover.username or "").strip().lower() in allowed_rovers
        ]
    
    # Nếu không phải Admin hoặc không phải chủ sở hữu, từ chối
    raise HTTPException(status_code=403, detail="Bạn không có quyền xem danh sách Rover của trạm nÃ y.")

@router.post("/api/bases/{serial}/rovers", response_model=schemas.RoverAccountResponse)
async def create_rover_for_base(
    serial: str,
    rover_data: schemas.RoverAccountCreate,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # 1. Kiểm tra quyền
    is_admin = current_user.role == auth.Role.ADMIN
    is_authorized_rover_editor = (
        current_user.role in (auth.Role.COORDINATOR, auth.Role.VIEWER) and 
        auth.can_edit_rovers(current_user)
    )

    if not is_admin and not is_authorized_rover_editor:
        raise HTTPException(status_code=403, detail="Bạn không có quyền tạo tÃ i khoản Rover.")

    if not is_admin:
        if current_user.role == auth.Role.VIEWER:
            if str(serial).strip().upper() not in await crud.get_viewer_visible_base_serials(db, devices_db, current_user):
                raise HTTPException(status_code=403, detail="Bạn không được phép tạo Rover cho trạm Base này.")
        else:
            device = await crud.get_device_by_serial(devices_db, serial)
            if not device or (device.user_id != current_user.id and str(serial).strip().upper() not in crud.get_coordinator_view_base_serials(current_user)):
                raise HTTPException(status_code=403, detail="Bạn không được phép tạo Rover cho trạm Base này.")

    # 2. Kiểm tra trùng lặp trong DB Local
    existing_rover = await crud.get_rover_by_username(db, rover_data.username)
    if existing_rover:
        raise HTTPException(status_code=400, detail="Tên đăng nhập Rover đã tồn tại (DB).")

    # 1. Lấy thông tin thiết bị để kiểm tra Mountpoint hợp lệ
    device_info = await crud.get_device_by_serial(devices_db, serial)
    if not device_info:
        raise HTTPException(status_code=404, detail="Trạm Base không tồn tại.")
    
    service_conf = crud.normalize_service_config(device_info.service_config)
    
    # 2. Xác định Mountpoint (Logic mới)
    target_mountpoint = rover_data.mountpoint_allow
    
    valid_mps = []
    if service_conf.get('server1_enabled'):
        valid_mps.append(service_conf.get('mountpoint1'))
    if service_conf.get('server2_enabled'):
        valid_mps.append(service_conf.get('mountpoint2'))
        
    # Lọc giá trị None/Rỗng
    valid_mps = [mp for mp in valid_mps if mp]

    # Gộp thêm extra mountpoints của user (nếu có)
    if current_user.extra_mountpoints:
        extra_list = [mp.strip() for mp in re.split(r'[;,\s\n]+', current_user.extra_mountpoints) if mp.strip()]
        valid_mps.extend(extra_list)

    if not valid_mps:
        target_mountpoint = f"MP_{serial}"
    elif target_mountpoint not in valid_mps:
        # Nếu là Admin hoặc MP nằm trong danh sách hợp lệ mở rộng thì cho qua
        if not is_admin and target_mountpoint not in valid_mps:
            target_mountpoint = valid_mps[0]

    duration_seconds = 315360000 # 10 năm mặc định
    
    if rover_data.expiry_days > 0:
        duration_seconds = rover_data.expiry_days * 86400  # 1 ngÃ y = 86400 giây

    # 3. GỌI TCP ĐẾN NTRIP CASTER (Tạo trên toàn bộ Caster được cấu hình)
    hosts, config = _read_caster_config_from_env()
    port = int(config.get("port") or settings.CASTER_PORT or 2101)
    
    target_hosts = [str(h).strip() for h in hosts if h.strip()]
    if not target_hosts:
        default_host = str(config.get("host") or settings.CASTER_HOST or "").strip()
        if default_host:
            target_hosts = [default_host]

    if not target_hosts:
        raise HTTPException(status_code=500, detail="Không cấu hình được NTRIP Caster Server.")

    creator_identifier = current_user.username 
    created_any = False
    
    for host in target_hosts:
        try:
            from app.caster_client import CasterClient
            caster = CasterClient(host, port)
            await caster.create_user(
                creator=creator_identifier,
                username=rover_data.username,
                password=rover_data.password,
                mountpoints=target_mountpoint, 
                expiry_seconds=duration_seconds, 
                max_users=1,
                show_position=rover_data.show_position,
                rtcm_cycle_enabled=rover_data.rtcm_cycle_enabled,
                rtcm_disconnect_interval_seconds=rover_data.rtcm_disconnect_interval_seconds,
                rtcm_disconnect_duration_seconds=rover_data.rtcm_disconnect_duration_seconds,
            )
            created_any = True
            logger.info(f"✓ Created Rover '{rover_data.username}' for MP '{target_mountpoint}' on Caster {host}")
        except Exception as host_err:
            logger.error(f"Failed to create rover '{rover_data.username}' on Caster {host}: {host_err}")

    if not created_any:
        raise HTTPException(status_code=502, detail="Không thể tạo tài khoản trên bất kỳ NTRIP Caster Server nào.")

    # 4. Lưu vÃ o DB
    new_rover = await crud.create_rover_account(
        db, 
        username=rover_data.username,
        password=rover_data.password,
        base_serial=serial,
        creator_id=current_user.id,
        show_position=rover_data.show_position,
        rtcm_cycle_enabled=rover_data.rtcm_cycle_enabled,
        rtcm_disconnect_interval_seconds=rover_data.rtcm_disconnect_interval_seconds,
        rtcm_disconnect_duration_seconds=rover_data.rtcm_disconnect_duration_seconds,
        is_shared_with_managers=rover_data.is_shared_with_managers
    )
    
    return new_rover

@router.put("/api/rovers/{rover_id}", response_model=schemas.RoverAccountResponse)
async def update_rover(
    rover_id: int,
    update_req: schemas.RoverUpdate,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db), # Add devices_db here
    current_user: models.User = Depends(auth.get_current_user)
):
    # 1. Lấy thông tin Rover cũ
    rover = await crud.get_rover_by_id(db, rover_id)
    if not rover:
        raise HTTPException(status_code=404, detail="Không tìm thấy tÃ i khoản Rover.")

    # 2. Kiểm tra quyền
    has_perm = await crud.check_rover_management_permission(rover, current_user, devices_db, db)
    if not has_perm:
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý Rover này.")
    if update_req.auto_save_sessions is not None and current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được cấu hình lưu phiên Rover.")
    if update_req.auto_save_fixed_points is not None and current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được cấu hình tự lưu điểm Fixed.")


    # 3. Chuẩn bị dữ liệu mới (nếu client không gửi thì dùng cái cũ)
    new_password = update_req.password if update_req.password else rover.password
    if update_req.selected_serials and current_user.role != auth.Role.ADMIN:
        manageable_serials = await crud.get_rover_manageable_base_serials(devices_db, current_user, db)
        selected_serials = {
            str(serial).strip().upper()
            for serial in update_req.selected_serials
            if str(serial).strip()
        }
        if not selected_serials.issubset(manageable_serials):
            raise HTTPException(status_code=403, detail="Rover chỉ được gán vào trạm trong phạm vi của bạn.")
    # Empty string is intentional: it means remove every allowed mountpoint.
    # Only None means "keep the current value".
    new_mountpoints = update_req.mountpoints if update_req.mountpoints is not None else rover.mountpoints
    if current_user.role != auth.Role.ADMIN and update_req.mountpoints is not None:
        serials_for_mps = update_req.selected_serials or (rover.base_serial.split(",") if rover.base_serial else [])
        if not await crud.rover_mountpoints_within_scope(devices_db, current_user, serials_for_mps, update_req.mountpoints):
            raise HTTPException(status_code=403, detail="Mountpoint không thuộc trạm được cấp cho bạn.")
    
    # 4. GỌI CASTER ĐỂ CẬP NHẬT
    try:
        from app.caster_client import CasterClient
        creator_identifier = current_user.username 
        new_max_connections = update_req.max_connections if update_req.max_connections is not None else rover.max_connections
        target_show_pos = update_req.show_position if update_req.show_position is not None else rover.show_position
        target_rtcm_cycle_enabled = update_req.rtcm_cycle_enabled if update_req.rtcm_cycle_enabled is not None else bool(getattr(rover, "rtcm_cycle_enabled", False))
        target_rtcm_cycle_interval = update_req.rtcm_disconnect_interval_seconds if update_req.rtcm_disconnect_interval_seconds is not None else int(getattr(rover, "rtcm_disconnect_interval_seconds", 300) or 300)
        target_rtcm_cycle_duration = update_req.rtcm_disconnect_duration_seconds if update_req.rtcm_disconnect_duration_seconds is not None else int(getattr(rover, "rtcm_disconnect_duration_seconds", 10) or 10)
        
        # Determine updated target casters
        # If request provides target_casters, use it. Otherwise use what's in DB.
        old_hosts_str = rover.target_casters if rover.target_casters else ""
        old_hosts_set = set(h.strip() for h in old_hosts_str.split(";") if h.strip())
        
        # Resolve hosts where this rover actually exists from live caster cache.
        live_hosts_set = set()
        try:
            rover_key = str(rover.username or "").strip().lower()
            async with global_caster_cache_lock:
                for cache_data in (await redis_client.get_all_caster_cache()).values():
                    host = str(cache_data.get("host", "") or "").strip()
                    if not host:
                        continue
                    for u in (cache_data.get("users") or []):
                        if str(u.get("username", "") or "").strip().lower() == rover_key:
                            live_hosts_set.add(host)
                            break
        except Exception as e:
            logger.warning(f"Failed to resolve live caster hosts for '{rover.username}': {e}")

        if update_req.target_casters is not None:
            requested_hosts = [str(h).strip() for h in update_req.target_casters if str(h).strip()]
            if requested_hosts:
                updated_target_hosts = requested_hosts
            else:
                # Frontend legacy case: empty selection should not cause no-op update.
                updated_target_hosts = list(old_hosts_set or live_hosts_set)
        else:
            # Load from DB first, then from live cache.
            updated_target_hosts = list(old_hosts_set or live_hosts_set)

        if not updated_target_hosts:
            # Last fallback to all configured hosts to ensure complete sync
            env_hosts, env_cfg = _read_caster_config_from_env()
            if env_hosts:
                updated_target_hosts = [str(h).strip() for h in env_hosts if h.strip()]
            else:
                default_h = str(env_cfg.get("host") or settings.CASTER_HOST or "").strip()
                if default_h:
                    updated_target_hosts = [default_h]

        # Keep order and deduplicate
        seen_hosts = set()
        updated_target_hosts = [h for h in updated_target_hosts if h and not (h in seen_hosts or seen_hosts.add(h))]
        
        new_hosts_set = set(updated_target_hosts)
        removed_hosts = old_hosts_set - new_hosts_set
        
        port = int(settings.CASTER_PORT or 2101)
        results_log = []
        creator_user = await crud.get_user_by_id(db, rover.created_by_user_id)
        creator_username = creator_user.username if creator_user else "admin"
        id_candidates = []
        for ident in (creator_username, creator_identifier, "admin", "id"):
            ident_s = str(ident or "").strip()
            if ident_s and ident_s not in id_candidates:
                id_candidates.append(ident_s)

        durations_by_host = {}
        if update_req.expiry_days is None:
            for host in updated_target_hosts:
                remaining = await CasterClient(host, port).get_user_remaining_seconds(rover.username)
                if remaining is None:
                    if host in old_hosts_set or host in live_hosts_set:
                        raise HTTPException(status_code=502, detail=f"Không đọc được thời hạn Rover '{rover.username}' trên Caster {host}.")
                    remaining = max(0, int(rover.expires_at - time.time())) if rover.expires_at else 315360000
                durations_by_host[host] = remaining

        # 4a. Update/Create on selected hosts
        for host in updated_target_hosts:
            try:
                caster = CasterClient(host, port)
                duration_seconds = update_req.expiry_days * 86400 if update_req.expiry_days is not None else durations_by_host[host]
                created_ok = await caster.create_user(
                    creator=creator_username,
                    username=rover.username,
                    password=new_password,
                    mountpoints=new_mountpoints, 
                    expiry_seconds=duration_seconds, 
                    max_users=new_max_connections,
                    show_position=target_show_pos,
                    rtcm_cycle_enabled=bool(target_rtcm_cycle_enabled),
                    rtcm_disconnect_interval_seconds=int(target_rtcm_cycle_interval),
                    rtcm_disconnect_duration_seconds=int(target_rtcm_cycle_duration),
                )
                if not created_ok:
                    logger.warning(f"Caster {host} did not confirm create/update for rover '{rover.username}'")

                # Ensure show_position is synced on legacy caster variants.
                toggle_ok = False
                for ident in id_candidates:
                    if await caster.toggle_show_pos(
                        identifier=ident,
                        username=rover.username,
                        enabled=target_show_pos
                    ):
                        toggle_ok = True
                        break
                if not toggle_ok:
                    logger.warning(f"Caster {host} did not confirm show_position toggle for rover '{rover.username}'")
                results_log.append(host)
            except Exception as e:
                logger.error(f"Failed to update rover on caster {host}: {e}")

        # 4b. Cleanup removed hosts
        for host in removed_hosts:
            try:
                caster = CasterClient(host, port)
                deleted_ok = await caster.delete_user(
                    identifier=creator_username,
                    username=rover.username
                )
                if not deleted_ok:
                    logger.warning(f"Caster {host} did not confirm delete for rover '{rover.username}'")
                logger.info(f"✓ Cleaned up (deleted) rover '{rover.username}' from removed caster {host}")
            except Exception as e:
                logger.warning(f"Failed to cleanup rover from removed caster {host}: {e}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Caster Multi-Update Error: {e}")
        raise HTTPException(status_code=502, detail=f"Lỗi cập nhật Caster: {str(e)}")

    # 5. Cập nhật DB Local
    update_data = {
        "password": new_password,
        "mountpoints": new_mountpoints,
        "max_connections": new_max_connections,
        "show_position": target_show_pos,
        "rtcm_cycle_enabled": bool(target_rtcm_cycle_enabled),
        "rtcm_disconnect_interval_seconds": int(target_rtcm_cycle_interval),
        "rtcm_disconnect_duration_seconds": int(target_rtcm_cycle_duration),
        "target_casters": ";".join(updated_target_hosts)
    }
    
    # 🔥 FIX: Cập nhật base_serial nếu client gửi lên danh sách trạm mới
    if update_req.selected_serials:
        update_data["base_serial"] = ",".join(update_req.selected_serials)
        
    if update_req.is_shared_with_managers is not None:
        update_data["is_shared_with_managers"] = update_req.is_shared_with_managers
    if update_req.auto_save_sessions is not None:
        update_data["auto_save_sessions"] = update_req.auto_save_sessions
    if update_req.auto_save_fixed_points is not None:
        update_data["auto_save_fixed_points"] = update_req.auto_save_fixed_points

    if update_req.expiry_days is not None:
        update_data["expires_at"] = int(time.time()) + update_req.expiry_days * 86400
        
    updated_rover = await crud.update_rover_account(db, rover_id, update_data)
    
    return updated_rover

@router.delete("/api/rovers/{rover_id}")
async def delete_rover(
    rover_id: int,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db), # Add devices_db here
    current_user: models.User = Depends(auth.get_current_user)
):
    if current_user.role == auth.Role.VIEWER:
        raise HTTPException(status_code=403, detail="Viewer chỉ được tạo và sửa Rover, không được xóa.")

    # 1. Lấy thông tin Rover
    rover_to_delete = await crud.get_rover_by_id(db, rover_id)
    if not rover_to_delete:
        raise HTTPException(status_code=404, detail="Không tìm thấy tÃ i khoản Rover.")

    # 2. Kiểm tra quyền
    has_perm = await crud.check_rover_management_permission(rover_to_delete, current_user, devices_db, db)
    if not has_perm:
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý Rover này.")

    
    creator_user = await crud.get_user_by_id(db, rover_to_delete.created_by_user_id)
    creator_username = creator_user.username if creator_user else "admin"

    # 3. GỌI TCP ĐẾN NTRIP CASTER ĐỂ XÓA
    try:
        caster = await get_caster_client(db)
        await caster.delete_user(
            identifier=creator_username, 
            username=rover_to_delete.username
        )
        logger.info(f"âœ“ Deleted Rover '{rover_to_delete.username}' from Caster via TCP")
    except Exception as e:
        logger.warning(f"Failed to delete rover from Caster (might be already deleted): {e}")

    # 4. Xóa khỏi DB Local
    success = await crud.delete_rover_account(db, rover_id=rover_id)
    
    return {"status": "deleted", "rover_id": rover_id}

@router.post("/api/devices/{serial}/auto-deploy-token")
async def auto_deploy_device_token(
    serial: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_LICENSE))
):
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    previous_token = device.device_token
    license_key = license_manager.get_or_create_device_token(serial, previous_token)

    device.device_token = license_key
    system_info = dict(device.system_info or {}) if isinstance(device.system_info, dict) else {}
    current_auth_status = system_info.get("auth_status")
    auth_status = dict(current_auth_status) if isinstance(current_auth_status, dict) else {}
    auth_status.update({
        "server_token_present": True,
        "token_required": True,
        "token_synced": False,
        "agent_token_present": None,
        "needs_token_deploy": True,
        "token_check_available": False,
        "token_deployment_pending": True,
        "license_bypass": bool(system_info.get("auth_license_bypass")),
        "agent_reported_status": device.status,
    })
    system_info["auth_status"] = auth_status
    device.system_info = system_info
    try:
        await db.commit()
        await db.refresh(device)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail="This device token collides with an existing token. Retry token deployment."
        )

    # WebSocket authentication and MQTT license reconciliation use this cache.
    # Refresh it before dispatching the rotated token so the new credential is
    # accepted immediately and an older in-flight lookup cannot overwrite it.
    await mqtt_handler.cache_device_metadata(device)

    await ui_manager.broadcast({
        "type": "status_update",
        "data": schemas.Device.from_orm(device).model_dump(),
    })
        
    payload = {"command": "DEPLOY_LICENSE", "payload": {"license_key": license_key}}
    
    await crud.log_system_event(
        db=db,
        type="info",
        message=f"Auto-deployed Auth Token to '{device.name}' by {current_user.username}",
        user_id=current_user.id,
        serial=serial
    )
    
    try:
        return await send_command_to_pi(serial, payload)
    except Exception:
        # The token exists on the server, but no command transport accepted it.
        # Keep the retry action available and do not claim that delivery is pending.
        auth_status["token_deployment_pending"] = False
        auth_status["needs_token_deploy"] = True
        auth_status["token_check_available"] = False
        device.device_token = previous_token
        device.system_info = {**system_info, "auth_status": auth_status}
        await db.commit()
        await db.refresh(device)
        await mqtt_handler.cache_device_metadata(device)
        await ui_manager.broadcast({
            "type": "status_update",
            "data": schemas.Device.from_orm(device).model_dump(),
        })
        raise

@router.post("/api/devices/{serial}/license-bypass")
async def set_device_license_bypass(
    serial: str,
    payload: dict | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_LICENSE))
):
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    payload = payload or {}
    enabled = bool(payload.get("enabled"))
    system_info = dict(device.system_info or {})
    system_info["auth_license_bypass"] = enabled
    system_info["auth_license_bypass_by"] = current_user.username
    import time
    system_info["auth_license_bypass_at"] = int(time.time())
    device.system_info = system_info

    if enabled and device.status == "awaiting_license":
        device.status = "sleep" if device.base_config or device.service_config else "online"

    await db.commit()
    await db.refresh(device)
    await mqtt_handler.cache_device_metadata(device)

    await crud.log_system_event(
        db=db,
        type="warning" if enabled else "info",
        message=f"License bypass {'enabled' if enabled else 'disabled'} for '{device.name or serial}' by {current_user.username}",
        user_id=current_user.id,
        serial=serial
    )

    return {
        "status": "ok",
        "serial": serial,
        "license_bypass": enabled,
        "system_info": device.system_info or {},
        "device_status": device.status,
    }

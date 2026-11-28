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
from ..caster_helpers import _read_caster_config_from_env, get_caster_client, global_caster_cache_lock, validate_caster_hosts, saved_rover_duration
from ..caster_client import CasterClient
from ..database import settings
from ..websocket import manager as ui_manager
import base64
import logging
import os
import re
import time
import secrets
import math

logger = logging.getLogger(__name__)

from ..mobile_base import require_editable_base

router = APIRouter(dependencies=[Depends(require_editable_base)], tags=["Devices"])
DEVICE_NOT_FOUND = "Device not found"
VIEW_ONLY_STATION = "Trạm này chỉ được xem"


def _auto_cors_port(raw_config: dict) -> int:
    try:
        port = int(raw_config.get("port"))
    except (TypeError, ValueError):
        raise ValueError("Base port must be a valid number") from None
    if not 1 <= port <= 65535:
        raise ValueError("Base port must be between 1 and 65535")
    return port


def _add_auto_cors_projection(raw_config: dict, result: dict) -> None:
    enabled = raw_config.get('enable_itrf_vn2000_transform', True)
    if not isinstance(enabled, bool):
        raise ValueError('enable_itrf_vn2000_transform must be a boolean')
    result['enable_itrf_vn2000_transform'] = enabled
    if (raw_config.get('l0_deg') is None) != (raw_config.get('k0') is None):
        raise ValueError('Manual projection requires both l0_deg and k0')
    for key, low, high in (('l0_deg', 100, 112), ('k0', 0.999, 1.001),
                           ('resN', -1e6, 1e6), ('resE', -1e6, 1e6), ('resHS', -1e6, 1e6)):
        if raw_config.get(key) is None:
            continue
        try:
            value = float(raw_config[key])
        except (TypeError, ValueError):
            raise ValueError(f'Invalid {key}') from None
        if isinstance(raw_config[key], bool) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'Invalid {key}')
        result[key] = value


def _normalize_auto_cors_config(payload: dict) -> dict:
    raw_config = payload.get("auto_cors") if isinstance(payload, dict) else None
    if not isinstance(raw_config, dict):
        raise ValueError("AUTO_CORS requires the Base connection fields")

    host = str(raw_config.get("ip") or "").strip()
    user = str(raw_config.get("user") or "").strip()
    password = str(raw_config.get("password") or "")
    mountpoint = str(raw_config.get("mountpoint") or "").strip()
    if not host or not user or not password or not mountpoint:
        raise ValueError("Base IP, port, username, password, and mountpoint are required")
    if any(char in "\r\n" for char in f"{host}{user}{password}{mountpoint}"):
        raise ValueError("Base connection fields contain invalid line breaks")

    result = {
        "ip": host,
        "port": _auto_cors_port(raw_config),
        "user": user,
        "password": password,
        "mountpoint": mountpoint,
    }
    _add_auto_cors_projection(raw_config, result)
    return result


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


def _device_response_list(devices: list[models.Device], current_user: models.User) -> list:
    devices = [d for d in devices if not crud.is_blocked_device_serial(d.serial)]
    devices = _with_runtime_sync_flags(devices)
    if current_user.role not in (auth.Role.VIEWER, auth.Role.COORDINATOR):
        return devices
    items = []
    for device in devices:
        if current_user.role == auth.Role.COORDINATOR and device.user_id == current_user.id:
            items.append(device)
            continue
        item = schemas.Device.from_orm(device).model_dump()
        for key in ("base_config", "service_config", "system_info"):
            item[key] = schemas.redact_config_secrets(item.get(key))
        items.append(item)
    return items


def _rover_response_list(rovers: list[models.RoverAccount], current_user: models.User) -> list:
    items = [schemas.RoverAccountResponse.from_orm(rover).model_dump() for rover in rovers]
    return items


# === DEVICE ENDPOINTS ===
async def _resolve_device_user_scope(user_id, auth_db, current_user):
    if user_id is None:
        return current_user.id, current_user.role
    user_info = await crud.get_user_by_id(auth_db, user_id)
    is_child = user_info and getattr(user_info, 'parent_id', None) == current_user.id
    can_filter = (
        current_user.role == auth.Role.ADMIN
        or user_id == current_user.id
        or (current_user.role == auth.Role.COORDINATOR and is_child)
    )
    if not can_filter:
        logging.warning(
            f"IDOR ATTEMPT: User '{current_user.username}' (role={current_user.role}) "
            f"tried to filter by user_id={user_id}"
        )
        raise HTTPException(status_code=403, detail="Only admins can filter devices by user_id")
    return user_id, user_info.role if user_info else current_user.role


async def _coordinator_device_list(target_user_id, db, auth_db, current_user):
    coordinator = current_user if current_user.id == target_user_id else await crud.get_user_by_id(auth_db, target_user_id)
    if not coordinator:
        return []
    visible_serials = await crud.get_coordinator_visible_base_serials(db, coordinator)
    result = await db.execute(
        select(models.Device).where(func.upper(models.Device.serial).in_(visible_serials))
        .order_by(models.Device.name)
    )
    devices = result.scalars().all()
    logger.info(
        f"[DEVICES] {current_user.username} (role={auth.Role.COORDINATOR}) "
        f"→ {len(devices)} devices by user_id={target_user_id}"
    )
    return _device_response_list(devices, current_user)


async def _viewer_device_list(target_user_id, db, auth_db, current_user):
    user_info = current_user if current_user.id == target_user_id else await crud.get_user_by_id(auth_db, target_user_id)
    if not user_info:
        return []
    viewer_serials = await crud.get_viewer_visible_base_serials(auth_db, db, user_info)
    if not viewer_serials:
        return []
    result = await db.execute(
        select(models.Device).where(func.upper(models.Device.serial).in_(viewer_serials))
        .order_by(models.Device.name)
    )
    devices = result.scalars().all()
    logger.info(f"[DEVICES] Viewer '{current_user.username}' → returning {len(devices)} devices")
    return _device_response_list(devices, current_user)


@router.get("/api/devices", response_model=list[schemas.Device], responses={403: {"description": "Forbidden"}})
async def get_initial_devices(
    user_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    auth_db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)) 
):
    target_user_id, target_role = await _resolve_device_user_scope(user_id, auth_db, current_user)
    if target_role == auth.Role.COORDINATOR:
        return await _coordinator_device_list(target_user_id, db, auth_db, current_user)
    if target_role == auth.Role.VIEWER:
        return await _viewer_device_list(target_user_id, db, auth_db, current_user)

    # Fallback: Admin sees all
    logger.info(f"[DEVICES] {current_user.username} (role={target_role}) → returning ALL devices")
    return _device_response_list(await crud.get_all_devices(db), current_user)

async def _load_generic_command_device(serial, command, db, current_user):
    if current_user.role == auth.Role.COORDINATOR:
        raise HTTPException(status_code=403, detail="Coordinators can only send specific commands.")
    if not auth.has_permission(current_user, auth.Permission.EDIT_CHIP_CONFIG):
        raise HTTPException(status_code=403, detail="Permission denied.")
    name = None
    if command.command == "PROVISION_DEVICE":
        name = command.payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=400, detail="Tên không được để trống")

    device = await crud.get_device_by_serial(db, serial)
    discovered = not device and command.command == "PROVISION_DEVICE"
    discovered = discovered and serial in mqtt_handler.device_telemetry_cache
    if discovered:
        if not auth.has_permission(current_user, auth.Permission.MANAGE_LICENSE):
            raise HTTPException(status_code=403, detail="Bạn không có quyền đăng ký và cấp token cho trạm mới.")
        await auto_deploy_device_token(serial, db, current_user)
        device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail=DEVICE_NOT_FOUND)
    if current_user.role == auth.Role.COORDINATOR and device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail=VIEW_ONLY_STATION)
    return device, name


def _build_gnss_verification_payload(serial, command, device, capabilities):
    embedded = capabilities.get("gnss_verification_uart1") is True
    if device.detected_chip_type == "Ublox":
        telemetry = mqtt_handler.device_telemetry_cache.get(serial) or {}
        profile = command_builder.select_ublox_profile(telemetry, capabilities)
        return command_builder.build_ublox_verification_payload(
            device.base_config or {}, route_to_uart1=embedded, receiver_profile=profile
        )

    expected = command_builder.build_unicore_verification_payload(
        device.base_config or {}, current_port=embedded
    )
    selected_port = command.payload.get("port")
    if selected_port is not None:
        allowed_port = embedded and selected_port in ("COM1", "COM2", "COM3")
        if not allowed_port:
            raise ValueError("Cổng đối chiếu phải là COM1, COM2 hoặc COM3 trên ESP32.")
        expected["port"] = selected_port
    return expected


async def _send_gnss_verification(serial, command, db, device, current_user):
    live_info = (mqtt_handler.device_telemetry_cache.get(serial) or {}).get("system_info")
    capabilities = live_info if isinstance(live_info, dict) else device.system_info or {}
    supported_chip = device.detected_chip_type in ("Ublox", "Unicorecomm")
    if not supported_chip or capabilities.get("gnss_verification_supported") is not True:
        raise HTTPException(status_code=409, detail="Cập nhật agent để xác minh cấu hình GNSS.")
    try:
        expected = _build_gnss_verification_payload(serial, command, device, capabilities)
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    command_id = secrets.token_urlsafe(18)
    result = await send_command_to_pi(
        serial, {"command": command.command, "payload": expected, "command_id": command_id}
    )
    await crud.log_system_event(
        db=db, type="info", user_id=current_user.id, serial=serial,
        message=f"GNSS configuration verification by {current_user.username}",
    )
    return {**result, "command_id": command_id}


async def _send_storage_command(serial, command, db, device, current_user):
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ quản trị viên được quản lý bộ nhớ thiết bị.")
    storage = (device.system_info or {}).get("nvs", {})
    if not isinstance(storage, dict) or storage.get("cleanup_supported") is not True:
        raise HTTPException(status_code=409, detail="Firmware chưa hỗ trợ quản lý bộ nhớ. Hãy cập nhật ESP32.")
    if command.command == "CHECK_STORAGE_STATUS" and command.payload.get("inspect"):
        if storage.get("inventory_supported") is not True:
            raise HTTPException(status_code=409, detail="Cập nhật firmware để xem bảng dữ liệu NVS.")
        offset = command.payload.get("offset", 0)
        if type(offset) is not int or not 0 <= offset <= 8192:
            raise HTTPException(status_code=400, detail="Vị trí trang NVS không hợp lệ.")
        command.payload = {"inspect": True, "offset": offset}
    if command.command == "CLEAR_NVS_DATA":
        group = command.payload.get("group")
        if group not in ("unused_wifi", "auto_base_history") or command.payload.get("confirm") != group:
            raise HTTPException(status_code=400, detail="Chọn và xác nhận đúng nhóm dữ liệu cần xóa.")
    command_id = secrets.token_urlsafe(18)
    result = await send_command_to_pi(serial, {**command.model_dump(), "command_id": command_id})
    await crud.log_system_event(
        db=db, type="info", user_id=current_user.id, serial=serial,
        message=f"Storage command {command.command} ({command.payload.get('group', 'status')}) by {current_user.username}",
    )
    return {**result, "command_id": command_id}


async def _send_provision_device_command(serial, name, db, current_user):
    from .users import rename_device
    return await rename_device(serial, schemas.DeviceRename(name=name.strip()), db, current_user)


@router.post("/api/devices/{serial}/command", responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 409: {"description": "Conflict"}})
async def send_generic_command(
    serial: str,
    command: schemas.Command,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    device, name = await _load_generic_command_device(serial, command, db, current_user)
    if command.command == "VERIFY_GNSS_CONFIG":
        return await _send_gnss_verification(serial, command, db, device, current_user)
    if command.command in ("CHECK_STORAGE_STATUS", "CLEAR_NVS_DATA"):
        return await _send_storage_command(serial, command, db, device, current_user)
    if command.command == "PROVISION_DEVICE":
        return await _send_provision_device_command(serial, name, db, current_user)
    await crud.log_system_event(
        db=db,
        type="info",
        message=f"Command '{command.command}' sent to '{device.name}' by {current_user.username}",
        user_id=current_user.id,
        serial=serial,
    )
    return await send_command_to_pi(serial, command.model_dump())


@router.post("/api/devices/{serial}/reference-check", responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def start_base_reference_check(
    serial: str,
    request: schemas.BaseReferenceCheckRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_COORDINATES)),
):
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail=DEVICE_NOT_FOUND)
    if current_user.role == auth.Role.COORDINATOR and device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail=VIEW_ONLY_STATION)
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

@router.post("/api/devices/{serial}/cors-mountpoints", responses={400: {"description": "Bad Request"}, 404: {"description": "Not Found"}, 502: {"description": "Bad Gateway"}})
async def get_device_cors_mountpoints(
    serial: str,
    request: schemas.CasterMountpointLookupRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_COORDINATES)),
):
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail=DEVICE_NOT_FOUND)

    host = request.host.strip()
    username = request.username.strip()
    if any(char in "\r\n/" for char in host):
        raise HTTPException(status_code=400, detail="Base host must be a hostname or IP address")
    if not host or not username or not request.password:
        raise HTTPException(status_code=400, detail="Base host, username, and password are required")

    try:
        caster = CasterClient(
            host,
            request.port,
            admin_user=username,
            admin_password=request.password,
        )
        sources = await caster.get_sources(prefer_standard_ntrip=True)
    except Exception as exc:
        logger.warning("CORS sourcetable lookup failed for %s:%s: %s", host, request.port, exc)
        raise HTTPException(status_code=502, detail="Không thể kết nối hoặc đọc sourcetable từ Base") from exc

    if sources is None:
        raise HTTPException(status_code=502, detail="Base không trả về sourcetable hợp lệ")

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


async def _get_chip_config_device(db, serial, current_user):
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found in database")
    if current_user.role == auth.Role.COORDINATOR and device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail=VIEW_ONLY_STATION)
    return device


def _normalize_ublox_gnss_options(sensor_type, mode, params, gnss_options):
    if mode != "BASE" or sensor_type != "Ublox":
        return gnss_options
    try:
        normalized_options = command_builder.normalize_gnss_options(gnss_options)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    gnss_options = {
        **(gnss_options if isinstance(gnss_options, dict) else {}),
        "interference_detection_enabled": normalized_options["interference_detection_enabled"],
        "jamming_sensitivity_high": normalized_options["jamming_sensitivity_high"],
    }
    if isinstance(params, dict):
        params["gnss_options"] = gnss_options
    return gnss_options


def _normalize_chip_request(payload, device):
    sensor_type = payload.get("sensor_type")
    params = payload.get("params", {})
    mode = payload.get("mode")
    method = params.get("base_setup_method") if isinstance(params, dict) else None
    gnss_options = params.get("gnss_options") if isinstance(params, dict) else None
    existing_base_config = device.base_config if isinstance(device.base_config, dict) else {}
    if gnss_options is None:
        gnss_options = existing_base_config.get("gnss_options")
    if gnss_options and isinstance(params, dict) and "gnss_options" not in params:
        params["gnss_options"] = gnss_options
    if not sensor_type or not mode:
        raise HTTPException(status_code=400, detail="Missing required fields: sensor_type or mode")
    gnss_options = _normalize_ublox_gnss_options(sensor_type, mode, params, gnss_options)
    return {
        "sensor_type": sensor_type, "params": params, "mode": mode, "method": method,
        "gnss_options": gnss_options, "existing_base_config": existing_base_config,
    }


async def _load_chip_receiver_profile(serial, device, config):
    sensor_type = config["sensor_type"]
    mode = config["mode"]
    method = config["method"]
    if not (sensor_type == "Ublox" and mode == "BASE" and method in ("FIXED_LLA", "SURVEY_IN", "AUTO_CORS")):
        return None
    live = mqtt_handler.device_telemetry_cache.get(serial) or {}
    capabilities = live.get("system_info") or device.system_info or {}
    try:
        return await command_builder.wait_for_ublox_profile(
            mqtt_handler.device_telemetry_cache, serial, capabilities
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _prepare_auto_cors(payload, mode, method, device, params):
    config = None
    auto_apply = False
    await_source = False
    source_mode = "manual"
    if mode != "BASE" or method != "AUTO_CORS":
        return config, auto_apply, await_source, source_mode
    system_info = device.system_info if isinstance(device.system_info, dict) else {}
    if not system_info.get("auto_base_review_supported"):
        raise HTTPException(status_code=409, detail="Cập nhật agent để lấy và duyệt kết quả Auto Base trước khi áp dụng Fixed LLA.")
    raw_auto_cors = payload.get("auto_cors") if isinstance(payload.get("auto_cors"), dict) else {}
    source_mode = str(raw_auto_cors.get("source_mode") or "manual").strip().lower()
    if source_mode == "nearest":
        if not system_info.get("auto_base_nearest_supported"):
            raise HTTPException(status_code=409, detail="Cập nhật agent ESP32 để tự chọn base CORS gần nhất.")
        # Let the receiver enter Rover first; the periodic manager selects from fresh GGA.
        auto_apply = True
        await_source = True
        if isinstance(params, dict):
            params["auto_cors_source_mode"] = "nearest"
            params["auto_base_request_id"] = secrets.token_hex(12)
    elif source_mode == "manual":
        try:
            config = _normalize_auto_cors_config(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        raise HTTPException(status_code=400, detail="Nguồn CORS phải là manual hoặc nearest.")
    return config, auto_apply, await_source, source_mode


def _apply_normalized_chip_params(device, params, method, gnss_options, existing_base_config):
    normalized_params = crud.normalize_base_config(params)
    if normalized_params:
        if "gnss_options" not in normalized_params and "gnss_options" in existing_base_config:
            normalized_params["gnss_options"] = existing_base_config["gnss_options"]
        elif gnss_options and "gnss_options" not in normalized_params:
            normalized_params["gnss_options"] = gnss_options
        device.base_config = normalized_params
        return
    current_config = device.base_config if isinstance(device.base_config, dict) else {}
    merged_config = dict(current_config)
    for key, value in params.items():
        if key != "coords":
            merged_config[key] = value
    if "gnss_options" not in merged_config and "gnss_options" in existing_base_config:
        merged_config["gnss_options"] = existing_base_config["gnss_options"]
    elif gnss_options and "gnss_options" not in merged_config:
        merged_config["gnss_options"] = gnss_options
    if merged_config:
        device.base_config = merged_config


def _apply_nearest_auto_cors_config(device, params, existing_base_config):
    saved_config = {
        **existing_base_config,
        **{key: value for key, value in params.items() if key != "coords"},
        "base_setup_method": "AUTO_CORS",
        "auto_cors_source_mode": "nearest",
        "mode": "BASE",
    }
    saved_config.pop("auto_base_started_boot_id", None)
    saved_config.pop("auto_base_started_request_id", None)
    device.base_config = {**saved_config}


async def _persist_chip_config(db, serial, device, config, source_mode):
    params = config["params"]
    method = config["method"]
    gnss_options = config["gnss_options"]
    existing_base_config = config["existing_base_config"]
    try:
        device.detected_chip_type = config["sensor_type"]
        if isinstance(params, dict) and params and method != "AUTO_CORS":
            _apply_normalized_chip_params(device, params, method, gnss_options, existing_base_config)
        elif method == "AUTO_CORS" and source_mode == "nearest":
            _apply_nearest_auto_cors_config(device, params, existing_base_config)
        device.is_synced = False
        await db.commit()
        await db.refresh(device)
        mqtt_handler.invalidate_device_cache(serial)
        await ui_manager.broadcast({
            "type": "status_update",
            "data": schemas.Device.from_orm(device).model_dump(),
        })
    except Exception as error:
        await db.rollback()
        logger.exception("Failed to save chip config (%s)", type(error).__name__)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save configuration to database: {str(error)}",
        )


def _build_base_chip_command(config, receiver_profile, auto_cors_config, auto_cors_auto_apply, source_mode):
    sensor_type = config["sensor_type"]
    params = config["params"]
    method = config["method"]
    gnss_options = config["gnss_options"]
    mode = config["mode"]
    if method == "AUTO_CORS":
        auto_base_config = {
            **(params if isinstance(params, dict) else {}),
            "base_setup_method": "AUTO_CORS",
            "auto_cors_source_mode": source_mode,
            "mode": "BASE",
        }
        return {
            "command": "TRIGGER_AUTO_BASE",
            "payload": {
                "auto_apply": auto_cors_auto_apply,
                "source_mode": source_mode,
                **(auto_cors_config or {}),
                "timeout": 3600,
                "samples": 60,
                "sensor_type": sensor_type,
                "auto_base_config": auto_base_config,
            },
            "original_config": {"sensor_type": sensor_type, "mode": mode, "params": params},
        }, []
    if method == "SURVEY_IN":
        duration = params.get("survey_in_duration")
        accuracy = params.get("survey_in_accuracy")
        if not duration or not accuracy:
            raise ValueError("Missing survey_in parameters")
        commands = command_builder.build_base_survey_in_command(
            sensor_type, duration, accuracy, gnss_options, receiver_profile=receiver_profile
        )
        return None, commands
    if method == "FIXED_LLA":
        coords = params.get("coords")
        if not coords or not all(key in coords for key in ("lat", "lon", "alt")):
            raise ValueError("Missing or incomplete coordinates")
        commands = command_builder.build_base_fixed_lla_command(
            sensor_type, coords["lat"], coords["lon"], coords["alt"],
            params.get("accuracy", 10.0), gnss_options,
            altitude_reference=params.get("altitude_reference"), receiver_profile=receiver_profile,
        )
        return None, commands
    raise ValueError(f"Unknown base setup method: {method}")


def _build_chip_config_command(config, config_request, receiver_profile, auto_cors_config, auto_cors_auto_apply, source_mode):
    if config["mode"] != "BASE":
        raise ValueError(f"Unsupported mode: {config['mode']}")
    pi_command, commands_to_send = _build_base_chip_command(
        config, receiver_profile, auto_cors_config, auto_cors_auto_apply, source_mode
    )
    if pi_command:
        return pi_command, commands_to_send
    encoded_commands = [base64.b64encode(command).decode("ascii") for command in commands_to_send if command]
    if not encoded_commands:
        raise ValueError("No commands generated")
    return {
        "command": "EXECUTE_RAW_COMMANDS",
        "payload": {"commands_b64": encoded_commands, "receiver_profile": receiver_profile},
        "original_config": config_request.payload,
    }, commands_to_send


async def _send_chip_config(db, serial, device, current_user, pi_command, commands_to_send, await_source):
    try:
        response = await send_command_to_pi(serial, pi_command)
        await crud.log_system_event(
            db=db, type="info",
            message=f"GNSS config updated for '{device.name}' by {current_user.username}",
            user_id=current_user.id, serial=serial,
        )
        result = {
            "status": "command_sent_waiting_sync",
            "channel": response["channel"],
            "commands_sent": len(commands_to_send) if not pi_command else 1,
            "message": "Configuration saved to database and sent to device. Waiting for device to apply and confirm sync status...",
            "is_synced": False,
        }
        if await_source:
            result["auto_cors_selection"] = "awaiting_gga"
        return result
    except HTTPException as http_exc:
        logger.warning("Failed to send chip config command to %s: %s", serial, http_exc.detail)
        raise HTTPException(
            status_code=503,
            detail=f"Configuration saved to database successfully, but device is offline ({http_exc.detail}). Config will auto-sync when device comes back online.",
        )
    except Exception as error:
        logger.exception("Unexpected error sending chip config command to %s: %s", serial, error, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error while sending command: {str(error)}")


@router.post("/api/devices/{serial}/configure-chip", responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 409: {"description": "Conflict"}, 500: {"description": "Internal Server Error"}, 503: {"description": "Service Unavailable"}})
async def configure_chip_endpoint(
    serial: str,
    config_request: schemas.Command,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_COORDINATES))
):
    device = await _get_chip_config_device(db, serial, current_user)
    payload = config_request.payload or {}
    config = _normalize_chip_request(payload, device)
    receiver_profile = await _load_chip_receiver_profile(serial, device, config)
    auto_cors_config, auto_apply, await_source, source_mode = _prepare_auto_cors(
        payload, config["mode"], config["method"], device, config["params"]
    )
    await _persist_chip_config(db, serial, device, config, source_mode)
    try:
        pi_command, commands_to_send = _build_chip_config_command(
            config, config_request, receiver_profile, auto_cors_config, auto_apply, source_mode
        )
    except Exception as error:
        logger.exception("Failed to build chip config commands (%s)", type(error).__name__)
        raise HTTPException(
            status_code=400,
            detail=f"Failed to build configuration commands: {str(error)}",
        )
    return await _send_chip_config(
        db, serial, device, current_user, pi_command, commands_to_send, await_source
    )

# --- CÁC ENDPOINT CHO QUẢN LÝ ROVER ---
@router.get("/api/bases/{serial}/rovers", response_model=list[schemas.RoverAccountResponse], responses={403: {"description": "Forbidden"}})
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
            rovers = await crud.get_rovers_by_base_serial(db, base_serial=serial)
            visible = await crud.get_visible_rovers_for_user(db, devices_db, current_user)
            visible_ids = {rover.id for rover in visible}
            return _rover_response_list([rover for rover in rovers if rover.id in visible_ids], current_user)

    if current_user.role == auth.Role.VIEWER:
        if not auth.can_view_rover_page(current_user):
            raise HTTPException(status_code=403, detail="Bạn không có quyền xem trang Rover.")
        if str(serial).strip().upper() not in await crud.get_viewer_visible_base_serials(db, devices_db, current_user):
            raise HTTPException(status_code=403, detail="Bạn không có quyền xem Rover của trạm này.")
        rovers = await crud.get_rovers_by_base_serial(db, base_serial=serial)
        visible = await crud.get_visible_rovers_for_user(db, devices_db, current_user)
        visible_ids = {rover.id for rover in visible}
        visible_rovers = [rover for rover in rovers if rover.id in visible_ids]
        return _rover_response_list(visible_rovers, current_user)
    
    # Nếu không phải Admin hoặc không phải chủ sở hữu, từ chối
    raise HTTPException(status_code=403, detail="Bạn không có quyền xem danh sách Rover của trạm này.")

async def _authorize_rover_creation(serial, db, devices_db, current_user):
    is_admin = current_user.role == auth.Role.ADMIN
    can_edit = current_user.role in (auth.Role.COORDINATOR, auth.Role.VIEWER) and auth.can_edit_rovers(current_user)
    if not is_admin and not can_edit:
        raise HTTPException(status_code=403, detail="Bạn không có quyền tạo tài khoản Rover.")
    if not is_admin and current_user.role == auth.Role.VIEWER:
        visible = await crud.get_viewer_visible_base_serials(db, devices_db, current_user)
        if str(serial).strip().upper() not in visible:
            raise HTTPException(status_code=403, detail="Bạn không được phép tạo Rover cho trạm Base này.")
    elif not is_admin:
        device = await crud.get_device_by_serial(devices_db, serial)
        if not device or (
            device.user_id != current_user.id
            and str(serial).strip().upper() not in crud.get_coordinator_view_base_serials(current_user)
        ):
            raise HTTPException(status_code=403, detail="Bạn không được phép tạo Rover cho trạm Base này.")
    return is_admin


def _rover_target_mountpoint(serial, rover_data, current_user, is_admin, service_conf):
    target = rover_data.mountpoint_allow
    valid = []
    if service_conf.get('server1_enabled'):
        valid.append(service_conf.get('mountpoint1'))
    if service_conf.get('server2_enabled'):
        valid.append(service_conf.get('mountpoint2'))
    valid = [mountpoint for mountpoint in valid if mountpoint]
    if current_user.extra_mountpoints:
        extra_mountpoints = [
            mountpoint.strip() for mountpoint in re.split(r'[;,\s]+', current_user.extra_mountpoints)
            if mountpoint.strip()
        ]
        valid.extend(extra_mountpoints)
    if not valid:
        return f"MP_{serial}"
    if target not in valid and not is_admin:
        return valid[0]
    return target


async def _provision_rover_on_casters(rover_data, current_user, target_mountpoint, duration_seconds):
    hosts, config = _read_caster_config_from_env()
    port = int(config.get("port") or settings.CASTER_PORT or 2101)
    target_hosts = [str(host).strip() for host in hosts if host.strip()]
    if not target_hosts:
        default_host = str(config.get("host") or settings.CASTER_HOST or "").strip()
        if default_host:
            target_hosts = [default_host]
    if not target_hosts:
        raise HTTPException(status_code=500, detail="Không cấu hình được NTRIP Caster Server.")

    created_any = False
    for host in target_hosts:
        try:
            from app.caster_client import CasterClient
            caster = CasterClient(host, port)
            await caster.create_user(
                creator=current_user.username,
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
            logger.info("Created rover on caster")
        except Exception as host_err:
            logger.exception(f"Failed to create rover '{rover_data.username}' on Caster {host}: {host_err}")
    if not created_any:
        raise HTTPException(status_code=502, detail="Không thể tạo tài khoản trên bất kỳ NTRIP Caster Server nào.")


@router.post("/api/bases/{serial}/rovers", response_model=schemas.RoverAccountResponse, responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}, 502: {"description": "Bad Gateway"}})
async def create_rover_for_base(
    serial: str,
    rover_data: schemas.RoverAccountCreate,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    is_admin = await _authorize_rover_creation(serial, db, devices_db, current_user)

    # 2. Kiểm tra trùng lặp trong DB Local
    existing_rover = await crud.get_rover_by_username(db, rover_data.username)
    if existing_rover:
        raise HTTPException(status_code=400, detail="Tên đăng nhập Rover đã tồn tại (DB).")

    # 1. Lấy thông tin thiết bị để kiểm tra Mountpoint hợp lệ
    device_info = await crud.get_device_by_serial(devices_db, serial)
    if not device_info:
        raise HTTPException(status_code=404, detail="Trạm Base không tồn tại.")
    service_conf = crud.normalize_service_config(device_info.service_config)
    target_mountpoint = _rover_target_mountpoint(serial, rover_data, current_user, is_admin, service_conf)
    duration_seconds = 315360000 # 10 năm mặc định
    if rover_data.expiry_days > 0:
        duration_seconds = rover_data.expiry_days * 86400  # 1 ngày = 86400 giây
    await _provision_rover_on_casters(rover_data, current_user, target_mountpoint, duration_seconds)
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
        is_shared_with_managers=rover_data.is_shared_with_managers,
        auto_save_sessions=getattr(rover_data, 'auto_save_sessions', True),
        auto_save_fixed_points=getattr(rover_data, 'auto_save_fixed_points', False),
    )
    return new_rover

async def _validate_rover_update_scope(rover, update_req, devices_db, db, current_user):
    if update_req.auto_save_sessions is not None and current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được cấu hình lưu phiên Rover.")
    if update_req.auto_save_fixed_points is not None and current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Chỉ admin được cấu hình tự lưu điểm Fixed.")
    if update_req.selected_serials and current_user.role != auth.Role.ADMIN:
        manageable = await crud.get_rover_manageable_base_serials(devices_db, current_user, db)
        selected = {
            str(serial).strip().upper()
            for serial in update_req.selected_serials
            if str(serial).strip()
        }
        if not selected.issubset(manageable):
            raise HTTPException(status_code=403, detail="Rover chỉ được gán vào trạm trong phạm vi của bạn.")
    if current_user.role != auth.Role.ADMIN and update_req.mountpoints is not None:
        serials = update_req.selected_serials or (rover.base_serial.split(",") if rover.base_serial else [])
        allowed = await crud.rover_mountpoints_within_scope(
            devices_db, current_user, serials, update_req.mountpoints
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="Mountpoint không thuộc trạm được cấp cho bạn.")


def _request_value_or_current(requested, current):
    return current if requested is None else requested


def _rover_update_values(rover, update_req):
    return {
        "password": update_req.password or rover.password,
        "mountpoints": _request_value_or_current(update_req.mountpoints, rover.mountpoints),
        "max_connections": _request_value_or_current(update_req.max_connections, rover.max_connections),
        "show_position": _request_value_or_current(update_req.show_position, rover.show_position),
        "rtcm_cycle_enabled": _request_value_or_current(
            update_req.rtcm_cycle_enabled, bool(getattr(rover, "rtcm_cycle_enabled", False))
        ),
        "rtcm_cycle_interval": _request_value_or_current(
            update_req.rtcm_disconnect_interval_seconds,
            int(getattr(rover, "rtcm_disconnect_interval_seconds", 300) or 300),
        ),
        "rtcm_cycle_duration": _request_value_or_current(
            update_req.rtcm_disconnect_duration_seconds,
            int(getattr(rover, "rtcm_disconnect_duration_seconds", 10) or 10),
        ),
    }


async def _live_rover_hosts(username):
    live_hosts = set()
    try:
        rover_key = str(username or "").strip().lower()
        async with global_caster_cache_lock:
            for cache_data in (await redis_client.get_all_caster_cache()).values():
                host = str(cache_data.get("host", "") or "").strip()
                if not host:
                    continue
                for user in (cache_data.get("users") or []):
                    if str(user.get("username", "") or "").strip().lower() == rover_key:
                        live_hosts.add(host)
                        break
    except Exception as error:
        logger.warning("Failed to resolve live caster hosts for '%s': %s", username, error)
    return live_hosts


def _requested_rover_hosts(old_hosts, update_req, live_hosts):
    if update_req.target_casters is not None:
        requested = [str(host).strip() for host in update_req.target_casters if str(host).strip()]
        return requested if requested else list(old_hosts or live_hosts)
    return list(old_hosts or live_hosts)


def _default_rover_hosts():
    env_hosts, env_config = _read_caster_config_from_env()
    if env_hosts:
        return [str(host).strip() for host in env_hosts if host.strip()]
    default_host = str(env_config.get("host") or settings.CASTER_HOST or "").strip()
    return [default_host] if default_host else []


def _unique_rover_hosts(hosts):
    seen = set()
    return [host for host in hosts if host and not (host in seen or seen.add(host))]


def _validated_rover_hosts(rover, update_req, live_hosts):
    old_hosts = {
        host.strip() for host in (rover.target_casters if rover.target_casters else "").split(";")
        if host.strip()
    }
    updated = _requested_rover_hosts(old_hosts, update_req, live_hosts)
    if not updated:
        updated = _default_rover_hosts()
    updated = _unique_rover_hosts(updated)
    try:
        updated = validate_caster_hosts(updated)
    except ValueError as error:
        raise HTTPException(status_code=403, detail=str(error)) from None
    return old_hosts, updated


def _removed_rover_hosts(old_hosts, updated_hosts):
    removed_hosts = set()
    for host in old_hosts - set(updated_hosts):
        try:
            removed_hosts.update(validate_caster_hosts([host]))
        except ValueError:
            # A stale database host must not select an arbitrary network target.
            continue
    return removed_hosts


async def _rover_durations_by_host(rover, updated_hosts, update_req, port):
    if update_req.expiry_days is not None:
        return {}
    durations = {}
    for host in updated_hosts:
        remaining = await CasterClient(host, port).get_user_remaining_seconds(rover.username)
        if remaining is None:
            try:
                remaining = saved_rover_duration(rover)
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from None
        durations[host] = remaining
    return durations


async def _update_rover_on_caster(
    host, port, creator_username, identifiers, rover, update_req, values, durations
):
    caster = CasterClient(host, port)
    duration = (
        update_req.expiry_days * 86400
        if update_req.expiry_days is not None
        else durations[host]
    )
    created = await caster.create_user(
        creator=creator_username,
        username=rover.username,
        password=values["password"],
        mountpoints=values["mountpoints"],
        expiry_seconds=duration,
        max_users=values["max_connections"],
        show_position=values["show_position"],
        rtcm_cycle_enabled=bool(values["rtcm_cycle_enabled"]),
        rtcm_disconnect_interval_seconds=int(values["rtcm_cycle_interval"]),
        rtcm_disconnect_duration_seconds=int(values["rtcm_cycle_duration"]),
    )
    if not created:
        raise RuntimeError("Caster did not confirm account restore/update")
    for identifier in identifiers:
        toggled = await caster.toggle_show_pos(
            identifier=identifier,
            username=rover.username,
            enabled=values["show_position"],
        )
        if toggled:
            return
    logger.warning("Caster %s did not confirm show_position toggle for rover '%s'", host, rover.username)


async def _restore_rover_on_casters(updated_hosts, port, creator_username, identifiers, rover, update_req, values, durations):
    failed_hosts = []
    for host in updated_hosts:
        try:
            await _update_rover_on_caster(
                host, port, creator_username, identifiers, rover, update_req, values, durations
            )
        except Exception as error:
            logger.exception("Failed to update rover on caster %s: %s", host, error)
            failed_hosts.append(host)
    if failed_hosts:
        raise HTTPException(
            status_code=502,
            detail=f"Caster chưa xác nhận lưu Rover: {', '.join(failed_hosts)}. Cấu hình đã lưu được giữ nguyên; có thể thử lại.",
        )


async def _cleanup_removed_rover_hosts(removed_hosts, port, creator_username, rover):
    for host in removed_hosts:
        try:
            caster = CasterClient(host, port)
            deleted = await caster.delete_user(identifier=creator_username, username=rover.username)
            if not deleted:
                logger.warning("Caster %s did not confirm delete for rover '%s'", host, rover.username)
            logger.info("✓ Cleaned up (deleted) rover '%s' from removed caster %s", rover.username, host)
        except Exception as error:
            logger.warning("Failed to cleanup rover from removed caster %s: %s", host, error)


async def _sync_rover_casters(db, rover, update_req, current_user, values):
    live_hosts = await _live_rover_hosts(rover.username)
    old_hosts, updated_hosts = _validated_rover_hosts(rover, update_req, live_hosts)
    removed_hosts = _removed_rover_hosts(old_hosts, updated_hosts)
    port = int(settings.CASTER_PORT or 2101)
    creator = await crud.get_user_by_id(db, rover.created_by_user_id)
    creator_username = creator.username if creator else "admin"
    identifiers = []
    for identifier in (creator_username, current_user.username, "admin", "id"):
        normalized = str(identifier or "").strip()
        if normalized and normalized not in identifiers:
            identifiers.append(normalized)
    durations = await _rover_durations_by_host(rover, updated_hosts, update_req, port)
    await _restore_rover_on_casters(
        updated_hosts, port, creator_username, identifiers, rover, update_req, values, durations
    )
    await _cleanup_removed_rover_hosts(removed_hosts, port, creator_username, rover)
    return updated_hosts


def _rover_update_data(update_req, values, updated_hosts):
    data = {
        "password": values["password"],
        "mountpoints": values["mountpoints"],
        "max_connections": values["max_connections"],
        "show_position": values["show_position"],
        "rtcm_cycle_enabled": bool(values["rtcm_cycle_enabled"]),
        "rtcm_disconnect_interval_seconds": int(values["rtcm_cycle_interval"]),
        "rtcm_disconnect_duration_seconds": int(values["rtcm_cycle_duration"]),
        "target_casters": ";".join(updated_hosts),
    }
    if update_req.selected_serials:
        data["base_serial"] = ",".join(update_req.selected_serials)
    if update_req.is_shared_with_managers is not None:
        data["is_shared_with_managers"] = update_req.is_shared_with_managers
    if update_req.auto_save_sessions is not None:
        data["auto_save_sessions"] = update_req.auto_save_sessions
    if update_req.auto_save_fixed_points is not None:
        data["auto_save_fixed_points"] = update_req.auto_save_fixed_points
    if update_req.expiry_days is not None:
        data["expires_at"] = int(time.time()) + update_req.expiry_days * 86400
    return data


@router.put("/api/rovers/{rover_id}", response_model=schemas.RoverAccountResponse, responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 409: {"description": "Conflict"}, 502: {"description": "Bad Gateway"}})
async def update_rover(
    rover_id: int,
    update_req: schemas.RoverUpdate,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    rover = await crud.get_rover_by_id(db, rover_id)
    if not rover:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản Rover.")
    has_permission = await crud.check_rover_management_permission(rover, current_user, devices_db, db)
    if not has_permission:
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý Rover này.")
    await _validate_rover_update_scope(rover, update_req, devices_db, db, current_user)
    try:
        values = _rover_update_values(rover, update_req)
        updated_hosts = await _sync_rover_casters(db, rover, update_req, current_user, values)
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Caster Multi-Update Error: %s", error)
        raise HTTPException(status_code=502, detail=f"Lỗi cập nhật Caster: {str(error)}")
    update_data = _rover_update_data(update_req, values, updated_hosts)
    return await crud.update_rover_account(db, rover_id, update_data)

@router.delete("/api/rovers/{rover_id}", responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
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
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản Rover.")

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
        logger.info(f"✓ Đã xóa Rover '{rover_to_delete.username}' khỏi Caster qua TCP")
    except Exception as e:
        logger.warning(f"Failed to delete rover from Caster (might be already deleted): {e}")

    # 4. Xóa khỏi DB Local
    _ = await crud.delete_rover_account(db, rover_id=rover_id)
    
    return {"status": "deleted", "rover_id": rover_id}

@router.post("/api/devices/{serial}/auto-deploy-token", responses={400: {"description": "Bad Request"}})
async def auto_deploy_device_token(
    serial: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_LICENSE))
):
    if crud.is_blocked_device_serial(serial):
        raise HTTPException(status_code=400, detail="Blocked device serial")
    normalized_serial = str(serial).strip().upper()
    result = await db.execute(
        select(models.Device).where(func.upper(models.Device.serial) == normalized_serial)
    )
    device = result.scalars().first()
    if not device:
        # MQTT discovery can show a new Pi before it has a DB row. Explicit
        # admin token issuance registers it, as /api/license/pi already does.
        serial = str(serial).strip()
        if not crud.is_valid_serial(serial):
            raise HTTPException(status_code=400, detail="Invalid device serial")
        discovered = mqtt_handler.device_telemetry_cache.get(serial) or {}
        device = models.Device(
            serial=serial,
            name=discovered.get("name") or serial,
            status="awaiting_license",
            is_provisioned=False,
        )
        db.add(device)
    # Keep token compatibility checks and MQTT routing tied to the DB's
    # canonical serial, even when the URL differs only by letter casing.
    serial = device.serial
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

@router.post("/api/devices/{serial}/license-bypass", responses={404: {"description": "Not Found"}})
async def set_device_license_bypass(
    serial: str,
    payload: dict | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_LICENSE))
):
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail=DEVICE_NOT_FOUND)

    payload = payload or {}
    enabled = bool(payload.get("enabled"))
    system_info = dict(device.system_info or {})
    system_info["auth_license_bypass"] = enabled
    system_info["auth_license_bypass_by"] = current_user.username
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

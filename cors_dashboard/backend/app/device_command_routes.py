"""Station configuration, scoped locks and administrative commands."""

import logging
import time
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import APIRouter, Depends, HTTPException, Query
import uuid
import base64
import csv
import io
from fastapi.responses import StreamingResponse

from . import auth, crud, models, schemas
from .database import get_db, get_auth_db
from . import mqtt as mqtt_handler
from . import command_builder
from .command_dispatcher import send_command_to_pi
from .station_config import _device_service_config, _preserve_relay_config_metadata
from .station_host_lock import (
    check_host_lock,
    host_lock_snapshot,
    refresh_host_lock,
    locked_server_ids,
    supports_server_device_lock,
)
from .operations_routes import _ops_visible_devices, _device_stream_alerts, _ops_events_query
from .websocket import manager as ui_manager
from .station_runtime import (
    _get_ui_status_emit_lock,
    device_update_buffer,
    device_update_buffer_lock,
    latest_device_telemetry_cache,
    pending_ui_status_lock,
    pending_ui_status_updates,
)

logger = logging.getLogger("app.main")
from .mobile_base import require_editable_base

router = APIRouter(dependencies=[Depends(require_editable_base)])
DEVICE_NOT_FOUND = "Thiết bị không tồn tại"
tools_router = APIRouter(dependencies=[Depends(require_editable_base)])


@router.post("/api/devices/{serial}/configure-chip", responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 409: {"description": "Conflict"}, 500: {"description": "Internal Server Error"}, 503: {"description": "Service Unavailable"}})
async def configure_chip_endpoint(
    serial: str,
    config_request: schemas.Command,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_COORDINATES))
):
    # ==================== BƯỚC 1: KIỂM TRA THIẾT BỊ ====================
    device_in_db = await crud.get_device_by_serial(db, serial)
    if not device_in_db:
        raise HTTPException(
            status_code=404,
            detail="Device not found in database"
        )
    if current_user.role == auth.Role.COORDINATOR and device_in_db.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Trạm này chỉ được xem")

    # ==================== BƯỚC 2: PARSE PAYLOAD ====================
    payload = config_request.payload
    sensor_type = payload.get("sensor_type")
    params = payload.get("params", {})
    mode = payload.get("mode")
    method = params.get("base_setup_method")
    gnss_options = params.get("gnss_options") if isinstance(params, dict) else None

    # Mặc định kế thừa cấu hình RTCM/GNSS đã được admin thiết lập trên trạm nếu params không truyền
    existing_base_config = device_in_db.base_config if isinstance(device_in_db.base_config, dict) else {}
    if gnss_options is None and isinstance(existing_base_config, dict):
        gnss_options = existing_base_config.get("gnss_options")
    if gnss_options and isinstance(params, dict) and "gnss_options" not in params:
        params["gnss_options"] = gnss_options

    if not sensor_type or not mode:
        raise HTTPException(
            status_code=400,
            detail="Missing required fields: sensor_type or mode"
        )

    receiver_profile = None
    if sensor_type == 'Ublox' and mode == 'BASE' and method in ('FIXED_LLA', 'SURVEY_IN', 'AUTO_CORS'):
        live = mqtt_handler.device_telemetry_cache.get(serial) or {}
        capabilities = live.get('system_info') or device_in_db.system_info or {}
        try:
            receiver_profile = await command_builder.wait_for_ublox_profile(mqtt_handler.device_telemetry_cache, serial, capabilities)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    auto_cors_config = None
    if mode == 'BASE' and method == 'AUTO_CORS':
        if not (device_in_db.system_info or {}).get('auto_base_review_supported'):
            raise HTTPException(status_code=409, detail='Cập nhật agent để lấy và duyệt kết quả Auto Base trước khi áp dụng Fixed LLA.')
        from .routers.devices import _normalize_auto_cors_config
        try:
            auto_cors_config = _normalize_auto_cors_config(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ==================== BƯỚC 3: LƯU CẤU HÌNH VÀO DATABASE ====================
    try:
        if sensor_type:
            device_in_db.detected_chip_type = sensor_type
        if isinstance(params, dict) and params and method != "AUTO_CORS":
            normalized_params = crud.normalize_base_config(params)
            if normalized_params:
                if "gnss_options" not in normalized_params and "gnss_options" in existing_base_config:
                    normalized_params["gnss_options"] = existing_base_config["gnss_options"]
                elif gnss_options and "gnss_options" not in normalized_params:
                    normalized_params["gnss_options"] = gnss_options
                device_in_db.base_config = normalized_params
            else:
                current_base_config = device_in_db.base_config if isinstance(device_in_db.base_config, dict) else {}
                merged_base_config = dict(current_base_config)
                for key, value in params.items():
                    if key != "coords":
                        merged_base_config[key] = value
                if "gnss_options" not in merged_base_config and "gnss_options" in existing_base_config:
                    merged_base_config["gnss_options"] = existing_base_config["gnss_options"]
                elif gnss_options and "gnss_options" not in merged_base_config:
                    merged_base_config["gnss_options"] = gnss_options
                if merged_base_config:
                    device_in_db.base_config = merged_base_config
        device_in_db.is_synced = False

        await db.commit()
        await db.refresh(device_in_db)

        # 🔥 FIX: Invalidate sync cache để background task không check dữ liệu cũ
        mqtt_handler.invalidate_device_cache(serial)

        logging.info(f"✓ Saved base config for {serial} to database: Chip={sensor_type}, Method={method}, Marked as UNSYNCED")

        await ui_manager.broadcast({
            "type": "status_update",
            "data": schemas.Device.from_orm(device_in_db).model_dump()
        })

    except Exception as e:
        await db.rollback()
        logging.exception(f"❌ Failed to save config to DB for {serial}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save configuration to database: {str(e)}"
        )

    # ==================== BƯỚC 4 & 5: BUILD VÀ GỬI LỆNH XUỐNG AGENT ====================
    try:
        commands_to_send = []
        pi_command = None

        if mode == "BASE":
            if method == "AUTO_CORS":
                import os
                pi_command = {
                    "command": "TRIGGER_AUTO_BASE",
                    "payload": {
                        "review_only": True,
                        **auto_cors_config,
                        "timeout": 3600,
                        "samples": 60,
                        "sensor_type": sensor_type
                    },
                    "original_config": config_request.payload
                }
                logging.info(f"Built Auto Base Setup trigger for {serial}")
            elif method == "SURVEY_IN":
                survey_duration = params.get('survey_in_duration')
                survey_accuracy = params.get('survey_in_accuracy')

                if not survey_duration or not survey_accuracy:
                    raise ValueError("Missing survey_in parameters")

                commands_to_send = command_builder.build_base_survey_in_command(
                    sensor_type, survey_duration, survey_accuracy, gnss_options, receiver_profile=receiver_profile
                )
                logging.info(f"Built Survey-In commands for {serial}: {survey_duration}s, {survey_accuracy}m")
            elif method == "FIXED_LLA":
                coords = params.get('coords')
                if not coords or not all(k in coords for k in ['lat', 'lon', 'alt']):
                    raise ValueError("Missing or incomplete coordinates")

                accuracy = params.get('accuracy', 10.0)
                commands_to_send = command_builder.build_base_fixed_lla_command(
                    sensor_type, coords['lat'], coords['lon'], coords['alt'], accuracy, gnss_options,
                    altitude_reference=params.get('altitude_reference'), receiver_profile=receiver_profile
                )
                logging.info(f"Built Fixed LLA commands for {serial}: lat/lon/alt, acc={accuracy}m")
            else:
                raise ValueError(f"Unknown base setup method: {method}")
        else:
            raise ValueError(f"Unsupported mode: {mode}")

        if not pi_command:
            encoded_commands = [base64.b64encode(cmd).decode('ascii') for cmd in commands_to_send if cmd]
            if not encoded_commands:
                raise ValueError("No commands generated")

            pi_command = {
                "command": "EXECUTE_RAW_COMMANDS",
                "payload": {"commands_b64": encoded_commands, "receiver_profile": receiver_profile},
                "original_config": config_request.payload
            }

    except Exception as e:
        logging.exception(f"❌ Failed to build configuration commands for {serial}: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to build configuration commands: {str(e)}"
        )

    # ==================== BƯỚC 6: GỬI LỆNH ====================
    try:
        response = await send_command_to_pi(serial, pi_command)

        logging.info(f"✓ Sent configuration commands to {serial}")

        await crud.log_system_event(
            db=db,
            type="info",
            message=f"GNSS config updated for '{device_in_db.name}' by {current_user.username}",
            user_id=current_user.id,
            serial=serial
        )

        return {
            "status": "command_sent_waiting_sync",
            "channel": response['channel'],
            "commands_sent": len(commands_to_send) if not pi_command else 1,
            "message": "Configuration saved to database and sent to device. Waiting for device to apply and confirm sync status...",
            "is_synced": False
        }

    except HTTPException as http_exc:
        logging.warning(f"⚠️ Failed to send command to {serial}: {http_exc.detail}. Config auto-sync when online.")
        raise HTTPException(
            status_code=503,
            detail=f"Configuration saved to database successfully, but device is offline ({http_exc.detail}). Config will auto-sync when device comes back online."
        )
    except Exception as e:
        logging.exception(f"❌ Unexpected error sending command to {serial}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal error while sending command: {str(e)}"
        )


@router.post("/api/devices/{serial}/configure-service", responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}, 503: {"description": "Service Unavailable"}})
async def configure_service_endpoint(
    serial: str,
    payload: schemas.ServiceConfig,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user) # Thay require_permission để check thủ công
):
    device_in_db = await crud.get_device_for_update(db, serial)
    if not device_in_db:
        raise HTTPException(status_code=404, detail="Device not found in database")

    # 1. Kiểm tra quyền sở hữu (Nếu là COORDINATOR)
    if current_user.role == auth.Role.COORDINATOR and device_in_db.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý thiết bị nÃ y.")

    # 2. Kiểm tra MANAGE_STATION_MODE (Nếu thay đổi mode sleep/always-on toàn cục hoặc theo server)
    old_service_cfg = _device_service_config(device_in_db)
    incoming_config = payload.model_dump(exclude_unset=True)
    for key in schemas.service_password_fields(incoming_config):
        if incoming_config.get(key) in (None, ""):
            incoming_config.pop(key, None)
    check_host_lock(device_in_db, incoming_config, is_admin=current_user.role == auth.Role.ADMIN)
    old_on_demand = old_service_cfg.get("stream_on_demand", False)
    new_on_demand = incoming_config.get("stream_on_demand", old_on_demand)
    station_mode_changed = old_on_demand != new_on_demand or any(
        old_service_cfg.get(f"server{sid}_stream_on_demand") != incoming_config.get(
            f"server{sid}_stream_on_demand", old_service_cfg.get(f"server{sid}_stream_on_demand"))
        for sid in schemas.service_server_ids({**old_service_cfg, **incoming_config})
    )

    if station_mode_changed:
        if not auth.has_permission(current_user, auth.Permission.MANAGE_STATION_MODE):
             raise HTTPException(
                 status_code=403,
                 detail="Chỉ ADMIN mới có quyền thay đổi chế độ Sleep/Always-on."
             )

    # 3. Yêu cầu EDIT_SERVICE_CONFIG cho các config khác
    if not auth.has_permission(current_user, auth.Permission.EDIT_SERVICE_CONFIG):
         raise HTTPException(status_code=403, detail="Bạn không có quyền sửa cấu hình dịch vụ.")

    # --- BƯỚC 1: LƯU CẤU HÌNH "CHUẨN" VÀO DATABASE ---
    service_config_payload = _preserve_relay_config_metadata(
        serial, old_service_cfg, {**old_service_cfg, **incoming_config}
    )
    service_config_payload.pop('removed_servers', None)
    service_config_payload = schemas.remove_service_servers(service_config_payload, incoming_config.get('removed_servers', []))
    try:
        refresh_host_lock(device_in_db, service_config_payload)
        device_in_db.service_config = service_config_payload
        await db.commit()
        await db.refresh(device_in_db)

        # 🔥 FIX: Invalidate sync cache
        mqtt_handler.invalidate_device_cache(serial)

        logging.info(f"✓ Saved service_config for {serial} to database.")
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save service config to database.")

        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save service config to database.")

    # --- BƯỚC 2: GỬI LỆNH ĐẾN PI ---
    pi_command = {
        "command": "DEPLOY_SERVICE_CONFIG",
        "payload": schemas.service_config_deployment_payload(
            device_in_db.version, service_config_payload, incoming_config,
            mqtt_handler.device_sync_cache.get(f"{serial}_service_config"),
        )
    }
    try:
        response = await send_command_to_pi(serial, pi_command)
        await crud.log_system_event(
            db=db,
            type="info",
            message=f"Service config updated for '{device_in_db.name}' by {current_user.username}",
            user_id=current_user.id,
            serial=serial
        )
        return {"status": "service_config_sent_and_saved", "channel": response.get('channel', 'unknown'),
                "service_config": service_config_payload, "server_config_lock": device_in_db.server_config_lock}
    except HTTPException as e:
        raise HTTPException(status_code=503, detail=f"Config saved to DB, but failed to send command to Pi: {e.detail}")


@router.post("/api/devices/{serial}/configured-relay-power", responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 409: {"description": "Conflict"}, 503: {"description": "Service Unavailable"}})
async def set_configured_relay_power(
    serial: str,
    request: schemas.ConfiguredRelayPowerRequest,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail=DEVICE_NOT_FOUND)
    if not serial.strip().upper().startswith("RELAY_"):
        raise HTTPException(status_code=404, detail="Thiết bị không phải Relay cấu hình")
    if not auth.has_permission(current_user, auth.Permission.MANAGE_STATION_MODE):
        raise HTTPException(status_code=403, detail="Bạn không có quyền bật/tắt trạm Relay.")
    if current_user.role == auth.Role.COORDINATOR and device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý thiết bị này.")

    service_config = _device_service_config(device)
    agent_service_config = crud.normalize_service_config(
        mqtt_handler.device_sync_cache.get(f"{serial}_service_config")
    )
    supports_power_control = (
        service_config.get("agent_station_power_control") is True
        or agent_service_config.get("agent_station_power_control") is True
    )
    if not supports_power_control:
        raise HTTPException(status_code=409, detail="Relay chưa hỗ trợ điều khiển bật/tắt toàn trạm.")
    service_config["agent_station_power_control"] = True

    try:
        response = await send_command_to_pi(
            serial,
            {"command": "SET_CONFIGURED_RELAY_ACTIVE", "payload": {"active": request.active}},
        )
    except HTTPException as exc:
        raise HTTPException(status_code=503, detail=f"Không gửi được lệnh đến Relay: {exc.detail}")

    service_config["agent_station_enabled"] = request.active
    device.service_config = service_config
    await db.commit()
    await db.refresh(device)
    await ui_manager.broadcast({"type": "status_update", "data": schemas.Device.from_orm(device).model_dump()})
    await crud.log_system_event(
        db=db,
        type="info" if request.active else "warning",
        message=f"Relay '{device.name or serial}' {'enabled' if request.active else 'fully disabled'} by {current_user.username}",
        user_id=current_user.id,
        serial=serial,
    )
    return {"status": "command_sent", "active": request.active, "channel": response.get("channel", "unknown")}


@router.post("/api/devices/{serial}/reset", responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def reset_pi_device(
    serial: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # 1. Lấy thiết bị từ DB TRƯỚC để kiểm tra sự tồn tại
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail=DEVICE_NOT_FOUND)

    # 2. Phân quyền: Cần EDIT_SERVICE_CONFIG. Nếu là Coordinator thì phải là người sở hữu.
    if not auth.has_permission(current_user, auth.Permission.EDIT_SERVICE_CONFIG):
        raise HTTPException(status_code=403, detail="Bạn không có quyền khởi động lại thiết bị.")

    if current_user.role == auth.Role.COORDINATOR and device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Bạn không có quyền quản lý thiết bị này.")

    # 3. Gửi lệnh xuống thiết bị (Chỉ gửi khi đã qua cửa kiểm tra an ninh)
    try:
        await send_command_to_pi(serial, {"command": "REBOOT_DEVICE", "payload": {}})
    except Exception as e:
        # Log lỗi nhưng không chặn dòng chảy để cập nhật DB cho UI phản hồi
        logging.exception(f"Failed to send reboot command to {serial}: {e}")

    # 4. Cập nhật DB ngay lập tức
    device.status = "rebooting"

    await db.commit()

    # 🔥 FIX: Invalidate sync cache on reset
    mqtt_handler.invalidate_device_cache(serial)

    # 5. Broadcast để UI cập nhật ngay
    await ui_manager.broadcast({
        "type": "status_update",
        "data": schemas.Device.from_orm(device).model_dump()
    })

    logging.info(f"User '{current_user.username}' sent REBOOT command to Pi '{serial}'")
    await crud.log_system_event(
        db=db,
        type="warning",
        message=f"Device '{device.name}' rebooted by {current_user.username}",
        user_id=current_user.id,
        serial=serial
    )
    return {"status": "reboot_command_sent"}


@router.post("/api/devices/{serial}/hard-reset", responses={404: {"description": "Not Found"}})
async def hard_reset_pi_device(
    serial: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.DELETE_DEVICE))
):
    # 1. Lấy thiết bị từ DB TRƯỚC để kiểm tra sự tồn tại
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail=DEVICE_NOT_FOUND)

    # 3. Gửi lệnh xuống thiết bị (Chỉ gửi khi đã qua cửa kiểm tra an ninh)
    try:
        await send_command_to_pi(serial, {"command": "DELETE_DEVICE", "payload": {}})
    except Exception as e:
        # Log lỗi nhưng không chặn dòng chảy để cập nhật DB cho UI phản hồi
        logging.exception(f"Failed to send hard-reset command to {serial}: {e}")

    # 4. Cập nhật DB ngay lập tức
    device.status = "rebooting_for_reset"
    device.is_provisioned = False
    device.detected_chip_type = "UNKNOWN"
    device.base_config = {}
    device.service_config = {}
    device.ntrip_connected = False
    device.bps = 0

    await db.commit()

    # 🔥 FIX: Invalidate sync cache on reset
    mqtt_handler.invalidate_device_cache(serial)

    # 5. Broadcast để UI cập nhật ngay
    await ui_manager.broadcast({
        "type": "status_update",
        "data": schemas.Device.from_orm(device).model_dump()
    })

    logging.info(f"User '{current_user.username}' sent HARD-RESET command to Pi '{serial}'")
    await crud.log_system_event(
        db=db,
        type="warning",
        message=f"Device '{device.name}' hard-reset (factory reset) by {current_user.username}",
        user_id=current_user.id,
        serial=serial
    )
    return {"status": "hard_reset_command_sent"}


@router.delete("/api/devices/{serial}", responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def delete_device_from_list(
    serial: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.DELETE_DEVICE))
):
    device = await crud.get_device_by_serial(db, serial)

    if not device:
        raise HTTPException(status_code=404, detail="Trạm không tồn tại")

    # Kiểm tra quyền (Coordinator chỉ xóa được trạm của mình)
    if current_user.role == auth.Role.COORDINATOR:
        if device.user_id != current_user.id:
            raise HTTPException(
                status_code=403,
                detail="Bạn chỉ có thể xóa trạm được gán cho mình"
            )

    # Gửi lệnh xuống cho Pi tự xóa file config.json và license.key rồi reboot!
    try:
        await send_command_to_pi(serial, {"command": "DELETE_DEVICE"})
        logging.info(f"Gửi lệnh xóa thành công xuống Pi '{serial}'")
    except Exception as e:
        logging.warning(f"Không thể gửi lệnh xóa xuống Pi '{serial}' (có thể thiết bị đang tắt): {e}")

    # Xóa khỏi database
    _ = str(device.detected_chip_type or "").upper()
    # Serialize deletion with MQTT status writes. If a retained relay status
    # was already writing, it finishes first and this delete wins; if deletion
    # wins, the metadata tombstone is visible before later writes proceed.
    async with mqtt_handler.mqtt_db_write_lock:
        await db.delete(device)
        await db.commit()
        await mqtt_handler.mark_device_metadata_missing(serial)
        async with device_update_buffer_lock:
            retained_updates = [
                item for item in device_update_buffer
                if str((item or {}).get("serial") or "") != str(serial)
            ]
            device_update_buffer.clear()
            device_update_buffer.extend(retained_updates)

    # Thêm: Dọn dẹp in-memory MQTT cache để tránh thiết bị bị zombie hóa
    for cache in [
        mqtt_handler.device_write_cache,
        mqtt_handler.device_status_cache,
        mqtt_handler.device_sync_cache,
        mqtt_handler.device_name_cache,
        mqtt_handler.device_telemetry_cache,
        latest_device_telemetry_cache,
    ]:
        cache.pop(serial, None)

    # Xóa tất cả các keys liên quan đến serial (như config) trong cache đồng bộ
    for key in list(mqtt_handler.device_sync_cache.keys()):
        if str(key).startswith(f"{serial}_"):
            mqtt_handler.device_sync_cache.pop(key, None)

    # Serialize deletion with an already-extracted status broadcast for the
    # same station. If status started first, delete is sent last; if delete
    # started first, the retained status sees the tombstone and is dropped.
    async with _get_ui_status_emit_lock(serial):
        async with pending_ui_status_lock:
            pending_ui_status_updates.pop(serial, None)

        logging.info(f"User '{current_user.username}' deleted device '{serial}' from list (not reset)")

        # Broadcast để các client khác cập nhật UI
        await ui_manager.broadcast({
            "type": "device_deleted",
            "serial": serial
        })


    await crud.log_system_event(
        db=db,
        type="warning",
        message=f"Device '{device.name}' removed from list by {current_user.username}",
        user_id=current_user.id,
        serial=serial
    )

    return {"status": "deleted", "serial": serial}


@router.post("/api/devices/{serial}/lock", responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 409: {"description": "Conflict"}})
async def lock_device(
    serial: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
    policy: Optional[schemas.StationHostLockRequest] = None,
):
    if current_user.role != auth.Role.ADMIN:
        raise HTTPException(403, "Chỉ admin được khóa host")
    device = await crud.get_device_for_update(db, serial)
    if not device:
        raise HTTPException(404, "Device not found")
    config = dict(_device_service_config(device))
    locked = sorted(policy.locked_servers) if policy is not None else [1, 2]
    scoped = supports_server_device_lock(device)
    if locked and not scoped:
        raise HTTPException(409, "Cập nhật agent_universal.py trên trạm để hỗ trợ device_lock từng server, rồi thử lại.")
    # A lock is independent of service enable switches. An empty policy remains
    # pending until telemetry confirms unlock, so an offline unlock is retried.
    device.server_config_lock = {**host_lock_snapshot(config), "locked_servers": locked} if locked else {"locked_servers": []}
    device.is_synced = False
    await db.commit()
    mqtt_handler.invalidate_device_cache(serial)
    # Serialize delivery with edits/retries and reread the newest policy. A
    # concurrent admin request must not be overwritten by this older command.
    device = await crud.get_device_for_update(db, serial)
    if not device:
        raise HTTPException(404, "Device not found")
    locked = locked_server_ids(device.server_config_lock)
    scoped = supports_server_device_lock(device)
    config = dict(_device_service_config(device))
    delivered = True
    command = {"command": "LOCK_DEVICE", "payload": {"locked_servers": locked}} if scoped else {"command": "UNLOCK_DEVICE", "payload": {}}
    if locked and not scoped:
        delivered = False
    else:
        try:
            await send_command_to_pi(serial, command)
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
            delivered = False
        else:
            device.is_locked = False
    await db.commit()
    await db.refresh(device)
    await ui_manager.broadcast({"type": "status_update", "data": schemas.Device.from_orm(device).model_dump()})
    await crud.log_system_event(db=db, type="warning", message=f"Admin {current_user.username} set device_lock for {serial}; locked servers: {locked}", user_id=current_user.id, serial=serial)
    return {"status": "device_lock_saved", "delivered": delivered,
            "server_config_lock": device.server_config_lock, "service_config": config}


@router.post("/api/devices/{serial}/unlock")
async def unlock_device(
    serial: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS)),
):
    return await lock_device(serial, db, current_user, schemas.StationHostLockRequest(locked_servers=[]))


@router.get("/api/devices/export/csv")
async def export_devices_to_csv(
    db: AsyncSession = Depends(get_db),
    auth_db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EXPORT_DATA))
):
    stream = io.StringIO()
    writer = csv.writer(stream)

    headers = [
        # === THÔNG TIN CHUNG ===
        "serial",
        "name",
        "status",
        "timestamp",
        "human_readable_time",
        "total_bps",  # Tổng BPS (server1 + server2)
        "detected_chip_type",
        "assigned_coordinator",
        "ntrip_connected",

        # === CẤU HÌNH BASE STATION ===
        "base_setup_method",     # FIXED_LLA hoặc SURVEY_IN
        "base_lat",
        "base_lon",
        "base_alt",
        "base_accuracy_m",       # Accuracy (mét)

        # === NTRIP SERVER 1 ===
        "server1_enabled",
        "server1_ntrip_version",  # v1.0 hoặc v2.0
        "server1_host",
        "server1_port",
        "server1_mountpoint",
        "server1_username",
        "server1_bps",            # Traffic hiện tại (B/s)
        "server1_connected",      # True/False

        # === NTRIP SERVER 2 ===
        "server2_enabled",
        "server2_ntrip_version",
        "server2_host",
        "server2_port",
        "server2_mountpoint",
        "server2_username",
        "server2_bps",
        "server2_connected",

        # === RTCM CLIENT (ROVER MODE) ===
        "rtcm_client_enabled",
        "rtcm_server",
        "rtcm_port",
        "rtcm_mountpoint",
        "rtcm_username"
    ]

    writer.writerow(headers)

    # ===================================================================
    # LẤY DANH SÁCH THIẾT BỊ VÀ XỬ LÝ
    # ===================================================================
    devices = await crud.get_all_devices(db)

    # Prefetch coordinator usernames in one query to avoid N+1 lookups.
    user_ids = sorted({int(d.user_id) for d in devices if getattr(d, "user_id", None) is not None})
    coordinator_map = {}
    if user_ids:
        rs_users = await auth_db.execute(
            select(models.User.id, models.User.username).where(models.User.id.in_(user_ids))
        )
        coordinator_map = {int(row[0]): str(row[1]) for row in rs_users.all() if row and row[0] is not None}

    for device in devices:
        # === THÔNG TIN CHUNG ===
        human_time = time.strftime(
            '%Y-%m-%d %H:%M:%S',
            time.localtime(device.timestamp)
        ) if device.timestamp else "N/A"

        # Lấy tên Coordinator
        coordinator_name = ""
        if device.user_id:
            coordinator_name = coordinator_map.get(int(device.user_id), "")

        # === CẤU HÌNH BASE ===
        base_config = device.base_config if isinstance(device.base_config, dict) else {}
        coords = base_config.get('coords', {})
        if not isinstance(coords, dict):
            coords = {}
        base_method = base_config.get('base_setup_method', '')
        base_lat = coords.get('lat', base_config.get('lat', ''))
        base_lon = coords.get('lon', base_config.get('lon', ''))
        base_alt = coords.get('alt', base_config.get('alt', ''))
        base_accuracy = base_config.get('accuracy', base_config.get('acc', ''))

        # === CẤU HÌNH SERVICE ===
        service_config = device.service_config if isinstance(device.service_config, dict) else {}

        # --- SERVER 1 ---
        s1_enabled = service_config.get('server1_enabled', False)
        s1_version = service_config.get('ntrip_version1', 1)
        s1_host = service_config.get('serverhost1', service_config.get('host1', ''))
        s1_port = service_config.get('port1', service_config.get('serverport1', ''))
        s1_mountpoint = service_config.get('mountpoint1', service_config.get('mp1', ''))
        s1_username = service_config.get('username1', service_config.get('user1', 'source'))

        # --- SERVER 2 ---
        s2_enabled = service_config.get('server2_enabled', False)
        s2_version = service_config.get('ntrip_version2', 1)
        s2_host = service_config.get('serverhost2', service_config.get('host2', ''))
        s2_port = service_config.get('port2', service_config.get('serverport2', ''))
        s2_mountpoint = service_config.get('mountpoint2', service_config.get('mp2', ''))
        s2_username = service_config.get('username2', service_config.get('user2', 'source'))

        # --- RTCM CLIENT ---
        rtcm_enabled = service_config.get('rtcm_enabled', False)
        rtcm_server = service_config.get('rtcmserver1', '')
        rtcm_port = service_config.get('rtcmport1', '')
        rtcm_mountpoint = service_config.get('rtcmmountpoint1', '')
        rtcm_username = service_config.get('rtcmusername1', '')

        # === NTRIP STATUS (TRAFFIC HIỆN TẠI) ===
        ntrip_status = device.ntrip_status
        if not isinstance(ntrip_status, dict):
            ntrip_status = {}

        s1_status = ntrip_status.get('server1')
        if not isinstance(s1_status, dict):
            s1_status = {}

        s1_bps = int(s1_status.get('bps', 0) or 0)
        s1_connected = bool(s1_status.get('connected', False))
        s2_status = ntrip_status.get('server2')
        if not isinstance(s2_status, dict):
            s2_status = {}

        s2_bps = int(s2_status.get('bps', 0) or 0)
        s2_connected = bool(s2_status.get('connected', False))

        # Backward-compatible fallback for offline/legacy rows.
        if s1_bps == 0 and s2_bps == 0 and isinstance(device.ntrip_status, dict):
            s1_bps = int(device.ntrip_status.get('server1_bps', 0) or 0)
            s2_bps = int(device.ntrip_status.get('server2_bps', 0) or 0)
            s1_connected = s1_connected or (s1_bps > 0)
            s2_connected = s2_connected or (s2_bps > 0)

        total_bps = s1_bps + s2_bps

        # ===================================================================
        # TẠO HÀNG DỮ LIỆU
        # ===================================================================
        row = [
            # Thông tin chung
            device.serial,
            device.name,
            device.status,
            device.timestamp,
            human_time,
            total_bps,  # Tổng traffic
            device.detected_chip_type or "UNKNOWN",
            coordinator_name,
            device.ntrip_connected,

            # Base config
            base_method,
            base_lat,
            base_lon,
            base_alt,
            base_accuracy,

            # Server 1
            s1_enabled,
            f"v{s1_version}.0",  # Format: v1.0 hoặc v2.0
            s1_host,
            s1_port,
            s1_mountpoint,
            s1_username,
            s1_bps,
            s1_connected,

            # Server 2
            s2_enabled,
            f"v{s2_version}.0",
            s2_host,
            s2_port,
            s2_mountpoint,
            s2_username,
            s2_bps,
            s2_connected,

            # RTCM Client
            rtcm_enabled,
            rtcm_server,
            rtcm_port,
            rtcm_mountpoint,
            rtcm_username
        ]

        writer.writerow(row)

    # ===================================================================
    # TRẢ VỀ FILE CSV
    # ===================================================================
    response = StreamingResponse(
        iter([stream.getvalue()]),
        media_type="text/csv"
    )

    filename = f"cors_devices_full_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"

    return response


@tools_router.get("/api/devices/alerts")
async def get_device_alerts(
    serial: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    devices = await _ops_visible_devices(devices_db, auth_db, current_user)
    if serial:
        devices = [d for d in devices if d.serial == serial]
    now_ts = int(time.time())
    alerts = []
    for device in devices:
        alerts.extend(_device_stream_alerts(device, now_ts))
    if severity:
        alerts = [a for a in alerts if a.get("severity") == severity]
    if type:
        alerts = [a for a in alerts if a.get("type") == type]
    return {"timestamp": now_ts, "count": len(alerts), "items": alerts}


@tools_router.get("/api/devices/{serial}/history", responses={403: {"description": "Forbidden"}})
async def get_device_history(
    serial: str,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    visible = await _ops_visible_devices(devices_db, auth_db, current_user)
    visible_map = {d.serial: d for d in visible}
    if serial not in visible_map:
        raise HTTPException(status_code=403, detail="Device is outside your scope")
    events = await _ops_events_query(devices_db, limit=100, device_serial=serial)
    device = visible_map[serial]
    return {
        "serial": serial,
        "current": schemas.Device.from_orm(device).model_dump(),
        "events": events,
        "alerts": _device_stream_alerts(device, int(time.time())),
    }


@tools_router.post("/api/devices/bulk-action", responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}})
async def bulk_device_action(
    request: schemas.DeviceBulkActionRequest,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)),
):
    action = str(request.action or "").strip().lower()
    if action not in {"lock", "unlock", "reset"}:
        raise HTTPException(status_code=400, detail="action must be lock, unlock, or reset")
    if action in {"lock", "unlock"} and not auth.has_permission(current_user, auth.Permission.MANAGE_USERS):
        raise HTTPException(status_code=403, detail="Missing permission for lock/unlock")
    if action in {"lock", "unlock"} and current_user.role != auth.Role.ADMIN:
        raise HTTPException(status_code=403, detail="Only admin can lock or unlock stations")
    if action == "reset" and not auth.has_permission(current_user, auth.Permission.EDIT_SERVICE_CONFIG):
        raise HTTPException(status_code=403, detail="Missing permission for reset")

    visible = await _ops_visible_devices(devices_db, auth_db, current_user)
    visible_map = {d.serial: d for d in visible}
    results = []
    for serial in [s.strip() for s in request.serials if s.strip()]:
        device = visible_map.get(serial)
        if not device:
            results.append({"serial": serial, "status": "skipped", "detail": "outside scope or not found"})
            continue
        if current_user.role == auth.Role.COORDINATOR and device.user_id != current_user.id:
            results.append({"serial": serial, "status": "skipped", "detail": "view only station"})
            continue
        try:
            if action == "lock":
                response = await lock_device(serial, devices_db, current_user)
                status_text = "hosts_locked"
            elif action == "unlock":
                response = await unlock_device(serial, devices_db, current_user)
                status_text = "hosts_unlocked"
            else:
                response = await send_command_to_pi(serial, {"command": "REBOOT_DEVICE", "payload": {}})
                device.status = "rebooting"
                status_text = "reset"
            await devices_db.commit()
            await devices_db.refresh(device)
            await ui_manager.broadcast({"type": "status_update", "data": schemas.Device.from_orm(device).model_dump()})
            results.append({"serial": serial, "status": status_text, "channel": response.get("channel", "unknown")})
        except Exception as exc:
            results.append({"serial": serial, "status": "error", "detail": str(exc)})

    await crud.log_system_event(
        db=devices_db,
        type="warning" if action == "reset" else "info",
        message=f"{current_user.username} executed bulk device action '{action}' on {len(results)} station(s)",
        user_id=current_user.id,
    )
    return {"action": action, "results": results}


@tools_router.get("/api/device-config-templates")
async def get_device_config_templates(
    auth_db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_CONFIG)),
):
    settings_data = await crud.get_system_settings(auth_db)
    templates = settings_data.get("device_config_templates", [])
    if not isinstance(templates, list):
        templates = []
    if current_user.role == auth.Role.ADMIN:
        visible_templates = templates
    elif current_user.role == auth.Role.COORDINATOR:
        visible_templates = [
            item for item in templates
            if isinstance(item, dict) and item.get("created_by") == current_user.username
        ]
    else:
        visible_templates = []
    return {"items": visible_templates}


@tools_router.post("/api/device-config-templates")
async def create_device_config_template(
    request: schemas.DeviceConfigTemplateCreateRequest,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_SERVICE_CONFIG)),
):
    settings_data = await crud.get_system_settings(auth_db)
    templates = settings_data.get("device_config_templates", [])
    if not isinstance(templates, list):
        templates = []
    template = {
        "id": str(uuid.uuid4()),
        "name": request.name.strip(),
        "description": request.description or "",
        "base_config": request.base_config or {},
        "service_config": request.service_config or {},
        "created_by": current_user.username,
        "created_at": int(time.time()),
    }
    templates.append(template)
    await crud.update_system_settings(auth_db, {"device_config_templates": templates})
    await crud.log_system_event(
        db=devices_db,
        type="info",
        message=f"{current_user.username} created station config template '{template['name']}'",
        user_id=current_user.id,
    )
    return template


@tools_router.post("/api/devices/apply-template", responses={404: {"description": "Not Found"}})
async def apply_device_config_template(
    request: schemas.DeviceApplyTemplateRequest,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_SERVICE_CONFIG)),
):
    settings_data = await crud.get_system_settings(auth_db)
    templates = settings_data.get("device_config_templates", [])
    template = next((t for t in templates if str(t.get("id")) == request.template_id), None) if isinstance(templates, list) else None
    if template and current_user.role != auth.Role.ADMIN and template.get("created_by") != current_user.username:
        template = None
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    visible = await _ops_visible_devices(devices_db, auth_db, current_user)
    visible_map = {d.serial: d for d in visible}
    results = []
    for serial in [s.strip() for s in request.serials if s.strip()]:
        device = visible_map.get(serial)
        if not device:
            results.append({"serial": serial, "status": "skipped", "detail": "outside scope or not found"})
            continue
        if current_user.role == auth.Role.COORDINATOR and device.user_id != current_user.id:
            results.append({"serial": serial, "status": "skipped", "detail": "view only station"})
            continue
        device = await crud.get_device_for_update(devices_db, serial)
        if request.apply_service_config:
            try:
                check_host_lock(device, template.get("service_config") or {}, is_admin=current_user.role == auth.Role.ADMIN)
            except HTTPException as exc:
                results.append({"serial": serial, "status": "skipped", "detail": exc.detail})
                continue
        if request.apply_base_config and isinstance(template.get("base_config"), dict) and template.get("base_config"):
            device.base_config = template["base_config"]
        if request.apply_service_config and isinstance(template.get("service_config"), dict) and template.get("service_config"):
            device.service_config = _preserve_relay_config_metadata(
                serial, _device_service_config(device), {**_device_service_config(device), **template["service_config"]}
            )
        refresh_host_lock(device, device.service_config or {})
        device.is_synced = False
        results.append({"serial": serial, "status": "applied"})
    await devices_db.commit()

    for serial in [r["serial"] for r in results if r.get("status") == "applied"]:
        device = visible_map.get(serial)
        if device:
            await devices_db.refresh(device)
            await ui_manager.broadcast({"type": "status_update", "data": schemas.Device.from_orm(device).model_dump()})

    await crud.log_system_event(
        db=devices_db,
        type="info",
        message=f"{current_user.username} applied template '{template.get('name')}' to {len([r for r in results if r.get('status') == 'applied'])} station(s)",
        user_id=current_user.id,
    )
    return {"template": template, "results": results}


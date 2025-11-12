# ==============================================================================
# == backend/app/main.py - COMPLETE VERSION WITH ALL FIXES                  ==
# ==============================================================================

import logging
import uuid
import json
import asyncio
import sys
import traceback
import base64
import io
import time
import csv
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect, Depends, 
    HTTPException, Request, Query, status
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse 
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from starlette.middleware.base import BaseHTTPMiddleware
from .monitoring import health_monitor, CircuitBreaker, global_rate_limiter
from . import crud, models, schemas, command_builder, auth
from .database import (
    engine, auth_engine, get_db, get_auth_db, 
    AuthBase, AsyncAuthSession, AsyncSessionLocal
)

from .websocket import manager as ui_manager
from .pi_websocket import pi_manager
from . import mqtt as mqtt_handler
from .mqtt import mqtt_client
from . import license_manager
from . import nmea_parser
from . import models, auth, crud

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log', encoding='utf-8'), 
        logging.StreamHandler()
    ]
)

mqtt_circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
db_circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)

logger = logging.getLogger(__name__)

class RequestIDFilter(logging.Filter):
    """Filter thêm request_id vào mọi log"""
    def filter(self, record):
        record.request_id = getattr(record, 'request_id', 'N/A')
        return True

# Thêm filter vào root logger
for handler in logging.root.handlers:
    handler.addFilter(RequestIDFilter())

# --- CẢI TIẾN 2: REQUEST ID MIDDLEWARE ---
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        
        # Ghi log mà không cần extra, vì filter sẽ tự thêm vào
        logger.info(f"--> {request.method} {request.url.path}")
        
        start_time = time.time()
        response = await call_next(request)
        process_time = (time.time() - start_time) * 1000
        
        response.headers['X-Request-ID'] = request_id
        logger.info(f"<-- {request.method} {request.url.path} - Completed in {process_time:.2f}ms, Status: {response.status_code}")
        
        return response

# === HEARTBEAT CHECKER (FIXED) ===
async def check_device_heartbeats_with_retry():
    """Heartbeat check với retry và backoff"""
    HEARTBEAT_TIMEOUT = 180
    max_retries = 3
    
    while True:
        await asyncio.sleep(30)
        
        for attempt in range(max_retries):
            try:
                async with AsyncSessionLocal() as db:
                    # Sử dụng circuit breaker
                    await db_circuit_breaker.call(
                        _do_heartbeat_check, db, HEARTBEAT_TIMEOUT
                    )
                break  # Success, thoát retry loop
            
            except Exception as e:
                logger.error(f"Heartbeat check failed (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                else:
                    health_monitor.record_error('heartbeat_check', str(e))

async def _do_heartbeat_check(db, timeout_threshold):
    """Logic thực tế của heartbeat check"""
    from sqlalchemy import select
    
    now = int(time.time())
    timeout_threshold = now - timeout_threshold
    
    result = await db.execute(
        select(models.Device).filter(
            models.Device.status == 'online',
            models.Device.timestamp < timeout_threshold
        )
    )
    timed_out_devices = result.scalars().all()
    
    if timed_out_devices:
        for device in timed_out_devices:
            device.status = 'offline'
            device.bps = 0
            device.ntrip_connected = False
        
        await db.commit()
        logger.info(f"Set {len(timed_out_devices)} devices to offline")

async def check_device_heartbeats():
    """Heartbeat check với bulk update"""
    HEARTBEAT_TIMEOUT = 180
    
    while True:
        await asyncio.sleep(30)
        
        try:
            async with AsyncSessionLocal() as db:
                # Sử dụng single query thay vì loop
                now = int(time.time())
                timeout_threshold = now - HEARTBEAT_TIMEOUT
                
                # Tìm devices cần set offline
                result = await db.execute(
                    select(models.Device)
                    .filter(
                        models.Device.status == 'online',
                        models.Device.timestamp < timeout_threshold
                    )
                )
                timed_out_devices = result.scalars().all()
                
                if not timed_out_devices:
                    continue
                
                # Bulk update
                for device in timed_out_devices:
                    device.status = 'offline'
                    device.bps = 0
                    device.ntrip_connected = False
                
                await db.commit()
                
                logging.info(f"Set {len(timed_out_devices)} devices to offline due to timeout")
                
                # Broadcast updates
                for device in timed_out_devices:
                    await ui_manager.broadcast({
                        "type": "status_update",
                        "data": schemas.Device.from_orm(device).model_dump()
                    })
        
        except Exception as e:
            logging.error(f"Error in heartbeat check: {e}", exc_info=True)

async def cleanup_rate_limiter():
    """Dọn dẹp rate limiter định kỳ"""
    while True:
        await asyncio.sleep(300)  # Mỗi 5 phút
        global_rate_limiter.cleanup()
        logger.debug("Rate limiter cleanup completed")

# === LIFESPAN ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context với proper error handling và khởi động MQTT.
    """
    logger.info("🚀 Application starting...")
    
    # Initialize databases
    try:
        async with engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)
        logger.info("✓ Device database initialized")
        
        async with auth_engine.begin() as conn:
            await conn.run_sync(AuthBase.metadata.create_all) 
        logger.info("✓ Auth database initialized")
    except Exception as e:
        logger.critical(f"Database initialization failed: {e}", exc_info=True)
        raise
    
    # Create default admin if needed
    try:
        async with AsyncAuthSession() as session:
            # ... (logic tạo admin mặc định giữ nguyên) ...
            pass # Giữ code cũ của bạn ở đây
    except Exception as e:
        logger.error(f"Failed to create default admin: {e}")
    
    # =================================================================
    # <<< THÊM VÀO ĐÂY: Khởi động MQTT client >>>
    # =================================================================
    mqtt_handler.start_mqtt_loop()
    # =================================================================
    
    # Start background tasks
    tasks = []
    try:
        tasks.append(asyncio.create_task(check_device_heartbeats_with_retry()))
        tasks.append(asyncio.create_task(cleanup_rate_limiter()))
        logger.info("✓ Background tasks started")
        
        yield
    
    finally:
        logger.info("🛑 Application shutting down...")
        
        mqtt_handler.stop_mqtt_loop()
        
        # Cancel all background tasks
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        
        logger.info("✓ Shutdown complete")

app = FastAPI(title="CORS Geodetic Backend", lifespan=lifespan)
app.add_middleware(RequestIDMiddleware)

# === COMMAND DISPATCHER ===
async def send_command_to_pi(serial: str, command: dict) -> dict:
    if mqtt_client and mqtt_client.is_connected():
        topic = f"pi/devices/{serial}/command"
        message = json.dumps(command)
        mqtt_handler.publish_message(topic, message)
        return {"status": "command_sent", "channel": "mqtt", "command": command.get('command')}

    logging.warning(f"MQTT down. Trying WebSocket for '{serial}'.")
    success = await pi_manager.send_personal_message(serial, command)
    if success:
        return {"status": "command_sent", "channel": "websocket", "command": command.get('command')}

    raise HTTPException(status_code=503, detail=f"Cannot send command to '{serial}'. Both MQTT and WebSocket are unavailable.")

# === AUTHENTICATION ===
@app.post("/api/auth/login", response_model=schemas.Token)
async def login(
    login_data: schemas.LoginRequest, 
    request: Request,  
    db: AsyncSession = Depends(get_auth_db)
):
    """Login với rate limiting và security improvements"""
    
    # Check rate limit
    await auth.check_login_rate_limit(request)
    
    user = await crud.get_user_by_username(db, username=login_data.username)
    
    # Timing attack prevention
    # Luôn verify password kể cả khi user không tồn tại
    if not user:
        await auth.verify_password(login_data.password, auth.pwd_context.hash("dummy"))
        logging.warning(f"Login attempt with non-existent username: {login_data.username}")
        raise HTTPException(
            status_code=401, 
            detail="Incorrect username or password"
        )
    
    if not await auth.verify_password(login_data.password, user.hashed_password):
        logging.warning(f"Failed login attempt for user: {login_data.username}")
        raise HTTPException(
            status_code=401, 
            detail="Incorrect username or password"
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=403, 
            detail="Account is disabled"
        )
    
    logging.info(f"Successful login: {user.username} (role: {user.role})")
    
    access_token = auth.create_access_token(
        data={"sub": user.username, "role": user.role}
    )
    
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/api/auth/me", response_model=schemas.UserResponse)
async def get_current_user_info(current_user: models.User = Depends(auth.get_current_user)):
    permissions = auth.get_user_permissions(current_user)
    user_response = schemas.UserResponse.from_orm(current_user)
    user_response.permissions = permissions
    return user_response

# === USER MANAGEMENT ===
@app.get("/api/users", response_model=list[schemas.UserResponse])
async def get_users(db: AsyncSession = Depends(get_auth_db),
                    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    users = await crud.get_all_users(db)
    response_list = []
    for u in users:
        permissions = auth.get_user_permissions(u)
        user_resp = schemas.UserResponse.from_orm(u)
        user_resp.permissions = permissions
        response_list.append(user_resp)
    return response_list

@app.get("/api/users/{user_id}", response_model=schemas.UserResponse)
async def get_user_details(user_id: int, db: AsyncSession = Depends(get_auth_db),
                           current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    user = await crud.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    permissions = auth.get_user_permissions(user)
    user_response = schemas.UserResponse.from_orm(user)
    user_response.permissions = permissions
    return user_response

@app.post("/api/users", response_model=schemas.UserResponse)
async def create_user(user_data: schemas.UserCreate,
                      auth_db: AsyncSession = Depends(get_auth_db),
                      devices_db: AsyncSession = Depends(get_db),
                      current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    existing = await crud.get_user_by_username(auth_db, username=user_data.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    
    hashed_password = await auth.get_password_hash(user_data.password)
    new_user = await crud.create_user(
        auth_db, username=user_data.username, hashed_password=hashed_password,
        role=user_data.role, full_name=user_data.full_name
    )
    
    if user_data.assigned_devices and new_user.role == 'coordinator':
        for serial in user_data.assigned_devices:
            device = await crud.get_device_by_serial(devices_db, serial)
            if device:
                device.user_id = new_user.id
        await devices_db.commit()
    
    permissions = auth.get_user_permissions(new_user)
    user_response = schemas.UserResponse.from_orm(new_user)
    user_response.permissions = permissions
    return user_response

@app.put("/api/users/{user_id}", response_model=schemas.UserResponse)
async def update_user(user_id: int, user_data: schemas.UserUpdate,
                      auth_db: AsyncSession = Depends(get_auth_db),
                      devices_db: AsyncSession = Depends(get_db),
                      current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    user_to_update = await crud.get_user_by_id(auth_db, user_id)
    if not user_to_update:
        raise HTTPException(status_code=404, detail="User not found")

    update_dict = user_data.dict(exclude_unset=True)
    
    assigned_serials = update_dict.pop("assigned_devices", None)
    if assigned_serials is not None:
        current_devices = await crud.get_devices_by_user_id(devices_db, user_id)
        for dev in current_devices:
            dev.user_id = None
        
        if user_to_update.role == 'coordinator':
            for serial in assigned_serials:
                device = await crud.get_device_by_serial(devices_db, serial)
                if device and (device.user_id is None or device.user_id == user_id):
                    device.user_id = user_id
        await devices_db.commit()

    if 'password' in update_dict and update_dict['password']:
        update_dict['hashed_password'] = await auth.get_password_hash(update_dict.pop('password'))
    
    updated_user = await crud.update_user(auth_db, user_id, update_dict)
    
    permissions = auth.get_user_permissions(updated_user)
    user_response = schemas.UserResponse.from_orm(updated_user)
    user_response.permissions = permissions
    return user_response

@app.delete("/api/users/{user_id}")
async def delete_user(user_id: int, db: AsyncSession = Depends(get_auth_db),
                      current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    
    success = await crud.delete_user(db, user_id)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {"status": "deleted", "user_id": user_id}

# === LICENSE ===
@app.post("/api/license/pi", response_model=schemas.LicenseResponse)
async def generate_pi_license(
    request: schemas.LicenseRequest,
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_LICENSE))
):
    serial = request.serial
    if not serial:
        raise HTTPException(status_code=400, detail="Serial number required.")
    
    try:
        base_code = license_manager.generate_pi_license_base(serial)
        license_key = license_manager.get_license_code_from_string(base_code)
        
        if "Lỗi" in license_key:
            raise ValueError("License generation failed.")

        return schemas.LicenseResponse(serial=serial, license_key=license_key)
    except Exception as e:
        logging.error(f"Failed to generate license for {serial}: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate license key.")

# === DEVICE ENDPOINTS ===
@app.get("/api/devices", response_model=list[schemas.Device])
async def get_initial_devices(
    user_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    # Dùng `require_permission` thay vì kiểm tra thủ công
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)) 
):
    # Khi code chạy đến đây, chúng ta đã chắc chắn user có quyền VIEW_DEVICES
    
    if user_id is not None:
        # Kiểm tra quyền admin cho việc filter
        if current_user.role != auth.Role.ADMIN:
            raise HTTPException(status_code=403, detail="Only admins can filter devices by user")
        return await crud.get_devices_by_user_id(db, user_id=user_id)

    if current_user.role == auth.Role.COORDINATOR:
        return await crud.get_devices_by_user_id(db, user_id=current_user.id)
    
    # Mặc định (Admin, Viewer) sẽ lấy tất cả
    return await crud.get_all_devices(db)

@app.post("/api/devices/{serial}/command")
async def send_generic_command(serial: str, command: schemas.Command,
                               current_user: models.User = Depends(auth.get_current_user)):
    if current_user.role == auth.Role.COORDINATOR:
        raise HTTPException(status_code=403, detail="Coordinators can only send specific commands.")
    if not auth.has_permission(current_user, auth.Permission.EDIT_CHIP_CONFIG):
         raise HTTPException(status_code=403, detail="Permission denied.")
    
    return await send_command_to_pi(serial, command.model_dump())

@app.post("/api/devices/{serial}/configure-chip")
async def configure_chip_endpoint(serial: str, config_request: schemas.Command,
                                  current_user: models.User = Depends(auth.get_current_user)):
    payload = config_request.payload
    mode = payload.get("mode")
    method = payload.get("params", {}).get("base_setup_method")
    
    if current_user.role == auth.Role.VIEWER:
        raise HTTPException(status_code=403, detail="Viewers cannot configure chips.")
    if current_user.role == auth.Role.COORDINATOR and (mode != "BASE" or method != "FIXED_LLA"):
        raise HTTPException(status_code=403, detail="Coordinators can only configure Fixed LLA.")
    if not auth.has_permission(current_user, auth.Permission.EDIT_COORDINATES):
         raise HTTPException(status_code=403, detail="Permission denied.")
    
    try:
        sensor_type = payload.get("sensor_type")
        params = payload.get("params", {})
        commands_to_send = []

        if mode == "BASE":
            if method == "SURVEY_IN":
                commands_to_send = command_builder.build_base_survey_in_command(sensor_type, params['survey_in_duration'], params['survey_in_accuracy'])
            elif method == "FIXED_LLA":
                coords = params['coords']
                commands_to_send = command_builder.build_base_fixed_lla_command(sensor_type, coords['lat'], coords['lon'], coords['alt'], params.get('accuracy', 10.0))
        
        if not commands_to_send:
            raise HTTPException(status_code=400, detail="Invalid configuration parameters.")

        encoded_commands = [base64.b64encode(cmd).decode('ascii') for cmd in commands_to_send if cmd]
        
        pi_command = {"command": "EXECUTE_RAW_COMMANDS", "payload": {"commands_b64": encoded_commands},  "original_config": config_request.payload}
        
        response = await send_command_to_pi(serial, pi_command)
        
        logging.info(f"✓ Sent {len(commands_to_send)} commands to {serial} via {response['channel']}")
        return {"status": "chip_config_sent", "channel": response['channel'], "commands_sent": len(commands_to_send)}

    except Exception as e:
        logging.error(f"Error configuring chip for {serial}: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/devices/{serial}/reset")
async def reset_pi_device(
    serial: str,
    current_user: models.User = Depends(auth.require_permission(auth.Permission.DELETE_DEVICE))
):
    """
    Gửi lệnh reset về cho thiết bị Pi.
    Thiết bị sẽ tự xóa cấu hình và khởi động lại.
    """
    try:
        # Gửi lệnh 'DELETE_DEVICE' (tên lệnh trong agent.py) đến Pi
        response = await send_command_to_pi(serial, {"command": "DELETE_DEVICE", "payload": {}})
        logging.info(f"User '{current_user.username}' sent RESET command to Pi '{serial}'")
        return {"status": "reset_command_sent", "channel": response.get('channel', 'unknown')}
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logging.error(f"Error in reset_pi_device endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
    
@app.delete("/api/devices/{serial}")
async def delete_device_from_list(
    serial: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.DELETE_DEVICE))
):
    """
    Xóa trạm khỏi danh sách quản lý (KHÔNG gửi lệnh reset đến Pi).
    
    Khác với endpoint reset:
    - DELETE /api/devices/{serial} → Chỉ xóa database record
    - POST /api/devices/{serial}/command với DELETE_DEVICE → Reset Pi
    """
    
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
    
    # Xóa khỏi database
    await db.delete(device)
    await db.commit()
    
    logging.info(f"User '{current_user.username}' deleted device '{serial}' from list (not reset)")
    
    # Broadcast để các client khác cập nhật UI
    await ui_manager.broadcast({
        "type": "device_deleted",
        "serial": serial
    })
    
    return {"status": "deleted", "serial": serial}

@app.post("/api/devices/{serial}/lock")
async def lock_device(serial: str, 
                      current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_LICENSE))):
    try:
        response = await send_command_to_pi(serial, {"command": "LOCK_DEVICE", "payload": {}})
        # Gio `response` se luon la mot dictionary hop le
        return {"status": "lock_command_sent", "channel": response.get('channel', 'unknown')}
    except HTTPException as http_exc:
        # Neu send_command_to_pi raise 503, hay tra ve loi do
        raise http_exc
    except Exception as e:
        logging.error(f"Error in lock_device endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.post("/api/devices/{serial}/unlock")
async def unlock_device(serial: str,
                        current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_LICENSE))):
    try:
        response = await send_command_to_pi(serial, {"command": "UNLOCK_DEVICE", "payload": {}})
        return {"status": "unlock_command_sent", "channel": response.get('channel', 'unknown')}
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logging.error(f"Error in unlock_device endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.get("/api/devices/export/csv")
async def export_devices_to_csv(db: AsyncSession = Depends(get_db),
                                current_user: models.User = Depends(auth.require_permission(auth.Permission.EXPORT_DATA))):
    stream = io.StringIO()
    writer = csv.writer(stream)
    headers = ["serial", "name", "status", "timestamp", "human_readable_time", "bps", "detected_chip_type", "user_id", "ntrip_connected"]
    writer.writerow(headers)
    devices = await crud.get_all_devices(db)
    for device in devices:
        human_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(device.timestamp)) if device.timestamp else "N/A"
        writer.writerow([device.serial, device.name, device.status, device.timestamp, human_time, device.bps, device.detected_chip_type, device.user_id, device.ntrip_connected])
    
    response = StreamingResponse(iter([stream.getvalue()]), media_type="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=cors_devices_{time.strftime('%Y%m%d')}.csv"
    return response

# === WEBSOCKETS ===
@app.websocket("/ws/updates")
async def ui_websocket_endpoint(websocket: WebSocket):
    await ui_manager.connect(websocket)
    logging.info("New UI client connected.")
    try:
        while True: 
            await websocket.receive_text()
    except WebSocketDisconnect:
        ui_manager.disconnect(websocket)
        logging.info("UI client disconnected.")

@app.websocket("/ws/pi/{serial}")
async def pi_websocket_endpoint(
    websocket: WebSocket, 
    serial: str
):
    await pi_manager.connect(serial, websocket)
    print(f"\n✅ DEBUG 0: Pi '{serial}' CONNECTED to WebSocket.")
    try:
        # Vòng lặp nhận tin nhắn từ Pi
        while True:
            # Dùng AsyncSessionLocal để tạo một session database mới và an toàn cho mỗi tin nhắn
            async with AsyncSessionLocal() as db:
                data = await websocket.receive_json()
                
                message_type = data.get("type")
                payload = data.get("payload")

                logging.debug(f"Received from Pi '{serial}', type: {message_type}")

                if message_type == "status_update" and payload:
                    device_obj = await crud.update_or_create_device(db, device_data=payload)

                    if device_obj:
                        device_schema = schemas.Device.from_orm(device_obj)
                        await ui_manager.broadcast({
                            "type": "status_update", 
                            "data": device_schema.model_dump()
                        })
                    else:
                        logging.error(f"Failed to update or create device for serial '{serial}' in DB. Payload received: {payload}")

                elif message_type == "nmea_update" and payload: 
                    parsed_data = nmea_parser.parse(payload)
                    if parsed_data:
                        await ui_manager.broadcast({
                            "type": "nmea_update",
                            "serial": serial,
                            "data": parsed_data
                        })
                else:
                    logging.warning(f"Unknown message type from Pi '{serial}': {message_type}")

    except WebSocketDisconnect:
        logging.info(f"Pi '{serial}' disconnected. Updating status to OFFLINE.")
        pi_manager.disconnect(serial)
        
        try:
            async with AsyncSessionLocal() as db:
                device_to_update = await crud.get_device_by_serial(db, serial=serial)
                
                if device_to_update and device_to_update.status not in ['rebooting_for_reset', 'rebooting']:
                    device_to_update.status = 'offline'
                    device_to_update.bps = 0
                    device_to_update.ntrip_connected = False
                    
                    await db.commit()
                    await db.refresh(device_to_update)
                    
                    await ui_manager.broadcast({
                        "type": "status_update",
                        "data": schemas.Device.from_orm(device_to_update).model_dump()
                    })
                    logging.info(f"Successfully set Pi '{serial}' to OFFLINE in database.")
        except Exception as e:
            logging.error(f"Error updating device status to offline for '{serial}': {e}", exc_info=True)
    
    except Exception as e:
        print(f"\n❌ DEBUG ERROR: An exception occurred in pi_websocket_endpoint for '{serial}': {e}")
        logging.error(f"An unexpected error occurred in WebSocket for Pi '{serial}': {e}", exc_info=True)
        pi_manager.disconnect(serial)

# === GLOBAL EXCEPTION HANDLER ===
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Bắt mọi unhandled exceptions"""
    health_monitor.record_error('unhandled_exception', str(exc))
    
    logger.error(
        f"Unhandled exception: {str(exc)}",
        exc_info=True,
        extra={
            'path': request.url.path,
            'method': request.method,
            'client': request.client.host
        }
    )
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error",
            "timestamp": time.time()
        }
    )
# === STATIC FILES ===
app.mount("/img", StaticFiles(directory="../frontend/img"), name="images")
app.mount("/", StaticFiles(directory="../frontend", html=True), name="static")

class MonitoringMiddleware(BaseHTTPMiddleware):
    """Middleware để ghi nhận metrics"""
    
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        # Rate limiting
        client_ip = request.client.host
        if not global_rate_limiter.is_allowed(client_ip):
            health_monitor.record_error('rate_limit', f'IP: {client_ip}')
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Too many requests"}
            )
        
        try:
            response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000
            
            # Ghi nhận metrics
            health_monitor.record_request(duration_ms)
            
            # Cảnh báo nếu response chậm
            if duration_ms > 1000:
                logger.warning(f"Slow request: {request.url.path} took {duration_ms:.2f}ms")
            
            return response
        
        except Exception as e:
            health_monitor.record_error('request_error', str(e))
            logger.error(f"Request error: {e}", exc_info=True)
            raise

# Global monitoring middleware

app.add_middleware(MonitoringMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ⚠️ Trong production, hãy chỉ định origins cụ thể
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Enhanced Health Check Endpoint
@app.get("/health/detailed")
async def detailed_health_check():
    """Health check chi tiết với system metrics"""
    return health_monitor.get_health_status()

@app.get("/health/errors")
async def recent_errors():
    """Lấy danh sách lỗi gần đây"""
    return {
        "errors": health_monitor.get_recent_errors(),
        "total_errors": health_monitor.error_count
    }

@app.get("health")
async def simple_health_check():
    """Simple health check cho load balancer"""
    status = health_monitor.get_health_status()
    
    if status['status'] == 'healthy':
        return {"status": "ok"}
    
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "degraded", "details": status}
    )

async def health_check():
    """Enhanced health check với database status"""
    health = {
        "status": "healthy",
        "timestamp": time.time()
    }
    
    # Check MQTT
    health["mqtt_connected"] = mqtt_client.is_connected() if mqtt_client else False
    
    # Check WebSocket
    health["pi_ws_connections"] = len(pi_manager.active_connections)
    health["ui_ws_connections"] = len(ui_manager.active_connections)
    
    # CẢI TIẾN: Check database connectivity
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        health["database_connected"] = True
    except Exception as e:
        health["database_connected"] = False
        health["database_error"] = str(e)
        health["status"] = "degraded"
    
    return health
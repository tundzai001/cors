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
import os
import time
import csv
import psutil
import secrets
from contextlib import asynccontextmanager
from typing import Optional, List, Any

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect, Depends, 
    HTTPException, Request, Query, status
)
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select, update
from starlette.middleware.base import BaseHTTPMiddleware
from .monitoring import health_monitor, CircuitBreaker, global_rate_limiter
from . import crud, models, schemas, command_builder, auth
from starlette.responses import Response
from .database import (
    engine, auth_engine, get_db, get_auth_db, 
    AuthBase, AsyncAuthSession, AsyncSessionLocal, settings
)

from .websocket import manager as ui_manager, visitor_manager
from .pi_websocket import pi_manager
from . import mqtt as mqtt_handler
from .mqtt import mqtt_client
from . import license_manager
from . import nmea_parser
from . import models, auth, crud
from .security import rate_limiter
from .monitoring import health_monitor
from .caster_client import CasterClient
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

BATCH_SIZE = 20  
BATCH_TIMEOUT = 2.0 

mqtt_circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
db_circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)

logger = logging.getLogger(__name__)
device_update_buffer = []

async def log_system_event(
    db: AsyncSession,
    type: str,     # success, error, warning, info
    message: str,
    user_id: int = None,
    serial: str = None,
    ip: str = None
):
    """Hàm helper để ghi log vào DB từ bất kỳ đâu"""
    icon_map = {
        "success": "✅",
        "error": "❌",
        "warning": "⚠️",
        "info": "ℹ️"
    }
    try:
        log_entry = schemas.ActivityLogCreate(
            timestamp=int(time.time()),
            type=type,
            message=message,
            icon=icon_map.get(type, "📝"),
            user_id=user_id,
            device_serial=serial,
            ip_address=ip
        )
        await crud.create_activity_log(db, log_entry)
        
        await ui_manager.broadcast({
            "type": "activity_log_event",
            "data": {
                "type": type,
                "message": message,
                "icon": icon_map.get(type, "📝"),
                "timestamp": int(time.time())
            }
        })
    except Exception as e:
        logger.error(f"Failed to write activity log: {e}")

class IgnoreSocketErrorsFilter(logging.Filter):
    def filter(self, record):
        # Chặn các log warning của asyncio liên quan đến socket.send
        if "socket.send() raised exception" in record.getMessage():
            return False
        return True

# Áp dụng filter cho logger của asyncio
logging.getLogger("asyncio").addFilter(IgnoreSocketErrorsFilter())

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
    HEARTBEAT_TIMEOUT = 300 
    max_retries = 3
    
    while True:
        await asyncio.sleep(60) # Check mỗi 60s là đủ, không cần 30s
        
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
            device.ntrip_status = {
                "server1": {"connected": False, "bps": 0},
                "server2": {"connected": False, "bps": 0}
            }
        
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
async def periodic_buffer_flush():
    """Chạy ngầm mỗi 1.0 giây để ghi DB"""
    while True:
        await asyncio.sleep(1.0)
        if device_update_buffer:
            try:
                # Mở ĐÚNG 1 KẾT NỐI DB để lưu tất cả updates
                async with AsyncSessionLocal() as db:
                    await flush_device_buffer(db)
            except Exception as e:
                logger.error(f"Flush task error: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context với proper error handling, khởi động MQTT và Rate Limiter.
    """
    logger.info("🚀 Application starting...")
    
    # List để track tất cả background tasks
    tasks = []
    
    try:
        # ===================================================================
        # BƯỚC 1: INITIALIZE DATABASES
        # ===================================================================
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
        
        # ===================================================================
        # BƯỚC 2: CREATE DEFAULT ADMIN USER
        # ===================================================================
        try:
            async with AsyncAuthSession() as db_session:
                admin_user = await crud.get_user_by_username(db_session, username="admin")
                if not admin_user:
                    logger.warning("Không tìm thấy người dùng 'admin' mặc định. Đang tiến hành tạo...")
                    hashed_password = await auth.get_password_hash("Admin@123")
                    await crud.create_user(
                        db=db_session,
                        username="admin",
                        hashed_password=hashed_password,
                        role=auth.Role.ADMIN,
                        full_name="Administrator"
                    )
                    logger.info("✓ Đã tạo người dùng 'admin' mặc định thành công.")
        except Exception as e:
            logger.error(f"Failed to create default admin: {e}")
        
        # ===================================================================
        # BƯỚC 2.5: CLEANUP GHOST DEVICES (optional)
        # ===================================================================
        try:
            async with AsyncSessionLocal() as db:
                deleted_count = await crud.cleanup_ghost_devices(db)
                if deleted_count > 0:
                    logger.info(f"🗑️ Cleaned up {deleted_count} ghost devices on startup")
        except Exception as e:
            logger.warning(f"Ghost device cleanup failed (non-critical): {e}")
        
        # ===================================================================
        # BƯỚC 3: START MQTT
        # ===================================================================
        mqtt_handler.start_mqtt_loop()
        
        # ===================================================================
        # BƯỚC 4: START BACKGROUND TASKS
        # ===================================================================
        
        # Task 1: Check device heartbeats
        async def check_device_heartbeats_with_retry():
            """Heartbeat check với retry và backoff"""
            from .database import AsyncSessionLocal
            from . import schemas
            from .websocket import manager as ui_manager
            
            HEARTBEAT_TIMEOUT = 180
            max_retries = 3
            
            while True:
                await asyncio.sleep(30)
                
                for attempt in range(max_retries):
                    try:
                        async with AsyncSessionLocal() as db:
                            now = int(time.time())
                            timeout_threshold = now - HEARTBEAT_TIMEOUT
                            
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
                                
                                # Broadcast updates
                                for device in timed_out_devices:
                                    await ui_manager.broadcast({
                                        "type": "status_update",
                                        "data": schemas.Device.from_orm(device).model_dump()
                                    })
                        
                        break  # Success, thoát retry loop
                    
                    except Exception as e:
                        logger.error(f"Heartbeat check failed (attempt {attempt+1}/{max_retries}): {e}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 ** attempt) 
        
        # Task 2: Global rate limiter cleanup (nếu có)
        async def global_rate_limiter_cleanup_task():
            """Dọn dẹp global rate limiter định kỳ"""
            from .monitoring import global_rate_limiter
            while True:
                await asyncio.sleep(300)  # Mỗi 5 phút
                try:
                    global_rate_limiter.cleanup()
                    logger.debug("Global rate limiter cleanup completed")
                except Exception as e:
                    logger.error(f"Global rate limiter cleanup error: {e}")

        # Task 3: Security rate limiter cleanup - THÊM MỚI
        async def security_rate_limiter_cleanup_task():
            """Dọn dẹp dữ liệu cũ trong security rate limiter"""
            from .security import rate_limiter
            while True:
                await asyncio.sleep(900)  # Mỗi 15 phút
                try:
                    rate_limiter.cleanup_stale_data()
                    logger.debug("Security rate limiter cleanup completed")
                except Exception as e:
                    logger.error(f"Security rate limiter cleanup error: {e}")
        
        # Tạo và track tất cả tasks
        tasks.append(asyncio.create_task(check_device_heartbeats_with_retry()))
        tasks.append(asyncio.create_task(global_rate_limiter_cleanup_task()))
        tasks.append(asyncio.create_task(security_rate_limiter_cleanup_task()))
        tasks.append(asyncio.create_task(periodic_buffer_flush()))
        logger.info("✓ Background tasks started")
        logger.info("=" * 60)
        logger.info("🎉 Application startup complete!")
        logger.info("=" * 60)
        
        yield
        
    finally:
        logger.info("🛑 Application shutting down...")
        
        # --- BƯỚC 1: DỪNG MQTT TRƯỚC (Ngắt nguồn dữ liệu vào) ---
        try:
            mqtt_handler.stop_mqtt_loop()
            logger.info("✓ MQTT stopped")
            await asyncio.sleep(1.0) 
        except Exception as e:
            logger.error(f"Error stopping MQTT: {e}")
        
        # --- BƯỚC 2: HỦY CÁC BACKGROUND TASKS ---
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info(f"✓ Cancelled {len(tasks)} background tasks")
        
        # --- BƯỚC 3: ĐÓNG KẾT NỐI DATABASE (QUAN TRỌNG NHẤT) ---
        try:
            logger.info("Setting all devices to OFFLINE...")
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(models.Device)
                    .where(models.Device.status == 'online')
                    .values(
                        status='offline',
                        ntrip_connected=False,
                        bps=0
                    )
                )
                await db.commit()
            logger.info("✓ All devices marked as OFFLINE")
        except Exception as e:
            logger.error(f"Error setting devices offline: {e}")

        # 4. ĐÓNG KẾT NỐI DATABASE (Cuối cùng)
        try:
            logger.info("Closing database connections...")
            await engine.dispose()
            await auth_engine.dispose()
            logger.info("✓ Database connections closed gracefully")
        except Exception as e:
            logger.error(f"Error closing databases: {e}")
            
        logger.info("✅ Shutdown sequence complete")

app = FastAPI(title="CORS Geodetic Backend", lifespan=lifespan, version="0.1.0", docs_url=None, openapi_url=None)
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

# --- CASTER CLIENT FACTORY ---
async def get_caster_client(db: AsyncSession) -> CasterClient:
    """Helper để khởi tạo Caster Client từ SystemSettings"""
    settings = await crud.get_system_settings(db)
    
    # Lấy config hoặc dùng default giống file config.json cũ
    host = settings.get('caster_host', 'aitogy.click') 
    port = int(settings.get('caster_port', 2101))
    
    return CasterClient(host, port)

# === 🆕 WEBSOCKET THEO DÕI KHÁCH TRUY CẬP (PUBLIC) ===
@app.websocket("/ws/track-visitors")
async def track_visitors_endpoint(websocket: WebSocket):
    client_ip = websocket.client.host
    await visitor_manager.connect(websocket, client_ip)
    
    await ui_manager.broadcast({
        "type": "visitor_update",
        "count": visitor_manager.get_count(),
        "ips": list(visitor_manager.active_ips) # Gửi danh sách IP để Admin soi (tùy chọn)
    })
    
    try:
        while True:
            # Giữ kết nối sống, nhận ping pong
            await websocket.receive_text()
    except:
        # Khi ngắt kết nối
        visitor_manager.disconnect(websocket, client_ip)
        # 📢 Báo lại cho Admin khi có người thoát
        await ui_manager.broadcast({
            "type": "visitor_update",
            "count": visitor_manager.get_count(),
            "ips": list(visitor_manager.active_ips)
        })

def ensure_device_ownership(current_user: models.User, device: models.Device):
    """
    Chặn IDOR: Kiểm tra xem user có sở hữu thiết bị này không.
    - Admin: Luôn được phép.
    - Coordinator: Phải là người được gán (user_id khớp).
    - Viewer: Thường chỉ xem, nhưng nếu có quyền sửa thì cũng phải check sở hữu (tùy logic).
    """
    # Admin có quyền tối thượng
    if current_user.role == auth.Role.ADMIN:
        return

    # Nếu là Coordinator (hoặc role khác), phải là chủ sở hữu
    if device.user_id != current_user.id:
        logging.warning(f"IDOR DETECTED: User {current_user.username} tried to access device {device.serial} owned by {device.user_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Bạn không có quyền thao tác trên thiết bị này (Không phải chủ sở hữu)."
        ) 
# === AUTHENTICATION ===
@app.post("/api/auth/login", response_model=schemas.Token)
async def login(
    login_data: schemas.LoginRequest, 
    request: Request,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db)  
):
    user = await crud.get_user_by_username(auth_db, username=login_data.username)
    user_role = user.role if user else None
    
    try:
        await rate_limiter.check_and_record(
            auth_db,
            request, 
            username=login_data.username,
            is_success=False,
            user_role=user_role
        )
    except HTTPException as e:
        raise e
    
    user = await crud.get_user_by_username(auth_db, username=login_data.username)
    
    if not user:
        await auth.verify_password(
            login_data.password, 
            auth.pwd_context.hash("dummy_password_12345")
        )
        logger.warning(
            f"❌ Login failed: Unknown user '{login_data.username}' "
            f"from {request.client.host}"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    if not await auth.verify_password(login_data.password, user.hashed_password):
        logger.warning(
            f"❌ Login failed: Wrong password for '{login_data.username}' "
            f"from {request.client.host}"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    if not user.is_active:
        logger.warning(
            f"❌ Login failed: Inactive account '{login_data.username}'"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled"
        )
    
    logger.info(
        f"✅ Successful login: '{user.username}' (role: {user.role}) "
        f"from {request.client.host}"
    )
    
    await rate_limiter.check_and_record(
        auth_db,  
        request, 
        username=user.username,
        is_success=True
    )
    
    access_token = auth.create_access_token(
        data={"sub": user.username, "role": user.role}
    )

    await log_system_event(
        db=devices_db,
        type="success",
        message=f"User '{user.username}' logged in",
        user_id=user.id,
        ip=request.client.host
    )

    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/api/public/map-stations")
async def get_public_map_stations(db: AsyncSession = Depends(get_db)):
    try:
        # Lấy tất cả thiết bị
        result = await db.execute(select(models.Device))
        devices = result.scalars().all()
        
        public_data = []
        
        for dev in devices:
            base_config = dev.base_config or {}
            
            # === TRY FORMAT 1: NESTED (Trạm thật) ===
            coords = base_config.get('coords', {})
            lat = coords.get('lat')
            lon = coords.get('lon')
            
            # === TRY FORMAT 2: FLAT (Relay/Virtual) ===
            if not lat or not lon:
                lat = base_config.get('lat')
                lon = base_config.get('lon')
            
            # Chỉ thêm vào danh sách nếu có đủ tọa độ hợp lệ
            if lat and lon:
                try:
                    public_data.append({
                        "name": dev.name,
                        "status": dev.status,
                        "lat": float(lat),
                        "lon": float(lon)
                    })
                except (ValueError, TypeError) as e:
                    logging.warning(f"⚠️ Invalid coordinates for {dev.name}: {e}")
                    continue
        
        logging.info(f"✓ Public map returned {len(public_data)} stations")
        return public_data
        
    except Exception as e:
        logging.error(f"❌ Error fetching public map data: {e}")
        return []


@app.get("/api/auth/me", response_model=schemas.UserResponse)
async def get_current_user_info(current_user: models.User = Depends(auth.get_current_user)):
    permissions = auth.get_user_permissions(current_user)
    user_response = schemas.UserResponse.from_orm(current_user)
    user_response.permissions = permissions
    return user_response

#=== Admin endpoint ===
@app.get("/api/admin/settings", response_model=dict)
async def get_system_settings(
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Lấy trạng thái hiện tại của các cài đặt bảo mật."""
    settings = await crud.get_system_settings(db)
    return settings

@app.post("/api/admin/settings")
async def update_system_settings(
    settings_data: dict,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Cập nhật cài đặt bảo mật."""
    await crud.update_system_settings(db, settings_data)
    logger.info(f"Admin '{current_user.username}' updated system settings: {settings_data}")
    return {"status": "ok", "updated_settings": settings_data}

@app.get("/api/admin/security/stats")
async def get_security_stats(
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """
    Enhanced security stats với error handling
    """
    try:
        # 1. Security stats
        security_stats = await rate_limiter.get_stats(db)
        
        # 2. System health
        system_health = health_monitor.get_health_status()
        
        return {
            "status": "ok",
            "timestamp": time.time(),
            "rate_limiter": security_stats,
            "system_health": system_health
        }
    
    except Exception as e:
        logger.error(f"Error in get_security_stats: {e}", exc_info=True)
        
        # Return partial data instead of crashing
        return JSONResponse(
            status_code=status.HTTP_200_OK,  # Still return 200 with error info
            content={
                "status": "error",
                "error": str(e),
                "timestamp": time.time(),
                "rate_limiter": {
                    "error": "Failed to load security stats",
                    "blocked_ips_count": 0,
                    "total_ips_tracked": 0,
                    "attack_mode": False
                },
                "system_health": health_monitor.get_health_status()
            }
        )

@app.post("/api/admin/security/unblock-ip")
async def unblock_ip(
    request_data: dict,  # {"ip_address": "1.2.3.4"}
    db: AsyncSession = Depends(get_auth_db), 
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Admin unblock một IP (thao tác trên database)."""
    ip = request_data.get("ip_address")
    if not ip:
        raise HTTPException(status_code=400, detail="ip_address is required")
    
    success = await rate_limiter.unblock_ip(db, ip) 
    
    if success:
        logger.info(f"Admin '{current_user.username}' unblocked IP: {ip}")
        return {"status": "unblocked", "ip": ip}
    else:
        return {"status": "not_found", "ip": ip}

@app.post("/api/admin/security/block-ip")
async def block_ip_manually(
    request_data: dict,  # {"ip_address": "1.2.3.4"}
    db: AsyncSession = Depends(get_auth_db), 
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Admin block thủ công một IP (lưu vào database)."""
    ip = request_data.get("ip_address")
    if not ip:
        raise HTTPException(status_code=400, detail="ip_address is required")
    
    await rate_limiter.block_ip(db, ip) 
    logger.warning(f"⚠️ Admin '{current_user.username}' permanently blocked IP: {ip}")
    
    return {"status": "blocked", "ip": ip}

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

@app.put("/api/devices/{serial}/name")
async def rename_device(
    serial: str,
    request: schemas.DeviceRename, 
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_DEVICE_NAME))):
    # 1. Kiểm tra thiết bị
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    # Check IDOR
    ensure_device_ownership(current_user, device)

    if not request.name.strip():
        raise HTTPException(status_code=400, detail="Tên không được để trống")

    # 2. Cập nhật Database
    device.name = request.name
    await db.commit()
    
    # 3. Gửi lệnh xuống Agent
    try:
        await send_command_to_pi(serial, {
            "command": "PROVISION_DEVICE", 
            "payload": {"name": request.name}
        })
    except Exception as e:
        logging.warning(f"Rename DB ok but failed to send to Agent: {e}")

    await log_system_event(
        db=db,
        type="info",
        message=f"Device '{device.name}' renamed to '{request.name}' by {current_user.username}",
        user_id=current_user.id,
        serial=serial
    )
    
    return {"status": "success", "name": device.name}

@app.post("/api/users", response_model=schemas.UserResponse)
async def create_user(
    user_data: schemas.UserCreate,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    existing = await crud.get_user_by_username(auth_db, username=user_data.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    try:
        # Dòng này có thể ném ra lỗi ValueError
        hashed_password = await auth.get_password_hash(user_data.password)
    except ValueError as e:
        # Nếu có lỗi từ chính sách mật khẩu, trả về lỗi 400 với thông điệp cụ thể
        raise HTTPException(status_code=400, detail=str(e))
    # -----------------------------------------------------------
        
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
    await log_system_event(
        db=devices_db,
        type="success",
        message=f"Admin '{current_user.username}' created user '{new_user.username}' (role: {new_user.role})",
        user_id=current_user.id
    )
    
    return user_response

@app.put("/api/users/{user_id}", response_model=schemas.UserResponse)
async def update_user(
    user_id: int, 
    user_data: schemas.UserUpdate,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    
    user_to_update = await crud.get_user_by_id(auth_db, user_id)
    if not user_to_update:
        raise HTTPException(status_code=404, detail="User not found")

    update_dict = user_data.dict(exclude_unset=True)
    
    # ===================================================================
    # FIX: LOGIC CẬP NHẬT DANH SÁCH THIẾT BỊ (ASSIGN/UNASSIGN)
    # ===================================================================
    assigned_serials = update_dict.pop("assigned_devices", None)
    
    if assigned_serials is not None:
        # 1. Lấy tất cả thiết bị HIỆN TẠI đang gán cho user này
        current_devices = await crud.get_devices_by_user_id(devices_db, user_id)
        
        # 2. Gỡ bỏ tất cả trước (Reset về None)
        for dev in current_devices:
            dev.user_id = None
            # Broadcast cập nhật UI: Trạm này đã bị gỡ
            await ui_manager.broadcast({
                "type": "status_update",
                "data": schemas.Device.from_orm(dev).model_dump()
            })
        
        # 3. Nếu user là Coordinator -> Gán danh sách mới
        if user_to_update.role == 'coordinator' or user_data.role == 'coordinator':
            for serial in assigned_serials:
                device = await crud.get_device_by_serial(devices_db, serial)
                # Chỉ gán nếu trạm chưa có chủ hoặc chủ chính là user này (đề phòng tranh chấp)
                # Hoặc Admin có quyền ghi đè (tùy logic, ở đây cho phép ghi đè để tiện quản lý)
                if device:
                    device.user_id = user_id
                    # Broadcast cập nhật UI: Trạm này đã có chủ mới
                    await ui_manager.broadcast({
                        "type": "status_update",
                        "data": schemas.Device.from_orm(device).model_dump()
                    })

        await devices_db.commit()
    # ===================================================================

    if 'password' in update_dict and update_dict['password']:
        update_dict['hashed_password'] = await auth.get_password_hash(update_dict.pop('password'))
    
    updated_user = await crud.update_user(auth_db, user_id, update_dict)
    
    permissions = auth.get_user_permissions(updated_user)
    user_response = schemas.UserResponse.from_orm(updated_user)
    user_response.permissions = permissions
    
    await log_system_event(
        db=devices_db,
        type="info",
        message=f"Admin '{current_user.username}' updated user '{updated_user.username}'",
        user_id=current_user.id
    )
    
    return user_response

@app.delete("/api/users/{user_id}")
async def delete_user(
    user_id: int, 
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    
    # 1. Kiểm tra user tồn tại
    user = await crud.get_user_by_id(auth_db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    
    # ===================================================================
    # FIX: GỠ THIẾT BỊ + BROADCAST ĐỂ UI CẬP NHẬT NGAY LẬP TỨC
    # ===================================================================
    try:
        # Tìm các thiết bị đang thuộc về user này
        result = await devices_db.execute(select(models.Device).where(models.Device.user_id == user_id))
        devices_to_unlink = result.scalars().all()

        for dev in devices_to_unlink:
            dev.user_id = None # Gỡ bỏ user_id
            
            # QUAN TRỌNG: Gửi thông báo WebSocket xuống UI để Admin thấy ngay là trạm đã "trống"
            await ui_manager.broadcast({
                "type": "status_update",
                "data": schemas.Device.from_orm(dev).model_dump()
            })
        
        await devices_db.commit() # Lưu thay đổi vào DB Device
        logger.info(f"Unlinked {len(devices_to_unlink)} devices from deleted user {user_id}")

    except Exception as e:
        logger.error(f"Error unlinking devices: {e}")
        # Không raise lỗi để tiếp tục xóa user
    # ===================================================================

    # 3. Xóa user trong Auth DB
    success = await crud.delete_user(auth_db, user_id) 
    if not success:
         raise HTTPException(status_code=404, detail="User not found during deletion")

    await log_system_event(
        db=devices_db,
        type="warning",
        message=f"Admin '{current_user.username}' deleted user '{user.username}' and released devices.",
        user_id=current_user.id
    )
    
    return {"status": "deleted", "user_id": user_id}

#=== CASTER CONFIGURATION ===
@app.put("/api/admin/caster-config")
async def update_caster_config(
    config: schemas.CasterConfig,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """Cập nhật Host/Port của NTRIP Caster"""
    settings_data = {
        "caster_host": config.host,
        "caster_port": config.port
    }
    await crud.update_system_settings(db, settings_data)
    return {"status": "success", "config": settings_data}

@app.get("/api/admin/caster-config")
async def get_caster_config_endpoint(
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    settings = await crud.get_system_settings(db)
    return {
        "host": settings.get('caster_host', 'aitogy.xyz'),
        "port": settings.get('caster_port', 2101)
    }

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
    current_user: models.User = Depends(auth.require_permission(auth.Permission.VIEW_DEVICES)) 
):
    """
    ✅ CHỐNG IDOR: Backend LUÔN ƯU TIÊN current_user.id từ JWT token
    """
    
    # 🔒 COORDINATOR: Bỏ qua mọi tham số user_id (CHỐNG IDOR)
    if current_user.role == auth.Role.COORDINATOR:
        logging.info(
            f"Coordinator '{current_user.username}' requested devices. "
            f"Ignoring user_id parameter (if any) and using id={current_user.id}"
        )
        return await crud.get_devices_by_user_id(db, user_id=current_user.id)
    
    # 🔒 ADMIN: Cho phép filter, nhưng validate
    if user_id is not None:
        if current_user.role != auth.Role.ADMIN:
            logging.warning(
                f"⚠️ IDOR ATTEMPT: User '{current_user.username}' (role={current_user.role}) "
                f"tried to filter by user_id={user_id}"
            )
            raise HTTPException(
                status_code=403, 
                detail="Only admins can filter devices by user_id"
            )
        return await crud.get_devices_by_user_id(db, user_id=user_id)
    
    # Mặc định: Admin và Viewer xem tất cả
    return await crud.get_all_devices(db)

@app.post("/api/devices/{serial}/command")
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
    ensure_device_ownership(current_user, device)
    await log_system_event(
        db=db,
        type="info",
        message=f"Command '{command.command}' sent to '{device.name}' by {current_user.username}",
        user_id=current_user.id,
        serial=serial
    )
    return await send_command_to_pi(serial, command.model_dump())

# --- CÁC ENDPOINT CHO QUẢN LÝ ROVER ---
@app.get("/api/bases/{serial}/rovers", response_model=list[schemas.RoverAccountResponse])
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
        device = await crud.get_device_by_serial(devices_db, serial)
        if device and device.user_id == current_user.id:
            return await crud.get_rovers_by_base_serial(db, base_serial=serial)
    
    # Nếu không phải Admin hoặc không phải chủ sở hữu, từ chối
    raise HTTPException(status_code=403, detail="Bạn không có quyền xem danh sách Rover của trạm này.")

@app.post("/api/bases/{serial}/rovers", response_model=schemas.RoverAccountResponse)
async def create_rover_for_base(
    serial: str,
    rover_data: schemas.RoverAccountCreate,
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # 1. Kiểm tra quyền
    is_admin = current_user.role == auth.Role.ADMIN
    is_authorized_coordinator = (
        current_user.role == auth.Role.COORDINATOR and 
        current_user.can_create_rovers
    )

    if not is_admin and not is_authorized_coordinator:
        raise HTTPException(status_code=403, detail="Bạn không có quyền tạo tài khoản Rover.")

    if not is_admin:
        device = await crud.get_device_by_serial(devices_db, serial)
        if not device or device.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Bạn không được phép tạo Rover cho trạm Base này.")

    # 2. Kiểm tra trùng lặp trong DB Local
    existing_rover = await crud.get_rover_by_username(db, rover_data.username)
    if existing_rover:
        raise HTTPException(status_code=400, detail="Tên đăng nhập Rover đã tồn tại (DB).")

    # 1. Lấy thông tin thiết bị để kiểm tra Mountpoint hợp lệ
    device_info = await crud.get_device_by_serial(devices_db, serial)
    if not device_info:
        raise HTTPException(status_code=404, detail="Trạm Base không tồn tại.")
    
    service_conf = device_info.service_config or {}
    
    # 2. Xác định Mountpoint (Logic mới)
    target_mountpoint = rover_data.mountpoint_allow
    
    valid_mps = []
    if service_conf.get('server1_enabled'):
        valid_mps.append(service_conf.get('mountpoint1'))
    if service_conf.get('server2_enabled'):
        valid_mps.append(service_conf.get('mountpoint2'))
        
    # Lọc giá trị None/Rỗng
    valid_mps = [mp for mp in valid_mps if mp]

    if not valid_mps:
        target_mountpoint = f"MP_{serial}"
    elif target_mountpoint not in valid_mps:
        target_mountpoint = valid_mps[0]

    duration_seconds = 315360000 # 10 năm mặc định
    
    if rover_data.expiry_days > 0:
        duration_seconds = rover_data.expiry_days * 86400  # 1 ngày = 86400 giây

    # 3. GỌI TCP ĐẾN NTRIP CASTER
    try:
        caster = await get_caster_client(db)
        
        creator_identifier = current_user.username 
        
        await caster.create_user(
            creator=creator_identifier,
            username=rover_data.username,
            password=rover_data.password,
            mountpoints=target_mountpoint, 
            expiry_seconds=duration_seconds, 
            max_users=1
        )
        logger.info(f"✓ Created Rover '{rover_data.username}' for MP '{target_mountpoint}'")
        
    except Exception as e:
        logger.error(f"Failed to create rover on Caster: {e}")
        raise HTTPException(status_code=502, detail=f"Lỗi Caster: {str(e)}")

    # 4. Lưu vào DB
    new_rover = await crud.create_rover_account(
        db, 
        username=rover_data.username,
        password=rover_data.password,
        base_serial=serial,
        creator_id=current_user.id
    )
    
    return new_rover

@app.put("/api/rovers/{rover_id}", response_model=schemas.RoverAccountResponse)
async def update_rover(
    rover_id: int,
    update_req: schemas.RoverUpdate,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # 1. Lấy thông tin Rover cũ
    rover = await crud.get_rover_by_id(db, rover_id)
    if not rover:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản Rover.")

    # 2. Kiểm tra quyền
    is_admin = current_user.role == auth.Role.ADMIN
    is_creator = rover.created_by_user_id == current_user.id
    if not is_admin and not is_creator:
        raise HTTPException(status_code=403, detail="Bạn không có quyền sửa tài khoản này.")

    # 3. Chuẩn bị dữ liệu mới (nếu client không gửi thì dùng cái cũ)
    new_password = update_req.password if update_req.password else rover.password
    new_mountpoints = update_req.mountpoints if update_req.mountpoints else rover.mountpoints
    
    # Xử lý thời hạn (Mặc định reset lại thời hạn nếu có gửi expiry_days)
    duration_seconds = 315360000 # Default
    if update_req.expiry_days:
        duration_seconds = update_req.expiry_days * 86400
    
    # 4. GỌI CASTER ĐỂ CẬP NHẬT (Ghi đè user cũ)
    try:
        caster = await get_caster_client(db)
        creator_identifier = current_user.username 
        
        # Hàm create_user của Caster thường hoạt động như "Upsert" (Tạo mới hoặc Ghi đè)
        await caster.create_user(
            creator=creator_identifier,
            username=rover.username, # Username không đổi
            password=new_password,
            mountpoints=new_mountpoints, 
            expiry_seconds=duration_seconds, 
            max_users=1
        )
    except Exception as e:
        logger.error(f"Caster Update Error: {e}")
        raise HTTPException(status_code=502, detail=f"Lỗi cập nhật Caster: {str(e)}")

    # 5. Cập nhật DB Local
    update_data = {
        "password": new_password,
        "mountpoints": new_mountpoints
    }
    updated_rover = await crud.update_rover_account(db, rover_id, update_data)
    
    return updated_rover

@app.delete("/api/rovers/{rover_id}")
async def delete_rover(
    rover_id: int,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # 1. Lấy thông tin Rover
    rover_to_delete = await crud.get_rover_by_id(db, rover_id)
    if not rover_to_delete:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản Rover.")

    # 2. Kiểm tra quyền
    is_admin = current_user.role == auth.Role.ADMIN
    is_creator = rover_to_delete.created_by_user_id == current_user.id

    if not is_admin and not is_creator:
        raise HTTPException(status_code=403, detail="Bạn không có quyền xóa tài khoản Rover này.")
    
    creator_user = await crud.get_user_by_id(db, rover_to_delete.created_by_user_id)
    creator_username = creator_user.username if creator_user else "admin"

    # 3. GỌI TCP ĐẾN NTRIP CASTER ĐỂ XÓA
    try:
        caster = await get_caster_client(db)
        await caster.delete_user(
            identifier=creator_username, 
            username=rover_to_delete.username
        )
        logger.info(f"✓ Deleted Rover '{rover_to_delete.username}' from Caster via TCP")
    except Exception as e:
        logger.warning(f"Failed to delete rover from Caster (might be already deleted): {e}")

    # 4. Xóa khỏi DB Local
    success = await crud.delete_rover_account(db, rover_id=rover_id)
    
    return {"status": "deleted", "rover_id": rover_id}

# === ROVER MANAGEMENT - GLOBAL VIEW ===
@app.get("/api/rovers/all", response_model=list[schemas.RoverAccountResponse])
async def get_all_rovers_global(
    db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):

    # 1. Kiểm tra quyền cơ bản
    if current_user.role == auth.Role.VIEWER:
        raise HTTPException(
            status_code=403, 
            detail="Viewer không có quyền xem danh sách Rover."
        )
    
    if current_user.role == auth.Role.COORDINATOR and not current_user.can_create_rovers:
        raise HTTPException(
            status_code=403, 
            detail="Bạn chưa được cấp quyền quản lý Rover."
        )
    
    # 2. Lấy danh sách Rover dựa trên role
    if current_user.role == auth.Role.ADMIN:
        rovers = await crud.get_all_rovers_by_user(db, user_id=current_user.id, role="admin")
    else:
        rovers = await crud.get_all_rovers_by_user(db, user_id=current_user.id, role="coordinator")
    
    # 3. Làm giàu dữ liệu (XỬ LÝ HIỂN THỊ NHIỀU TRẠM)
    enriched_rovers = []
    
    # Cache devices để tránh query DB quá nhiều lần trong vòng lặp
    all_devices_result = await devices_db.execute(select(models.Device))
    all_devices = {d.serial: d.name for d in all_devices_result.scalars().all()}

    for rover in rovers:
        rover_dict = schemas.RoverAccountResponse.from_orm(rover).model_dump()
        
        # Xử lý base_serial có thể là "SN1,SN2"
        serials = rover.base_serial.split(',')
        station_names = []
        
        for sn in serials:
            s_name = all_devices.get(sn.strip())
            if s_name:
                station_names.append(s_name)
            else:
                station_names.append(sn) # Nếu không tìm thấy tên thì hiện serial
        
        # Nối tên các trạm lại: "Trạm A, Trạm B"
        rover_dict['base_name'] = ", ".join(station_names)
         
        enriched_rovers.append(rover_dict)
    return enriched_rovers

@app.post("/api/rovers/create-multi", response_model=schemas.RoverAccountResponse)
async def create_multi_base_rover(
    request: schemas.RoverMultiCreate,
    db: AsyncSession = Depends(get_auth_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    # 1. Kiểm tra quyền (như cũ)
    is_admin = current_user.role == auth.Role.ADMIN
    if not is_admin and not (current_user.role == auth.Role.COORDINATOR and current_user.can_create_rovers):
        raise HTTPException(status_code=403, detail="Bạn không có quyền tạo tài khoản Rover.")

    # 2. Kiểm tra trùng (như cũ)
    existing = await crud.get_rover_by_username(db, request.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username đã tồn tại.")

    # 3. Chuẩn bị dữ liệu
    duration_seconds = request.expiry_days * 86400
    target_mountpoints = request.mountpoint_override.strip()
    
    if not request.selected_serials:
         raise HTTPException(status_code=400, detail="Phải chọn ít nhất 1 trạm.")
    
    # Gộp serial lại thành chuỗi để lưu vào base_serial
    combined_serials = ",".join(request.selected_serials)

    # 4. GỌI CASTER (Tạo user thật)
    try:
        caster = await get_caster_client(db)
        creator_identifier = current_user.username 
        
        await caster.create_user(
            creator=creator_identifier,
            username=request.username,
            password=request.password,
            mountpoints=target_mountpoints, 
            expiry_seconds=duration_seconds, 
            max_users=1
        )
        logging.info(f"✓ Created Multi-Rover '{request.username}' allowed: {target_mountpoints}")

    except Exception as e:
        logging.error(f"Caster Error: {e}")
        raise HTTPException(status_code=502, detail=f"Lỗi Caster: {str(e)}")

    # 5. LƯU VÀO DB (Ghi rõ mountpoint)
    new_rover = await crud.create_rover_account(
        db, 
        username=request.username,
        password=request.password,
        base_serial=combined_serials,   # Lưu: "SN1,SN2"
        mountpoints=target_mountpoints, # Lưu: "MP1;MP2" (Cột mới)
        creator_id=current_user.id
    )
    
    return new_rover

#=== DEVICE CONFIGURATION ENDPOINTS ===
@app.post("/api/devices/{serial}/configure-chip")
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

    # Kiểm tra quyền sở hữu (IDOR protection)
    ensure_device_ownership(current_user, device_in_db)
    
    # ==================== BƯỚC 2: PARSE PAYLOAD ====================
    payload = config_request.payload
    sensor_type = payload.get("sensor_type")
    params = payload.get("params", {})
    mode = payload.get("mode")
    method = params.get("base_setup_method")
    
    if not sensor_type or not mode:
        raise HTTPException(
            status_code=400,
            detail="Missing required fields: sensor_type or mode"
        )

    # ==================== BƯỚC 3: LƯU CẤU HÌNH VÀO DATABASE ====================
    try:
        # Cập nhật chip type
        if sensor_type:
            device_in_db.detected_chip_type = sensor_type
        
        # Cập nhật base config
        if params:
            device_in_db.base_config = params
        
        # ✅ ĐÁNH DẤU CHƯA ĐỒNG BỘ
        device_in_db.is_synced = False
        
        # Commit vào DB
        await db.commit()
        await db.refresh(device_in_db)
        
        logging.info(
            f"✓ Saved base config for {serial} to database:\n"
            f"  Chip: {sensor_type}\n"
            f"  Method: {method}\n"
            f"  Marked as UNSYNCED"
        )
        
        await ui_manager.broadcast({
            "type": "status_update",
            "data": schemas.Device.from_orm(device_in_db).model_dump()
        })

    except Exception as e:
        await db.rollback()
        logging.error(f"❌ Failed to save config to DB for {serial}: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to save configuration to database: {str(e)}"
        )

    # ==================== BƯỚC 4: BUILD COMMANDS ====================
    try:
        commands_to_send = []
        
        if mode == "BASE":
            if method == "SURVEY_IN":
                # Build Survey-In commands
                survey_duration = params.get('survey_in_duration')
                survey_accuracy = params.get('survey_in_accuracy')
                
                if not survey_duration or not survey_accuracy:
                    raise ValueError("Missing survey_in parameters")
                
                commands_to_send = command_builder.build_base_survey_in_command(
                    sensor_type, 
                    survey_duration, 
                    survey_accuracy
                )
                
                logging.info(
                    f"Built Survey-In commands for {serial}:\n"
                    f"  Duration: {survey_duration}s\n"
                    f"  Accuracy: {survey_accuracy}m"
                )
            
            elif method == "FIXED_LLA":
                # Build Fixed LLA commands
                coords = params.get('coords')
                if not coords or not all(k in coords for k in ['lat', 'lon', 'alt']):
                    raise ValueError("Missing or incomplete coordinates")
                
                accuracy = params.get('accuracy', 10.0)
                
                commands_to_send = command_builder.build_base_fixed_lla_command(
                    sensor_type,
                    coords['lat'],
                    coords['lon'],
                    coords['alt'],
                    accuracy
                )
                
                logging.info(
                    f"Built Fixed LLA commands for {serial}:\n"
                    f"  Lat: {coords['lat']}\n"
                    f"  Lon: {coords['lon']}\n"
                    f"  Alt: {coords['alt']}\n"
                    f"  Accuracy: {accuracy}m"
                )
            
            else:
                raise ValueError(f"Unknown base setup method: {method}")
        
        else:
            raise ValueError(f"Unsupported mode: {mode}")
        
        # Encode commands to base64
        encoded_commands = [
            base64.b64encode(cmd).decode('ascii') 
            for cmd in commands_to_send 
            if cmd
        ]
        
        if not encoded_commands:
            raise ValueError("No commands generated")

    except Exception as e:
        logging.error(f"❌ Failed to build commands for {serial}: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to build configuration commands: {str(e)}"
        )

    # ==================== BƯỚC 5: GỬI LỆNH XUỐNG AGENT ====================
    pi_command = {
        "command": "EXECUTE_RAW_COMMANDS",
        "payload": {"commands_b64": encoded_commands},
        "original_config": config_request.payload  # Để Agent biết context
    }
    
    try:
        response = await send_command_to_pi(serial, pi_command)
        
        logging.info(
            f"✓ Sent {len(commands_to_send)} configuration commands to {serial}\n"
            f"  Channel: {response['channel']}\n"
            f"  Status: Waiting for device confirmation..."
        )
        
        await log_system_event(
            db=db,
            type="info",
            message=f"GNSS config updated for '{device_in_db.name}' by {current_user.username}",
            user_id=current_user.id,
            serial=serial
        )
        
        # ✅ TRẢ VỀ SUCCESS - CHỜ AGENT XÁC NHẬN
        return {
            "status": "command_sent_waiting_sync",
            "channel": response['channel'],
            "commands_sent": len(commands_to_send),
            "message": (
                "Configuration saved to database and sent to device. "
                "Waiting for device to apply and confirm sync status..."
            ),
            "is_synced": False  # Hiện tại vẫn chưa đồng bộ
        }

    except HTTPException as http_exc:
        # Device offline hoặc không thể gửi lệnh
        logging.warning(
            f"⚠️ Failed to send command to {serial}: {http_exc.detail}\n"
            f"  Config đã được lưu vào DB.\n"
            f"  Sẽ tự động đồng bộ khi device online lại."
        )
        
        # ✅ TRẢ VỀ PARTIAL SUCCESS
        raise HTTPException(
            status_code=503,
            detail=(
                f"Configuration saved to database successfully, "
                f"but device is offline ({http_exc.detail}). "
                f"Config will auto-sync when device comes back online."
            )
        )
    
    except Exception as e:
        logging.error(
            f"❌ Unexpected error sending command to {serial}: {e}",
            exc_info=True
        )
        
        raise HTTPException(
            status_code=500,
            detail=f"Internal error while sending command: {str(e)}"
        )

@app.post("/api/devices/{serial}/configure-service")
async def configure_service_endpoint(
    serial: str,
    payload: schemas.ServiceConfig,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_SERVICE_CONFIG))
):
    device_in_db = await crud.get_device_by_serial(db, serial)
    if not device_in_db:
        raise HTTPException(status_code=404, detail="Device not found in database")

    ensure_device_ownership(current_user, device_in_db)
    # --- BƯỚC 1: LƯU CẤU HÌNH "CHUẨN" VÀO DATABASE ---
    try:
        device_in_db.service_config = payload.model_dump()
        await db.commit()
        await db.refresh(device_in_db)
        logging.info(f"✓ Saved service_config for {serial} to database.")
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save service config to database.")
    
    # --- BƯỚC 2: GỬI LỆNH ĐẾN PI ---
    pi_command = {
        "command": "DEPLOY_SERVICE_CONFIG",
        "payload": payload.model_dump()
    }
    try:
        response = await send_command_to_pi(serial, pi_command)
        await log_system_event(
            db=db,
            type="info",
            message=f"Service config updated for '{device_in_db.name}' by {current_user.username}",
            user_id=current_user.id,
            serial=serial
        )
        return {"status": "service_config_sent_and_saved", "channel": response.get('channel', 'unknown')}
    except HTTPException as e:
        raise HTTPException(status_code=503, detail=f"Config saved to DB, but failed to send command to Pi: {e.detail}")

@app.post("/api/devices/{serial}/reset")
async def reset_pi_device(
    serial: str,
    db: AsyncSession = Depends(get_db), 
    current_user: models.User = Depends(auth.require_permission(auth.Permission.DELETE_DEVICE))
):
    # 1. Lấy thiết bị từ DB TRƯỚC để kiểm tra sự tồn tại
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Thiết bị không tồn tại")

    ensure_device_ownership(current_user, device)

    # 3. Gửi lệnh xuống thiết bị (Chỉ gửi khi đã qua cửa kiểm tra an ninh)
    try:
        await send_command_to_pi(serial, {"command": "DELETE_DEVICE", "payload": {}})
    except Exception as e:
        # Log lỗi nhưng không chặn dòng chảy để cập nhật DB cho UI phản hồi
        logging.error(f"Failed to send reset command to {serial}: {e}")

    # 4. Cập nhật DB ngay lập tức
    device.status = "rebooting_for_reset"
    device.is_provisioned = False 
    device.detected_chip_type = "UNKNOWN"  
    device.base_config = {}                
    device.service_config = {}             
    device.ntrip_connected = False
    device.bps = 0
    
    await db.commit()
    # 5. Broadcast để UI cập nhật ngay
    await ui_manager.broadcast({
        "type": "status_update",
        "data": schemas.Device.from_orm(device).model_dump()
    })

    logging.info(f"User '{current_user.username}' sent RESET command to Pi '{serial}'")
    await log_system_event(
        db=db,
        type="warning",
        message=f"Device '{device.name}' reset by {current_user.username}",
        user_id=current_user.id,
        serial=serial
    )
    return {"status": "reset_command_sent"}

@app.delete("/api/devices/{serial}")
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
    
    # Xóa khỏi database
    await db.delete(device)
    await db.commit()
    
    logging.info(f"User '{current_user.username}' deleted device '{serial}' from list (not reset)")
    
    # Broadcast để các client khác cập nhật UI
    await ui_manager.broadcast({
        "type": "device_deleted",
        "serial": serial
    })
    
    await log_system_event(
        db=db,
        type="warning",
        message=f"Device '{device.name}' removed from list by {current_user.username}",
        user_id=current_user.id,
        serial=serial
    )
    
    return {"status": "deleted", "serial": serial}

@app.post("/api/devices/{serial}/lock")
async def lock_device(
    serial: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    device = await crud.get_device_by_serial(db, serial)
    try:
        response = await send_command_to_pi(serial, {"command": "LOCK_DEVICE", "payload": {}})
        await log_system_event(
            db=db,
            type="warning",
            message=f"Device '{device.name}' LOCKED by Admin {current_user.username}",
            user_id=current_user.id,
            serial=serial
        )
        return {"status": "lock_command_sent", "channel": response.get('channel', 'unknown')}
    except HTTPException as http_exc:
        # Neu send_command_to_pi raise 503, hay tra ve loi do
        raise http_exc
    except Exception as e:
        logging.error(f"Error in lock_device endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.post("/api/devices/{serial}/unlock")
async def unlock_device(
    serial: str,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    device = await crud.get_device_by_serial(db, serial)
    try:
        response = await send_command_to_pi(serial, {"command": "UNLOCK_DEVICE", "payload": {}})
        await log_system_event(
            db=db,
            type="success",
            message=f"Device '{device.name}' UNLOCKED by Admin {current_user.username}",
            user_id=current_user.id,
            serial=serial
        )
        return {"status": "unlock_command_sent", "channel": response.get('channel', 'unknown')}
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logging.error(f"Error in unlock_device endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.get("/api/devices/export/csv")
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
    
    for device in devices:
        # === THÔNG TIN CHUNG ===
        human_time = time.strftime(
            '%Y-%m-%d %H:%M:%S', 
            time.localtime(device.timestamp)
        ) if device.timestamp else "N/A"
        
        # Lấy tên Coordinator
        coordinator_name = ""
        if device.user_id:
            user = await crud.get_user_by_id(auth_db, user_id=device.user_id)
            if user:
                coordinator_name = user.username
        
        # === CẤU HÌNH BASE ===
        base_config = device.base_config or {}
        coords = base_config.get('coords', {})
        base_method = base_config.get('base_setup_method', '')
        base_lat = coords.get('lat', '')
        base_lon = coords.get('lon', '')
        base_alt = coords.get('alt', '')
        base_accuracy = base_config.get('accuracy', '')
        
        # === CẤU HÌNH SERVICE ===
        service_config = device.service_config or {}
        
        # --- SERVER 1 ---
        s1_enabled = service_config.get('server1_enabled', False)
        s1_version = service_config.get('ntrip_version1', 1)
        s1_host = service_config.get('serverhost1', '')
        s1_port = service_config.get('port1', '')
        s1_mountpoint = service_config.get('mountpoint1', '')
        s1_username = service_config.get('username1', 'source')
        
        # --- SERVER 2 ---
        s2_enabled = service_config.get('server2_enabled', False)
        s2_version = service_config.get('ntrip_version2', 1)
        s2_host = service_config.get('serverhost2', '')
        s2_port = service_config.get('port2', '')
        s2_mountpoint = service_config.get('mountpoint2', '')
        s2_username = service_config.get('username2', 'source')
        
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
            
        s1_bps = s1_status.get('bps', 0)
        s1_connected = s1_status.get('connected', False)
        s2_status = ntrip_status.get('server2')
        if not isinstance(s2_status, dict):
            s2_status = {} 

        s2_bps = s2_status.get('bps', 0)
        s2_connected = s2_status.get('connected', False)
        
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
            device.detected_chip_type,
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

# === ACTIVITY LOGGING ENDPOINTS ===
@app.get("/api/activity/feed", response_model=List[schemas.ActivityLogResponse])
async def get_activity_feed(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    return await crud.get_activity_logs(db, limit=limit)

@app.get("/api/activity/heatmap", response_model=schemas.HeatmapData)
async def get_heatmap_data(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    data = await crud.get_activity_heatmap(db)
    return {"data": data}

# === WEBSOCKETS ===
@app.websocket("/ws/updates")
async def ui_websocket_endpoint(websocket: WebSocket):
    await ui_manager.connect(websocket)
    # logging.info("New UI client connected.") 
    try:
        while True: 
            # Giữ kết nối sống, chờ tin nhắn (dù UI chỉ nhận chứ không gửi gì)
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        # Khi client ngắt kết nối, xóa khỏi manager
        ui_manager.disconnect(websocket)
        # logging.info("UI client disconnected.")

async def flush_device_buffer(db: AsyncSession):
    global device_update_buffer
    if not device_update_buffer: 
        return

    # Lấy snapshot và xóa buffer ngay lập tức
    current_batch = device_update_buffer[:]
    device_update_buffer.clear()

    if not current_batch: 
        return

    # Deduplication: Giữ lại bản tin mới nhất cho mỗi serial
    unique_updates = {}
    for item in current_batch:
        if 'serial' in item:
            unique_updates[item['serial']] = item
    
    final_batch = list(unique_updates.values())

    if final_batch:
        try:
            # Gọi hàm batch update trong crud.py
            await crud.batch_update_devices(db, final_batch)
            #logger.info(f"⚡ Flushed {len(final_batch)} device updates to DB")
        except Exception as e:
            logger.error(f"Error flushing DB: {e}")

@app.websocket("/ws/pi/{serial}")
async def pi_websocket_endpoint(websocket: WebSocket, serial: str):
    await pi_manager.connect(serial, websocket)
    
    try:
        while True:
            data = await websocket.receive_json()
            message_type = data.get("type")
            payload = data.get("payload")

            if message_type == "status_update" and payload:
                # 1. Bắn ra UI ngay (Real-time)
                await ui_manager.broadcast({
                    "type": "status_update",
                    "data": payload
                })
                # 2. Đẩy vào Buffer (DB lưu sau)
                device_update_buffer.append(payload)
            
            elif message_type == "nmea_update" and payload:
                parsed_data = nmea_parser.parse(payload)
                if parsed_data:
                    await ui_manager.broadcast({
                        "type": "nmea_update",
                        "serial": serial,
                        "data": parsed_data
                    })

    except WebSocketDisconnect:
        logging.info(f"Pi '{serial}' disconnected.")
        pi_manager.disconnect(serial)
        
        # --- FIXED: KHÔNG GỌI DB TRỰC TIẾP ---
        offline_payload = {
            "serial": serial,
            "status": "offline",
            "ntrip_connected": False,
            "bps": 0,
            "ntrip_status": {
                "server1": {"connected": False, "bps": 0},
                "server2": {"connected": False, "bps": 0}
            },
            "timestamp": int(time.time())
        }
        
        # 1. Báo UI ngay
        await ui_manager.broadcast({
            "type": "status_update",
            "data": offline_payload
        })
        
        # 2. Đẩy vào buffer -> Background task sẽ lưu DB sau 1s
        device_update_buffer.append(offline_payload)
    
    except Exception as e:
        logging.error(f"WS Error '{serial}': {e}")
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

# ============================================================================
# HEALTH MONITORING ENDPOINTS
# ============================================================================

@app.get("/api/health/detailed")
async def detailed_health_check():
    """
    Enhanced health check với system metrics cho monitoring dashboard
    """
    try:
        # System metrics
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Application metrics từ health_monitor
        health_status = health_monitor.get_health_status()
        
        return {
            "status": health_status.get("status", "unknown"),
            "timestamp": time.time(),
            
            # System Resources
            "system": {
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "memory_used_mb": memory.used / (1024 * 1024),
                "memory_total_mb": memory.total / (1024 * 1024),
                "disk_percent": disk.percent,
                "disk_used_gb": disk.used / (1024 * 1024 * 1024),
                "disk_total_gb": disk.total / (1024 * 1024 * 1024)
            },
            
            # Application Metrics
            "application": {
                "total_requests": health_status.get("total_requests", 0),
                "avg_response_time_ms": health_status.get("avg_response_time_ms", 0),
                "error_count": health_status.get("error_count", 0),
                "active_connections": len(ui_manager.active_connections) if hasattr(ui_manager, 'active_connections') else 0
            },
            
            # Circuit Breakers Status
            "circuit_breakers": {
                "mqtt": {
                    "state": mqtt_circuit_breaker.state,
                    "failure_count": mqtt_circuit_breaker.failure_count
                },
                "database": {
                    "state": db_circuit_breaker.state,
                    "failure_count": db_circuit_breaker.failure_count
                }
            }
        }
    
    except Exception as e:
        logger.error(f"Error in detailed health check: {e}", exc_info=True)
        
        # Return partial data on error
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "error",
                "error": str(e),
                "timestamp": time.time(),
                "system": {
                    "cpu_percent": 0,
                    "memory_percent": 0,
                    "disk_percent": 0
                },
                "application": {
                    "total_requests": 0,
                    "avg_response_time_ms": 0,
                    "error_count": health_monitor.error_count
                }
            }
        )

@app.get("/api/health/errors")
async def recent_errors():
    """Lấy danh sách lỗi gần đây"""
    return {
        "errors": health_monitor.get_recent_errors(),
        "total_errors": health_monitor.error_count
    }

@app.get("/health")
@app.get("/api/health")
async def simple_health_check():
    """Simple health check cho load balancer"""
    status = health_monitor.get_health_status()
    
    if status['status'] == 'healthy':
        return {"status": "ok"}
    
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "degraded", "details": status}
    )

# === ADMIN SERVER LOGS VIEWER ===
@app.get("/api/admin/server-logs")
async def get_server_logs(
    lines: int = 100, 
    level: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))
):
    """
    Đọc N dòng cuối cùng từ file app.log để hiển thị lên UI.
    Chỉ Admin mới có quyền xem.
    """
    log_file = 'app.log'
    if not os.path.exists(log_file):
        return {"logs": ["Log file not found."]}

    try:
        # Đọc file log (cách đơn giản để lấy n dòng cuối)
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            # Đọc toàn bộ lines (lưu ý: nếu file quá lớn >100MB cần dùng cách khác tối ưu hơn)
            # Với server nhỏ/vừa, cách này ổn.
            all_lines = f.readlines()
            
            # Lấy N dòng cuối
            last_lines = all_lines[-lines:]
            
            # Filter theo level nếu cần (INFO, ERROR, WARNING)
            if level and level != 'ALL':
                last_lines = [line for line in last_lines if level in line]
                
            return {"logs": [line.strip() for line in last_lines]}
    except Exception as e:
        return {"logs": [f"Error reading log file: {str(e)}"]}

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
    allow_origins=["https://cors.aitogy.com"],  # ⚠️ Trong production, hãy chỉ định origins cụ thể
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
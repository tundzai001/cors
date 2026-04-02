# ==============================================================================
# == backend/app/mqtt.py - FIXED: Batch Sync Checking + Pool Optimization
# ==============================================================================
import json
import logging
import asyncio
import os
import time
from typing import Awaitable, Callable, Optional
import paho.mqtt.client as mqtt
from sqlalchemy import select

from . import schemas
from .websocket import manager
from .database import settings, device_session_scope
from . import crud, nmea_parser, models

# Serialize MQTT DB writes to avoid connection pool spikes under burst traffic.
mqtt_db_write_lock = asyncio.Lock()

main_loop = None
client_id = f"backend-listen-{settings.MQTT_HOST}-{os.getpid()}"
mqtt_client = mqtt.Client(client_id)

if hasattr(settings, 'MQTT_USERNAME') and settings.MQTT_USERNAME:
    mqtt_client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)

# --- CACHE ---
device_write_cache = {}      # Lưu thời gian lần cuối ghi DB (Save)
device_status_cache = {}     # Lưu trạng thái cũ (online/offline)
device_sync_cache = {}       # Lưu trạng thái sync (True/False)
device_last_sync_check = {}  # Lưu thời gian lần cuối check DB
device_name_cache = {}       # Lưu tên trạm để ghép vào bản tin offline (LWT không kèm tên)

# 🔥 NEW: Global sync check tracking
LAST_GLOBAL_SYNC_CHECK = 0
GLOBAL_SYNC_CHECK_INTERVAL = 60.0  # Batch check 60s/lần

DB_WRITE_INTERVAL = 120.0    # Ghi DB định kỳ 2 phút/lần nếu không có gì thay đổi
IS_SHUTTING_DOWN = False 
external_command_handler: Optional[Callable[[str, bytes], Awaitable[None]]] = None

# ==============================================================================
# MQTT CALLBACKS
# ==============================================================================

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logging.info("✓ MQTT Connected Successfully.")
        client.subscribe("pi/devices/+/status", qos=0)
        client.subscribe("pi/devices/+/raw_data", qos=0)
        client.subscribe("pi/devices/+/base_config_state", qos=1)
        client.subscribe("pi/devices/+/service_config_state", qos=1)
        # NTRIP control plane -> backend listens and translates to agent commands.
        client.subscribe("ntrip/rtcm/start", qos=1)
    else:
        logging.error(f"MQTT Connect Failed with code: {rc}")

def on_disconnect(client, userdata, rc, properties=None):
    if rc != 0:
        logging.warning(f"⚠️ MQTT Disconnected unexpectedly (RC: {rc}). Auto-reconnecting...")

def on_message(client, userdata, msg):
    if IS_SHUTTING_DOWN: 
        return

    if msg.topic.startswith("ntrip/rtcm/"):
        if external_command_handler and main_loop and main_loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(
                    external_command_handler(msg.topic, msg.payload),
                    main_loop,
                )
            except RuntimeError:
                pass
        else:
            logging.warning("Received NTRIP MQTT command but no external command handler is registered")
        return
    
    if main_loop and main_loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(
                handle_message_async(msg.topic, msg.payload, is_retained=bool(getattr(msg, 'retain', False))), 
                main_loop
            )
        except RuntimeError:
            pass

# ==============================================================================
# 🔥 BACKGROUND TASK: BATCH SYNC CHECKER (Chạy 60s/lần)
# ==============================================================================

async def batch_sync_checker():
    """
    Background task chạy mỗi 60s để check sync status của TẤT CẢ devices online.
    Chỉ MỞ 1 KẾT NỐI DB duy nhất cho tất cả devices.
    
    BEFORE: 30 devices × 1 DB conn/device/msg = 30+ conn/s → Pool tràn
    AFTER:  1 DB conn cho tất cả devices/60s → Giảm 1800 lần!
    """
    global LAST_GLOBAL_SYNC_CHECK
    
    while True:
        await asyncio.sleep(GLOBAL_SYNC_CHECK_INTERVAL)
        
        if IS_SHUTTING_DOWN:
            break
        
        try:
            # Lấy danh sách devices online từ cache (không cần DB)
            online_serials = [
                serial for serial, status in device_status_cache.items()
                if status in ['online', 'sleep']
            ]
            
            if not online_serials:
                continue
            
            # 🔥 CHỈ MỞ 1 KẾT NỐI DB DUY NHẤT
            async with device_session_scope() as session:
                # Lấy tất cả devices cùng lúc bằng 1 query
                result = await session.execute(
                    select(models.Device).filter(
                        models.Device.serial.in_(online_serials)
                    )
                )
                devices = result.scalars().all()
                
                checked_count = 0
                
                # Check sync cho từng device (KHÔNG mở thêm DB connection)
                for device in devices:
                    serial = device.serial
                    
                    # Lấy config từ cache (đã nhận qua MQTT)
                    agent_base = device_sync_cache.get(f"{serial}_base_config", {})
                    agent_service = device_sync_cache.get(f"{serial}_service_config", {})
                    
                    # DB config (chuẩn)
                    db_base = device.base_config or {}
                    db_service = device.service_config or {}
                    
                    is_relay_like = serial.startswith(("RELAY_", "MONITOR_", "VIRTUAL_"))
                    
                    # So sánh
                    base_ok, base_errs = _compare_configs(agent_base, db_base, "Base", is_relay=is_relay_like)
                    srv_ok, srv_errs = _compare_configs(agent_service, db_service, "Service", is_relay=is_relay_like)
                    
                    is_synced = base_ok and srv_ok
                    
                    # Cập nhật cache
                    old_sync = device_sync_cache.get(serial)
                    device_sync_cache[serial] = is_synced
                    device_last_sync_check[serial] = time.time()
                    
                    # Log nếu sync status thay đổi
                    if old_sync is not None and old_sync != is_synced:
                        if is_synced:
                            logging.info(f"✓ {serial} SYNCED")
                        else:
                            logging.warning(f"⚠️ {serial} OUT OF SYNC: {base_errs + srv_errs}")
                    
                    checked_count += 1
            
            LAST_GLOBAL_SYNC_CHECK = time.time()
            logging.info(f"✓ Batch sync check completed: {checked_count}/{len(online_serials)} devices")
        
        except Exception as e:
            logging.error(f"❌ Batch sync checker error: {e}", exc_info=True)

# ==============================================================================
# MAIN MESSAGE HANDLER (ĐàTỐI ƯU HÓA - KHÔNG MỞ DB MỖI MESSAGE)
# ==============================================================================

async def handle_message_async(topic: str, payload: bytes, is_retained: bool = False):
    if IS_SHUTTING_DOWN:
        return
        
    try:
        topic_parts = topic.split('/')
        if len(topic_parts) < 4:
            return
        
        message_type = topic_parts[-1]
        serial = topic_parts[2]
        
        # ==================== 1. XỬ LÝ NMEA DATA ====================
        if message_type == "raw_data":
            try:
                line = payload.decode('ascii', errors='ignore')
                parsed_data = nmea_parser.parse(line)
                if parsed_data:
                    await manager.broadcast({
                        "type": "nmea_update",
                        "serial": serial,
                        "data": parsed_data
                    })
            except Exception as e:
                logging.debug(f"NMEA parse error: {e}")
            return

        # ==================== 2. XỬ LÝ STATUS UPDATE ====================
        try:
            data = json.loads(payload.decode())
            if 'rtk_fix_status' not in data:
                # Fallback: Dựa vào status cũ (nếu agent chưa update)
                if data.get('status') in ['online', 'sleep']:
                    data['rtk_fix_status'] = 'DGPS'
                    data['base_mode_active'] = bool(data.get('base_config'))
                else:
                    data['rtk_fix_status'] = 'NO_FIX'
                    data['base_mode_active'] = False
        except json.JSONDecodeError as e:
            logging.error(f"❌ JSON decode error on topic {topic}: {e}")
            return

        if message_type in ["status", "status_update"]:
            serial_upper = str(serial or "").upper()
            is_relay_like = (
                serial_upper.startswith("RELAY_")
                or serial_upper.startswith("MONITOR_")
                or serial_upper.startswith("VIRTUAL_")
            )

            # MQTT retained snapshots from old publishers can resurrect phantom relay rows.
            # Ignore retained relay-like status if the device does not exist in DB.
            if is_retained and is_relay_like:
                try:
                    async with device_session_scope() as _db:
                        existing = await crud.get_device_by_serial(_db, serial)
                    if not existing:
                        logging.info(f"Ignoring retained ghost status for unknown serial: {serial}")
                        return
                except Exception as e:
                    logging.warning(f"Retained ghost-check failed for {serial}: {e}")

            now = time.time()
            
            # --- 2.1. TÍNH TOÁN BPS ---
            ntrip_stats = data.get('ntrip_stats')
            if ntrip_stats and isinstance(ntrip_stats, dict):
                total_bps = sum(
                    val for val in ntrip_stats.values() 
                    if isinstance(val, (int, float))
                )
                data['bps'] = total_bps
            
            # --- 2.2. LƯU CONFIG VÀO CACHE ĐỂ BATCH CHECKER DÙNG ---
            # 🔥 QUAN TRỌNG: Lưu config vào cache để batch checker có thể so sánh
            agent_base = data.get('base_config', {})
            agent_service = data.get('service_config', {})
            
            if agent_base:
                device_sync_cache[f"{serial}_base_config"] = agent_base
            if agent_service:
                device_sync_cache[f"{serial}_service_config"] = agent_service
            
            # --- 2.3. ĐỌC SYNC STATUS TỪ CACHE (KHÔNG MỞ DB) ---
            # 🔥 THAY ĐỔI CHÍNH: Không check DB mỗi message nữa!
            # Batch checker sẽ update cache mỗi 60s
            is_synced = device_sync_cache.get(serial, True)
            sync_errors = []
            
            # --- CẬP NHẬT DATA ĐỂ GỬI UI ---
            data['is_synced'] = is_synced
            data['sync_errors'] = sync_errors

            # --- 2.4. BẢO VỆ TÊN TRẠM ---
            # Bản tin LWT (offline) không kèm tên → lấy từ DB trước khi broadcast
            if not data.get('name'):
                try:
                    async with device_session_scope() as _db:
                        _dev = await crud.get_device_by_serial(_db, serial)
                        if _dev and _dev.name:
                            data['name'] = _dev.name
                except Exception:
                    pass  # Không tên vẫn broadcast được, không chặn luồng

            # --- 2.5. BROADCAST REAL-TIME TO UI ---
            await manager.broadcast({
                "type": "status_update", 
                "data": data
            })
            
            # --- 2.5. LOGIC QUYẾT ĐỊNH GHI DB ---
            last_write_time = device_write_cache.get(serial, 0)
            last_known_status = device_status_cache.get(serial, "unknown")
            new_status = data.get("status")
            
            should_write_db = False
            
            # Điều kiện 1: Trạng thái Online/Offline thay đổi
            if new_status and new_status != last_known_status:
                should_write_db = True
                if last_known_status == "online" and new_status == "offline":
                    logging.warning(f"⚠️ DEVICE OFFLINE: {serial}")
                elif last_known_status == "offline" and new_status == "online":
                    logging.info(f"✓ Device back online: {serial}")

            # Điều kiện 2: Heartbeat (Quá thời gian chưa ghi)
            elif (now - last_write_time) > DB_WRITE_INTERVAL:
                should_write_db = True
                logging.debug(f"⏰ Heartbeat update for {serial}")
            
            # --- 2.6. THỰC HIỆN GHI DB ---
            if should_write_db:
                # Chuẩn bị dữ liệu sạch cho DB
                db_data = data.copy()
                db_data['timestamp'] = int(now)
                db_data['is_synced'] = is_synced
                
                # 🔥 PRESERVE CONFIG KHI DEVICE OFFLINE
                # Nếu device offline, KHÔNG cập nhật config & chip_type
                # Để tránh để config bị xóa khi device tạm ngoại tuyến
                if new_status == 'offline':
                    # Xóa config từ db_data để CRUD không cập nhật
                    db_data.pop('base_config', None)
                    db_data.pop('service_config', None)
                    db_data.pop('detected_chip_type', None)
                
                # Loại bỏ các field tạm thời
                fields_to_remove = [
                    'bps', 'ntrip_stats', 'base_coords', 'gnss_stats', 'sync_errors'
                ]
                
                for field in fields_to_remove:
                    if field in db_data:
                        del db_data[field]
                
                # Retry logic với exponential backoff
                max_db_retries = 3
                for attempt in range(max_db_retries):
                    try:
                        # Single-writer section: avoids pool exhaustion under reconnect storms.
                        async with mqtt_db_write_lock:
                            async with device_session_scope() as session:
                                await crud.update_or_create_device(
                                    session,
                                    device_data=db_data
                                )
                                await session.commit()
                        
                        device_write_cache[serial] = now
                        if new_status:
                            device_status_cache[serial] = new_status
                        
                        break # Success
                    except Exception as db_error:
                        if attempt < max_db_retries - 1:
                            # Exponential backoff: 0.5s, 1s, 2s
                            await asyncio.sleep(0.5 * (2 ** attempt))
                        else:
                            logging.error(f"❌ DB Write Failed for {serial}: {db_error}")

    except Exception as e:
        logging.error(f"❌ Critical error in handle_message_async: {e}", exc_info=True)

# ==============================================================================
# CONFIG COMPARISON (Giữ nguyên)
# ==============================================================================

def _compare_configs(config1: dict, config2: dict, prefix: str = "", is_relay: bool = False) -> tuple[bool, list[str]]:
    """
    So sánh cấu hình: config1 (Agent/Real), config2 (DB/Target).
    Đã tối ưu để tránh lỗi NoneType và sai số Float.
    """
    errors = []
    
    if config1 is None: 
        config1 = {}
    if config2 is None: 
        config2 = {}

    ignore_fields = {
        'timestamp', 'last_update', 'created_at', 'updated_at',
        'is_provisioned', 'device_name', 'serial',
        'is_synced', 'sync_errors', 'warning',
        'bps', 'ntrip_connected', 'ntrip_status', 'gnss_stats',
        'user_id'
    }

    def normalize_val(v):
        if v is None: 
            return ""
        s = str(v).strip().lower()
        if s == "true": 
            return "true"
        if s == "false": 
            return "false"
        if s == "none" or s == "null": 
            return ""
        # Xử lý số float: làm tròn về 5 chữ số thập phân để so sánh
        try:
            f = float(s)
            return round(f, 5) 
        except ValueError:
            return s

    for key, db_val in config2.items():
        if key in ignore_fields:
            continue
            
        agent_val = config1.get(key)
        current_path = f"{prefix}.{key}" if prefix else key
        
        if agent_val is None and db_val is not None:
            if is_relay:
                continue
            errors.append(f"[{current_path}] Agent=None")
            continue
            
        if isinstance(db_val, dict):
            if not isinstance(agent_val, dict):
                if not db_val and not agent_val: 
                    continue
                errors.append(f"[{current_path}] Type mismatch")
                continue
            
            is_match, sub_errors = _compare_configs(agent_val, db_val, current_path, is_relay=is_relay)
            errors.extend(sub_errors)
            continue

        norm_db = normalize_val(db_val)
        norm_agent = normalize_val(agent_val)
        
        if norm_db != norm_agent:
            if norm_db == "" and norm_agent == "":
                continue
            
            errors.append(f"[{current_path}] DB={db_val} vs Agent={agent_val}")

    return (len(errors) == 0), errors

# ==============================================================================
# START/STOP MQTT
# ==============================================================================

def start_mqtt_loop():
    global main_loop, IS_SHUTTING_DOWN
    try:
        IS_SHUTTING_DOWN = False
        main_loop = asyncio.get_running_loop()
        
        mqtt_client.on_connect = on_connect
        mqtt_client.on_disconnect = on_disconnect
        mqtt_client.on_message = on_message
        
        mqtt_client.connect_async(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=60)
        mqtt_client.loop_start()
        
        # 🔥 START BATCH SYNC CHECKER TASK
        asyncio.create_task(batch_sync_checker())
        
        logging.info("✓ MQTT Loop + Batch Sync Checker Started.")
    except Exception as e:
        logging.error(f"MQTT Start Error: {e}")


def stop_mqtt_loop():
    global IS_SHUTTING_DOWN
    try:
        IS_SHUTTING_DOWN = True
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        logging.info("MQTT Stopped.")
    except:
        pass


def publish_message(topic: str, payload: str, qos: int = 1):
    try:
        if not mqtt_client.is_connected():
            logging.warning("MQTT not connected, cannot publish")
            return
        mqtt_client.publish(topic, payload, qos=qos)
    except Exception as e:
        logging.error(f"Publish Error: {e}")


def set_external_command_handler(handler: Callable[[str, bytes], Awaitable[None]]):
    """Register a coroutine handler for non-device MQTT command topics."""
    global external_command_handler
    external_command_handler = handler
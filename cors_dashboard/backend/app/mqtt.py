# ==============================================================================
# == backend/app/mqtt.py - FIXED: Batch Sync Checking + Pool Optimization
# ==============================================================================
import json
import logging
import asyncio
import os
import time
import hashlib
from typing import Awaitable, Callable, Optional
import paho.mqtt.client as mqtt
from sqlalchemy import select

from . import schemas
from .websocket import manager
from .database import settings, device_session_scope
from . import crud, nmea_parser, models
from .rtcm_signal_decoder import rtcm_signal_decoder

# Serialize MQTT DB writes to avoid connection pool spikes under burst traffic.
mqtt_db_write_lock = asyncio.Lock()


def _fingerprint_token(token: str | None) -> str | None:
    value = str(token or "").strip()
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _version_matches(agent_version: str | None) -> bool:
    if not agent_version:
        return False
    return str(agent_version).strip().upper() == str(settings.APP_VERSION).strip().upper()

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
device_telemetry_cache = {}  # Snapshot live cho bot/API đọc tọa độ và vệ tinh hiện tại
RTCM_SATELLITE_CACHE_TTL_MS = 10_000


def _cache_rtcm_signal_snapshot(serial: str, decoded: dict) -> None:
    """Accumulate fresh RTCM satellites exactly as the dashboard live view does."""
    if not isinstance(decoded, dict):
        return
    now_ms = int(time.time() * 1000)
    live = device_telemetry_cache.setdefault(str(serial), {})
    satellite_map = live.setdefault("rtcm_satellites", {})
    if not isinstance(satellite_map, dict):
        satellite_map = {}
        live["rtcm_satellites"] = satellite_map
    raw_satellites = decoded.get("satellites") or []
    if isinstance(raw_satellites, dict):
        raw_satellites = list(raw_satellites.values())
    for index, item in enumerate(raw_satellites):
        if not isinstance(item, dict):
            continue
        satellite = dict(item)
        try:
            last_seen = int(float(satellite.get("lastSeen") or now_ms))
        except (TypeError, ValueError):
            last_seen = now_ms
        if 0 < last_seen < 10_000_000_000:
            last_seen *= 1000
        if last_seen <= 0 or last_seen > now_ms + 60_000:
            last_seen = now_ms
        satellite["lastSeen"] = last_seen
        key = str(
            satellite.get("id")
            or f"RTCM:{satellite.get('system') or 'un'}:{satellite.get('prn') or index}"
        )
        satellite_map[key] = satellite
    for key, item in list(satellite_map.items()):
        try:
            last_seen = int(item.get("lastSeen") or 0)
        except (TypeError, ValueError, AttributeError):
            last_seen = 0
        if last_seen and now_ms - last_seen > RTCM_SATELLITE_CACHE_TTL_MS:
            satellite_map.pop(key, None)
    fresh_satellites = list(satellite_map.values())
    live["rtcm_signal_data"] = {
        **decoded,
        "satellites": fresh_satellites,
        "satellite_count": len(fresh_satellites),
        "updated_at_ms": now_ms,
    }
    live["satellite_count"] = len(fresh_satellites)

# 🔥 NEW: Global sync check tracking
LAST_GLOBAL_SYNC_CHECK = 0
GLOBAL_SYNC_CHECK_INTERVAL = 60.0  # Batch check 60s/lần

DB_WRITE_INTERVAL = 120.0    # Ghi DB định kỳ 2 phút/lần nếu không có gì thay đổi
IS_SHUTTING_DOWN = False 
external_command_handler: Optional[Callable[[str, bytes], Awaitable[None]]] = None

def invalidate_device_cache(serial: str):
    """Xóa cache của thiết bị để buộc background worker fetch lại từ DB."""
    device_sync_cache.pop(serial, None)
    device_last_sync_check.pop(serial, None)
    device_write_cache.pop(serial, None)
    logging.info(f"♻️ Cache invalidated for device {serial}")

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
                    if device.name:
                        device_name_cache[serial] = device.name
                    
                    # Lấy config từ cache (đã nhận qua MQTT)
                    agent_base = device_sync_cache.get(f"{serial}_base_config", {})
                    agent_service = device_sync_cache.get(f"{serial}_service_config", {})
                    
                    # DB config (chuẩn)
                    db_base = device.base_config or {}
                    db_service = device.service_config or {}
                    
                    is_relay_like = serial.startswith(("RELAY_", "MONITOR_"))
                    
                    # So sánh
                    base_ok, base_errs = _compare_configs(agent_base, db_base, "Base", is_relay=is_relay_like)
                    srv_ok, srv_errs = _compare_configs(agent_service, db_service, "Service", is_relay=is_relay_like)
                    
                    version_ok = _version_matches(device.version)
                    version_errs = [] if version_ok else [f"[Version] Agent={device.version or '??'} vs Server={settings.APP_VERSION}"]
                    is_synced = base_ok and srv_ok and version_ok
                    
                    # Cập nhật cache
                    old_sync = device_sync_cache.get(serial)
                    device_sync_cache[serial] = is_synced
                    if not is_synced:
                        device_sync_cache[f"{serial}_errors"] = base_errs + srv_errs + version_errs
                    else:
                        device_sync_cache.pop(f"{serial}_errors", None)
                    device_last_sync_check[serial] = time.time()
                    
                    # Log nếu sync status thay đổi
                    if old_sync is not None and old_sync != is_synced:
                        if is_synced:
                            logging.info("Device %s SYNCED", serial)
                        else:
                            logging.warning("Device %s OUT OF SYNC: %s", serial, base_errs + srv_errs + version_errs)
                    
                    if device.is_synced != is_synced:
                        device.is_synced = is_synced
                        await session.commit()
                    
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
            if payload.startswith(b'{'):
                try:
                    data = json.loads(payload.decode('ascii'))
                    if data.get("type") == "rtcm_skyview_calculated":
                        # Payload is already fully calculated at the Edge! Just broadcast it.
                        _cache_rtcm_signal_snapshot(serial, data)
                        await manager.broadcast({
                            "type": "rtcm_signal_update",
                            "serial": serial,
                            "data": data
                        })
                        return
                    elif data.get("type") == "rtcm_skyview":
                        decoded_rtcm = rtcm_signal_decoder.decode_json(serial, data)
                        if decoded_rtcm:
                            _cache_rtcm_signal_snapshot(serial, decoded_rtcm)
                            await manager.broadcast({
                                "type": "rtcm_signal_update",
                                "serial": serial,
                                "data": decoded_rtcm
                            })
                        return
                except Exception:
                    pass

            decoded_rtcm = await rtcm_signal_decoder.decode(serial, payload)
            if decoded_rtcm:
                _cache_rtcm_signal_snapshot(serial, decoded_rtcm)
                await manager.broadcast({
                    "type": "rtcm_signal_update",
                    "serial": serial,
                    "data": decoded_rtcm
                })
                return

            try:
                line = payload.decode('ascii', errors='ignore')
                parsed_data = nmea_parser.parse(line)
                if parsed_data:
                    live = device_telemetry_cache.setdefault(serial, {})
                    nmea_live = live.setdefault("nmea_data", {})
                    parsed_type = str(parsed_data.get("type") or "").upper()
                    if parsed_type == "GGA":
                        nmea_live["gga"] = parsed_data
                    elif parsed_type == "GSA":
                        nmea_live["gsa"] = parsed_data
                    elif parsed_type == "GSV":
                        nmea_live["satellites"] = parsed_data.get("satellites") or []
                    elif parsed_type:
                        nmea_live[parsed_type.lower()] = parsed_data
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
            # 🔥 FIX: Đảm bảo data là dict, nếu là list hoặc kiểu khác thì bỏ qua
            if not isinstance(data, dict):
                logging.warning(f"⚠️ Received non-dict payload on topic {topic}: {type(data)}")
                return

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
                data['bps'] = int(ntrip_stats.get('server1_bps', 0) or 0) + int(ntrip_stats.get('server2_bps', 0) or 0)
            
            # --- 2.2. LƯU CONFIG VÀO CACHE ĐỂ BATCH CHECKER DÙNG ---
            # 🔥 QUAN TRỌNG: Lưu config vào cache để batch checker có thể so sánh
            agent_base = data.get('base_config', {})
            agent_service = data.get('service_config', {})
            command_source = data.get('command_source')
            command_source_changed = False
            if isinstance(command_source, dict):
                system_info = data.get('system_info') if isinstance(data.get('system_info'), dict) else {}
                system_info['command_source'] = command_source
                data['system_info'] = system_info
                command_cache_key = f"{serial}_command_source"
                command_source_changed = device_sync_cache.get(command_cache_key) != command_source
                if command_source_changed:
                    device_sync_cache[command_cache_key] = command_source
            
            if agent_base:
                device_sync_cache[f"{serial}_base_config"] = agent_base
            if agent_service:
                device_sync_cache[f"{serial}_service_config"] = agent_service
            
            # --- 2.3. ĐỌC SYNC STATUS TỪ CACHE (KHÔNG MỞ DB) ---
            # 🔥 THAY ĐỔI CHÍNH: Không check DB mỗi message nữa!
            # Batch checker sẽ update cache mỗi 60s
            raw_status = str(data.get("status") or "").strip().lower()
            is_offline_packet = raw_status == "offline"
            is_synced = device_sync_cache.get(serial, True)
            sync_errors = device_sync_cache.get(f"{serial}_errors", [])[:]
            agent_version = data.get("version")
            if agent_version and not _version_matches(agent_version):
                is_synced = False
                err_msg = f"Agent version {agent_version or '??'} != server {settings.APP_VERSION}"
                if err_msg not in sync_errors:
                    sync_errors.append(err_msg)

            db_device = None
            agent_token_fp = data.get("agent_token_fingerprint")
            try:
                async with device_session_scope() as _db:
                    db_device = await crud.get_device_by_serial(_db, serial)
            except Exception as e:
                logging.debug("Device lookup for sync metadata failed for %s: %s", serial, e)

            if db_device:
                agent_reported_status = raw_status
                db_system_info = getattr(db_device, "system_info", None)
                license_bypass = bool(isinstance(db_system_info, dict) and db_system_info.get("auth_license_bypass"))
                expected_fp = _fingerprint_token(getattr(db_device, "device_token", None))
                token_synced = bool(expected_fp and agent_token_fp and expected_fp == agent_token_fp)
                token_check_available = bool(agent_token_fp) or not is_offline_packet
                needs_token_deploy = False if license_bypass else ((not expected_fp) or (token_check_available and not token_synced))
                if needs_token_deploy:
                    is_synced = False
                    if expected_fp:
                        sync_errors.append("Agent token is missing or does not match server token")
                    else:
                        sync_errors.append("Server token has not been issued to this agent")
                    data["status"] = "awaiting_license"
                    data["bps"] = 0
                    data["ntrip_connected"] = False
                    data["ntrip_status"] = {}
                    data["ntrip_stats"] = {}
                system_info = data.get("system_info") if isinstance(data.get("system_info"), dict) else {}
                system_info["auth_status"] = {
                    "server_token_present": bool(expected_fp),
                    "token_required": bool(expected_fp),
                    "token_synced": token_synced if token_check_available else None,
                    "agent_token_present": bool(agent_token_fp) if token_check_available else None,
                    "needs_token_deploy": needs_token_deploy,
                    "license_bypass": license_bypass,
                    "agent_reported_status": agent_reported_status,
                    "token_check_available": token_check_available,
                }
                data["system_info"] = system_info
                if not data.get("name") and db_device.name:
                    data["name"] = db_device.name

            # Ensure savings_stats and antenna_status are persisted in system_info JSONB
            system_info = data.get("system_info") if isinstance(data.get("system_info"), dict) else {}
            savings_stats = data.get("savings_stats")
            if savings_stats and isinstance(savings_stats, dict):
                system_info["savings_stats"] = savings_stats
            antenna_status = data.get("antenna_status")
            if antenna_status and isinstance(antenna_status, dict):
                system_info["antenna_status"] = antenna_status
            data["system_info"] = system_info

            # --- CẬP NHẬT DATA ĐỂ GỬI UI ---
            data['is_synced'] = is_synced
            data['sync_errors'] = sync_errors
            cached_name = device_name_cache.get(serial)
            if cached_name:
                data['name'] = cached_name

            # --- 2.4. BẢO VỆ TÊN & CONFIG TRẠM ---
            # Bản tin LWT (offline) không kèm tên/config -> lấy từ DB trước khi broadcast
            new_status = data.get('status')
            if not data.get('name') or new_status == 'offline':
                try:
                    async with device_session_scope() as _db:
                        _dev = await crud.get_device_by_serial(_db, serial)
                        if _dev:
                            if not data.get('name') and _dev.name:
                                data['name'] = _dev.name
                            # FIX: Khi offline, ghép config từ DB vào broadcast
                            # để UI/map không mất vị trí trạm
                            if new_status == 'offline':
                                if not data.get('base_config') and _dev.base_config:
                                    data['base_config'] = _dev.base_config
                                if not data.get('service_config') and _dev.service_config:
                                    data['service_config'] = _dev.service_config
                                if not data.get('detected_chip_type') and _dev.detected_chip_type:
                                    data['detected_chip_type'] = _dev.detected_chip_type
                except Exception:
                    pass  # Không tên vẫn broadcast được, không chặn luồng

            # --- 2.5. BROADCAST REAL-TIME TO UI ---
            live_snapshot = dict(device_telemetry_cache.get(serial) or {})
            live_snapshot.update(data)
            device_telemetry_cache[serial] = live_snapshot
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
            if command_source_changed:
                should_write_db = True
            elif new_status and new_status != last_known_status:
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
                    'bps', 'base_coords', 'gnss_stats', 'sync_errors', 'rtcm_stats'
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
    
    if not isinstance(config1, dict): 
        config1 = {}
    if not isinstance(config2, dict): 
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
        # Xử lý số float: làm tròn về 9 chữ số thập phân để so sánh
        try:
            f = float(s)
            return round(f, 9) 
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

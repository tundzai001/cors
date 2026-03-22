# ==============================================================================
# == backend/app/crud.py 
# ==============================================================================
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update, func, and_, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from . import models, schemas
from .database import settings
import time
import logging
import asyncio
import re

logger = logging.getLogger(__name__)

async def batch_update_devices(db: AsyncSession, devices_data: list[dict]) -> int:
    """
    Cập nhật hàng loạt thiết bị (Batch Update) sử dụng PostgreSQL UPSERT.
    Hàm này được tối ưu để chịu tải cao, tự động Retry khi gặp Deadlock.
    """
    if not devices_data:
        return 0
    
    # ===== CẤU HÌNH RETRY =====
    MAX_RETRIES = 3
    RETRY_DELAY = 0.1  # 100ms
    
    for attempt in range(MAX_RETRIES):
        try:
            # 1. Deduplication: Chỉ giữ lại update mới nhất cho mỗi Serial
            unique_devices_map = {}
            
            for device in devices_data:
                serial = device.get('serial')
                if not serial:
                    continue

                # --- Tự động tính toán lại ntrip_status nếu thiếu ---
                # (Phòng trường hợp payload chỉ có ntrip_stats thô)
                if 'ntrip_status' not in device and 'ntrip_stats' in device:
                    raw_stats = device.get('ntrip_stats', {})
                    try:
                        s1_bps = int(raw_stats.get('server1_bps', 0))
                        s2_bps = int(raw_stats.get('server2_bps', 0))
                    except:
                        s1_bps, s2_bps = 0, 0
                    
                    device['ntrip_status'] = {
                        "server1": {"connected": s1_bps > 0, "bps": s1_bps},
                        "server2": {"connected": s2_bps > 0, "bps": s2_bps}
                    }
                    device['bps'] = s1_bps + s2_bps
                    device['ntrip_connected'] = (s1_bps + s2_bps) > 0

                # Chuẩn bị record
                # Lưu ý: Dùng .get(key, default) để tránh lỗi KeyError
                device_record = {
                    'serial': serial,
                    'name': device.get('name'), 
                    # Với payload partial (ví dụ offline event), không ép default để tránh ghi đè dữ liệu cũ.
                    'status': device.get('status'),
                    'timestamp': device.get('timestamp', int(time.time())),
                    'bps': device.get('bps'),
                    'ntrip_status': device.get('ntrip_status'),
                    'ntrip_connected': device.get('ntrip_connected'),
                    'detected_chip_type': device.get('detected_chip_type'),
                    'base_config': device.get('base_config'),
                    'service_config': device.get('service_config'),
                    'system_info': device.get('system_info'),
                    'is_provisioned': device.get('is_provisioned'),
                    # is_locked thường ít khi gửi qua websocket status, nên dùng default cẩn thận
                    # Tốt nhất là không update is_locked ở đây nếu không có trong payload
                }

                # Chỉ mang theo is_synced khi payload gửi thật sự.
                # Tránh đưa NULL vào cột NOT NULL khi dùng bulk UPSERT.
                if 'is_synced' in device:
                    device_record['is_synced'] = True if device.get('is_synced') is None else bool(device.get('is_synced'))
                
                unique_devices_map[serial] = device_record
            
            # 2. Sắp xếp để tránh Deadlock (Lock order consistency)
            values_list = sorted(unique_devices_map.values(), key=lambda x: x['serial'])
            
            if not values_list:
                return 0
            
            async def execute_upsert(batch_values: list[dict], update_is_synced: bool = False) -> None:
                if not batch_values:
                    return

                stmt = pg_insert(models.Device).values(batch_values)

                # Định nghĩa các cột cần update khi trùng Serial
                update_dict = {
                    'status': func.coalesce(stmt.excluded.status, models.Device.status),
                    'timestamp': stmt.excluded.timestamp,
                    'bps': func.coalesce(stmt.excluded.bps, models.Device.bps),
                    'ntrip_status': func.coalesce(stmt.excluded.ntrip_status, models.Device.ntrip_status),
                    'ntrip_connected': func.coalesce(stmt.excluded.ntrip_connected, models.Device.ntrip_connected),
                    # Không cho UNKNOWN ghi đè chip type hợp lệ đã lưu trước đó.
                    'detected_chip_type': func.coalesce(func.nullif(stmt.excluded.detected_chip_type, 'UNKNOWN'), models.Device.detected_chip_type),
                    'base_config': func.coalesce(stmt.excluded.base_config, models.Device.base_config),
                    'service_config': func.coalesce(stmt.excluded.service_config, models.Device.service_config),
                    'system_info': func.coalesce(stmt.excluded.system_info, models.Device.system_info),
                    # Chỉ update name nếu có dữ liệu
                    'name': func.coalesce(stmt.excluded.name, models.Device.name),
                    'is_provisioned': func.coalesce(stmt.excluded.is_provisioned, models.Device.is_provisioned),
                }

                if update_is_synced:
                    update_dict['is_synced'] = stmt.excluded.is_synced

                stmt = stmt.on_conflict_do_update(
                    index_elements=['serial'],
                    set_=update_dict,
                    # Ignore out-of-order stale status packets.
                    where=(stmt.excluded.timestamp >= models.Device.timestamp)
                )

                await db.execute(stmt)

            batch_with_is_synced = [item for item in values_list if 'is_synced' in item]
            batch_without_is_synced = [item for item in values_list if 'is_synced' not in item]

            await execute_upsert(batch_without_is_synced, update_is_synced=False)
            await execute_upsert(batch_with_is_synced, update_is_synced=True)
            await db.commit()
            
            # Log thành công (chỉ log khi retry để đỡ rác)
            if attempt > 0:
                logger.info(f"✓ Batch update succeeded on attempt {attempt+1}")
            
            return len(values_list)

        except DBAPIError as e:
            await db.rollback()
            msg = str(e).lower()
            if "deadlock" in msg or "concurrent update" in msg:
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_DELAY * (2 ** attempt)
                    logger.warning(f"⚠️ DB Deadlock (Attempt {attempt+1}). Retrying in {wait}s...")
                    await asyncio.sleep(wait)
                    continue
            logger.error(f"❌ Batch update DB Error: {e}")
            return 0
            
        except Exception as e:
            await db.rollback()
            logger.error(f"❌ Batch update Unexpected Error: {e}")
            return 0
            
    return 0

async def get_device_by_serial(db: AsyncSession, serial: str):
    result = await db.execute(select(models.Device).filter(models.Device.serial == serial))
    return result.scalars().first()

async def get_all_devices(db: AsyncSession):
    result = await db.execute(select(models.Device).order_by(models.Device.name))
    return result.scalars().all()

# ==============================================================================
# VALIDATION FUNCTION: Chặn Ghost Devices
# ==============================================================================

def is_valid_serial(serial: str) -> bool:
    """
    Kiểm tra serial có hợp lệ không.
    
    CÁC ĐỊNH DẠNG HỢP LỆ:
    - Ublox: 10000000xxxxxxxx (16 ký tự hex)
    - LoRa Pi: LP_xxxxxxxxxxxx (MAC address)
    - Relay: RELAY_xxxxxxx
    
    KHÔNG HỢP LỆ:
    - Pi-xxxx (auto-generated name)
    - Garbled text
    - Empty/None
    """
    if not serial or len(serial) < 8:
        return False
    
    # Pattern 1: Ublox (16 hex chars)
    if re.match(r'^[0-9a-fA-F]{16}$', serial):
        return True
    
    # Pattern 2: LoRa Pi (LP_MAC)
    if re.match(r'^LP_[0-9A-F]{12}$', serial, re.IGNORECASE):
        return True
    
    # Pattern 3: Relay (RELAY_XXX)
    if serial.startswith('RELAY_'):
        return True
    
    # Bạn có thể thêm pattern khác nếu cần
    
    return False


async def update_or_create_device(db: AsyncSession, device_data: dict) -> models.Device | None:
    """Tạo mới hoặc cập nhật thiết bị với validation chặt chẽ"""
    
    serial = device_data.get("serial")
    if not serial: 
        return None

    # ✅ BƯỚC 1: VALIDATE SERIAL TRƯỚC KHI XỬ LÝ
    if not is_valid_serial(serial):
        logger.warning(
            f"⚠️ REJECTED INVALID SERIAL: '{serial}' "
            f"(status={device_data.get('status')}, name={device_data.get('name')})"
        )
        return None  # Từ chối xử lý serial không hợp lệ

    def normalize_base_config(base_config: dict) -> dict:
        if not base_config or 'coords' in base_config:
            return base_config or {}
        
        lat = base_config.get('lat')
        lon = base_config.get('lon')
        alt = base_config.get('alt')
        
        if lat and lon:
            normalized = {
                "base_setup_method": base_config.get('base_setup_method', 'FIXED_LLA'),
                "coords": {"lat": lat, "lon": lon, "alt": alt or 0.0}
            }
            for key, value in base_config.items():
                if key not in ['lat', 'lon', 'alt', 'base_setup_method']:
                    normalized[key] = value
            return normalized
        
        return base_config

    existing_device = await get_device_by_serial(db, serial)

    new_base_config = normalize_base_config(device_data.get("base_config", {}))
    new_service_config = device_data.get("service_config", {})
    new_chip_type = device_data.get("detected_chip_type", "UNKNOWN")
    new_name = device_data.get("name")
    
    raw_is_provisioned = device_data.get("is_provisioned") 
    current_ts = device_data.get("timestamp", int(time.time()))
    new_status = device_data.get("status")
    new_is_synced = device_data.get("is_synced", True)
    
    def build_ntrip_status(device_data: dict, service_config: dict) -> dict:
        ntrip_stats = device_data.get("ntrip_stats", {})
        ntrip_status = {}
        if service_config.get("server1_enabled"):
            s1_bps = ntrip_stats.get("server1_bps", 0)
            ntrip_status["server1"] = {"connected": s1_bps > 0, "bps": s1_bps}
        if service_config.get("server2_enabled"):
            s2_bps = ntrip_stats.get("server2_bps", 0)
            ntrip_status["server2"] = {"connected": s2_bps > 0, "bps": s2_bps}
        return ntrip_status
    
    # ✅ BƯỚC 2: CHỈ TẠO MỚI KHI ĐÃ PROVISION
    if not existing_device:
        # ⚠️ QUAN TRỌNG: Chỉ tạo device mới nếu:
        # 1. Có is_provisioned=True (Agent đã được kích hoạt)
        # 2. HOẶC là Relay (luôn provision sẵn)
        
        is_relay = serial.startswith('RELAY_')
        is_explicitly_provisioned = raw_is_provisioned is True
        
        if not is_relay and not is_explicitly_provisioned:
            logger.warning(
                f"⚠️ BLOCKED AUTO-CREATE: Serial '{serial}' is not provisioned. "
                f"Ignoring status update from unprovisioned device."
            )
            return None  # Từ chối tạo device chưa provision
        
        # Nếu đã pass validation → Tạo device mới
        initial_provisioned = is_relay or is_explicitly_provisioned
        initial_ntrip_status = build_ntrip_status(device_data, new_service_config)
        
        values_to_create = {
            "serial": serial,
            "name": new_name or f"New-{serial[-6:]}",  # Tên rõ ràng hơn
            "status": new_status or "unknown",
            "timestamp": current_ts,
            "detected_chip_type": new_chip_type,
            "base_config": new_base_config,
            "service_config": new_service_config,
            "ntrip_connected": device_data.get("ntrip_connected", False),
            "ntrip_status": initial_ntrip_status,
            "is_provisioned": initial_provisioned,
            "is_synced": True
        }
        
        stmt = pg_insert(models.Device).values(values_to_create).on_conflict_do_nothing()
        await db.execute(stmt)
        await db.commit()
        
        logger.info(f"✅ AUTO-CREATED NEW DEVICE: {serial} (provisioned={initial_provisioned})")
        
        return await get_device_by_serial(db, serial)
    
    # ✅ BƯỚC 3: UPDATE EXISTING DEVICE (giữ nguyên logic cũ)
    should_update = False
    update_values = {}

    # Xử lý is_provisioned
    if existing_device.is_provisioned and raw_is_provisioned is False:
        update_values["is_provisioned"] = False
        update_values["name"] = new_name
        should_update = True
    elif not existing_device.is_provisioned and raw_is_provisioned is True:
        update_values["is_provisioned"] = True
        update_values["name"] = new_name
        should_update = True

    # Chip type
    if new_chip_type and new_chip_type != "UNKNOWN" and existing_device.detected_chip_type != new_chip_type:
        update_values["detected_chip_type"] = new_chip_type
        should_update = True

    # ✅ KIỂM TRA BASE CONFIG
    # 🔥 FIX: Không xóa config nếu device gửi dữ liệu rỗng
    # Chỉ cập nhật nếu device gửi config hợp lệ với dữ liệu thực tế
    if new_base_config and existing_device.base_config != new_base_config:
        update_values["base_config"] = new_base_config
        update_values["is_synced"] = False
        should_update = True

    # ✅ KIỂM TRA SERVICE CONFIG
    # 🔥 FIX: Không xóa config nếu device gửi dữ liệu rỗng
    if new_service_config and existing_device.service_config != new_service_config:
        update_values["service_config"] = new_service_config
        should_update = True
    
    # Status
    if new_status and new_status != existing_device.status:
        update_values["status"] = new_status
        update_values["timestamp"] = current_ts
        should_update = True
    else:
        update_values["timestamp"] = current_ts
        should_update = True

    # NTRIP connected
    new_ntrip = device_data.get("ntrip_connected")
    if new_ntrip is not None and new_ntrip != existing_device.ntrip_connected:
        update_values["ntrip_connected"] = new_ntrip
        should_update = True

    # NTRIP status
    current_service_config = new_service_config if new_service_config else existing_device.service_config
    new_ntrip_status = build_ntrip_status(device_data, current_service_config or {})
    
    if new_ntrip_status:
        old_ntrip_status = existing_device.ntrip_status or {}
        if old_ntrip_status != new_ntrip_status:
            update_values["ntrip_status"] = new_ntrip_status
            should_update = True
    
    # ✅ XỬ LÝ is_synced
    if new_is_synced is True and existing_device.is_synced is False:
        update_values["is_synced"] = True
        should_update = True

    # System info
    new_system_info = device_data.get("system_info")
    if new_system_info:
        # Logic so sánh đơn giản để giảm update DB không cần thiết
        old_info = existing_device.system_info or {}
        old_cpu = old_info.get('cpu', {}).get('usage_percent', 0)
        new_cpu = new_system_info.get('cpu', {}).get('usage_percent', 0)
        
        # Chỉ update nếu CPU thay đổi > 5% hoặc chưa có info
        if not old_info or abs(float(old_cpu) - float(new_cpu)) > 5:
            update_values["system_info"] = new_system_info
            should_update = True

    if should_update:
        stmt = (
            update(models.Device)
            .where(models.Device.serial == serial)
            .values(**update_values)
        )
        await db.execute(stmt)
        await db.commit()
        await db.refresh(existing_device)
        return existing_device

    return existing_device

async def get_system_settings(db: AsyncSession) -> dict:
    result = await db.execute(select(models.SystemSetting))
    settings_list = result.scalars().all()
    defaults = {
        'firewall_enabled': True, 
        'vpn_detection_enabled': True, 
        'pattern_analysis_enabled': True,
        'caster_host': settings.CASTER_HOST or 'aitogy.com.vn',
        'caster_port': settings.CASTER_PORT or 2101
    }
    settings_dict = {s.key: s.value for s in settings_list}
    for key, value in defaults.items():
        settings_dict.setdefault(key, value)
    return settings_dict

async def update_system_settings(db: AsyncSession, settings_to_update: dict):
    for key, value in settings_to_update.items():
        stmt = pg_insert(models.SystemSetting).values(key=key, value=value)
        stmt = stmt.on_conflict_do_update(index_elements=['key'], set_={'value': value})
        await db.execute(stmt)
    await db.commit()

async def get_user_by_username(db: AsyncSession, username: str) -> models.User | None:
    result = await db.execute(select(models.User).filter(models.User.username == username))
    return result.scalars().first()

async def get_user_by_id(db: AsyncSession, user_id: int) -> models.User | None:
    result = await db.execute(select(models.User).filter(models.User.id == user_id))
    return result.scalars().first()

async def get_all_users(db: AsyncSession) -> list[models.User]:
    result = await db.execute(select(models.User).order_by(models.User.username))
    return result.scalars().all()

async def create_user(db: AsyncSession, username: str, hashed_password: str, role: str, full_name: str | None = None) -> models.User:
    db_user = models.User(
        username=username, 
        hashed_password=hashed_password, 
        full_name=full_name, 
        role=role, 
        is_active=True, 
        created_at=int(time.time())
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

async def update_user(db: AsyncSession, user_id: int, update_data: dict) -> models.User | None:
    user = await get_user_by_id(db, user_id)
    if not user: 
        return None
    for key, value in update_data.items():
        if hasattr(user, key): 
            setattr(user, key, value)
    await db.commit()
    await db.refresh(user)
    return user

async def get_devices_by_user_id(db: AsyncSession, user_id: int) -> list[models.Device]:
    result = await db.execute(
        select(models.Device)
        .filter(models.Device.user_id == user_id)
        .order_by(models.Device.name)
    )
    return result.scalars().all()

async def delete_user(db: AsyncSession, user_id: int) -> bool:
    user = await get_user_by_id(db, user_id)
    if not user: 
        return False
    await db.delete(user)
    await db.commit()
    return True

async def create_rover_account(
    db: AsyncSession, 
    username: str, 
    password: str, 
    base_serial: str, 
    creator_id: int,
    mountpoints: str = None 
) -> models.RoverAccount:
    db_rover = models.RoverAccount(
        username=username, 
        password=password, 
        base_serial=base_serial, 
        mountpoints=mountpoints, 
        created_by_user_id=creator_id, 
        is_active=True, 
        created_at=int(time.time())
    )
    db.add(db_rover)
    await db.commit()
    await db.refresh(db_rover)
    return db_rover

async def get_rovers_by_base_serial(db: AsyncSession, base_serial: str) -> list[models.RoverAccount]:
    result = await db.execute(
        select(models.RoverAccount)
        .filter(models.RoverAccount.base_serial == base_serial)
    )
    return result.scalars().all()

async def get_rover_by_id(db: AsyncSession, rover_id: int) -> models.RoverAccount | None:
    result = await db.execute(
        select(models.RoverAccount)
        .filter(models.RoverAccount.id == rover_id)
    )
    return result.scalars().first()

async def get_rover_by_username(db: AsyncSession, username: str) -> models.RoverAccount | None:
    result = await db.execute(
        select(models.RoverAccount)
        .filter(models.RoverAccount.username == username)
    )
    return result.scalars().first()

async def update_rover_account(db: AsyncSession, rover_id: int, update_data: dict) -> models.RoverAccount | None:
    rover = await get_rover_by_id(db, rover_id)
    if not rover: 
        return None
    
    for key, value in update_data.items():
        # Chỉ cập nhật nếu value có giá trị (không None)
        if value is not None and hasattr(rover, key): 
            setattr(rover, key, value)
            
    await db.commit()
    await db.refresh(rover)
    return rover

async def delete_rover_account(db: AsyncSession, rover_id: int) -> bool:
    rover = await get_rover_by_id(db, rover_id)
    if not rover: 
        return False
    await db.delete(rover)
    await db.commit()
    return True

async def get_all_rovers_by_user(db: AsyncSession, user_id: int, role: str) -> list[models.RoverAccount]:
    if role == "admin":
        result = await db.execute(select(models.RoverAccount).order_by(models.RoverAccount.created_at.desc()))
        return result.scalars().all()
    
    if role == "coordinator":
        result = await db.execute(
            select(models.RoverAccount)
            .filter(models.RoverAccount.created_by_user_id == user_id)
            .order_by(models.RoverAccount.created_at.desc())
        )
        return result.scalars().all()
    
    return []

# ==============================================================================
# CLEANUP GHOST DEVICES: Xóa các device không hợp lệ (optional)
# ==============================================================================

async def cleanup_ghost_devices(db: AsyncSession) -> int:
    """
    Xóa các ghost devices không hợp lệ.
    Chạy 1 lần khi backend khởi động.
    """
    
    result = await db.execute(select(models.Device))
    all_devices = result.scalars().all()
    
    deleted_count = 0
    
    for device in all_devices:
        # Kiểm tra các dấu hiệu ghost device:
        # 1. Serial không hợp lệ
        # 2. Tên có dạng "Pi-XXXX" và chưa provision
        # 3. Không có base_config và service_config
        
        is_ghost = False
        reason = ""
        
        if not is_valid_serial(device.serial):
            is_ghost = True
            reason = "Invalid serial format"
        
        elif (device.name and device.name.startswith('Pi-') and 
              not device.is_provisioned and 
              not device.base_config and 
              not device.service_config):
            is_ghost = True
            reason = "Auto-generated unprovision device"
        
        if is_ghost:
            logger.warning(f"🗑️ Deleting ghost device: {device.serial} ({device.name}) - {reason}")
            await db.delete(device)
            deleted_count += 1
    
    if deleted_count > 0:
        await db.commit()
        logger.info(f"✅ Cleaned up {deleted_count} ghost devices")
    
    return deleted_count

async def create_activity_log(db: AsyncSession, log: schemas.ActivityLogCreate):
    db_log = models.ActivityLog(**log.dict())
    db.add(db_log)
    await db.commit()
    await db.refresh(db_log)
    return db_log

# 2. LẤY FEED (Mới nhất trước)
async def get_activity_logs(db: AsyncSession, limit: int = 15):
    result = await db.execute(
        select(models.ActivityLog)
        .order_by(models.ActivityLog.timestamp.desc())
        .limit(limit)
    )
    return result.scalars().all()

# 3. TÍNH TOÁN HEATMAP (Logic quan trọng)
async def get_activity_heatmap(db: AsyncSession):
    now = int(time.time())
    seven_days_ago = now - (7 * 24 * 3600)
    
    result = await db.execute(
        select(models.ActivityLog.timestamp)
        .where(models.ActivityLog.timestamp >= seven_days_ago)
    )
    timestamps = result.scalars().all()
    
    grid = [[0] * 24 for _ in range(7)]
    
    current_day_start = now - (now % 86400) # 00:00 hôm nay
    
    for ts in timestamps:
        day_diff = (current_day_start - (ts - (ts % 86400))) // 86400
        
        if 0 <= day_diff < 7:
            hour = int((ts + 7*3600) / 3600) % 24
            grid[int(day_diff)][hour] += 1
            
    return grid 
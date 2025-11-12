# backend/app/crud.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from . import models, schemas
import time

# --- DEVICE OPERATIONS ---
async def get_device_by_serial(db: AsyncSession, serial: str):
    result = await db.execute(select(models.Device).filter(models.Device.serial == serial))
    return result.scalars().first()

async def get_all_devices(db: AsyncSession):
    result = await db.execute(select(models.Device).order_by(models.Device.name))
    return result.scalars().all()

async def update_or_create_device(db: AsyncSession, device_data: dict) -> models.Device | None:
    """
    Cập nhật hoặc tạo thiết bị.
    
    LOGIC NÂNG CẤP:
    - Nếu nhận được is_provisioned: false từ một thiết bị đã tồn tại,
      coi đây là một tín hiệu RESET và xóa sạch cấu hình cũ của nó.
    """
    serial = device_data.get("serial")
    if not serial:
        return None

    existing_device = await get_device_by_serial(db, serial)
    is_being_reset = existing_device and not device_data.get("is_provisioned", True)

    # --- Trường hợp 1: Thiết bị bị reset ---
    if is_being_reset:
        print(f"DEBUG CRUD: Detected RESET for device '{serial}'. Wiping config.")
        existing_device.status = device_data.get("status", "unknown")
        existing_device.timestamp = device_data.get("timestamp", 0)
        existing_device.detected_chip_type = device_data.get("detected_chip_type", "UNKNOWN")
        
        # Xóa sạch dữ liệu cũ
        existing_device.name = device_data.get("name", f"Pi-{serial[-4:]}") # Cập nhật tên mặc định mới
        existing_device.base_config = {}
        existing_device.service_config = {}
        existing_device.user_id = None # Hủy gán khỏi user
        existing_device.bps = 0
        existing_device.ntrip_connected = False
        existing_device.ntrip_status = {}
        # is_locked giữ nguyên vì đây là hành động của admin

        await db.commit()
        await db.refresh(existing_device)
        return existing_device

    # --- Trường hợp 2: Cập nhật thông thường hoặc tạo mới ---
    else:
        ntrip_stats = device_data.get('ntrip_stats', {})
        bps = sum(ntrip_stats.values()) if isinstance(ntrip_stats, dict) else 0

        values_to_set = {
            "name": device_data.get("name", f"Pi-{serial[-4:]}"),
            "status": device_data.get("status", "unknown"),
            "timestamp": device_data.get("timestamp", 0),
            "bps": bps,
            "detected_chip_type": device_data.get("detected_chip_type", "UNKNOWN"),
            "base_config": device_data.get("base_config", {}),
            "service_config": device_data.get("service_config", {}),
            "ntrip_connected": device_data.get("ntrip_connected", False),
            "ntrip_status": device_data.get("ntrip_status", {}),
            "is_locked": device_data.get("is_locked", False)
        }
        
        # Nếu thiết bị chưa tồn tại, thêm serial vào để tạo mới
        if not existing_device:
            values_to_set["serial"] = serial
            stmt = sqlite_insert(models.Device).values(values_to_set)
        else:
            # Nếu đã tồn tại, dùng cú pháp update thông thường
            stmt = (
                sqlite_insert(models.Device)
                .values(serial=serial) # Chỉ cần serial để tìm
                .on_conflict_do_update(
                    index_elements=['serial'],
                    set_=values_to_set
                )
            )

        # Thực thi và trả về đối tượng đã được cập nhật/tạo mới
        result = await db.execute(stmt.returning(models.Device))
        await db.commit()
        
        scalar_result = result.scalar_one_or_none()
        # Nếu dùng upsert mà không có returning, cần query lại
        if not scalar_result:
             scalar_result = await get_device_by_serial(db, serial)
             
        return scalar_result

# --- USER OPERATIONS ---
async def get_user_by_username(db: AsyncSession, username: str) -> models.User | None:
    result = await db.execute(
        select(models.User).filter(models.User.username == username)
    )
    return result.scalars().first()

async def get_user_by_id(db: AsyncSession, user_id: int) -> models.User | None:
    result = await db.execute(
        select(models.User).filter(models.User.id == user_id)
    )
    return result.scalars().first()

async def get_all_users(db: AsyncSession) -> list[models.User]:
    result = await db.execute(select(models.User).order_by(models.User.username))
    return result.scalars().all()

async def create_user(
    db: AsyncSession, 
    username: str, 
    hashed_password: str, 
    role: str,
    full_name: str | None = None
) -> models.User:
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

async def update_user(
    db: AsyncSession, 
    user_id: int, 
    update_data: dict
) -> models.User | None:
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
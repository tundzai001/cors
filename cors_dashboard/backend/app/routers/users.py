from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from ..database import get_auth_db, get_db
from .. import crud, models, schemas, auth
from ..command_dispatcher import send_command_to_pi
from ..websocket import manager as ui_manager
import logging

logger = logging.getLogger(__name__)

# === HELPER: Lấy danh sách ownership từ SystemSettings ===
async def _get_ownership_info(db: AsyncSession, creator_id: int) -> tuple[set[int], set[int]]:
    """Truy vấn SystemSettings để tìm thông tin ownership.
    Returns:
        - my_created_ids: set user_id do creator_id tạo
        - all_owned_ids: set tất cả user_id đã có owner (bất kỳ ai tạo)
    Key format: user_created_by_{user_id} = creator_id"""
    result = await db.execute(
        select(models.SystemSetting).where(
            models.SystemSetting.key.like('user_created_by_%')
        )
    )
    settings = result.scalars().all()
    my_created_ids = set()
    all_owned_ids = set()
    for s in settings:
        try:
            uid = int(s.key.replace('user_created_by_', ''))
            all_owned_ids.add(uid)
            if s.value == creator_id:
                my_created_ids.add(uid)
        except (ValueError, TypeError):
            pass
    return my_created_ids, all_owned_ids

router = APIRouter(tags=["Users"])

# === USER MANAGEMENT ===
@router.get("/api/users", response_model=list[schemas.UserResponse])
async def get_users(db: AsyncSession = Depends(get_auth_db),
                    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    users = await crud.get_all_users(db)
    response_list = []
    
    # Nếu không phải admin, chỉ hiển thị chính mình + viewer do mình tạo
    if current_user.role != 'admin':
        my_created_ids, _ = await _get_ownership_info(db, current_user.id)
    
    for u in users:
        if current_user.role != 'admin':
            # Luôn hiển thị chính mình
            if u.id == current_user.id:
                pass  # OK, cho phép
            elif u.role == 'viewer' and u.id in my_created_ids:
                pass  # Viewer do mình tạo → cho phép
            else:
                continue  # Ẩn tất cả user khác
        permissions = auth.get_user_permissions(u)
        user_resp = schemas.UserResponse.from_orm(u)
        user_resp.permissions = permissions
        response_list.append(user_resp)
    return response_list

@router.get("/api/users/{user_id}", response_model=schemas.UserResponse)
async def get_user_details(user_id: int, db: AsyncSession = Depends(get_auth_db),
                           current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    user = await crud.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if current_user.role != 'admin':
        if user.id != current_user.id:
            # Chỉ cho xem viewer do mình tạo
            my_created_ids, _ = await _get_ownership_info(db, current_user.id)
            is_my_viewer = user.role == 'viewer' and user.id in my_created_ids
            if not is_my_viewer:
                raise HTTPException(status_code=403, detail="Không có quyền xem thông tin tài khoản này")
    permissions = auth.get_user_permissions(user)
    user_response = schemas.UserResponse.from_orm(user)
    user_response.permissions = permissions
    return user_response

@router.put("/api/devices/{serial}/name")
async def rename_device(
    serial: str,
    request: schemas.DeviceRename, 
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_DEVICE_NAME))):
    # 1. Kiểm tra thiết bị
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

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

    await crud.log_system_event(
        db=db,
        type="info",
        message=f"Device '{device.name}' renamed to '{request.name}' by {current_user.username}",
        user_id=current_user.id,
        serial=serial
    )
    
    return {"status": "success", "name": device.name}

@router.put("/api/devices/{serial}/serial")
async def rename_device_serial(
    serial: str,
    request: schemas.DeviceRenameSerial, 
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_DEVICE_NAME))):
    
    if current_user.role.strip().lower() != 'admin':
        raise HTTPException(status_code=403, detail=f"Chỉ admin mới có quyền đổi serial (Quyền hiện tại: {current_user.role})")

    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
        
    if not request.new_serial.strip():
        raise HTTPException(status_code=400, detail="Serial không được để trống")
        
    existing = await crud.get_device_by_serial(db, request.new_serial)
    if existing:
        raise HTTPException(status_code=400, detail="Serial mới đã tồn tại")

    try:
        # Gửi lệnh đổi serial qua MQTT để geotek script tự động cập nhật cấu hình
        try:
            await send_command_to_pi(serial, {"command": "CHANGE_SERIAL", "payload": {"new_serial": request.new_serial}})
        except Exception as e:
            logger.warning(f"Không thể gửi lệnh CHANGE_SERIAL qua MQTT cho {serial}: {e}")

        from sqlalchemy import text
        await db.execute(
            text("UPDATE devices SET serial = :new_serial WHERE serial = :old_serial"),
            {"new_serial": request.new_serial, "old_serial": serial}
        )
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
        
    await crud.log_system_event(
        db=db,
        type="warning",
        message=f"Device serial changed from '{serial}' to '{request.new_serial}' by {current_user.username}",
        user_id=current_user.id,
        serial=request.new_serial
    )
    
    return {"status": "success", "new_serial": request.new_serial}

@router.post("/api/users", response_model=schemas.UserResponse)
async def create_user(
    user_data: schemas.UserCreate,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    
    if current_user.role != 'admin' and user_data.role != 'viewer':
        raise HTTPException(status_code=403, detail="Chỉ admin mới được tạo tài khoản khác ngoài Viewer")

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
        
    parent_id = current_user.id if current_user.role != 'admin' else None
    new_user = await crud.create_user(
        auth_db, 
        username=user_data.username, 
        hashed_password=hashed_password,
        role=user_data.role, 
        full_name=user_data.full_name,
        is_active=user_data.is_active,
        can_create_rovers=user_data.can_create_rovers,
        extra_mountpoints=user_data.extra_mountpoints,
        parent_id=parent_id,
        allowed_bases=user_data.allowed_bases,
        allowed_rovers=user_data.allowed_rovers
    )
    
    if user_data.assigned_devices:
        if new_user.role in ('coordinator', 'operator'):
            for serial in user_data.assigned_devices:
                device = await crud.get_device_by_serial(devices_db, serial)
                if device:
                    device.user_id = new_user.id
            await devices_db.commit()
    
    permissions = auth.get_user_permissions(new_user)
    user_response = schemas.UserResponse.from_orm(new_user)
    user_response.permissions = permissions
    await crud.log_system_event(
        db=devices_db,
        type="success",
        message=f"'{current_user.username}' created user '{new_user.username}' (role: {new_user.role})",
        user_id=current_user.id
    )
    
    return user_response

@router.put("/api/users/{user_id}", response_model=schemas.UserResponse)
async def update_user(
    user_id: int, 
    user_data: schemas.UserUpdate,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    
    user_to_update = await crud.get_user_by_id(auth_db, user_id)
    if not user_to_update:
        raise HTTPException(status_code=404, detail="User not found")

    if current_user.role != 'admin':
        if user_to_update.id != current_user.id:
            # Chỉ được sửa viewer do mình tạo
            my_created_ids, _ = await _get_ownership_info(auth_db, current_user.id)
            is_my_viewer = user_to_update.role == 'viewer' and user_to_update.id in my_created_ids
            if not is_my_viewer:
                raise HTTPException(status_code=403, detail="Chỉ có thể sửa đổi tài khoản Viewer do bạn tạo")
        if user_data.role and user_data.role != user_to_update.role:
            raise HTTPException(status_code=403, detail="Không có quyền thay đổi Role")

    if current_user.id == user_id and user_data.is_active is False:
        raise HTTPException(status_code=400, detail="Không thể vô hiệu hóa tài khoản của chính bạn")

    update_dict = user_data.dict(exclude_unset=True)
    
    # ===================================================================
    # FIX: LOGIC CẬP NHẬT DANH SÁCH THIẾT BỊ (ASSIGN/UNASSIGN)
    # ===================================================================
    assigned_serials = update_dict.pop("assigned_devices", None)
    
    if assigned_serials is not None:
        target_role = update_dict.get('role', user_to_update.role)
        
        # 1. Lấy tất cả thiết bị HIỆN TẠI đang gán cho user nÃ y (chủ sở hữu)
        current_devices = await crud.get_devices_by_user_id(devices_db, user_id)
        
        # 2. Gỡ bỏ tất cả ownership trước (Reset về None)
        for dev in current_devices:
            dev.user_id = None
            # Broadcast cập nhật UI: Trạm này đã bị gỡ
            await ui_manager.broadcast({
                "type": "status_update",
                "data": schemas.Device.from_orm(dev).model_dump()
            })
        
        # 3. Áp dụng gán mới tùy theo vai trò
        if target_role in ('coordinator', 'operator'):
            for serial in assigned_serials:
                device = await crud.get_device_by_serial(devices_db, serial)
                if device:
                    device.user_id = user_id
                    # Broadcast cập nhật UI: Trạm nÃ y đã có chủ mới
                    await ui_manager.broadcast({
                        "type": "status_update",
                        "data": schemas.Device.from_orm(device).model_dump()
                    })
            await devices_db.commit()
            
            from sqlalchemy import delete
            from .. import models
            # Xóa cấu hình viewer cũ nếu có
            await auth_db.execute(delete(models.SystemSetting).where(models.SystemSetting.key == f'viewer_devices_{user_id}'))
            await auth_db.commit()
            
        elif target_role == 'viewer':
            await devices_db.commit() # commit việc gỡ ownership
            # Broadcast để UI Viewer cập nhật ngay lập tức
            await ui_manager.broadcast({
                "type": "user_assignment_updated",
                "data": {
                    "user_id": user_id,
                    "role": "viewer"
                }
            })
        else:
            await devices_db.commit()
    # ===================================================================

    if 'password' in update_dict and update_dict['password']:
        update_dict['hashed_password'] = await auth.get_password_hash(update_dict.pop('password'))
    
    updated_user = await crud.update_user(auth_db, user_id, update_dict)
    
    permissions = auth.get_user_permissions(updated_user)
    user_response = schemas.UserResponse.from_orm(updated_user)
    user_response.permissions = permissions
    
    await crud.log_system_event(
        db=devices_db,
        type="info",
        message=f"Admin '{current_user.username}' updated user '{updated_user.username}'",
        user_id=current_user.id
    )
    
    return user_response

@router.delete("/api/users/{user_id}")
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
        
    if current_user.role != 'admin':
        # Chỉ được xóa viewer do mình tạo
        my_created_ids, _ = await _get_ownership_info(auth_db, current_user.id)
        is_my_viewer = user.role == 'viewer' and user.id in my_created_ids
        if not is_my_viewer:
            raise HTTPException(status_code=403, detail="Chỉ có thể xóa tài khoản Viewer do bạn tạo")
    
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

    await crud.log_system_event(
        db=devices_db,
        type="warning",
        message=f"Admin '{current_user.username}' deleted user '{user.username}' and released devices.",
        user_id=current_user.id
    )
    
    return {"status": "deleted", "user_id": user_id}





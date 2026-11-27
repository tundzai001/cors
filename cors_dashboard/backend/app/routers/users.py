from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from ..database import get_auth_db, get_db
from .. import crud, models, schemas, auth
from .. import mqtt as mqtt_handler
from ..command_dispatcher import send_command_to_pi
from ..mobile_base import require_editable_base
from ..websocket import manager as ui_manager
import logging

logger = logging.getLogger(__name__)
USER_NOT_FOUND = "User not found"

# === HELPER: Coordinator-created viewer ownership ===
async def _get_ownership_info(db: AsyncSession, creator_id: int) -> tuple[set[int], set[int]]:
    """Find viewers created by this coordinator, including legacy records.
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
    children = await db.execute(select(models.User.id).where(models.User.parent_id == creator_id))
    my_created_ids.update(children.scalars().all())
    return my_created_ids, all_owned_ids

router = APIRouter(tags=["Users"])


async def _notify_station_scope_changed(db: AsyncSession, user_ids: set[int]) -> None:
    """Refresh affected coordinators and the viewers whose scope depends on them."""
    if not user_ids:
        return
    children = await db.execute(select(models.User.id).where(models.User.parent_id.in_(user_ids)))
    for affected_id in user_ids | set(children.scalars().all()):
        await ui_manager.broadcast({"type": "user_assignment_updated", "data": {"user_id": affected_id}})

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

@router.get("/api/users/{user_id}", response_model=schemas.UserResponse, responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def get_user_details(user_id: int, db: AsyncSession = Depends(get_auth_db),
                           current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    user = await crud.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND)
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

@router.put("/api/devices/{serial}/name", dependencies=[Depends(require_editable_base)], responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def rename_device(
    serial: str,
    request: schemas.DeviceRename, 
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.EDIT_DEVICE_NAME))):
    # 1. Kiểm tra thiết bị
    device = await crud.get_device_by_serial(db, serial)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if current_user.role == auth.Role.COORDINATOR and device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Trạm này chỉ được xem")

    if not request.name.strip():
        raise HTTPException(status_code=400, detail="Tên không được để trống")

    if crud.is_mobile_base(serial):
        from ..auto_cors_source import esp_rover_name
        try:
            esp_rover_name(device, request.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 2. Cập nhật Database
    device.name = request.name.strip()
    await db.commit()
    await mqtt_handler.cache_device_metadata(device)
    await ui_manager.broadcast({"type": "status_update", "data": {"serial": serial, "name": device.name}})
    
    # An offline agent cannot receive this non-retained command. Publishing it
    # makes relay listeners without this serial report a false awaiting_config.
    if str(device.status or "").lower() != "offline":
        try:
            await send_command_to_pi(serial, {
                "command": "PROVISION_DEVICE",
                "payload": {"name": device.name}
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

@router.put("/api/devices/{serial}/serial", dependencies=[Depends(require_editable_base)], responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}})
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
            logger.warning("Không thể gửi lệnh CHANGE_SERIAL qua MQTT (%s)", type(e).__name__)

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

@router.post("/api/users", response_model=schemas.UserResponse, responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}})
async def create_user(
    user_data: schemas.UserCreate,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    
    if current_user.role != 'admin' and user_data.role != 'viewer':
        raise HTTPException(status_code=403, detail="Chỉ admin mới được tạo tài khoản khác ngoài Viewer")
    if current_user.role == 'coordinator':
        visible = await crud.get_coordinator_visible_base_serials(devices_db, current_user)
        requested = {str(serial).strip().upper() for serial in user_data.allowed_bases or [] if str(serial).strip()}
        if not requested.issubset(visible):
            raise HTTPException(status_code=403, detail="Chỉ được cấp trạm trong phạm vi của bạn")

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
        can_view_rovers=user_data.can_view_rovers if current_user.role == 'admin' else False,
        can_create_rovers=user_data.can_create_rovers if current_user.role == 'admin' else False,
        can_create_users=user_data.can_create_users if current_user.role == 'admin' and user_data.role == 'coordinator' else False,
        can_view_mobile_bases=user_data.can_view_mobile_bases if (
            current_user.role == 'admin' or (current_user.role == 'coordinator' and getattr(current_user, 'can_view_mobile_bases', False))
        ) else False,
        extra_mountpoints=user_data.extra_mountpoints if current_user.role == 'admin' else None,
        parent_id=parent_id,
        allowed_bases=user_data.allowed_bases,
        view_only_bases=user_data.view_only_bases if current_user.role == 'admin' and user_data.role == 'coordinator' else None,
        allowed_rovers=user_data.allowed_rovers
    )
    
    previous_owners = set()
    if user_data.assigned_devices:
        if new_user.role == 'coordinator':
            for serial in user_data.assigned_devices:
                device = await crud.get_device_by_serial(devices_db, serial)
                if device:
                    if device.user_id is not None:
                        previous_owners.add(device.user_id)
                    device.user_id = new_user.id
            await devices_db.commit()
            await _notify_station_scope_changed(auth_db, previous_owners)
    
    permissions = auth.get_user_permissions(new_user)
    user_response = schemas.UserResponse.model_validate(new_user)
    user_response.permissions = permissions
    await crud.log_system_event(
        db=devices_db,
        type="success",
        message=f"'{current_user.username}' created user '{new_user.username}' (role: {new_user.role})",
        user_id=current_user.id
    )
    
    return user_response

@router.put("/api/users/{user_id}", response_model=schemas.UserResponse, responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def update_user(
    user_id: int, 
    user_data: schemas.UserUpdate,
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    
    user_to_update = await crud.get_user_by_id(auth_db, user_id)
    if not user_to_update:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND)

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
    if current_user.role != 'admin':
        forbidden = {'assigned_devices', 'view_only_bases', 'can_view_rovers', 'can_create_rovers', 'can_create_users', 'extra_mountpoints'}
        if not (current_user.role == 'coordinator' and getattr(current_user, 'can_view_mobile_bases', False)):
            forbidden.add('can_view_mobile_bases')
        if any(field in update_dict for field in forbidden):
            raise HTTPException(status_code=403, detail="Chỉ admin được thay đổi quyền tài khoản")
        if 'allowed_bases' in update_dict:
            if user_to_update.id == current_user.id:
                raise HTTPException(status_code=403, detail="Không thể tự cấp quyền trạm")
            visible = await crud.get_coordinator_visible_base_serials(devices_db, current_user)
            requested = {str(serial).strip().upper() for serial in update_dict['allowed_bases'] or [] if str(serial).strip()}
            if not requested.issubset(visible):
                raise HTTPException(status_code=403, detail="Chỉ được cấp trạm trong phạm vi của bạn")
    
    # ===================================================================
    # FIX: LOGIC CẬP NHẬT DANH SÁCH THIẾT BỊ (ASSIGN/UNASSIGN)
    # ===================================================================
    assigned_serials = update_dict.pop("assigned_devices", None)
    affected_users = {user_id}
    
    if assigned_serials is not None:
        target_role = update_dict.get('role', user_to_update.role)
        
        # 1. Lấy tất cả thiết bị HIỆN TẠI đang gán cho user này (chủ sở hữu)
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
        if target_role == 'coordinator':
            for serial in assigned_serials:
                device = await crud.get_device_by_serial(devices_db, serial)
                if device:
                    if device.user_id is not None:
                        affected_users.add(device.user_id)
                    device.user_id = user_id
                    # Broadcast cập nhật UI: Trạm này đã có chủ mới
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
        else:
            await devices_db.commit()
    # ===================================================================

    if 'password' in update_dict and update_dict['password']:
        update_dict['hashed_password'] = await auth.get_password_hash(update_dict.pop('password'))
    
    updated_user = await crud.update_user(auth_db, user_id, update_dict)

    if assigned_serials is not None or set(update_dict).intersection({
        'allowed_bases', 'view_only_bases', 'allowed_rovers', 'extra_mountpoints',
        'can_view_rovers', 'can_create_rovers', 'can_create_users', 'can_view_mobile_bases', 'role', 'is_active',
    }):
        # Notify after commit so the browser reloads the new device scope.
        await _notify_station_scope_changed(auth_db, affected_users)
    
    permissions = auth.get_user_permissions(updated_user)
    user_response = schemas.UserResponse.model_validate(updated_user)
    user_response.permissions = permissions
    
    await crud.log_system_event(
        db=devices_db,
        type="info",
        message=f"Admin '{current_user.username}' updated user '{updated_user.username}'",
        user_id=current_user.id
    )
    
    return user_response

@router.delete("/api/users/{user_id}", responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def delete_user(
    user_id: int, 
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.require_permission(auth.Permission.MANAGE_USERS))):
    
    # 1. Kiểm tra user tồn tại
    user = await crud.get_user_by_id(auth_db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND)
    
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
        logger.exception(f"Error unlinking devices: {e}")
        # Không raise lỗi để tiếp tục xóa user
    # ===================================================================

    # Capture children before deleting their parent so open pages lose stale scope.
    children = await auth_db.execute(select(models.User.id).where(models.User.parent_id == user_id))
    child_ids = set(children.scalars().all())

    # 3. Xóa user trong Auth DB
    success = await crud.delete_user(auth_db, user_id) 
    if not success:
         raise HTTPException(status_code=404, detail="User not found during deletion")
    for child_id in child_ids:
        await ui_manager.broadcast({"type": "user_assignment_updated", "data": {"user_id": child_id}})

    await crud.log_system_event(
        db=devices_db,
        type="warning",
        message=f"Admin '{current_user.username}' deleted user '{user.username}' and released devices.",
        user_id=current_user.id
    )
    
    return {"status": "deleted", "user_id": user_id}

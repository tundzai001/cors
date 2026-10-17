"""Authentication and current-user API routes."""

import logging
import secrets
import time
from datetime import datetime, timedelta
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from . import auth, crud, models, schemas
from .database import get_auth_db, get_db, settings
from .security import rate_limiter

logger = logging.getLogger("app.main")


login_router = APIRouter()

@login_router.post("/api/auth/login", response_model=schemas.Token)
async def login(
    request: Request,
    login_data: schemas.LoginRequest = Body(...),
    auth_db: AsyncSession = Depends(get_auth_db),
    devices_db: AsyncSession = Depends(get_db)
):
    device_id = (getattr(login_data, "device_id", None) or "").strip()
    if not device_id:
        device_id = f"dev_{secrets.token_hex(8)}"
    local_ip = (getattr(login_data, "local_ip", None) or "").strip() or None
    device_name = (getattr(login_data, "device_name", None) or "").strip() or None

    # Kiểm tra thiết bị có bị chặn không (chỉ chặn riêng máy này, không chặn cả mạng IP)
    if auth.is_device_blocked(device_id):
        logger.warning(f"❌ Login rejected: Blocked device '{device_id}' from {request.client.host if request else 'unknown'}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Thiết bị (máy tính) này đã bị quản trị viên chặn truy cập."
        )

    # Log login attempt
    logger.info(f"🔑 Login attempt for user: '{login_data.username}' from {request.client.host if request else 'unknown'} (device: {device_id})")
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
        f"from {request.client.host} (local_ip: {local_ip}, device: {device_name})"
    )

    await rate_limiter.check_and_record(
        auth_db,
        request,
        username=user.username,
        is_success=True
    )

    # Mặc định 24h nếu không chọn remember_me, 1 năm nếu có chọn
    if getattr(login_data, "remember_me", False):
        expires_delta = timedelta(minutes=auth.ACCESS_TOKEN_REMEMBER_ME_EXPIRE_MINUTES)
    else:
        expires_delta = timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES)

    session_id = f"sess_{secrets.token_urlsafe(24)}"
    now_ts = int(time.time())
    expires_at = now_ts + int(expires_delta.total_seconds())

    # Get remote IP respecting trusted proxies if configured
    remote_ip = request.client.host if request and request.client else "127.0.0.1"
    if hasattr(rate_limiter, "get_client_ip") and request:
        try:
            remote_ip = rate_limiter.get_client_ip(request)
        except Exception:
            pass

    user_agent = request.headers.get("User-Agent", "") if request else ""
    if not device_name:
        ua_lower = user_agent.lower()
        os_part = "Windows" if "windows" in ua_lower else "macOS" if "mac" in ua_lower else "Linux" if "linux" in ua_lower else "Android" if "android" in ua_lower else "iOS" if "iphone" in ua_lower or "ipad" in ua_lower else "Máy tính"
        br_part = "Edge" if "edg" in ua_lower else "Chrome" if "chrome" in ua_lower else "Firefox" if "firefox" in ua_lower else "Safari" if "safari" in ua_lower else "Trình duyệt"
        device_name = f"{os_part} ({br_part})"

    try:
        new_session = models.UserSession(
            session_id=session_id,
            user_id=user.id,
            username=user.username,
            role=user.role,
            ip_address=remote_ip,
            local_ip=local_ip,
            device_id=device_id,
            device_name=device_name[:100] if device_name else None,
            user_agent=user_agent[:500] if user_agent else None,
            created_at=now_ts,
            last_active_at=now_ts,
            expires_at=expires_at,
            is_revoked=False
        )
        auth_db.add(new_session)
        await auth_db.commit()
    except Exception as e:
        logger.warning(f"Could not record user session: {e}")

    access_token = auth.create_access_token(
        data={
            "sub": user.username,
            "role": user.role,
            "sid": session_id,
            "did": device_id,
        },
        expires_delta=expires_delta
    )

    log_desc = f"User '{user.username}' logged in (IP mạng: {remote_ip}"
    if local_ip:
        log_desc += f", IP máy: {local_ip}"
    log_desc += f", Thiết bị: {device_name})"

    await crud.log_system_event(
        db=devices_db,
        type="success",
        message=log_desc,
        user_id=user.id,
        ip=remote_ip
    )

    return {"access_token": access_token, "token_type": "bearer"}


get_current_user_info_router = APIRouter()

@get_current_user_info_router.post("/api/auth/heartbeat", status_code=204)
async def session_heartbeat(current_user: models.User = Depends(auth.get_current_user)):
    """Refresh activity through the shared authenticated-session dependency."""
    return None

@get_current_user_info_router.get("/api/auth/me", response_model=schemas.UserResponse)
async def get_current_user_info(current_user: models.User = Depends(auth.get_current_user)):
    permissions = auth.get_user_permissions(current_user)
    user_response = schemas.UserResponse.from_orm(current_user)
    user_response.permissions = permissions
    user_response.app_version = settings.APP_VERSION
    return user_response


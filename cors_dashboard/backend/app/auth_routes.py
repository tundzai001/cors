"""Authentication and current-user API routes."""

import logging
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import timedelta

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
    # Log login attempt
    logger.info(f"🔑 Login attempt for user: '{login_data.username}' from {request.client.host if request else 'unknown'}")
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
            f"â Œ Login failed: Unknown user '{login_data.username}' "
            f"from {request.client.host}"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )

    if not await auth.verify_password(login_data.password, user.hashed_password):
        logger.warning(
            f"â Œ Login failed: Wrong password for '{login_data.username}' "
            f"from {request.client.host}"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )

    if not user.is_active:
        logger.warning(
            f"â Œ Login failed: Inactive account '{login_data.username}'"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled"
        )

    logger.info(
        f"âœ… Successful login: '{user.username}' (role: {user.role}) "
        f"from {request.client.host}"
    )

    await rate_limiter.check_and_record(
        auth_db,
        request,
        username=user.username,
        is_success=True
    )

    # 🔥 FIX: Mặc định 24h nếu không chọn remember_me, 1 năm nếu có chọn
    if getattr(login_data, "remember_me", False):
        expires_delta = timedelta(minutes=auth.ACCESS_TOKEN_REMEMBER_ME_EXPIRE_MINUTES)
    else:
        expires_delta = timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES)

    access_token = auth.create_access_token(
        data={"sub": user.username, "role": user.role},
        expires_delta=expires_delta
    )

    await crud.log_system_event(
        db=devices_db,
        type="success",
        message=f"User '{user.username}' logged in",
        user_id=user.id,
        ip=request.client.host
    )

    return {"access_token": access_token, "token_type": "bearer"}


get_current_user_info_router = APIRouter()

@get_current_user_info_router.get("/api/auth/me", response_model=schemas.UserResponse)
async def get_current_user_info(current_user: models.User = Depends(auth.get_current_user)):
    permissions = auth.get_user_permissions(current_user)
    user_response = schemas.UserResponse.from_orm(current_user)
    user_response.permissions = permissions
    user_response.app_version = settings.APP_VERSION
    return user_response


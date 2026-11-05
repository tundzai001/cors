# ==============================================================================
# == backend/app/auth.py ==
# ==============================================================================

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt import InvalidTokenError as JWTError
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.concurrency import run_in_threadpool
from collections import defaultdict
import asyncio
import logging
import os
import re
import secrets
import time

from dotenv import load_dotenv

from . import models, crud
from .database import get_auth_db

logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

APP_ENV = os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or os.getenv("ENV") or "development"
IS_PRODUCTION = APP_ENV.lower() in {"prod", "production"}
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
INSECURE_SECRET_KEYS = {
    "",
    "CHANGE_ME",
    "CHANGE_ME_GENERATE_WITH_OPENSSL_RAND_BASE64_48",
    "1wDN7McXLUnqvO2uVY6aqqsGpUFJNdRwJkRq3qh8_5A",
}
if SECRET_KEY in INSECURE_SECRET_KEYS:
    if IS_PRODUCTION:
        raise RuntimeError("SECRET_KEY must be set in production. Refusing to start with an insecure JWT secret.")
    SECRET_KEY = secrets.token_urlsafe(64)
    logger.warning("SECRET_KEY is not set. Using an ephemeral development JWT secret; sessions reset on restart.")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24
ACCESS_TOKEN_REMEMBER_ME_EXPIRE_MINUTES = 60 * 24 * 365 # 1 year

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)
security = HTTPBearer()

class SimpleRateLimiter:
    def __init__(self, max_requests: int = 5, window_seconds: int = 300):
        self.max_requests = max_requests
        self.window = timedelta(seconds=window_seconds)
        self.requests = defaultdict(list)
        self.lock = asyncio.Lock()
    
    async def is_rate_limited(self, identifier: str) -> bool:
        async with self.lock:
            now = datetime.now()
            self.requests[identifier] = [t for t in self.requests[identifier] if now - t < self.window]
            if len(self.requests[identifier]) >= self.max_requests:
                return True
            self.requests[identifier].append(now)
            return False

login_rate_limiter = SimpleRateLimiter(max_requests=5, window_seconds=300)

async def check_login_rate_limit(request: Request):
    client_ip = request.client.host
    if await login_rate_limiter.is_rate_limited(client_ip):
        logger.warning(f"Rate limit exceeded for IP: {client_ip}")
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Quá nhiều lần đăng nhập thất bại. Vui lòng thử lại sau 5 phút.")

class PasswordPolicy:
    MIN_LENGTH = 8
    
    @classmethod
    def validate(cls, password: str) -> tuple[bool, str]:
        # === QUY TẮC 1: Kiểm tra độ dài tối thiểu ===
        if len(password) < cls.MIN_LENGTH:
            return False, f"Mật khẩu phải có ít nhất {cls.MIN_LENGTH} ký tự"

        # === QUY TẮC 2: Kiểm tra mật khẩu quá đơn giản ===
        if password.lower() in {'password', '12345678', 'admin123', 'password123', '123456'}:
            return False, "Mật khẩu quá đơn giản hoặc dễ đoán"

        # === QUY TẮC 3: Phải chứa ít nhất một chữ hoa và một chữ thường ===
        if not any(c.isupper() for c in password):
            return False, "Mật khẩu phải chứa ít nhất 1 chữ hoa"
        if not any(c.islower() for c in password):
            return False, "Mật khẩu phải chứa ít nhất 1 chữ thường"

        # === QUY TẮC 4: Phải chứa ít nhất một chữ số ===
        if not any(c.isdigit() for c in password):
            return False, "Mật khẩu phải chứa ít nhất 1 chữ số"

        # === QUY TẮC 5: Ký tự đặc biệt ===
        import re
        if not re.search(r'[^a-zA-Z0-9]', password):
            return False, "Mật khẩu phải chứa ít nhất 1 ký tự đặc biệt"

        # === QUY TẮC 6: Không được chứa ký tự cấm ===
        forbidden = [' ', '"', "'", '`', '&', '(', ')', '|', '<', '>']
        for c in forbidden:
            if c in password:
                return False, f"Mật khẩu không được chứa ký tự: {c}"

        return True, ""

class Role:
    ADMIN, VIEWER, COORDINATOR = "admin", "viewer", "coordinator"
class Permission:
    VIEW_DEVICES, VIEW_CONFIG = "view:devices", "view:config"
    VIEW_ROVERS, EDIT_ROVERS = "view:rovers", "edit:rovers"
    EDIT_DEVICE_NAME, EDIT_COORDINATES = "edit:device_name", "edit:coordinates"
    EDIT_CHIP_CONFIG, EDIT_SERVICE_CONFIG = "edit:chip_config", "edit:service_config"
    MANAGE_LICENSE, DELETE_DEVICE = "manage:license", "delete:device"
    MANAGE_USERS, EXPORT_DATA = "manage:users", "export:data"
    MANAGE_STATION_MODE, MANAGE_CASTER = "manage:station_mode", "manage:caster"

ROLE_PERMISSIONS = {
    Role.ADMIN: [
        Permission.VIEW_DEVICES, 
        Permission.VIEW_CONFIG,
        Permission.EDIT_DEVICE_NAME, 
        Permission.EDIT_COORDINATES,
        Permission.EDIT_CHIP_CONFIG,
        Permission.EDIT_SERVICE_CONFIG,
        Permission.MANAGE_LICENSE, 
        Permission.DELETE_DEVICE,
        Permission.MANAGE_USERS, 
        Permission.EXPORT_DATA,
        Permission.MANAGE_STATION_MODE,
        Permission.MANAGE_CASTER,
    ],
    Role.COORDINATOR: [
        Permission.VIEW_DEVICES, Permission.VIEW_CONFIG, Permission.EDIT_CHIP_CONFIG,
        Permission.EDIT_COORDINATES, Permission.EDIT_SERVICE_CONFIG, Permission.EDIT_DEVICE_NAME,
    ],
    Role.VIEWER: [
        Permission.VIEW_DEVICES, Permission.VIEW_CONFIG,
    ],
}

async def verify_password(plain_password: str, hashed_password: str) -> bool:
    return await run_in_threadpool(pwd_context.verify, plain_password, hashed_password)

async def get_password_hash(password: str) -> str:
    is_valid, error_msg = PasswordPolicy.validate(password)
    if not is_valid: raise ValueError(error_msg)
    return await run_in_threadpool(pwd_context.hash, password)

REVOKED_SESSIONS: set[str] = set()
BLOCKED_DEVICES: set[str] = set()
SESSION_LAST_ACTIVE_CACHE: dict[str, float] = {}
SESSION_ONLINE_WINDOW_SECONDS = 45
SESSION_ACTIVITY_INTERVAL_SECONDS = 10

def revoke_session_in_memory(session_id: str):
    REVOKED_SESSIONS.add(session_id)

def block_device_in_memory(device_id: str):
    BLOCKED_DEVICES.add(device_id)

def unblock_device_in_memory(device_id: str):
    BLOCKED_DEVICES.discard(device_id)

def is_session_revoked(session_id: str) -> bool:
    return session_id in REVOKED_SESSIONS

def is_device_blocked(device_id: str) -> bool:
    return device_id in BLOCKED_DEVICES

async def sync_auth_session_cache(db: AsyncSession):
    """Load blocked devices and recent revoked sessions into memory on startup or sync."""
    try:
        from sqlalchemy import select
        b_res = await db.execute(select(models.BlockedDevice.device_id))
        for did in b_res.scalars().all():
            BLOCKED_DEVICES.add(did)

        cutoff = int(time.time()) - 86400 * 7
        s_res = await db.execute(
            select(models.UserSession.session_id).filter(
                models.UserSession.is_revoked == True,
                models.UserSession.last_active_at > cutoff
            )
        )
        for sid in s_res.scalars().all():
            REVOKED_SESSIONS.add(sid)
    except Exception as e:
        logger.debug(f"Auth session cache sync note: {e}")

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    issued_at = datetime.now(timezone.utc)
    expire = issued_at + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire, "iat": issued_at})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as e:
        logger.warning(f"Token decode failed: {e}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token không hợp lệ hoặc đã hết hạn", headers={"WWW-Authenticate": "Bearer"})

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: AsyncSession = Depends(get_auth_db)) -> models.User:
    token = credentials.credentials
    payload = decode_token(token)
    username: str = payload.get("sub")
    if not username: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token không hợp lệ")

    sid = payload.get("sid")
    did = payload.get("did")
    if sid and is_session_revoked(sid):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên đăng nhập đã bị hủy bởi quản trị viên",
            headers={"WWW-Authenticate": "Bearer"}
        )
    if did and is_device_blocked(did):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Thiết bị này đã bị chặn truy cập bởi quản trị viên"
        )

    user = await crud.get_user_by_username(db, username=username)
    if not user: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User không tồn tại")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is disabled")

    if sid:
        now = time.time()
        if now - SESSION_LAST_ACTIVE_CACHE.get(sid, 0) > SESSION_ACTIVITY_INTERVAL_SECONDS:
            try:
                from sqlalchemy import update
                await db.execute(
                    update(models.UserSession)
                    .where(models.UserSession.session_id == sid)
                    .values(last_active_at=int(now))
                )
                await db.commit()
                SESSION_LAST_ACTIVE_CACHE[sid] = now
            except Exception as e:
                await db.rollback()
                logger.debug(f"Could not update session activity: {e}")

    return user

def require_permission(permission: str):
    async def permission_checker(current_user: models.User = Depends(get_current_user)) -> models.User:
        user_permissions = get_user_permissions(current_user)
        if permission not in user_permissions:
            logger.warning(f"Permission denied: User '{current_user.username}' (role: {current_user.role}) tried to access '{permission}'")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Bạn không có quyền '{permission}'.")
        return current_user
    return permission_checker

def has_permission(user: models.User, permission: str) -> bool:
    return permission in get_user_permissions(user)

def can_view_rover_page(user: models.User) -> bool:
    role = str(getattr(user, "role", "") or "").strip().lower()
    return role == Role.ADMIN or (
        role in (Role.VIEWER, Role.COORDINATOR)
        and bool(getattr(user, "can_view_rovers", False))
    )

def can_edit_rovers(user: models.User) -> bool:
    role = str(getattr(user, "role", "") or "").strip().lower()
    return role == Role.ADMIN or (
        role in (Role.VIEWER, Role.COORDINATOR)
        and can_view_rover_page(user)
        and bool(getattr(user, "can_create_rovers", False))
    )

def get_user_permissions(user: models.User) -> list[str]:
    perms = list(ROLE_PERMISSIONS.get(user.role, []))
    if can_view_rover_page(user) and Permission.VIEW_ROVERS not in perms:
        perms.append(Permission.VIEW_ROVERS)
    if can_edit_rovers(user) and Permission.EDIT_ROVERS not in perms:
        perms.append(Permission.EDIT_ROVERS)
    if user.role == Role.COORDINATOR and getattr(user, 'can_create_users', False):
        if Permission.MANAGE_USERS not in perms:
            perms.append(Permission.MANAGE_USERS)
    if user.role == Role.COORDINATOR and can_edit_rovers(user):
        if Permission.MANAGE_CASTER not in perms:
            perms.append(Permission.MANAGE_CASTER)
    return perms

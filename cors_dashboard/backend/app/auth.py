# ==============================================================================
# == backend/app/auth.py ==
# ==============================================================================

from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.concurrency import run_in_threadpool
from collections import defaultdict
import asyncio
import logging
import os

from . import models, crud
from .database import get_auth_db

logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("SECRET_KEY", "JSAjW2vXUwYPhMM4-djWW9h-THKTq9jKbd-8MzCxhlQ")
if SECRET_KEY == "JSAjW2vXUwYPhMM4-djWW9h-THKTq9jKbd-8MzCxhlQ":
    logger.warning("⚠️ USING DEFAULT SECRET_KEY! Change this in production!")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

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
        if len(password) < cls.MIN_LENGTH: return False, f"Password phải có ít nhất {cls.MIN_LENGTH} ký tự"
        if password.isdigit(): return False, "Password không thể toàn là số"
        if password.lower() in {'password', '12345678', 'admin123', 'password123'}: return False, "Password quá đơn giản"
        return True, ""

class Role: ADMIN, VIEWER, COORDINATOR = "admin", "viewer", "coordinator"
class Permission: VIEW_DEVICES, VIEW_CONFIG, EDIT_DEVICE_NAME, EDIT_COORDINATES, EDIT_CHIP_CONFIG, EDIT_SERVICE_CONFIG, MANAGE_LICENSE, DELETE_DEVICE, MANAGE_USERS, EXPORT_DATA = "view:devices", "view:config", "edit:device_name", "edit:coordinates", "edit:chip_config", "edit:service_config", "manage:license", "delete:device", "manage:users", "export:data"

ROLE_PERMISSIONS = {
    Role.ADMIN: [
        Permission.VIEW_DEVICES, Permission.VIEW_CONFIG,
        Permission.EDIT_DEVICE_NAME, Permission.EDIT_COORDINATES,
        Permission.EDIT_CHIP_CONFIG, Permission.EDIT_SERVICE_CONFIG,
        Permission.MANAGE_LICENSE, Permission.DELETE_DEVICE,
        Permission.MANAGE_USERS, Permission.EXPORT_DATA,
    ],
    Role.COORDINATOR: [
        Permission.VIEW_DEVICES, Permission.VIEW_CONFIG,
        Permission.EDIT_COORDINATES,
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

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire, "iat": datetime.utcnow()})
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
    user = await crud.get_user_by_username(db, username=username)
    if not user: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User không tồn tại")
    return user

def require_permission(permission: str):
    async def permission_checker(current_user: models.User = Depends(get_current_user)) -> models.User:
        user_permissions = ROLE_PERMISSIONS.get(current_user.role, [])
        if permission not in user_permissions:
            logger.warning(f"Permission denied: User '{current_user.username}' (role: {current_user.role}) tried to access '{permission}'")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Bạn không có quyền '{permission}'.")
        return current_user
    return permission_checker

def has_permission(user: models.User, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(user.role, [])

def get_user_permissions(user: models.User) -> list[str]:
    return ROLE_PERMISSIONS.get(user.role, [])
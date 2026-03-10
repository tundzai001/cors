# ==============================================================================
# == backend/app/database.py - Optimized for High Concurrency & Stability    ==
# ==============================================================================

import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from pydantic_settings import BaseSettings
import logging

# Cấu hình Log để theo dõi kết nối DB nếu cần
logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    # Legacy fallback (để không vỡ .env cũ)
    DB_POOL_SIZE: int | None = None
    DB_MAX_OVERFLOW: int | None = None
    DB_POOL_TIMEOUT: int | None = None
    DB_POOL_RECYCLE: int | None = None

    MQTT_HOST: str
    MQTT_PORT: int
    MQTT_USERNAME: str | None = None
    MQTT_PASSWORD: str | None = None
    
    # Database configs
    DATABASE_URL: str
    AUTH_DATABASE_URL: str
    
    # Device DB pooling (high traffic)
    DEVICE_DB_POOL_SIZE: int = 40
    DEVICE_DB_MAX_OVERFLOW: int = 60
    DEVICE_DB_POOL_TIMEOUT: int = 30
    DEVICE_DB_POOL_RECYCLE: int = 900
    DEVICE_DB_SESSION_CONCURRENCY: int = 80

    # Auth DB pooling (lower traffic)
    AUTH_DB_POOL_SIZE: int = 15
    AUTH_DB_MAX_OVERFLOW: int = 20
    AUTH_DB_POOL_TIMEOUT: int = 30
    AUTH_DB_POOL_RECYCLE: int = 900
    AUTH_DB_SESSION_CONCURRENCY: int = 30

    # Global DB behavior
    DB_COMMAND_TIMEOUT: int = 30
    DB_POOL_HARD_CAP: int = 120
    DB_ECHO: bool = False
    
    SECRET_KEY: str
    ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    
    RATE_LIMIT_MAX_ATTEMPTS: int = 5
    RATE_LIMIT_WINDOW_MINUTES: int = 15
    RATE_LIMIT_BLOCK_MINUTES: int = 30
    RATE_LIMIT_PERMANENT_THRESHOLD: int = 20
    
    class Config:
        env_file = ".env"
        extra = "ignore" # Bỏ qua các biến thừa nếu có

settings = Settings()

def _resolve_pool_value(new_value: int, legacy_value: int | None) -> int:
    return legacy_value if legacy_value is not None else new_value


def _cap_pool_value(value: int, cap: int) -> int:
    # Tránh cấu hình sai làm mở pool quá mức, gây nghẽn toàn DB server.
    return max(1, min(value, cap))


def create_pg_engine(
    database_url: str,
    *,
    app_name: str,
    pool_size: int,
    max_overflow: int,
    pool_timeout: int,
    pool_recycle: int,
):
    """
    Tạo Engine tối ưu cho PostgreSQL với AsyncPG.
    Đã bật tính năng tự phục hồi kết nối (pool_pre_ping).
    """
    db_name = database_url.rsplit('/', 1)[-1]
    logger.info(
        "Initializing DB Engine '%s' for %s (pool_size=%s, overflow=%s, timeout=%ss)",
        app_name,
        db_name,
        pool_size,
        max_overflow,
        pool_timeout,
    )
    
    safe_pool_size = _cap_pool_value(pool_size, settings.DB_POOL_HARD_CAP)
    safe_max_overflow = _cap_pool_value(max_overflow, settings.DB_POOL_HARD_CAP)

    engine_kwargs = {
        "echo": settings.DB_ECHO,
        "pool_size": safe_pool_size,
        "max_overflow": safe_max_overflow,
        "pool_timeout": pool_timeout,
        "pool_recycle": pool_recycle,
        "pool_pre_ping": True,
        "pool_use_lifo": True,
        "pool_reset_on_return": "rollback",
    }

    if database_url.startswith("postgresql+asyncpg"):
        engine_kwargs["connect_args"] = {
            "command_timeout": settings.DB_COMMAND_TIMEOUT,
            "server_settings": {
                "application_name": app_name,
            },
        }

    return create_async_engine(
        database_url,
        **engine_kwargs,
    )

# === DATABASE 1: Dữ liệu trạm (devices) ===
engine = create_pg_engine(
    settings.DATABASE_URL,
    app_name="cors-dashboard-devices",
    pool_size=_resolve_pool_value(settings.DEVICE_DB_POOL_SIZE, settings.DB_POOL_SIZE),
    max_overflow=_resolve_pool_value(settings.DEVICE_DB_MAX_OVERFLOW, settings.DB_MAX_OVERFLOW),
    pool_timeout=_resolve_pool_value(settings.DEVICE_DB_POOL_TIMEOUT, settings.DB_POOL_TIMEOUT),
    pool_recycle=_resolve_pool_value(settings.DEVICE_DB_POOL_RECYCLE, settings.DB_POOL_RECYCLE),
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
Base = declarative_base()
_device_db_semaphore = asyncio.Semaphore(settings.DEVICE_DB_SESSION_CONCURRENCY)

async def get_db():
    """Dependency Injection cho FastAPI"""
    async with _device_db_semaphore:
        async with AsyncSessionLocal() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

# === DATABASE 2: Authentication (users) ===
auth_engine = create_pg_engine(
    settings.AUTH_DATABASE_URL,
    app_name="cors-dashboard-auth",
    pool_size=_resolve_pool_value(settings.AUTH_DB_POOL_SIZE, settings.DB_POOL_SIZE),
    max_overflow=_resolve_pool_value(settings.AUTH_DB_MAX_OVERFLOW, settings.DB_MAX_OVERFLOW),
    pool_timeout=_resolve_pool_value(settings.AUTH_DB_POOL_TIMEOUT, settings.DB_POOL_TIMEOUT),
    pool_recycle=_resolve_pool_value(settings.AUTH_DB_POOL_RECYCLE, settings.DB_POOL_RECYCLE),
)
AsyncAuthSession = async_sessionmaker(auth_engine, expire_on_commit=False, autoflush=False)
AuthBase = declarative_base()
_auth_db_semaphore = asyncio.Semaphore(settings.AUTH_DB_SESSION_CONCURRENCY)

async def get_auth_db():
    """Dependency Injection cho Auth"""
    async with _auth_db_semaphore:
        async with AsyncAuthSession() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
# ==============================================================================
# == backend/app/database.py - Optimized for High Concurrency & Stability    ==
# ==============================================================================

import asyncio
from contextlib import asynccontextmanager
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
    CASTER_HOST: str | None = None
    CASTER_PORT: int | None = None
    
    ENABLE_PREFERRED_HOST: bool = False
    PREFERRED_HOST_MARKER: str = ""
    
    # Zalo Bot
    ENABLE_ZALO_BOT: bool = False
    ZALO_BOT_TOKEN: str = ""
    ZALO_BOT_ADMIN_USERNAME: str = ""
    ZALO_BOT_MODE: str = "polling"
    ZALO_WEBHOOK_URL: str = ""
    ZALO_WEBHOOK_SECRET: str = ""
    ENABLE_ZALO_ERROR_ALERTS: bool = True
    ZALO_STATE_ENCRYPTION_KEY: str = ""
    ZALO_SMART_ASSISTANT_V2: bool = True
    ZALO_ENABLE_WRITE_ACTIONS: bool = True
    ZALO_ENABLE_NOTIFICATIONS: bool = True
    ZALO_LINK_CODE_TTL_SECONDS: int = 30
    ZALO_PENDING_ACTION_TTL_SECONDS: int = 120
    ZALO_EXPORT_LINK_TTL_SECONDS: int = 300
    ZALO_SEND_RETRY_ATTEMPTS: int = 3
    ZALO_SEND_RETRY_BASE_DELAY_SECONDS: float = 0.4
    
    # Database configs
    DATABASE_URL: str
    AUTH_DATABASE_URL: str
    
    # Device DB pooling (high traffic)
    DEVICE_DB_POOL_SIZE: int | None = None
    DEVICE_DB_MAX_OVERFLOW: int | None = None
    DEVICE_DB_POOL_TIMEOUT: int | None = None
    DEVICE_DB_POOL_RECYCLE: int | None = None
    DEVICE_DB_SESSION_CONCURRENCY: int | None = None

    # Auth DB pooling (lower traffic)
    AUTH_DB_POOL_SIZE: int | None = None
    AUTH_DB_MAX_OVERFLOW: int | None = None
    AUTH_DB_POOL_TIMEOUT: int | None = None
    AUTH_DB_POOL_RECYCLE: int | None = None
    AUTH_DB_SESSION_CONCURRENCY: int | None = None

    # Global DB behavior
    DB_COMMAND_TIMEOUT: int = 30
    DB_POOL_HARD_CAP: int = 120
    DB_ECHO: bool = False
    DB_SESSION_RESERVE_CONNECTIONS: int = 5
    DB_TOTAL_CONNECTION_BUDGET: int = 80
    DB_SESSION_ACQUIRE_TIMEOUT: float = 5.0
    DEVICE_DB_CONNECTION_BUDGET: int | None = None
    AUTH_DB_CONNECTION_BUDGET: int | None = None
    
    SECRET_KEY: str
    ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    
    RATE_LIMIT_MAX_ATTEMPTS: int = 5
    RATE_LIMIT_WINDOW_MINUTES: int = 15
    RATE_LIMIT_BLOCK_MINUTES: int = 30
    RATE_LIMIT_PERMANENT_THRESHOLD: int = 20
    
    APP_VERSION: str = "V2.2.3"
    
    class Config:
        env_file = ".env"
        extra = "ignore" # Bỏ qua các biến thừa nếu có

settings = Settings()


class DatabaseBusyError(RuntimeError):
    """Raised when work cannot obtain a database slot promptly."""

DEFAULT_DEVICE_DB_POOL_SIZE = 40
DEFAULT_DEVICE_DB_MAX_OVERFLOW = 40
DEFAULT_DEVICE_DB_POOL_TIMEOUT = 20
DEFAULT_DEVICE_DB_POOL_RECYCLE = 900
DEFAULT_DEVICE_DB_SESSION_CONCURRENCY = 60

DEFAULT_AUTH_DB_POOL_SIZE = 12
DEFAULT_AUTH_DB_MAX_OVERFLOW = 12
DEFAULT_AUTH_DB_POOL_TIMEOUT = 20
DEFAULT_AUTH_DB_POOL_RECYCLE = 900
DEFAULT_AUTH_DB_SESSION_CONCURRENCY = 20


def _resolve_value(preferred: int | None, legacy: int | None, default: int) -> int:
    if preferred is not None:
        return preferred
    if legacy is not None:
        return legacy
    return default


def _cap_pool_value(value: int, cap: int) -> int:
    # Tránh cấu hình sai làm mở pool quá mức, gây nghẽn toàn DB server.
    return max(1, min(value, cap))


def _cap_non_negative(value: int, cap: int) -> int:
    return max(0, min(value, cap))


def _sanitize_pool_timeout(value: int) -> int:
    return max(3, value)


def _resolve_effective_pool(
    *,
    pool_size: int,
    max_overflow: int,
    pool_timeout: int,
    pool_recycle: int,
    connection_budget: int,
) -> tuple[int, int, int, int, int]:
    budget_cap = max(2, min(connection_budget, settings.DB_POOL_HARD_CAP))
    safe_pool_size = _cap_pool_value(pool_size, max(1, budget_cap - 1))
    remaining_budget = max(0, budget_cap - safe_pool_size)
    safe_max_overflow = _cap_non_negative(max_overflow, remaining_budget)
    safe_pool_timeout = _sanitize_pool_timeout(pool_timeout)
    safe_pool_recycle = max(30, pool_recycle)
    total_connections = safe_pool_size + safe_max_overflow
    return safe_pool_size, safe_max_overflow, safe_pool_timeout, safe_pool_recycle, total_connections


def _resolve_db_budgets() -> tuple[int, int]:
    total_budget = max(10, settings.DB_TOTAL_CONNECTION_BUDGET)
    device_budget = settings.DEVICE_DB_CONNECTION_BUDGET
    auth_budget = settings.AUTH_DB_CONNECTION_BUDGET

    if device_budget is not None and auth_budget is not None:
        return max(2, int(device_budget)), max(2, int(auth_budget))

    if device_budget is not None:
        device = max(2, int(device_budget))
        auth = max(2, total_budget - device)
        return device, auth

    if auth_budget is not None:
        auth = max(2, int(auth_budget))
        device = max(2, total_budget - auth)
        return device, auth

    # Default split for this workload: device DB gets larger budget than auth DB.
    device = max(2, int(total_budget * 0.75))
    auth = max(2, total_budget - device)
    return device, auth


def _resolve_session_concurrency(requested: int, total_connections: int, db_name: str) -> int:
    reserve = max(1, settings.DB_SESSION_RESERVE_CONNECTIONS)
    max_safe = max(1, total_connections - reserve)
    safe_value = max(1, min(requested, max_safe))
    if safe_value != requested:
        logger.warning(
            "Clamping %s session concurrency from %s to %s (pool_total=%s, reserve=%s)",
            db_name,
            requested,
            safe_value,
            total_connections,
            reserve,
        )
    return safe_value


def create_pg_engine(
    database_url: str,
    *,
    app_name: str,
    pool_size: int,
    max_overflow: int,
    pool_timeout: int,
    pool_recycle: int,
    connection_budget: int,
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
    
    safe_pool_size, safe_max_overflow, safe_pool_timeout, safe_pool_recycle, _ = _resolve_effective_pool(
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=pool_recycle,
        connection_budget=connection_budget,
    )

    engine_kwargs = {
        "echo": settings.DB_ECHO,
        "pool_size": safe_pool_size,
        "max_overflow": safe_max_overflow,
        "pool_timeout": safe_pool_timeout,
        "pool_recycle": safe_pool_recycle,
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
_device_budget, _auth_budget = _resolve_db_budgets()

_device_pool_size = _resolve_value(settings.DEVICE_DB_POOL_SIZE, settings.DB_POOL_SIZE, DEFAULT_DEVICE_DB_POOL_SIZE)
_device_max_overflow = _resolve_value(settings.DEVICE_DB_MAX_OVERFLOW, settings.DB_MAX_OVERFLOW, DEFAULT_DEVICE_DB_MAX_OVERFLOW)
_device_pool_timeout = _resolve_value(settings.DEVICE_DB_POOL_TIMEOUT, settings.DB_POOL_TIMEOUT, DEFAULT_DEVICE_DB_POOL_TIMEOUT)
_device_pool_recycle = _resolve_value(settings.DEVICE_DB_POOL_RECYCLE, settings.DB_POOL_RECYCLE, DEFAULT_DEVICE_DB_POOL_RECYCLE)
_device_session_concurrency = _resolve_value(
    settings.DEVICE_DB_SESSION_CONCURRENCY,
    None,
    DEFAULT_DEVICE_DB_SESSION_CONCURRENCY,
)
(
    _device_pool_size,
    _device_max_overflow,
    _device_pool_timeout,
    _device_pool_recycle,
    _device_total_connections,
) = _resolve_effective_pool(
    pool_size=_device_pool_size,
    max_overflow=_device_max_overflow,
    pool_timeout=_device_pool_timeout,
    pool_recycle=_device_pool_recycle,
    connection_budget=_device_budget,
)

engine = create_pg_engine(
    settings.DATABASE_URL,
    app_name="cors-dashboard-devices",
    pool_size=_device_pool_size,
    max_overflow=_device_max_overflow,
    pool_timeout=_device_pool_timeout,
    pool_recycle=_device_pool_recycle,
    connection_budget=_device_budget,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
Base = declarative_base()
_device_session_limit = _resolve_session_concurrency(
    _device_session_concurrency,
    _device_total_connections,
    "device-db",
)
_device_db_semaphore = asyncio.Semaphore(_device_session_limit)


@asynccontextmanager
async def device_session_scope():
    """Session scope dùng nội bộ cho background tasks, có giới hạn đồng thời."""
    try:
        await asyncio.wait_for(
            _device_db_semaphore.acquire(),
            timeout=max(0.5, float(settings.DB_SESSION_ACQUIRE_TIMEOUT)),
        )
    except asyncio.TimeoutError as exc:
        raise DatabaseBusyError("Device database is busy") from exc
    try:
        async with AsyncSessionLocal() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
    finally:
        _device_db_semaphore.release()

async def get_db():
    """Dependency Injection cho FastAPI"""
    async with device_session_scope() as session:
        yield session

# === DATABASE 2: Authentication (users) ===
_auth_pool_size = _resolve_value(settings.AUTH_DB_POOL_SIZE, settings.DB_POOL_SIZE, DEFAULT_AUTH_DB_POOL_SIZE)
_auth_max_overflow = _resolve_value(settings.AUTH_DB_MAX_OVERFLOW, settings.DB_MAX_OVERFLOW, DEFAULT_AUTH_DB_MAX_OVERFLOW)
_auth_pool_timeout = _resolve_value(settings.AUTH_DB_POOL_TIMEOUT, settings.DB_POOL_TIMEOUT, DEFAULT_AUTH_DB_POOL_TIMEOUT)
_auth_pool_recycle = _resolve_value(settings.AUTH_DB_POOL_RECYCLE, settings.DB_POOL_RECYCLE, DEFAULT_AUTH_DB_POOL_RECYCLE)
_auth_session_concurrency = _resolve_value(
    settings.AUTH_DB_SESSION_CONCURRENCY,
    None,
    DEFAULT_AUTH_DB_SESSION_CONCURRENCY,
)
(
    _auth_pool_size,
    _auth_max_overflow,
    _auth_pool_timeout,
    _auth_pool_recycle,
    _auth_total_connections,
) = _resolve_effective_pool(
    pool_size=_auth_pool_size,
    max_overflow=_auth_max_overflow,
    pool_timeout=_auth_pool_timeout,
    pool_recycle=_auth_pool_recycle,
    connection_budget=_auth_budget,
)

auth_engine = create_pg_engine(
    settings.AUTH_DATABASE_URL,
    app_name="cors-dashboard-auth",
    pool_size=_auth_pool_size,
    max_overflow=_auth_max_overflow,
    pool_timeout=_auth_pool_timeout,
    pool_recycle=_auth_pool_recycle,
    connection_budget=_auth_budget,
)
AsyncAuthSession = async_sessionmaker(auth_engine, expire_on_commit=False, autoflush=False)
AuthBase = declarative_base()
_auth_session_limit = _resolve_session_concurrency(
    _auth_session_concurrency,
    _auth_total_connections,
    "auth-db",
)
_auth_db_semaphore = asyncio.Semaphore(_auth_session_limit)

logger.info(
    "Effective DB pool config | device: pool=%s overflow=%s total=%s budget=%s session_limit=%s | auth: pool=%s overflow=%s total=%s budget=%s session_limit=%s",
    _device_pool_size,
    _device_max_overflow,
    _device_total_connections,
    _device_budget,
    _device_session_limit,
    _auth_pool_size,
    _auth_max_overflow,
    _auth_total_connections,
    _auth_budget,
    _auth_session_limit,
)


@asynccontextmanager
async def auth_session_scope():
    """Session scope cho auth background flow, giữ behavior nhất quán với dependency."""
    try:
        await asyncio.wait_for(
            _auth_db_semaphore.acquire(),
            timeout=max(0.5, float(settings.DB_SESSION_ACQUIRE_TIMEOUT)),
        )
    except asyncio.TimeoutError as exc:
        raise DatabaseBusyError("Authentication database is busy") from exc
    try:
        async with AsyncAuthSession() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
    finally:
        _auth_db_semaphore.release()

async def get_auth_db():
    """Dependency Injection cho Auth"""
    async with auth_session_scope() as session:
        yield session

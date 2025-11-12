# ==============================================================================
# == backend/app/database.py - CẢI TIẾN CONNECTION POOLING                  ==
# ==============================================================================

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool, QueuePool
from pydantic_settings import BaseSettings
import logging

logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    MQTT_HOST: str; MQTT_PORT: int
    MQTT_USERNAME: str | None = None; MQTT_PASSWORD: str | None = None
    DATABASE_URL: str; AUTH_DATABASE_URL: str
    DB_POOL_SIZE: int = 5; DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30; DB_POOL_RECYCLE: int = 3600
    DB_ECHO: bool = False
    SECRET_KEY: str; ALGORITHM: str; ACCESS_TOKEN_EXPIRE_MINUTES: int
    
    class Config:
        env_file = ".env"

settings = Settings()

def create_optimized_engine(database_url: str):
    if database_url.startswith("sqlite"):
        logger.info("Using SQLite with NullPool (no connection pooling)")
        return create_async_engine(
            database_url, echo=settings.DB_ECHO, poolclass=NullPool,
            connect_args={"check_same_thread": False}
        )
    
    logger.info(f"Using connection pool: size={settings.DB_POOL_SIZE}, max_overflow={settings.DB_MAX_OVERFLOW}")
    return create_async_engine(
        database_url, echo=settings.DB_ECHO, poolclass=QueuePool,
        pool_size=settings.DB_POOL_SIZE, max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT, pool_recycle=settings.DB_POOL_RECYCLE,
        pool_pre_ping=True,
    )

# === DATABASE 1: Dữ liệu trạm (devices) ===
engine = create_optimized_engine(settings.DATABASE_URL)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

# === DATABASE 2: Authentication (users) ===
auth_engine = create_optimized_engine(settings.AUTH_DATABASE_URL)
AsyncAuthSession = async_sessionmaker(auth_engine, expire_on_commit=False, autoflush=False)
AuthBase = declarative_base()

async def get_auth_db():
    async with AsyncAuthSession() as session:
        try:
            yield session
        finally:
            await session.close()
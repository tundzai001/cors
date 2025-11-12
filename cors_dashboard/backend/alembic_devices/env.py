# ==============================================================================
# == alembic_devices/env.py - Migration Environment for Devices DB           ==
# ==============================================================================

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Thêm đường dẫn dự án
project_root = os.path.realpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# --- Cấu hình Alembic ---
config = context.config

# Ghi đè URL từ .env
from app.database import settings
config.set_main_option(
    "sqlalchemy.url", 
    settings.DATABASE_URL.replace("sqlite+aiosqlite:", "sqlite:")
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import metadata cho devices
from app.models import Base
target_metadata = Base.metadata

# --- Hàm chạy migration ---
def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table='alembic_version_devices'  # Tách bảng version
        )

        with context.begin_transaction():
            context.run_migrations()

# --- Chạy ---
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
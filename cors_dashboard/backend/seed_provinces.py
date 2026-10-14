"""
Seed Vietnam administrative provinces and wards into database:
    cd backend
    python seed_provinces.py
"""
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load backend/.env if exists
load_dotenv(Path(__file__).with_name(".env"), override=False)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.database import engine, device_session_scope
from app.models import Base
from app.province_db_service import seed_vietnam_provinces


async def main():
    print("1. Kiểm tra và khởi tạo bảng hành chính nếu chưa có (provinces, wards)...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("2. Nạp dữ liệu 34 tỉnh thành và 3.321 xã/phường vào database...")
    async with device_session_scope() as session:
        result = await seed_vietnam_provinces(session)
        if result.get("already_seeded"):
            print(f"✓ Đã nạp từ trước: hiện có {result.get('provinces')} tỉnh/thành trong database.")
        elif "error" in result:
            print(f"✗ Lỗi khi nạp dữ liệu: {result.get('error')}")
        else:
            print(f"✓ Hoàn tất! Đã nạp thành công {result.get('provinces')} tỉnh/thành và {result.get('wards')} xã/phường vào database.")


if __name__ == "__main__":
    asyncio.run(main())

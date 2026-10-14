"""
Seed Vietnam administrative provinces, districts, and wards into database:
    cd backend
    python seed_provinces.py [--force]
"""
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"), override=False)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.database import engine, device_session_scope
from app.models import Base
from app.province_db_service import seed_vietnam_provinces


async def main():
    force = "--force" in sys.argv
    print("1. Kiểm tra và khởi tạo cấu trúc bảng hành chính (provinces, districts, wards)...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("2. Nạp dữ liệu địa giới hành chính (34 tỉnh/thành, 3.321 xã/phường) vào database...")
    async with device_session_scope() as session:
        result = await seed_vietnam_provinces(session, force=force)
        if result.get("already_seeded"):
            print(f"✓ Đã có dữ liệu từ trước: {result.get('provinces')} tỉnh/thành, {result.get('districts', 0)} quận/huyện, {result.get('wards', 0)} xã/phường trong database.")
            print("  (Để nạp lại dữ liệu đầy đủ, chạy: python seed_provinces.py --force)")
        elif "error" in result:
            print(f"✗ Lỗi khi nạp dữ liệu: {result.get('error')}")
        else:
            print(f"✓ Hoàn tất! Đã nạp thành công {result.get('provinces')} tỉnh/thành, {result.get('districts')} quận/huyện, {result.get('wards')} xã/phường vào database.")


if __name__ == "__main__":
    asyncio.run(main())

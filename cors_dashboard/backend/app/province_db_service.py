"""Service for Vietnamese administrative provinces and wards database."""
import json
import logging
import os
from typing import Any
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from . import models

logger = logging.getLogger("app.main")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SRC_GIS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src", "gis"))
UNITS_JSON_PATH = os.path.join(DATA_DIR, "vietnam_units.json")
if not os.path.exists(UNITS_JSON_PATH) and os.path.exists(os.path.join(SRC_GIS_DIR, "vietnam_units.json")):
    UNITS_JSON_PATH = os.path.join(SRC_GIS_DIR, "vietnam_units.json")


async def seed_vietnam_provinces(db: AsyncSession) -> dict[str, Any]:
    """Seed provinces, wards, and administrative units into the database if not already populated."""
    try:
        # Check if provinces already populated
        res = await db.execute(select(func.count(models.Province.code)))
        count = res.scalar() or 0
        if count > 0:
            return {"provinces": count, "already_seeded": True}

        if not os.path.exists(UNITS_JSON_PATH):
            logger.warning("vietnam_units.json not found at %s", UNITS_JSON_PATH)
            return {"provinces": 0, "wards": 0}

        with open(UNITS_JSON_PATH, "r", encoding="utf-8") as f:
            units = json.load(f)

        # 1. Seed administrative regions
        reg_data = [
            (1, 'Đông Bắc Bộ', 'Northeast', 'dong_bac_bo', 'northest'),
            (2, 'Tây Bắc Bộ', 'Northwest', 'tay_bac_bo', 'northwest'),
            (3, 'Đồng bằng sông Hồng', 'Red River Delta', 'dong_bang_song_hong', 'red_river_delta'),
            (4, 'Bắc Trung Bộ', 'North Central Coast', 'bac_trung_bo', 'north_central_coast'),
            (5, 'Duyên hải Nam Trung Bộ', 'South Central Coast', 'duyen_hai_nam_trung_bo', 'south_central_coast'),
            (6, 'Tây Nguyên', 'Central Highlands', 'tay_nguyen', 'central_highlands'),
            (7, 'Đông Nam Bộ', 'Southeast', 'dong_nam_bo', 'southeast'),
            (8, 'Đồng bằng sông Cửu Long', 'Mekong River Delta', 'dong_bang_song_cuu_long', 'southwest'),
        ]
        for rid, name, name_en, code_name, code_name_en in reg_data:
            db.add(models.AdministrativeRegion(
                id=rid, name=name, name_en=name_en, code_name=code_name, code_name_en=code_name_en
            ))

        # 2. Seed administrative units
        unit_data = [
            (1, 'Thành phố trực thuộc trung ương', 'Municipality', 'Thành phố', 'City', 'thanh_pho_truc_thuoc_trung_uong', 'municipality'),
            (2, 'Tỉnh', 'Province', 'Tỉnh', 'Province', 'tinh', 'province'),
            (3, 'Phường', 'Ward', 'Phường', 'Ward', 'phuong', 'ward'),
            (4, 'Xã', 'Commune', 'Xã', 'Commune', 'xa', 'commune'),
            (5, 'Đặc khu tại hải đảo', 'Special administrative region', 'Đặc khu', 'Special administrative region', 'dac_khu', 'special_administrative_region'),
        ]
        for uid, fn, fne, sn, sne, cn, cne in unit_data:
            db.add(models.AdministrativeUnit(
                id=uid, full_name=fn, full_name_en=fne, short_name=sn, short_name_en=sne, code_name=cn, code_name_en=cne
            ))

        await db.flush()

        # 3. Seed provinces and wards
        prov_objs = []
        ward_objs = []

        for p in units:
            p_code = str(p.get("Code", "")).strip()
            if not p_code:
                continue
            prov_objs.append(models.Province(
                code=p_code,
                name=p.get("Name", ""),
                name_en=p.get("NameEn"),
                full_name=p.get("FullName", ""),
                full_name_en=p.get("FullNameEn"),
                code_name=p.get("CodeName"),
                postal_code_prefix=p.get("PostalCodePrefix"),
            ))

            for w in p.get("Wards", []):
                w_code = str(w.get("Code", "")).strip()
                if not w_code:
                    continue
                ward_objs.append(models.Ward(
                    code=w_code,
                    name=w.get("Name", ""),
                    name_en=w.get("NameEn"),
                    full_name=w.get("FullName", ""),
                    full_name_en=w.get("FullNameEn"),
                    code_name=w.get("CodeName"),
                    postal_code=w.get("PostalCode"),
                    province_code=p_code,
                ))

        db.add_all(prov_objs)
        await db.flush()

        db.add_all(ward_objs)
        db.add(models.VNProvincesMetadata(
            dataset_version="v5.2.0",
            latest_decree="388/NQ-UBTVQH16",
            generated_at="2026-09-20T13:13:17Z"
        ))
        await db.commit()
        logger.info("Successfully seeded %d provinces and %d wards into database.", len(prov_objs), len(ward_objs))
        return {"provinces": len(prov_objs), "wards": len(ward_objs), "already_seeded": False}
    except Exception as exc:
        await db.rollback()
        logger.error("Error seeding vietnam provinces into database: %s", exc)
        return {"error": str(exc)}


async def search_administrative_units(db: AsyncSession, query: str, limit: int = 15) -> list[dict[str, Any]]:
    """Search provinces and wards in database by text, returning exact administrative matches."""
    cleaned = str(query or "").strip()
    if not cleaned:
        return []

    pattern = f"%{cleaned}%"
    results = []

    # 1. Search provinces
    res_p = await db.execute(
        select(models.Province)
        .where(or_(
            models.Province.name.ilike(pattern),
            models.Province.full_name.ilike(pattern),
            models.Province.code_name.ilike(pattern),
        ))
        .limit(5)
    )
    for p in res_p.scalars().all():
        results.append({
            "type": "province",
            "code": p.code,
            "name": p.name,
            "full_name": p.full_name,
            "postal_code_prefix": p.postal_code_prefix,
            "province_name": p.full_name,
        })

    # 2. Search wards
    res_w = await db.execute(
        select(models.Ward, models.Province.full_name.label("province_full_name"))
        .join(models.Province, models.Ward.province_code == models.Province.code, isouter=True)
        .where(or_(
            models.Ward.name.ilike(pattern),
            models.Ward.full_name.ilike(pattern),
            models.Ward.postal_code.ilike(pattern),
        ))
        .limit(limit)
    )
    for w, prov_name in res_w.all():
        results.append({
            "type": "ward",
            "code": w.code,
            "name": w.name,
            "full_name": w.full_name,
            "postal_code": w.postal_code,
            "province_code": w.province_code,
            "province_name": prov_name,
        })

    return results


if __name__ == "__main__":
    import asyncio
    import sys
    from pathlib import Path
    from dotenv import load_dotenv

    # Ensure backend directory is in python path
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    load_dotenv(backend_dir / ".env", override=False)

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    from app.database import engine, device_session_scope
    from app.models import Base

    async def _cli_main():
        print("1. Creating administrative tables if not exist...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("2. Seeding Vietnam provinces and wards...")
        async with device_session_scope() as session:
            res = await seed_vietnam_provinces(session)
            if res.get("already_seeded"):
                print(f"✓ Already seeded: {res.get('provinces')} provinces present in database.")
            elif "error" in res:
                print(f"✗ Failed: {res.get('error')}")
            else:
                print(f"✓ Successfully seeded {res.get('provinces')} provinces and {res.get('wards')} wards!")

    asyncio.run(_cli_main())

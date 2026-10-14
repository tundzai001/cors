"""Service for Vietnamese administrative provinces, districts, and wards database."""
import json
import logging
import os
import unicodedata
from typing import Any, Optional
from sqlalchemy import select, func, or_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from . import models

logger = logging.getLogger("app.main")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SRC_GIS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src", "gis"))
UNITS_JSON_PATH = os.path.join(DATA_DIR, "vietnam_units.json")
if not os.path.exists(UNITS_JSON_PATH) and os.path.exists(os.path.join(SRC_GIS_DIR, "vietnam_units.json")):
    UNITS_JSON_PATH = os.path.join(SRC_GIS_DIR, "vietnam_units.json")

# Tọa độ trung tâm hành chính chuẩn cho các tỉnh, thành phố và địa danh trọng yếu
KNOWN_COORDINATES = {
    # Miền Trung & Quảng Nam - Đà Nẵng
    "tam ky": (15.564, 108.481),
    "hoi an": (15.880, 108.338),
    "dien ban": (15.895, 108.247),
    "nui thanh": (15.424, 108.647),
    "duy xuyen": (15.816, 108.243),
    "thang binh": (15.742, 108.368),
    "quang nam": (15.564, 108.481),
    "da nang": (16.054, 108.202),
    "hai chau": (16.047, 108.220),
    "thanh khe": (16.061, 108.190),
    "son tra": (16.091, 108.261),
    "ngu hanh son": (16.012, 108.256),
    "lien chieu": (16.079, 108.148),
    "hoa vang": (16.046, 108.106),
    # Quần đảo chủ quyền thiêng liêng
    "hoang sa": (16.530, 112.020),
    "huyen hoang sa": (16.530, 112.020),
    "quan dao hoang sa": (16.530, 112.020),
    "truong sa": (9.880, 114.250),
    "huyen truong sa": (9.880, 114.250),
    "quan dao truong sa": (9.880, 114.250),
    # Các tỉnh miền Trung
    "hue": (16.464, 107.591),
    "thua thien hue": (16.464, 107.591),
    "dong ha": (16.816, 107.100),
    "quang tri": (16.816, 107.100),
    "dong hoi": (17.476, 106.601),
    "quang binh": (17.476, 106.601),
    "ha tinh": (18.343, 105.906),
    "vinh": (18.673, 105.681),
    "nghe an": (18.673, 105.681),
    "thanh hoa": (19.808, 105.776),
    "quang ngai": (15.121, 108.792),
    "quy nhon": (13.782, 109.220),
    "binh dinh": (13.782, 109.220),
    "tuy hoa": (13.088, 109.308),
    "phu yen": (13.088, 109.308),
    "nha trang": (12.239, 109.197),
    "cam ranh": (11.921, 109.159),
    "khanh hoa": (12.239, 109.197),
    "phan rang": (11.565, 108.988),
    "ninh thuan": (11.565, 108.988),
    "phan thiet": (10.929, 108.102),
    "binh thuan": (10.929, 108.102),
    # Tây Nguyên
    "kon tum": (14.354, 108.000),
    "pleiku": (13.983, 108.000),
    "gia lai": (13.983, 108.000),
    "buon ma thuot": (12.668, 108.038),
    "dak lak": (12.668, 108.038),
    "gia nghia": (12.003, 107.689),
    "dak nong": (12.003, 107.689),
    "da lat": (11.940, 108.458),
    "bao loc": (11.548, 107.808),
    "lam dong": (11.940, 108.458),
    # Miền Bắc & Thủ đô
    "ha noi": (21.0285, 105.8542),
    "ba dinh": (21.036, 105.828),
    "hoan kiem": (21.031, 105.853),
    "dong da": (21.018, 105.826),
    "hai ba trung": (21.009, 105.858),
    "cau giay": (21.036, 105.790),
    "thanh xuan": (20.993, 105.811),
    "tay ho": (21.066, 105.820),
    "long bien": (21.036, 105.893),
    "hoang mai": (20.974, 105.855),
    "nam tu liem": (21.017, 105.764),
    "bac tu liem": (21.069, 105.759),
    "ha dong": (20.972, 105.774),
    "hai phong": (20.845, 106.688),
    "ha long": (20.951, 107.073),
    "quang ninh": (20.951, 107.073),
    "mong cai": (21.524, 107.967),
    "bac ninh": (21.186, 106.076),
    "bac giang": (21.273, 106.195),
    "hai duong": (20.937, 106.315),
    "hung yen": (20.646, 106.051),
    "thai binh": (20.446, 106.337),
    "nam dinh": (20.434, 106.177),
    "ninh binh": (20.251, 105.975),
    "phu ly": (20.541, 105.914),
    "ha nam": (20.541, 105.914),
    "vinh yen": (21.309, 105.605),
    "vinh phuc": (21.309, 105.605),
    "viet tri": (21.323, 105.402),
    "phu tho": (21.323, 105.402),
    "thai nguyen": (21.594, 105.848),
    "tuyen quang": (21.824, 105.218),
    "lang son": (21.854, 106.762),
    "cao bang": (22.666, 106.264),
    "bac kan": (22.147, 105.835),
    "ha giang": (22.823, 104.984),
    "lao cai": (22.486, 103.971),
    "sa pa": (22.336, 103.844),
    "yen bai": (21.717, 104.897),
    "son la": (21.328, 103.915),
    "dien bien": (21.385, 103.017),
    "dien bien phu": (21.385, 103.017),
    "lai chau": (22.396, 103.468),
    "hoa binh": (20.817, 105.338),
    # Miền Nam & TP. Hồ Chí Minh
    "ho chi minh": (10.823, 106.630),
    "sai gon": (10.823, 106.630),
    "quan 1": (10.776, 106.700),
    "quan 3": (10.784, 106.684),
    "quan 7": (10.734, 106.721),
    "thu duc": (10.849, 106.753),
    "binh thanh": (10.810, 106.709),
    "tan binh": (10.801, 106.654),
    "go vap": (10.838, 106.666),
    "can tho": (10.045, 105.747),
    "bien hoa": (10.957, 106.843),
    "dong nai": (10.957, 106.843),
    "thu dau mot": (10.981, 106.652),
    "binh duong": (10.981, 106.652),
    "vung tau": (10.346, 107.084),
    "ba ria": (10.496, 107.168),
    "ba ria - vung tau": (10.346, 107.084),
    "dong xoai": (11.533, 106.883),
    "binh phuoc": (11.533, 106.883),
    "tay ninh": (11.310, 106.098),
    "tan an": (10.536, 106.413),
    "long an": (10.536, 106.413),
    "my tho": (10.354, 106.365),
    "tien giang": (10.354, 106.365),
    "ben tre": (10.242, 106.376),
    "tra vinh": (9.935, 106.346),
    "vinh long": (10.254, 105.972),
    "cao lanh": (10.460, 105.633),
    "sa dec": (10.294, 105.757),
    "dong thap": (10.460, 105.633),
    "long xuyen": (10.383, 105.417),
    "chau doc": (10.706, 105.116),
    "an giang": (10.383, 105.417),
    "rach gia": (10.013, 105.081),
    "phu quoc": (10.229, 103.957),
    "kien giang": (10.013, 105.081),
    "vi thanh": (9.784, 105.470),
    "hau giang": (9.784, 105.470),
    "soc trang": (9.603, 105.980),
    "bac lieu": (9.294, 105.728),
    "ca mau": (9.177, 105.152),
    # Hải đảo
    "con dao": (8.683, 106.600),
    "phu quy": (10.517, 108.950),
    "ly son": (15.383, 109.117),
    "con co": (17.167, 107.333),
    "bach long vi": (20.133, 107.717),
    "co to": (20.983, 107.767),
    "cat ba": (20.727, 107.045),
}


def remove_accents(text: str) -> str:
    """Normalize and strip Vietnamese diacritics for flexible search."""
    if not text:
        return ""
    nfkd = unicodedata.normalize('NFD', text)
    res = "".join(c for c in nfkd if unicodedata.category(c) != 'Mn')
    return res.replace('đ', 'd').replace('Đ', 'D').lower().strip()


def lookup_coords(name: str, code_name: Optional[str] = None) -> tuple[Optional[float], Optional[float]]:
    """Look up known latitude/longitude for administrative unit."""
    clean = remove_accents(name)
    if clean in KNOWN_COORDINATES:
        return KNOWN_COORDINATES[clean]
    if code_name:
        clean_code = code_name.replace("_", " ").strip()
        if clean_code in KNOWN_COORDINATES:
            return KNOWN_COORDINATES[clean_code]
    for key, coords in KNOWN_COORDINATES.items():
        if key in clean or clean in key:
            return coords
    return (None, None)


async def seed_vietnam_provinces(db: AsyncSession, force: bool = False) -> dict[str, Any]:
    """Seed all 63 provinces, 696 districts, and 10,035 wards into database."""
    try:
        # Check existing count
        res = await db.execute(select(func.count(models.Province.code)))
        prov_count = res.scalar() or 0
        if prov_count >= 34 and not force:
            res_d = await db.execute(select(func.count(models.District.code)))
            res_w = await db.execute(select(func.count(models.Ward.code)))
            return {
                "provinces": prov_count,
                "districts": res_d.scalar() or 0,
                "wards": res_w.scalar() or 0,
                "already_seeded": True
            }

        # Clear outdated data if re-seeding
        if prov_count > 0:
            await db.execute(delete(models.Ward))
            await db.execute(delete(models.District))
            await db.execute(delete(models.Province))
            await db.execute(delete(models.AdministrativeUnit))
            await db.execute(delete(models.AdministrativeRegion))
            await db.flush()

        if not os.path.exists(UNITS_JSON_PATH):
            logger.warning("vietnam_units.json not found at %s", UNITS_JSON_PATH)
            return {"provinces": 0, "districts": 0, "wards": 0}

        with open(UNITS_JSON_PATH, "r", encoding="utf-8") as f:
            units = json.load(f)

        # 1. Regions
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

        # 2. Administrative unit types
        unit_data = [
            (1, 'Thành phố trực thuộc trung ương', 'Municipality', 'Thành phố', 'City', 'thanh_pho_truc_thuoc_trung_uong', 'municipality'),
            (2, 'Tỉnh', 'Province', 'Tỉnh', 'Province', 'tinh', 'province'),
            (3, 'Quận', 'District', 'Quận', 'District', 'quan', 'district'),
            (4, 'Huyện', 'District', 'Huyện', 'District', 'huyen', 'district'),
            (5, 'Thị xã', 'Town', 'Thị xã', 'Town', 'thi_xa', 'town'),
            (6, 'Thành phố thuộc tỉnh', 'Provincial City', 'Thành phố', 'City', 'thanh_pho_thuoc_tinh', 'city'),
            (7, 'Phường', 'Ward', 'Phường', 'Ward', 'phuong', 'ward'),
            (8, 'Xã', 'Commune', 'Xã', 'Commune', 'xa', 'commune'),
            (9, 'Thị trấn', 'Township', 'Thị trấn', 'Township', 'thi_tran', 'township'),
        ]
        for uid, fn, fne, sn, sne, cn, cne in unit_data:
            db.add(models.AdministrativeUnit(
                id=uid, full_name=fn, full_name_en=fne, short_name=sn, short_name_en=sne, code_name=cn, code_name_en=cne
            ))

        await db.flush()

        prov_objs = []
        district_objs = []
        ward_objs = []

        for p in units:
            p_code = str(p.get("Code", "")).strip()
            if not p_code:
                continue
            p_name = p.get("Name", "")
            p_lat, p_lon = lookup_coords(p_name, p.get("CodeName"))
            prov_objs.append(models.Province(
                code=p_code,
                name=p_name,
                name_en=p.get("NameEn"),
                full_name=p.get("FullName", ""),
                full_name_en=p.get("FullNameEn"),
                code_name=p.get("CodeName"),
                latitude=p_lat,
                longitude=p_lon,
            ))

            # Direct wards (34-province model)
            for w in (p.get("Wards") or p.get("Ward") or []):
                w_code = str(w.get("Code", "")).strip()
                if not w_code:
                    continue
                w_name = w.get("Name", "")
                w_lat, w_lon = lookup_coords(w_name, w.get("CodeName"))
                if w_lat is None:
                    w_lat, w_lon = p_lat, p_lon
                ward_objs.append(models.Ward(
                    code=w_code,
                    name=w_name,
                    name_en=w.get("NameEn"),
                    full_name=w.get("FullName", ""),
                    full_name_en=w.get("FullNameEn"),
                    code_name=w.get("CodeName"),
                    province_code=p_code,
                    latitude=w_lat,
                    longitude=w_lon,
                    postal_code=w.get("PostalCode", ""),
                ))

            # Nested districts and wards (if 3-tier model)
            for d in (p.get("District") or []):
                d_code = str(d.get("Code", "")).strip()
                if not d_code:
                    continue
                d_name = d.get("Name", "")
                d_lat, d_lon = lookup_coords(d_name, d.get("CodeName"))
                if d_lat is None:
                    d_lat, d_lon = p_lat, p_lon
                district_objs.append(models.District(
                    code=d_code,
                    name=d_name,
                    name_en=d.get("NameEn"),
                    full_name=d.get("FullName", ""),
                    full_name_en=d.get("FullNameEn"),
                    code_name=d.get("CodeName"),
                    province_code=p_code,
                    latitude=d_lat,
                    longitude=d_lon,
                ))

                for w in (d.get("Ward") or []):
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
                        district_code=d_code,
                        province_code=p_code,
                        latitude=d_lat,
                        longitude=d_lon,
                    ))

        db.add_all(prov_objs)
        await db.flush()

        # Batch insert districts and wards to avoid excessive memory
        batch_size = 1000
        for i in range(0, len(district_objs), batch_size):
            db.add_all(district_objs[i:i + batch_size])
            await db.flush()

        for i in range(0, len(ward_objs), batch_size):
            db.add_all(ward_objs[i:i + batch_size])
            await db.flush()

        db.add(models.VNProvincesMetadata(
            dataset_version="v2.4.0-full",
            latest_decree="Official 63 Provinces & Municipalities",
            generated_at="2026-10-14T16:00:00Z"
        ))
        await db.commit()
        logger.info("Successfully seeded %d provinces, %d districts, and %d wards into database.",
                    len(prov_objs), len(district_objs), len(ward_objs))
        return {
            "provinces": len(prov_objs),
            "districts": len(district_objs),
            "wards": len(ward_objs),
            "already_seeded": False
        }
    except Exception as exc:
        await db.rollback()
        logger.error("Error seeding vietnam provinces into database: %s", exc)
        return {"error": str(exc)}


async def search_administrative_units(db: Optional[AsyncSession], query: str, limit: int = 15) -> list[dict[str, Any]]:
    """Search provinces, districts, and wards, returning exact coordinates and administrative names."""
    cleaned = str(query or "").strip()
    if not cleaned:
        return []

    norm_query = remove_accents(cleaned)
    pattern = f"%{cleaned}%"
    results = []
    seen = set()

    # 1. Database search if session provided
    if db is not None:
        try:
            # 1a. Search Districts (Thành phố thuộc tỉnh, Quận, Huyện)
            res_d = await db.execute(
                select(models.District, models.Province.full_name.label("province_name"))
                .join(models.Province, models.District.province_code == models.Province.code, isouter=True)
                .where(or_(
                    models.District.name.ilike(pattern),
                    models.District.full_name.ilike(pattern),
                    models.District.code_name.ilike(f"%{norm_query}%"),
                ))
                .limit(limit)
            )
            for d, prov_name in res_d.all():
                key = f"district_{d.code}"
                if key in seen:
                    continue
                seen.add(key)
                lat, lon = d.latitude, d.longitude
                if lat is None:
                    lat, lon = lookup_coords(d.name, d.code_name)
                results.append({
                    "type": "district",
                    "code": d.code,
                    "name": d.name,
                    "full_name": d.full_name,
                    "province_name": prov_name,
                    "display_name": f"{d.full_name}, {prov_name}" if prov_name else d.full_name,
                    "lat": lat,
                    "lon": lon,
                    "source": "Cơ sở dữ liệu hành chính Việt Nam"
                })

            # 1b. Search Provinces (Tỉnh / Thành phố trực thuộc trung ương)
            res_p = await db.execute(
                select(models.Province)
                .where(or_(
                    models.Province.name.ilike(pattern),
                    models.Province.full_name.ilike(pattern),
                    models.Province.code_name.ilike(f"%{norm_query}%"),
                ))
                .limit(5)
            )
            for p in res_p.scalars().all():
                key = f"province_{p.code}"
                if key in seen:
                    continue
                seen.add(key)
                lat, lon = p.latitude, p.longitude
                if lat is None:
                    lat, lon = lookup_coords(p.name, p.code_name)
                results.append({
                    "type": "province",
                    "code": p.code,
                    "name": p.name,
                    "full_name": p.full_name,
                    "province_name": p.full_name,
                    "display_name": p.full_name,
                    "lat": lat,
                    "lon": lon,
                    "source": "Cơ sở dữ liệu hành chính Việt Nam"
                })

            # 1c. Search Wards (Xã / Phường / Thị trấn)
            if len(results) < limit:
                res_w = await db.execute(
                    select(
                        models.Ward,
                        models.District.full_name.label("district_name"),
                        models.Province.full_name.label("province_name"),
                        models.District.latitude.label("district_lat"),
                        models.District.longitude.label("district_lon"),
                    )
                    .join(models.District, models.Ward.district_code == models.District.code, isouter=True)
                    .join(models.Province, models.Ward.province_code == models.Province.code, isouter=True)
                    .where(or_(
                        models.Ward.name.ilike(pattern),
                        models.Ward.full_name.ilike(pattern),
                        models.Ward.code_name.ilike(f"%{norm_query}%"),
                    ))
                    .limit(limit - len(results))
                )
                for w, dist_name, prov_name, d_lat, d_lon in res_w.all():
                    key = f"ward_{w.code}"
                    if key in seen:
                        continue
                    seen.add(key)
                    lat = w.latitude or d_lat
                    lon = w.longitude or d_lon
                    if lat is None and dist_name:
                        lat, lon = lookup_coords(dist_name)
                    disp_parts = [w.full_name]
                    if dist_name:
                        disp_parts.append(dist_name)
                    if prov_name:
                        disp_parts.append(prov_name)
                    results.append({
                        "type": "ward",
                        "code": w.code,
                        "name": w.name,
                        "full_name": w.full_name,
                        "district_name": dist_name,
                        "province_name": prov_name,
                        "display_name": ", ".join(disp_parts),
                        "lat": lat,
                        "lon": lon,
                        "source": "Cơ sở dữ liệu hành chính Việt Nam"
                    })
        except Exception as exc:
            logger.debug("Database administrative search failed, using fallback: %s", exc)

    # 2. In-memory fallback if database returned no results or is unavailable
    if not results and os.path.exists(UNITS_JSON_PATH):
        try:
            with open(UNITS_JSON_PATH, "r", encoding="utf-8") as f:
                units = json.load(f)

            for p in units:
                p_name = p.get("Name", "")
                p_full = p.get("FullName", "")
                p_norm = remove_accents(p_name)
                p_lat, p_lon = lookup_coords(p_name, p.get("CodeName"))

                # Direct ward matches (34-province model)
                for w in (p.get("Wards") or p.get("Ward") or []):
                    w_name = w.get("Name", "")
                    w_full = w.get("FullName", "")
                    w_norm = remove_accents(w_name)
                    if norm_query in w_norm or norm_query in remove_accents(w_full):
                        w_lat, w_lon = lookup_coords(w_name, w.get("CodeName"))
                        if w_lat is None:
                            w_lat, w_lon = p_lat, p_lon
                        results.append({
                            "type": "ward",
                            "code": w.get("Code"),
                            "name": w_name,
                            "full_name": w_full,
                            "province_name": p_full,
                            "display_name": f"{w_full}, {p_full}",
                            "lat": w_lat,
                            "lon": w_lon,
                            "source": "Cơ sở dữ liệu hành chính Việt Nam"
                        })
                        if len(results) >= limit:
                            break
                if len(results) >= limit:
                    break

                # Province match
                if norm_query in p_norm or norm_query in remove_accents(p_full):
                    results.append({
                        "type": "province",
                        "code": p.get("Code"),
                        "name": p_name,
                        "full_name": p_full,
                        "province_name": p_full,
                        "display_name": p_full,
                        "lat": p_lat,
                        "lon": p_lon,
                        "source": "Cơ sở dữ liệu hành chính Việt Nam"
                    })
                if len(results) >= limit:
                    break
        except Exception as exc:
            logger.debug("In-memory administrative search failed: %s", exc)

    return results


if __name__ == "__main__":
    import asyncio
    import sys
    from pathlib import Path
    from dotenv import load_dotenv

    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    load_dotenv(backend_dir / ".env", override=False)

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    from app.database import engine, device_session_scope
    from app.models import Base

    async def _cli_main():
        print("1. Khởi tạo cấu trúc bảng (provinces, districts, wards)...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("2. Nạp dữ liệu 63 tỉnh/thành, 696 quận/huyện, 10.035 xã/phường...")
        async with device_session_scope() as session:
            res = await seed_vietnam_provinces(session, force=True)
            if res.get("already_seeded"):
                print(f"✓ Đã có dữ liệu từ trước: {res.get('provinces')} tỉnh/thành trong DB.")
            elif "error" in res:
                print(f"✗ Thất bại: {res.get('error')}")
            else:
                print(f"✓ Thành công! Đã nạp {res.get('provinces')} tỉnh/thành, {res.get('districts')} quận/huyện, {res.get('wards')} xã/phường!")

    asyncio.run(_cli_main())

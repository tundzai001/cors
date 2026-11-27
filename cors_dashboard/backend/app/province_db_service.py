"""Service for Vietnamese administrative provinces, districts, and wards database."""
import asyncio
import json
from pathlib import Path
import logging
import os
import re
import unicodedata
from typing import Any, Optional
from sqlalchemy import select, func, delete, text
from sqlalchemy.ext.asyncio import AsyncSession
from . import models
from .administrative_catalog import PREFIX, normalize, query_parts, matches_place, search_catalog

logger = logging.getLogger("app.main")
VIETNAM_UNITS_FILENAME = "vietnam_units.json"

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SRC_GIS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src", "gis"))
UNITS_JSON_PATH = os.path.join(DATA_DIR, VIETNAM_UNITS_FILENAME)
if not os.path.exists(UNITS_JSON_PATH) and os.path.exists(os.path.join(SRC_GIS_DIR, VIETNAM_UNITS_FILENAME)):
    UNITS_JSON_PATH = os.path.join(SRC_GIS_DIR, VIETNAM_UNITS_FILENAME)

# Điểm đại diện của địa danh đã biết; không dùng làm tọa độ cho đơn vị con.
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
    clean = re.sub(PREFIX, '', clean)
    if clean in KNOWN_COORDINATES:
        return KNOWN_COORDINATES[clean]
    return (None, None)


async def seed_vietnam_provinces(db: AsyncSession, force: bool = False) -> dict[str, Any]:
    """Seed the bundled current two-tier dataset; legacy names stay versioned separately."""
    try:
        # Check existing count
        res = await db.execute(select(func.count(models.Province.code)))
        prov_count = res.scalar() or 0
        if prov_count >= 34 and not force:
            res_w = await db.execute(select(func.count(models.Ward.code)))
            return {
                "provinces": prov_count,
                "districts": 0,
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
            logger.warning("%s not found at %s", VIETNAM_UNITS_FILENAME, UNITS_JSON_PATH)
            return {"provinces": 0, "districts": 0, "wards": 0}

        units = json.loads(await asyncio.to_thread(Path(UNITS_JSON_PATH).read_text, encoding="utf-8"))

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
                        latitude=None,
                        longitude=None,
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
            dataset_version="v5.2.0",
            latest_decree="388/NQ-UBTVQH16",
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
        logger.exception("Error seeding vietnam provinces into database: %s", exc)
        return {"error": str(exc)}


async def list_legacy_units(db, kind, province_code=None, district_code=None):
    """Read historical SQL tables without mixing their keys with the current schema."""
    queries = {
        'province': "SELECT p.code,p.name,p.full_name,p.name_en,p.code_name,p.code AS province_code,NULL AS district_code FROM vn_admin_legacy.provinces p",
        'district': "SELECT d.code,d.name,d.full_name,d.province_code,NULL AS district_code FROM vn_admin_legacy.districts d",
        'ward': "SELECT w.code,w.name,w.full_name,d.province_code,w.district_code FROM vn_admin_legacy.wards w JOIN vn_admin_legacy.districts d ON d.code=w.district_code",
    }
    conditions, params = [], {}
    for key, value in [('province_code', province_code), ('district_code', district_code)]:
        if value:
            conditions.append(key + '=:' + key)
            params[key] = value
    statement = 'SELECT * FROM (' + queries[kind] + ') units'
    if conditions:
        statement += ' WHERE ' + ' AND '.join(conditions)
    try:
        async with db.begin_nested():
            rows = (await db.execute(text(statement + ' ORDER BY name'), params)).mappings().all()
        return [dict(row) for row in rows]
    except Exception as exc:
        logger.debug('Historical SQL lookup unavailable; using bundled names: %s', exc)
        from .administrative_catalog import catalog
        return [r for r in catalog('legacy') if r['type'] == kind
                and (not province_code or r['province_code'] == province_code)
                and (not district_code or r['district_code'] == district_code)]


async def search_administrative_units(db: Optional[AsyncSession], query: str, limit: int = 15,
                                      dataset: str = "all") -> list[dict[str, Any]]:
    """Search current SQL names and a separately versioned pre-merger catalogue."""
    if not str(query or "").strip():
        return []
    results = []
    if db is not None:
        tokens = ' '.join(query_parts(query)).split()
        versions = ('current', 'legacy') if dataset == 'all' else (dataset,)
        for version in versions if tokens else ():
            params = {f'token{i}': '%' + token + '%' for i, token in enumerate(tokens)}
            params['limit'] = limit * 4
            predicates = ' AND '.join(f"search_name LIKE :token{i}" for i in range(len(tokens)))
            if version == 'legacy':
                units_sql = """
                    SELECT 'ward' AS type, w.code, w.name, w.full_name,
                           p.code AS province_code, p.full_name AS province_name,
                           d.code AS district_code, d.full_name AS district_name,
                           lower(replace(w.code_name || ' ' || d.code_name || ' ' || p.code_name, '_', ' ')) AS search_name
                    FROM vn_admin_legacy.wards w
                    JOIN vn_admin_legacy.districts d ON d.code = w.district_code
                    JOIN vn_admin_legacy.provinces p ON p.code = d.province_code
                    UNION ALL
                    SELECT 'district', d.code, d.name, d.full_name, p.code, p.full_name,
                           NULL, NULL, lower(replace(d.code_name || ' ' || p.code_name, '_', ' '))
                    FROM vn_admin_legacy.districts d JOIN vn_admin_legacy.provinces p ON p.code = d.province_code
                    UNION ALL
                    SELECT 'province', p.code, p.name, p.full_name, p.code, p.full_name,
                           NULL, NULL, lower(replace(p.code_name, '_', ' '))
                    FROM vn_admin_legacy.provinces p
                """
            else:
                units_sql = """
                    SELECT 'ward' AS type, w.code, w.name, w.full_name,
                           w.province_code, p.full_name AS province_name,
                           NULL AS district_code, NULL AS district_name,
                           lower(replace(coalesce(w.code_name, '') || ' ' || coalesce(p.code_name, ''), '_', ' ')) AS search_name
                    FROM wards w JOIN provinces p ON p.code = w.province_code
                    UNION ALL
                    SELECT 'province', p.code, p.name, p.full_name,
                           p.code, p.full_name, NULL, NULL,
                           lower(replace(coalesce(p.code_name, ''), '_', ' '))
                    FROM provinces p
                """
            statement = text('SELECT * FROM (' + units_sql + ') units WHERE ' + predicates + ' LIMIT :limit')
            try:
                async with db.begin_nested():
                    found = (await db.execute(statement, params)).mappings().all()
                for row in found:
                    item = dict(row)
                    item.pop('search_name', None)
                    item['display_name'] = ', '.join(filter(None, [
                        item['full_name'] or item['name'], item['district_name'],
                        item['province_name'] if item['type'] != 'province' else None]))
                    if not matches_place(query, item['name'], item['display_name']):
                        continue
                    item.update(dataset=version, dataset_version='v2.4.1' if version == 'legacy' else 'v5.2.0',
                                lat=None, lon=None, coordinate_source=None,
                                source='Cơ sở dữ liệu hành chính Việt Nam', storage='postgresql')
                    results.append(item)
            except Exception as exc:
                logger.debug("Administrative SQL lookup unavailable (%s); using versioned catalogue: %s", version, exc)

    seen = {(r['dataset'], r['type'], r['code']) for r in results}
    for item in search_catalog(query, limit=limit, dataset=dataset):
        key = (item['dataset'], item['type'], item['code'])
        if key not in seen:
            results.append(item)
            seen.add(key)
    first = (query_parts(query) or [''])[0]
    results.sort(key=lambda r: (normalize(r['name']) != first,
                                r['dataset'] != 'current', len(normalize(r['name'])), r['display_name']))
    for item in results:
        # A known provincial point is never inherited by a ward or district.
        if item['type'] == 'province':
            item['lat'], item['lon'] = lookup_coords(item['name'])
            if item['lat'] is not None:
                item['coordinate_source'] = 'known_province_point'
    return results[:limit]


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
        print("2. Nạp dữ liệu hành chính hiện hành...")
        async with device_session_scope() as session:
            res = await seed_vietnam_provinces(session, force=True)
            if res.get("already_seeded"):
                print(f"✓ Đã có dữ liệu từ trước: {res.get('provinces')} tỉnh/thành trong DB.")
            elif "error" in res:
                print(f"✗ Thất bại: {res.get('error')}")
            else:
                print(f"✓ Thành công! Đã nạp {res.get('provinces')} tỉnh/thành, {res.get('districts')} quận/huyện, {res.get('wards')} xã/phường!")

    asyncio.run(_cli_main())

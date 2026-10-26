"""Administrative lookup against the upstream SQL shape and historical names."""
import unittest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.administrative_catalog import catalog, search_catalog, name_matches_query
from app.province_db_service import search_administrative_units, lookup_coords
from app.map_routes import get_vietnam_provinces, get_vietnam_wards, get_vietnam_districts
from import_legacy_provinces import source_sql, import_sql


class AdministrativeCatalogueTests(unittest.IsolatedAsyncioTestCase):
    def test_legacy_import_is_pinned_transactional_and_isolated(self):
        self.assertIn('INSERT INTO wards', source_sql())
        statement = import_sql({'provinces': 'a' * 32, 'wards': 'b' * 32})
        self.assertTrue(statement.startswith('BEGIN;'))
        self.assertTrue(statement.rstrip().endswith('COMMIT;'))
        self.assertIn('SET LOCAL search_path = vn_admin_legacy, pg_catalog;', statement)
        self.assertIn('Current provinces changed', statement)
        self.assertIn('10035', statement)

    def test_version_counts_and_historical_ward_disambiguation(self):
        for dataset, counts in [('current', (34, 0, 3321)), ('legacy', (63, 696, 10035))]:
            self.assertEqual(tuple(sum(r['type'] == kind for r in catalog(dataset))
                                   for kind in ('province', 'district', 'ward')), counts)
        for query in ('Ninh Vân, Ninh Bình', 'Ninh Van Ninh Binh',
                      'Xã Ninh Vân, Hoa Lư, Ninh Bình'):
            rows = search_catalog(query)
            self.assertEqual([r['code'] for r in rows], ['14554'])
            self.assertEqual(rows[0]['dataset'], 'legacy')
            self.assertIsNone(rows[0]['lat'])
        self.assertEqual(search_catalog('Ninh Vân, Ninh Bình', dataset='current'), [])
        self.assertFalse(name_matches_query('Ninh Vân, Ninh Bình', 'Thành phố Ninh Bình'))
        self.assertFalse(name_matches_query('Ninh Vân, Ninh Bình',
                         'Đường tránh Đồng Văn - Phủ Lý, Phù Vân, Tỉnh Ninh Bình'))
        self.assertEqual(lookup_coords('Ninh Vân, Ninh Bình'), (None, None))

    async def test_sql_import_without_district_or_gis_columns_is_read_directly(self):
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        try:
            async with engine.begin() as conn:
                await conn.execute(text('''CREATE TABLE provinces (
                    code TEXT PRIMARY KEY, name TEXT, full_name TEXT, code_name TEXT,
                    name_en TEXT, postal_code_prefix TEXT)'''))
                await conn.execute(text('''CREATE TABLE wards (
                    code TEXT PRIMARY KEY, name TEXT, full_name TEXT, code_name TEXT,
                    province_code TEXT)'''))
                await conn.execute(text("INSERT INTO provinces VALUES ('37','Ninh Bình','Tỉnh Ninh Bình','ninh_binh',NULL,NULL)"))
                # Not present in the bundled catalogue: this must come from SQL.
                await conn.execute(text("INSERT INTO wards VALUES ('test','Đá Mới','Xã Đá Mới','da_moi','37')"))
            async with AsyncSession(engine) as db:
                rows = await search_administrative_units(db, 'Đá Mới, Ninh Bình')
                self.assertEqual([r['code'] for r in rows], ['test'])
                self.assertIsNone(rows[0]['lat'])
                provinces = await get_vietnam_provinces(dataset='current', db=db)
                self.assertEqual([r['code'] for r in provinces], ['37'])
                wards = await get_vietnam_wards(province_code='37', district_code=None, dataset='current', db=db)
                self.assertEqual([r['code'] for r in wards], ['test'])
                self.assertEqual(await get_vietnam_districts(province_code='37', dataset='current', db=db), [])
                legacy = await search_administrative_units(db, 'Ninh Vân, Ninh Bình')
                self.assertEqual([r['code'] for r in legacy], ['14554'])
                self.assertEqual((await db.execute(text('SELECT count(*) FROM wards'))).scalar(), 1)
                await db.execute(text("ATTACH DATABASE ':memory:' AS vn_admin_legacy"))
                for sql in (
                    'CREATE TABLE vn_admin_legacy.provinces (code TEXT,name TEXT,full_name TEXT,name_en TEXT,code_name TEXT)',
                    'CREATE TABLE vn_admin_legacy.districts (code TEXT,name TEXT,full_name TEXT,code_name TEXT,province_code TEXT)',
                    'CREATE TABLE vn_admin_legacy.wards (code TEXT,name TEXT,full_name TEXT,code_name TEXT,district_code TEXT)',
                    "INSERT INTO vn_admin_legacy.provinces VALUES ('37','Ninh Bình','Tỉnh Ninh Bình',NULL,'ninh_binh')",
                    "INSERT INTO vn_admin_legacy.districts VALUES ('D','Hoa Lư','Thành phố Hoa Lư','hoa_lu','37')",
                    "INSERT INTO vn_admin_legacy.wards VALUES ('old-test','Gốc Cũ','Xã Gốc Cũ','goc_cu','D')",
                ):
                    await db.execute(text(sql))
                old = await search_administrative_units(db, 'Gốc Cũ, Ninh Bình')
                self.assertEqual([r['code'] for r in old], ['old-test'])
                self.assertEqual(old[0]['storage'], 'postgresql')
                wards = await get_vietnam_wards(province_code='37', district_code='D', dataset='legacy', db=db)
                self.assertEqual([r['code'] for r in wards], ['old-test'])
        finally:
            await engine.dispose()

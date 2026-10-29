"""Saved mountpoints remain available without a reachable caster."""
import io
import runpy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app import caster_routes, caster_source_store as store, models, schemas


@compiles(JSONB, 'sqlite')
def sqlite_jsonb(type_, compiler, **kwargs):
    return 'JSON'


class CasterSourceInventoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite://')
        async with self.engine.begin() as connection:
            await connection.run_sync(lambda db: models.AuthBase.metadata.create_all(db, tables=[models.CasterSource.__table__]))
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.sessions()
        self.admin = SimpleNamespace(role='admin')
        self.source = {'mountpoint': 'BASE', 'identifier': 'Station', 'password': 'private',
                       'latitude': 21.1, 'longitude': 105.7, 'datacount': 10}

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def rows(self):
        result = await self.db.execute(select(models.CasterSource))
        return result.scalars().all()

    async def test_import_is_durable_idempotent_and_keeps_credentials(self):
        await store.save_sources(self.db, 'caster.test', 2101, [self.source, self.source], 100)
        await self.db.commit()
        updated = {**self.source, 'password': '', 'datacount': 20, 'latitude': float('nan')}
        await store.save_sources(self.db, 'CASTER.TEST', 2101, [updated], 200)
        await store.save_sources(self.db, 'backup.test', 2101, [self.source], 200)
        await self.db.commit()
        rows = await self.rows()
        self.assertEqual(len(rows), 2, 'Identical mountpoint names on separate hosts stay separate')
        row = next(row for row in rows if row.host == 'caster.test')
        self.assertEqual((row.created_at, row.last_seen_at, row.password), (100, 200, 'private'))
        self.assertEqual(row.source_data['datacount'], 20)
        self.assertIsNone(row.source_data['latitude'])
        await self.db.close()
        self.db = self.sessions()
        response = store.inventory_response(await self.rows())
        self.assertEqual(response['total'], 2)
        self.assertNotIn('private', str(response))
        self.assertTrue(all('password' not in row for row in response['all']))

    async def test_failed_or_empty_sync_preserves_saved_sources(self):
        await store.save_sources(self.db, 'caster.test', 2101, [self.source], 100)
        await self.db.commit()
        with patch.object(store, 'auth_session_scope', self.sessions), \
             patch.object(store, 'lock_source_host', AsyncMock()), \
             patch.object(store, 'CasterClient') as client:
            client.return_value.get_sources = AsyncMock(return_value=[])
            result = await store.sync_caster_sources('caster.test', 2101)
            self.assertIsNone(result['error'])
            client.return_value.get_sources.return_value = None
            with self.assertLogs('app.main', level='WARNING'):
                result = await store.sync_caster_sources('caster.test', 2101)
            self.assertIsNotNone(result['error'])
        self.assertEqual(len(await self.rows()), 1)

    async def test_explicit_sync_reports_partial_failure_and_requires_admin(self):
        results = [{'host': 'one.test', 'error': None}, {'host': 'two.test', 'error': 'offline'}]
        with patch.object(caster_routes, '_read_caster_config_from_env', return_value=(['one.test', 'two.test'], {})), \
             patch.object(caster_routes, 'sync_configured_sources', AsyncMock(return_value=results)) as sync:
            response = await caster_routes.sync_admin_caster_sources(current_user=self.admin)
            self.assertEqual(response['synced_hosts'], 1)
            self.assertEqual(response['results'], results)
            sync.assert_awaited_once()
            with self.assertRaises(HTTPException):
                await caster_routes.sync_admin_caster_sources(current_user=SimpleNamespace(role='viewer'))

    async def test_list_reads_database_without_calling_caster(self):
        await store.save_sources(self.db, 'caster.test', 2101, [self.source], 100)
        await self.db.commit()
        with patch('app.caster_client.CasterClient', side_effect=AssertionError('No sockets on GET')), \
             patch.object(caster_routes, '_read_caster_config_from_env', return_value=(['caster.test', 'empty.test'], {})):
            result = await caster_routes.get_admin_caster_sources(db=self.db, current_user=self.admin)
        self.assertEqual(result['all'][0]['mountpoint'], 'BASE')
        self.assertEqual(result['by_host']['empty.test'], [])
        self.assertEqual(result['last_synced_at'], 100)
        with self.assertRaises(HTTPException):
            await caster_routes.get_admin_caster_sources(db=self.db, current_user=SimpleNamespace(role='viewer'))

    async def test_create_delete_updates_saved_inventory_only_after_caster_success(self):
        request = schemas.CasterSourceCreateRequest(host='caster.test', identifier='Station', mountpoint='BASE', password='private')
        delete_request = schemas.CasterSourceDeleteRequest(host='caster.test', identifier='Station', mountpoint='BASE')
        with patch('app.caster_client.CasterClient') as client, \
             patch.object(caster_routes, 'lock_source_host', AsyncMock()), \
             patch.object(caster_routes, 'validate_caster_hosts', side_effect=lambda hosts: hosts), \
             patch.object(caster_routes, '_read_caster_config_from_env', return_value=(['caster.test'], {'port': 2101})):
            client.return_value.create_source = AsyncMock(return_value=False)
            with self.assertRaises(HTTPException):
                await caster_routes.create_admin_caster_source(request, db=self.db, current_user=self.admin)
            self.assertEqual(await self.rows(), [])
            client.return_value.create_source.return_value = True
            await caster_routes.create_admin_caster_source(request, db=self.db, current_user=self.admin)
            self.assertEqual(len(await self.rows()), 1)
            client.return_value.delete_source = AsyncMock(return_value=False)
            client.return_value.get_sources = AsyncMock(return_value=None)
            with self.assertRaises(HTTPException):
                await caster_routes.delete_admin_caster_source(delete_request, db=self.db, current_user=self.admin)
            self.assertEqual(len(await self.rows()), 1)
            client.return_value.get_sources.return_value = []
            await caster_routes.delete_admin_caster_source(delete_request, db=self.db, current_user=self.admin)
            self.assertEqual(await self.rows(), [])


class CasterSourceMigrationTests(unittest.TestCase):
    def test_postgres_upgrade_and_downgrade(self):
        migration = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'alembic_auth/versions/20261022_caster_sources.py'))
        output = io.StringIO()
        context = MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})
        with Operations.context(context):
            migration['upgrade']()
            migration['downgrade']()
        sql = output.getvalue()
        self.assertIn('CREATE TABLE caster_sources', sql)
        self.assertIn('source_data JSONB NOT NULL', sql)
        self.assertIn('UNIQUE (host, port, mountpoint)', sql)
        self.assertIn('DROP TABLE caster_sources', sql)


if __name__ == '__main__':
    unittest.main()

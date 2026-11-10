"""Restore saved access without extending expiry or reporting failed writes as success."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from app import models, schemas
from app.caster_helpers import saved_rover_duration
from app.routers.devices import update_rover


class RoverRestoreTests(unittest.IsolatedAsyncioTestCase):
    def test_saved_expiry_does_not_renew_or_revive_disabled_access(self):
        with patch('app.caster_helpers.time.time', return_value=1000):
            self.assertEqual(saved_rover_duration(SimpleNamespace(is_active=True, expires_at=4600)), 3600)
            self.assertEqual(saved_rover_duration(SimpleNamespace(is_active=True, expires_at=None)), 315360000)
            for active, expires in [(False, None), (True, 999)]:
                rover = SimpleNamespace(is_active=active, expires_at=expires)
                with self.assertRaises(ValueError):
                    saved_rover_duration(rover)

    async def test_save_restores_missing_account_and_requires_confirmation(self):
        rover = models.RoverAccount(id=7, username='cp07', password='saved-secret', base_serial='',
            mountpoints='QN.CP', target_casters='caster.test', max_connections=10,
            show_position=True, expires_at=int(time.time())+3600, is_active=True,
            created_by_user_id=1, rtcm_cycle_enabled=False)
        admin = SimpleNamespace(id=1, username='admin', role='admin')
        caster = AsyncMock()
        caster.get_user_remaining_seconds.return_value = None
        caster.create_user.return_value = True
        db = AsyncMock()
        with patch('app.crud.get_rover_by_id', AsyncMock(return_value=rover)), \
             patch('app.crud.check_rover_management_permission', AsyncMock(return_value=True)), \
             patch('app.crud.get_user_by_id', AsyncMock(return_value=admin)), \
             patch('app.crud.update_rover_account', AsyncMock(return_value=rover)) as update, \
             patch('app.routers.devices.validate_caster_hosts', side_effect=lambda hosts: hosts), \
             patch('app.routers.devices.redis_client.get_all_caster_cache', AsyncMock(return_value={})), \
             patch('app.caster_client.CasterClient', return_value=caster):
            await update_rover(7, schemas.RoverUpdate(), db, AsyncMock(), admin)
            payload = caster.create_user.await_args.kwargs
            self.assertEqual(payload['password'], 'saved-secret')
            self.assertEqual(payload['mountpoints'], 'QN.CP')
            self.assertTrue(3500 < payload['expiry_seconds'] <= 3600)
            self.assertNotIn('expires_at', update.await_args.args[2])
            update.reset_mock()
            caster.create_user.return_value = False
            request = schemas.RoverUpdate()
            devices_db = AsyncMock()
            with self.assertRaises(HTTPException) as error:
                await update_rover(7, request, db, devices_db, admin)
            self.assertEqual(error.exception.status_code, 502)
            update.assert_not_awaited()

    async def test_sync_by_username_restores_missing_account_and_reports_rejection(self):
        from app import rover_accounts_routes as routes
        rover = models.RoverAccount(id=7, username='cp07', password='saved-secret',
            target_casters='caster.test', mountpoints='QN.CP', max_connections=10,
            show_position=True, expires_at=None, is_active=True, base_serial='')
        admin = SimpleNamespace(id=1, username='admin', role='admin')
        caster = AsyncMock()
        caster.get_user_remaining_seconds.return_value = None
        caster.create_user.return_value = True
        with patch.object(routes.crud, 'get_rover_by_username', AsyncMock(return_value=rover)), \
             patch.object(routes.crud, 'check_rover_management_permission', AsyncMock(return_value=True)), \
             patch.object(routes, '_read_caster_config_from_env', return_value=(['caster.test'], {'port':2101})), \
             patch.object(routes, 'validate_caster_hosts', side_effect=lambda hosts: hosts), \
             patch('app.caster_client.CasterClient', return_value=caster):
            result = await routes.sync_rover_by_username('cp07', schemas.RoverUpdate(), AsyncMock(), AsyncMock(), admin)
            self.assertIs(result, rover)
            self.assertEqual(caster.create_user.await_args.kwargs['expiry_seconds'], 315360000)
            caster.create_user.return_value = False
            request = schemas.RoverUpdate()
            auth_db = AsyncMock()
            devices_db = AsyncMock()
            with self.assertRaises(HTTPException) as error:
                await routes.sync_rover_by_username('cp07', request, auth_db, devices_db, admin)
            self.assertEqual(error.exception.status_code, 502)

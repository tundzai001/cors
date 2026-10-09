"""Explicit admin token issuance registers a Pi discovered before its DB row."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from app import crud, license_manager, models, mqtt


class TokenOnboardingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.serial = '10000000e44764d2'
        self.db = SimpleNamespace(execute=AsyncMock(), add=Mock(), commit=AsyncMock(),
                                  refresh=AsyncMock(), rollback=AsyncMock())
        source = Path(__file__).parents[1] / 'app/routers/devices.py'
        node = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == 'auto_deploy_device_token')
        node.decorator_list = []
        self.dispatch = AsyncMock(return_value={'status': 'sent'})
        self.scope = dict(
            AsyncSession=object, Depends=lambda dependency: None, get_db=lambda: None,
            auth=SimpleNamespace(require_permission=lambda permission: None,
                                 Permission=SimpleNamespace(MANAGE_LICENSE='manage:license')),
            models=models, crud=SimpleNamespace(is_valid_serial=crud.is_valid_serial,
                                               log_system_event=AsyncMock()),
            select=select, func=func, HTTPException=HTTPException, IntegrityError=IntegrityError,
            license_manager=license_manager,
            mqtt_handler=SimpleNamespace(device_telemetry_cache={self.serial: {'name': 'Pi-4764d2'}},
                                         cache_device_metadata=mqtt.cache_device_metadata),
            ui_manager=SimpleNamespace(broadcast=AsyncMock()),
            schemas=SimpleNamespace(Device=SimpleNamespace(from_orm=lambda device:
                                    SimpleNamespace(model_dump=lambda: {'serial': device.serial}))),
            send_command_to_pi=self.dispatch,
        )
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), self.scope)
        self.admin = SimpleNamespace(id=1, username='admin')

    def lookup_returns(self, device):
        self.db.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: device))

    async def test_discovered_pi_is_registered_and_missing_auth_cache_replaced(self):
        self.lookup_returns(None)
        cache = {self.serial: {'exists': False, 'cached_at': 0}}

        async def dispatch(serial, command):
            self.db.commit.assert_awaited_once()
            self.assertTrue(cache[serial]['exists'])
            self.assertEqual(cache[serial]['device_token'], command['payload']['license_key'])
            self.assertEqual(command['command'], 'DEPLOY_LICENSE')
            return {'status': 'sent'}

        self.dispatch.side_effect = dispatch
        with patch.object(mqtt, 'device_metadata_cache', cache), patch.object(mqtt, 'device_metadata_locks', {}):
            result = await self.scope['auto_deploy_device_token'](self.serial, self.db, self.admin)
        self.assertEqual(result, {'status': 'sent'})
        device = self.db.add.call_args.args[0]
        self.assertEqual(device.name, 'Pi-4764d2')
        self.assertFalse(device.is_provisioned)
        self.assertEqual(device.status, 'awaiting_license')
        self.assertTrue(license_manager.is_device_token(device.device_token))

    async def test_existing_device_keeps_token_and_registration(self):
        token = license_manager.generate_device_token()
        device = models.Device(serial=self.serial, name='Existing', device_token=token,
                               is_provisioned=True, status='online', system_info={})
        self.lookup_returns(device)
        with patch.object(mqtt, 'device_metadata_cache', {}), patch.object(mqtt, 'device_metadata_locks', {}):
            await self.scope['auto_deploy_device_token'](self.serial.upper(), self.db, self.admin)
        self.db.add.assert_not_called()
        self.assertEqual(device.device_token, token)
        self.assertEqual(device.name, 'Existing')
        self.assertTrue(device.is_provisioned)
        self.assertEqual(self.dispatch.call_args.args[0], self.serial)

    async def test_invalid_serial_cannot_create_device(self):
        self.lookup_returns(None)
        with self.assertRaises(HTTPException) as error:
            await self.scope['auto_deploy_device_token']('Pi-invalid', self.db, self.admin)
        self.assertEqual(error.exception.status_code, 400)
        self.db.add.assert_not_called()
        self.db.commit.assert_not_awaited()
        self.dispatch.assert_not_awaited()


class LicenseFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_token_conflicts_and_database_failures_rollback_cleanly(self):
        from app import main, schemas
        device = models.Device(serial='10000000e44764d2', device_token='existing')
        for error, expected_status in (
            (IntegrityError('insert', {}, Exception('duplicate')), 409),
            (RuntimeError('database unavailable'), 500),
        ):
            with self.subTest(status=expected_status):
                db = SimpleNamespace(commit=AsyncMock(side_effect=error), rollback=AsyncMock())
                with patch.object(main.crud, 'get_device_by_serial', AsyncMock(return_value=device)):
                    with self.assertRaises(HTTPException) as raised:
                        await main.generate_pi_license(
                            schemas.LicenseRequest(serial=device.serial), db, SimpleNamespace(id=1, role='admin'))
                self.assertEqual(raised.exception.status_code, expected_status)
                db.rollback.assert_awaited_once()



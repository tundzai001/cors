"""Provisioning persists an admin name before offline status reads the DB."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from app import crud, models, mqtt, schemas
from app.routers import devices, users


class StationNamePreservationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.serial = '10000000e44764d2'
        self.device = models.Device(serial=self.serial, name='Pi-4764d2', device_token='saved-token', system_info={})
        self.db = SimpleNamespace(commit=AsyncMock())
        self.admin = SimpleNamespace(id=1, username='admin', role='admin')

    async def test_provision_saves_name_and_updates_caches_before_agent_dispatch(self):
        names = {self.serial: 'Pi-4764d2'}
        metadata = {self.serial: {'exists': True, 'name': 'Pi-4764d2'}}
        async def dispatch(serial, command):
            self.db.commit.assert_awaited_once()
            self.assertEqual(self.device.name, 'DN.MinhQuan')
            self.assertEqual(names[serial], 'DN.MinhQuan')
            self.assertEqual(metadata[serial]['name'], 'DN.MinhQuan')
            self.assertEqual(command, {'command': 'PROVISION_DEVICE', 'payload': {'name': 'DN.MinhQuan'}})
            return {'status': 'sent'}
        with patch.object(crud, 'get_device_by_serial', AsyncMock(return_value=self.device)), \
             patch.object(crud, 'log_system_event', AsyncMock()), \
             patch.object(users, 'send_command_to_pi', AsyncMock(side_effect=dispatch)), \
             patch.object(devices, 'send_command_to_pi', AsyncMock()) as generic, \
             patch.object(users.ui_manager, 'broadcast', AsyncMock()), \
             patch.object(mqtt, 'device_metadata_cache', metadata), \
             patch.object(mqtt, 'device_name_cache', names), \
             patch.object(mqtt, 'device_metadata_locks', {}):
            result = await devices.send_generic_command(self.serial,
                schemas.Command(command='PROVISION_DEVICE', payload={'name': ' DN.MinhQuan '}), self.db, self.admin)
        self.assertEqual(result['name'], 'DN.MinhQuan')
        self.assertEqual(self.device.device_token, 'saved-token')
        generic.assert_not_awaited()

    async def test_rename_is_preserved_when_agent_is_offline(self):
        self.device.status = 'offline'
        with patch.object(crud, 'get_device_by_serial', AsyncMock(return_value=self.device)), \
             patch.object(crud, 'log_system_event', AsyncMock()), \
             patch.object(users, 'send_command_to_pi', AsyncMock()) as dispatch, \
             patch.object(users.ui_manager, 'broadcast', AsyncMock()) as broadcast, \
             patch.object(mqtt, 'device_metadata_cache', {}), \
             patch.object(mqtt, 'device_name_cache', {}), \
             patch.object(mqtt, 'device_metadata_locks', {}):
            result = await users.rename_device(self.serial, schemas.DeviceRename(name='DN.MinhQuan'), self.db, self.admin)
            self.assertEqual(mqtt.device_name_cache[self.serial], 'DN.MinhQuan')
            self.assertEqual(mqtt.device_metadata_cache[self.serial]['name'], 'DN.MinhQuan')
        dispatch.assert_not_awaited()
        self.assertEqual(result['name'], 'DN.MinhQuan')
        self.assertEqual(broadcast.await_args.args[0]['data']['name'], 'DN.MinhQuan')

    async def test_blank_provision_name_cannot_be_sent(self):
        with patch.object(crud, 'get_device_by_serial', AsyncMock(return_value=self.device)), \
             patch.object(devices, 'send_command_to_pi', AsyncMock()) as dispatch:
            with self.assertRaises(HTTPException) as error:
                await devices.send_generic_command(self.serial,
                    schemas.Command(command='PROVISION_DEVICE', payload={'name': ' '}), self.db, self.admin)
        self.assertEqual(error.exception.status_code, 400)
        self.db.commit.assert_not_awaited()
        dispatch.assert_not_awaited()

    async def test_discovered_pi_registers_before_provisioning(self):
        self.serial = 'LP_001122334455'
        self.device.serial = self.serial
        calls = []

        async def register(serial, db, user):
            calls.append('register')
            self.assertEqual(serial, self.serial)

        async def rename(serial, request, db, user):
            calls.append('rename')
            self.assertEqual(request.name, 'Ubuntu station')
            return {'status': 'success', 'name': request.name}

        with patch.object(crud, 'get_device_by_serial', AsyncMock(side_effect=[None, self.device])), \
             patch.object(devices, 'auto_deploy_device_token', AsyncMock(side_effect=register)), \
             patch.object(users, 'rename_device', AsyncMock(side_effect=rename)), \
             patch.object(mqtt, 'device_telemetry_cache', {self.serial: {'status': 'unprovisioned'}}):
            result = await devices.send_generic_command(self.serial,
                schemas.Command(command='PROVISION_DEVICE', payload={'name': ' Ubuntu station '}), self.db, self.admin)
        self.assertEqual(calls, ['register', 'rename'])
        self.assertEqual(result['name'], 'Ubuntu station')

    async def test_arbitrary_command_does_not_register_unknown_device(self):
        with patch.object(crud, 'get_device_by_serial', AsyncMock(return_value=None)), \
             patch.object(devices, 'auto_deploy_device_token', AsyncMock()) as register, \
             patch.object(mqtt, 'device_telemetry_cache', {self.serial: {'status': 'online'}}):
            with self.assertRaises(HTTPException) as error:
                await devices.send_generic_command(self.serial,
                    schemas.Command(command='REBOOT', payload={}), self.db, self.admin)
        self.assertEqual(error.exception.status_code, 404)
        register.assert_not_awaited()

    async def test_provision_cannot_bypass_license_permission(self):
        with patch.object(crud, 'get_device_by_serial', AsyncMock(return_value=None)), \
             patch.object(devices, 'auto_deploy_device_token', AsyncMock()) as register, \
             patch.object(devices.auth, 'has_permission', side_effect=lambda user, permission: permission != devices.auth.Permission.MANAGE_LICENSE), \
             patch.object(mqtt, 'device_telemetry_cache', {self.serial: {'status': 'online'}}):
            with self.assertRaises(HTTPException) as error:
                await devices.send_generic_command(self.serial,
                    schemas.Command(command='PROVISION_DEVICE', payload={'name': 'Station'}), self.db, self.admin)
        self.assertEqual(error.exception.status_code, 403)
        register.assert_not_awaited()

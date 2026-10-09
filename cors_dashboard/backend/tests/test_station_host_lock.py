import ast
import asyncio
import logging
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, Mock, patch

from fastapi import Depends, FastAPI, HTTPException
from app import auth, crud, models, schemas, mqtt
from app.routers import api_keys
from app.station_host_lock import check_host_lock, host_lock_snapshot, refresh_host_lock


class StationHostLockTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = {'serverhost1': 'host-one.test', 'port1': 2101, 'server1_enabled': True,
                       'serverhost2': 'host-two.test', 'port2': 2102, 'server2_enabled': True,
                       'username1': 'source', 'username2': 'source', 'mountpoint1': 'ONE', 'mountpoint2': 'TWO',
                       'server1_stream_active': True, 'server2_stream_active': True,
                       'server1_stream_on_demand': False, 'server2_stream_on_demand': False}
        self.device = models.Device(serial='TEST', name='Test', user_id=2, status='online', version='2.2.6',
                                    service_config=dict(self.config), base_config={}, is_synced=True, is_locked=False)
        self.admin = SimpleNamespace(id=1, username='admin', role='admin')
        self.user = SimpleNamespace(id=2, username='user', role='coordinator')
        self.db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock(), refresh=AsyncMock())
        self.command = AsyncMock(return_value={'channel': 'mqtt'})
        self.crud = SimpleNamespace(get_device_for_update=AsyncMock(return_value=self.device),
                                    log_system_event=AsyncMock(), get_system_settings=AsyncMock())
        source = Path(__file__).parents[1] / 'app' / 'main.py'
        names = {'lock_device', 'unlock_device', 'configure_service_endpoint', 'configure_integration_station', 'apply_device_config_template'}
        definitions = [node for node in ast.parse(source.read_text()).body if isinstance(node, ast.AsyncFunctionDef) and node.name in names]
        self.assertEqual(len(definitions), len(names))
        self.ns = dict(app=FastAPI(), AsyncSession=object, get_db=lambda: None, get_auth_db=lambda: None,
                       Depends=Depends, HTTPException=HTTPException, Optional=Optional, auth=auth, models=models,
                       schemas=schemas, crud=self.crud, api_keys=api_keys, logging=logging,
                       check_host_lock=check_host_lock, host_lock_snapshot=host_lock_snapshot, refresh_host_lock=refresh_host_lock,
                       send_command_to_pi=self.command, _device_service_config=lambda d: d.service_config or {},
                       _preserve_relay_config_metadata=lambda serial, old, new: new,
                       mqtt_handler=SimpleNamespace(invalidate_device_cache=Mock(), device_sync_cache={}),
                       ui_manager=SimpleNamespace(broadcast=AsyncMock()), _ops_visible_devices=AsyncMock(return_value=[self.device]))
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), 'exec'), self.ns)

    async def lock_s2(self):
        return await self.ns['lock_device']('TEST', self.db, self.admin, schemas.StationHostLockRequest(allowed_servers=[1]))

    async def test_block_host_two_preserves_host_one_without_whole_device_lock(self):
        result = await self.lock_s2()
        self.assertTrue(result['delivered'])
        self.assertTrue(self.device.service_config['server1_enabled'])
        self.assertTrue(self.device.service_config['server1_stream_active'])
        self.assertFalse(self.device.service_config['server2_enabled'])
        self.assertFalse(self.device.service_config['server2_stream_active'])
        self.assertFalse(self.device.is_locked)
        commands = [call.args[1] for call in self.command.await_args_list]
        self.assertEqual([item['command'] for item in commands], ['DEPLOY_SERVICE_CONFIG'])
        self.assertEqual(commands[0]['payload']['serverhost1'], 'host-one.test')

    async def test_user_and_integration_cannot_bypass_host_policy(self):
        await self.lock_s2()
        for changes in ({'server2_enabled': True}, {'serverhost2': 'other.test'}, {'port2': 9999},
                        {'mountpoint2': 'OTHER'}, {'server1_enabled': False}, {'serverhost1': 'other.test'},
                        {'server1_stream_active': False}, {'server2_stream_active': True}):
            with self.subTest(changes=changes), self.assertRaises(HTTPException) as error:
                await self.ns['configure_service_endpoint']('TEST', schemas.ServiceConfig(**changes), self.db, self.user)
            self.assertEqual(error.exception.status_code, 403)
            with self.assertRaises(HTTPException):
                await self.ns['configure_integration_station']('TEST', schemas.ServiceConfig(**changes), self.db,
                    SimpleNamespace(created_by=1, id=1, edit_config=True, all_stations=True))
        with self.assertRaises(HTTPException):
            check_host_lock(self.device, {'target_host': 'other.test'})
        with self.assertRaises(HTTPException):
            check_host_lock(self.device, {'host2': 'other.test'})
        # Unblocked server credentials/mountpoint and GNSS/general settings remain editable.
        await self.ns['configure_service_endpoint']('TEST', schemas.ServiceConfig(mountpoint1='RENAMED'), self.db, self.user)
        self.assertEqual(self.device.service_config['mountpoint1'], 'RENAMED')
        self.assertFalse(self.device.service_config['server2_enabled'])

    async def test_template_cannot_bypass_lock(self):
        await self.lock_s2()
        self.crud.get_system_settings.return_value = {'device_config_templates': [{
            'id': 'bad', 'created_by': 'user', 'service_config': {'server2_enabled': True}, 'base_config': {'lat': 1}}]}
        request = schemas.DeviceApplyTemplateRequest(template_id='bad', serials=['TEST'], apply_service_config=True, apply_base_config=True)
        result = await self.ns['apply_device_config_template'](request, self.db, self.db, self.user)
        self.assertEqual(result['results'][0]['status'], 'skipped')
        self.assertEqual(self.device.base_config, {})
        self.assertFalse(self.device.service_config['server2_enabled'])

    async def test_admin_can_change_policy_and_unlock_does_not_enable_blocked_host(self):
        await self.lock_s2()
        await self.ns['configure_service_endpoint']('TEST', schemas.ServiceConfig(serverhost1='new.test'), self.db, self.admin)
        self.assertEqual(self.device.server_config_lock['serverhost1'], 'new.test')
        await self.ns['unlock_device']('TEST', self.db, self.admin)
        self.assertIsNone(self.device.server_config_lock)
        self.assertFalse(self.device.service_config['server2_enabled'])
        check_host_lock(self.device, {'server2_enabled': True})

    async def test_offline_policy_persists_and_nonadmin_cannot_lock(self):
        self.command.side_effect = HTTPException(503, 'offline')
        result = await self.lock_s2()
        self.assertFalse(result['delivered'])
        self.assertFalse(self.device.server_config_lock['server2_enabled'])
        self.db.commit.assert_awaited()
        for function in ('lock_device', 'unlock_device'):
            with self.assertRaises(HTTPException) as error:
                await self.ns[function]('TEST', self.db, self.user)
            self.assertEqual(error.exception.status_code, 403)

    async def test_reconnect_retries_policy_and_releases_old_whole_device_lock(self):
        await self.lock_s2()
        self.device.is_locked = True
        self.device.status = 'locked'
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [self.device]))), commit=AsyncMock())
        @asynccontextmanager
        async def session_scope():
            yield db
        sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
        command = AsyncMock()
        with patch.object(mqtt.asyncio, 'sleep', sleep), patch.object(mqtt, 'IS_SHUTTING_DOWN', False), \
             patch.object(mqtt, 'device_status_cache', {'TEST': 'locked'}), patch.object(mqtt, 'device_session_scope', session_scope), \
             patch.object(mqtt, '_compare_configs', side_effect=[(True, []), (False, ['changed'])]), \
             patch.object(mqtt, '_version_matches', return_value=True), patch.object(crud, 'get_device_for_update', AsyncMock(return_value=self.device)), patch('app.command_dispatcher.send_command_to_pi', command):
            with self.assertRaises(asyncio.CancelledError):
                await mqtt.batch_sync_checker()
        self.assertEqual([call.args[1]['command'] for call in command.await_args_list], ['UNLOCK_DEVICE', 'DEPLOY_SERVICE_CONFIG'])
        self.assertFalse(command.await_args_list[-1].args[1]['payload']['server2_enabled'])

    async def test_telemetry_upsert_cannot_replace_policy_or_locked_config(self):
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
        await crud.batch_update_devices(db, [{"serial": "TEST", "timestamp": 100,
            "service_config": {"server2_enabled": True}, "server_config_lock": {"server2_enabled": True}}])
        statement = db.execute.call_args.args[0]
        sql = str(statement)
        self.assertIn("devices.server_config_lock IS NOT NULL", sql)
        self.assertIn("THEN devices.service_config", sql)
        self.assertNotIn("server_config_lock =", sql)

    def test_agent_blocked_host_cannot_publish_even_in_fail_open(self):
        import threading
        source = Path(__file__).parents[3] / 'geodetic' / 'agent_universal.py'
        names = {'_to_bool', 'resolve_effective_stream_switch', '_get_server_stream_switches'}
        definitions = [node for node in ast.parse(source.read_text()).body if isinstance(node, ast.FunctionDef) and node.name in names]
        event = threading.Event()
        namespace = {'control_plane_fail_open_event': event}
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), 'exec'), namespace)
        config = {**self.config, 'server2_enabled': False}
        for fail_open in (False, True):
            if fail_open:
                event.set()
            self.assertTrue(namespace['_get_server_stream_switches'](config, 1, apply_fail_open=True)['can_push'])
            self.assertFalse(namespace['_get_server_stream_switches'](config, 2, apply_fail_open=True)['can_push'])

    async def test_writes_lock_current_policy_row(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: self.device))))
        self.assertIs(await crud.get_device_for_update(db, 'TEST'), self.device)
        self.assertIn('FOR UPDATE', str(db.execute.call_args.args[0]))
        for ids in ([3], [1, 1]):
            with self.assertRaises(ValueError):
                schemas.StationHostLockRequest(allowed_servers=ids)


if __name__ == '__main__':
    unittest.main()

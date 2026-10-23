"""Prefix-7 bases reuse Rover credentials, with no provisioning or license."""
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from app import models, crud, mqtt
from app import esp_base_manager as manager
from app.mobile_base import require_editable_base, is_readonly_base
from app.database import get_db


def rows(items):
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


class Prefix7DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def sync(self, rovers, sources=None, devices=None, casters=None):
        source = models.CasterSource(host='caster.test', port=2201, mountpoint='70001',
                                     last_seen_at=int(time.time()), source_data={'datacount': 100})
        auth_db, device_db = AsyncMock(), AsyncMock()
        device_db.add = MagicMock()
        auth_db.execute.side_effect = [rows(sources if sources is not None else [source]), rows(rovers)]
        device_db.execute.side_effect = [MagicMock(), rows(devices or [])]
        with patch.object(manager, 'auth_session_scope') as auth_scope, \
             patch.object(manager, 'device_session_scope') as device_scope, \
             patch.object(manager, '_get_configured_casters', AsyncMock(return_value=casters or [('caster.test', 2201, 'admin', 'secret')])), \
             patch.object(manager, 'notify_decoder_worker', AsyncMock()) as notify, \
             patch.object(manager, 'CasterClient') as caster, \
             patch.object(mqtt, 'cache_device_metadata', AsyncMock()) as cache:
            auth_scope.return_value.__aenter__.return_value = auth_db
            device_scope.return_value.__aenter__.return_value = device_db
            await manager.sync_prefix7_bases()
        caster.assert_not_called()
        auth_db.add.assert_not_called()
        auth_db.delete.assert_not_awaited()
        return notify, device_db, cache

    def rover(self, **kwargs):
        return models.RoverAccount(username='70001', password='saved-password', is_active=True,
                                   mountpoints='70001', **kwargs)

    async def test_discovery_uses_real_credentials_and_no_license(self):
        notify, db, cache = await self.sync([self.rover()])
        device = db.add.call_args.args[0]
        self.assertEqual(device.serial, 'MONITOR_70001')
        self.assertTrue(device.system_info['auth_license_bypass'])
        self.assertIsNone(device.device_token)
        self.assertTrue(device.is_provisioned)
        self.assertTrue(is_readonly_base(device))
        notify.assert_awaited_once_with('start', device.serial, '70001', '70001', 'saved-password',
                                       lat=0., lon=0., alt=0., host='caster.test', port=2201)
        cache.assert_awaited_once_with(device)

    async def test_invalid_credentials_stop_without_default_password(self):
        invalid = self.rover()
        invalid.mountpoints = 'OTHER'
        inactive = self.rover()
        inactive.is_active = False
        for rover in (None, self.rover(expires_at=1), inactive, invalid):
            notify, db, _ = await self.sync([rover] if rover else [])
            self.assertEqual(notify.await_args.args[0], 'stop')
            self.assertEqual(notify.await_args.args[4], '')
            self.assertEqual(db.add.call_args.args[0].system_info['decoder_status'], 'missing_rover_credentials')

    async def test_reuses_device_and_deduplicates_sources(self):
        device = models.Device(serial='esp_112233445566', status='awaiting_license',
                               device_token='old-token', service_config={'mountpoint1': '70001'})
        source = models.CasterSource(host='caster.test', port=2201, mountpoint='70001', last_seen_at=int(time.time()), source_data={'datacount': 20})
        notify, db, _ = await self.sync([self.rover()], sources=[source, source], devices=[device])
        db.add.assert_not_called()
        self.assertEqual(notify.await_args.args[1], device.serial)
        self.assertEqual(notify.await_count, 1)
        self.assertTrue(device.system_info['auth_license_bypass'])
        self.assertEqual(device.status, 'offline')
        self.assertIsNone(device.device_token)

    async def test_idle_missing_counter_or_stale_snapshot_never_starts_decoder(self):
        now = int(time.time())
        for data, observed in (({'datacount': 0}, now), ({}, now),
                               ({'datacount': 'invalid'}, now), ({'datacount': 100}, now - 3600)):
            source = models.CasterSource(host='caster.test', port=2201, mountpoint='70001',
                                         source_data=data, last_seen_at=observed)
            device = models.Device(serial='MONITOR_70001', status='online', bps=500,
                                   ntrip_connected=True, service_config={'mountpoint1': '70001'})
            notify, db, _ = await self.sync([self.rover()], sources=[source], devices=[device])
            self.assertEqual(notify.await_args.args[0], 'stop')
            self.assertEqual(device.status, 'offline')
            self.assertEqual(device.bps, 0)
            self.assertFalse(device.ntrip_connected)
            self.assertEqual(device.system_info['decoder_status'], 'source_offline')
            db.add.assert_not_called()

    async def test_duplicate_mountpoint_prefers_caster_with_data(self):
        now = int(time.time())
        idle = models.CasterSource(host='a.test', port=2201, mountpoint='70001',
                                   last_seen_at=now, source_data={'datacount': 0})
        active = models.CasterSource(host='z.test', port=2201, mountpoint='70001',
                                     last_seen_at=now, source_data={'datacount': 30})
        notify, db, _ = await self.sync([self.rover()], sources=[idle, active],
                                      casters=[('a.test', 2201, '', ''), ('z.test', 2201, '', '')])
        self.assertEqual(notify.await_count, 1)
        self.assertEqual(notify.await_args.args[0], 'start')
        self.assertEqual(notify.await_args.kwargs['host'], 'z.test')
        self.assertEqual(db.add.call_count, 1)

    async def test_counter_changes_start_stop_and_resume_same_mountpoint(self):
        now = int(time.time())
        source = models.CasterSource(host='caster.test', port=2201, mountpoint='70001',
                                     source_data={'datacount': 0}, last_seen_at=now)
        device = models.Device(serial='MONITOR_70001', status='offline', service_config={'mountpoint1': '70001'})
        for count, action in ((0, 'stop'), (20, 'start'), (0, 'stop'), (50, 'start')):
            source.source_data = {'datacount': count}
            notify, _, _ = await self.sync([self.rover()], sources=[source], devices=[device])
            self.assertEqual(notify.await_args.args[0], action)
        self.assertEqual(device.serial, 'MONITOR_70001')

    async def test_removed_source_stops_decoder(self):
        device = models.Device(serial='MONITOR_70001', service_config={'mountpoint1': '70001'})
        notify, _, _ = await self.sync([self.rover()], sources=[], devices=[device])
        self.assertEqual(notify.await_args.args[:3], ('stop', device.serial, '70001'))

    async def test_prefix7_excluded_from_legacy_provisioning(self):
        dev = models.Device(serial='esp_112233445566', status='online', service_config={'mountpoint1': '70001'})
        session = AsyncMock()
        session.execute.return_value = rows([dev])
        with patch.object(manager, 'device_session_scope') as scope, \
             patch.object(manager, 'auto_provision_esp_rover_account', AsyncMock()) as provision, \
             patch.object(manager, 'auto_deprovision_esp_rover_account', AsyncMock()) as deprovision:
            scope.return_value.__aenter__.return_value = session
            await manager.sync_esp_base_rovers()
        provision.assert_not_awaited()
        deprovision.assert_not_awaited()

    async def test_trusted_exemption_accepts_rtcm_status_without_token(self):
        serial = 'MONITOR_70001'
        payload = {'serial': serial, 'status': 'online', 'timestamp': time.time(),
                   'ntrip_stats': {'server1_bps': 512}, 'rtcm_stats': {'1005': {'count': 1}}}
        async def accept(data, **kwargs):
            return SimpleNamespace(accepted=True, payload=data)
        broadcast = AsyncMock()
        with patch.multiple(mqtt, IS_SHUTTING_DOWN=False, status_broadcast_handler=broadcast,
                            device_telemetry_cache={}, device_sync_cache={}, device_name_cache={},
                            device_write_cache={serial: time.time()}, device_status_cache={serial: 'online'}), \
             patch.object(mqtt, 'get_device_metadata', AsyncMock(return_value={'exists': True, 'license_bypass': True})), \
             patch.object(mqtt.status_reconciler, 'accept', AsyncMock(side_effect=accept)):
            await mqtt.handle_message_async(f'pi/devices/{serial}/status', json.dumps(payload).encode())
        data = broadcast.await_args.args[0]['data']
        self.assertEqual(data['status'], 'online')
        self.assertEqual(data['bps'], 512)
        self.assertEqual(data['rtcm_stats'], payload['rtcm_stats'])
        self.assertFalse(data['system_info']['auth_status']['needs_token_deploy'])


class ReadonlyApiTests(unittest.TestCase):
    def test_guard_blocks_bulk_and_license_before_write(self):
        app = FastAPI()
        called = MagicMock()
        async def mutate():
            called()
            return {'ok': True}
        for path in ('/api/devices/{serial}/command', '/api/devices/bulk-action', '/api/license/pi'):
            app.add_api_route(path, mutate, methods=['POST'], dependencies=[Depends(require_editable_base)])
        app.dependency_overrides[get_db] = lambda: MagicMock()
        with patch.object(crud, 'get_device_by_serial', AsyncMock(return_value=None)):
            with TestClient(app) as client:
                for path, body in (('/api/devices/MONITOR_70001/command', {}),
                                   ('/api/devices/bulk-action', {'serials': ['MONITOR_70001']}),
                                   ('/api/license/pi', {'serial': 'MONITOR_70001'})):
                    self.assertEqual(client.post(path, json=body).status_code, 403)
                called.assert_not_called()
                self.assertEqual(client.post('/api/devices/esp_112233445566/command', json={}).status_code, 200)
        self.assertTrue(crud.is_mobile_base('MONITOR_70001'))
        self.assertFalse(crud.is_mobile_base('MONITOR_HANOI'))

    def test_real_routes_have_guard(self):
        from app.main import app
        def flatten(router):
            for route in router.routes:
                original = getattr(route, 'original_router', None)
                if original:
                    yield from flatten(original)
                else:
                    yield route
        wanted = {'/api/devices/{serial}/command', '/api/devices/{serial}/configure-chip',
                  '/api/devices/{serial}/configure-service', '/api/devices/{serial}/auto-deploy-token',
                  '/api/devices/{serial}/license-bypass', '/api/devices/{serial}/name',
                  '/api/license/pi', '/api/devices/bulk-action', '/api/devices/apply-template',
                  '/api/devices/{serial}/serial', '/api/integrations/stations/{serial}/configure-chip',
                  '/api/integrations/stations/{serial}/configure-service'}
        seen = set()
        for route in flatten(app.router):
            if getattr(route, 'path', '') in wanted:
                self.assertIn(require_editable_base, [d.call for d in route.dependant.dependencies], route.path)
                seen.add(route.path)
        self.assertEqual(seen, wanted)

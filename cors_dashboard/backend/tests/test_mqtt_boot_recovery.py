"""A rebooted ESP heartbeat must reach DB persistence and the dashboard."""
from contextlib import asynccontextmanager
import json
import base64
import asyncio
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app import crud, mqtt, command_builder
from app.status_reconciler import StatusReconciler


class MqttBootRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_accepted_websocket_mon_ver_is_available_to_configuration(self):
        serial = 'esp_204340DD2568'
        extensions = ['FWVER=HPG 1.13', 'PROTVER=27.12', 'MOD=ZED-F9P']
        raw = b'CORE'.ljust(30, b'\0') + b'00190000'.ljust(10, b'\0')
        raw += b''.join(item.encode().ljust(30, b'\0') for item in extensions)
        status = {'serial': serial, 'status': 'online', 'server_received_at': time.time(),
                  'gnss_receiver': {'source': 'ubx_mon_ver', 'age_seconds': 0,
                                    'payload_b64': base64.b64encode(raw).decode()}}
        with patch.object(mqtt, 'device_telemetry_cache', {}):
            mqtt.cache_websocket_status(serial, status, {'gnss_valset_supported': True})
            live = mqtt.device_telemetry_cache[serial]
            self.assertEqual(command_builder.select_ublox_profile(live, live['system_info'])['firmware'], 'HPG 1.13')
            mqtt.cache_websocket_status(serial, {'serial': serial, 'status': 'online', 'server_received_at': time.time()})
            self.assertNotIn('gnss_receiver', mqtt.device_telemetry_cache[serial])
            mqtt.cache_websocket_status(serial, {**status, 'gnss_receiver': None})
            with self.assertRaises(ValueError):
                command_builder.select_ublox_profile(mqtt.device_telemetry_cache[serial], {})

    async def test_configuration_waits_for_first_online_receiver_report(self):
        serial = 'esp_204340DD2568'
        cache = {}
        extensions = ['FWVER=HPG 1.13', 'PROTVER=27.12', 'MOD=ZED-F9P']
        raw = b'CORE'.ljust(30, b'\0') + b'00190000'.ljust(10, b'\0')
        raw += b''.join(item.encode().ljust(30, b'\0') for item in extensions)
        async def publish():
            await asyncio.sleep(0.01)
            cache[serial] = {'status': 'online', 'server_received_at': time.time(),
                'gnss_receiver': {'source': 'ubx_mon_ver', 'age_seconds': 0,
                                  'payload_b64': base64.b64encode(raw).decode()}}
        task = asyncio.create_task(publish())
        profile = await command_builder.wait_for_ublox_profile(cache, serial, {}, timeout=2)
        await task
        self.assertEqual(profile['firmware'], 'HPG 1.13')
        with self.assertRaises(ValueError):
            await command_builder.wait_for_ublox_profile({serial: {'status': 'offline'}}, serial, {}, timeout=2)
        with self.assertRaises(ValueError):
            await command_builder.wait_for_ublox_profile({}, serial, {}, timeout=0.01)

    async def test_online_with_reset_uptime_is_broadcast_and_persisted(self):
        serial = 'esp_204340DD2568'
        reconciler = StatusReconciler()
        await reconciler.accept(dict(serial=serial, status='offline',
                                     agent_timestamp=1036, status_boot_id='old',
                                     status_sequence=100), source='mqtt')
        db = SimpleNamespace(commit=AsyncMock())

        @asynccontextmanager
        async def session():
            yield db

        broadcast, writer = AsyncMock(), AsyncMock(return_value=None)
        metadata = dict(exists=True, license_bypass=True, status='offline',
                        offline_since=1793000000, name='esp_nam_seo')
        with patch.multiple(mqtt, IS_SHUTTING_DOWN=False,
                            status_reconciler=reconciler,
                            status_broadcast_handler=broadcast,
                            device_telemetry_cache={}, device_sync_cache={},
                            device_write_cache={serial: time.time()},
                            device_status_cache={serial: 'offline'}, device_name_cache={}), \
             patch.object(mqtt, 'get_device_metadata', AsyncMock(return_value=metadata)), \
             patch.object(mqtt, 'device_session_scope', session), \
             patch.object(crud, 'update_or_create_device', writer):
            payload = dict(serial=serial, status='online', timestamp=430,
                           agent_timestamp=430, status_boot_id='new', status_sequence=66)
            await mqtt.handle_message_async(f'pi/devices/{serial}/status',
                                            json.dumps(payload).encode())
        broadcast.assert_awaited_once()
        writer.assert_awaited_once()
        for data in [broadcast.await_args.args[0]['data'], writer.await_args.kwargs['device_data']]:
            self.assertEqual(data['status'], 'online')
            self.assertIsNone(data['offline_since'])
            self.assertGreater(data['timestamp'], 1_000_000_000)
            self.assertIsNone(data['agent_timestamp'])

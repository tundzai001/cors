"""A rebooted ESP heartbeat must reach DB persistence and the dashboard."""
from contextlib import asynccontextmanager
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app import crud, mqtt
from app.status_reconciler import StatusReconciler


class MqttBootRecoveryTests(unittest.IsolatedAsyncioTestCase):
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

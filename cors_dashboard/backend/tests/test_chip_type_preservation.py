"""Persist late GNSS detection before an offline event reuses DB metadata."""
from contextlib import asynccontextmanager
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app import crud, mqtt


class ChipTypePreservationTests(unittest.IsolatedAsyncioTestCase):
    async def receive(self, chip):
        serial = 'esp_9896E5124B00'
        metadata = {'exists': True, 'license_bypass': True, 'status': 'online',
                    'detected_chip_type': 'Ublox'}
        db = SimpleNamespace(commit=AsyncMock())

        @asynccontextmanager
        async def session():
            yield db

        async def accept(data, **kwargs):
            return SimpleNamespace(accepted=True, payload=data)

        async def write(session, device_data):
            if device_data.get('detected_chip_type'):
                metadata['detected_chip_type'] = device_data['detected_chip_type']
            return None

        broadcast = AsyncMock()
        writer = AsyncMock(side_effect=write)
        with patch.multiple(mqtt, IS_SHUTTING_DOWN=False,
                            status_broadcast_handler=broadcast,
                            device_telemetry_cache={}, device_sync_cache={},
                            device_write_cache={serial: time.time()},
                            device_status_cache={serial: 'online'},
                            device_name_cache={}), \
             patch.object(mqtt, 'get_device_metadata', AsyncMock(return_value=metadata)), \
             patch.object(mqtt, 'device_session_scope', session), \
             patch.object(mqtt.status_reconciler, 'accept', AsyncMock(side_effect=accept)), \
             patch.object(crud, 'update_or_create_device', writer):
            payload = {'serial': serial, 'status': 'online', 'detected_chip_type': chip}
            await mqtt.handle_message_async(f'pi/devices/{serial}/status', json.dumps(payload).encode())
            online_writes = writer.await_count
            # LWT has no chip field and must reuse the newly persisted detection.
            await mqtt.handle_message_async(f'pi/devices/{serial}/status', json.dumps({
                'serial': serial, 'status': 'offline', 'status_event': 'lwt',
            }).encode())
        return online_writes, broadcast.await_args_list[-1].args[0]['data']

    async def test_changed_chip_is_saved_inside_heartbeat_interval_and_survives_offline(self):
        writes, offline = await self.receive('Unicorecomm')
        self.assertEqual(writes, 1)
        self.assertEqual(offline['detected_chip_type'], 'Unicorecomm')

    async def test_unknown_or_unchanged_chip_does_not_force_a_write(self):
        for chip in ('UNKNOWN', None, 'Ublox'):
            with self.subTest(chip=chip):
                writes, offline = await self.receive(chip)
                self.assertEqual(writes, 0)
                self.assertEqual(offline['detected_chip_type'], 'Ublox')

"""Blocked PI identities never reach telemetry, registration or station lists."""
import time
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Response
from app import crud, mqtt, models, map_routes, station_runtime
from app.routers.devices import _device_response_list


class BlockedDeviceSerialTests(unittest.IsolatedAsyncioTestCase):
    async def test_retained_lp_and_esp_snapshots_cannot_resurrect_missing_devices(self):
        with patch.object(mqtt, 'get_device_metadata', AsyncMock(return_value={'exists': False})), \
             patch.object(mqtt, 'status_broadcast_handler', AsyncMock()) as broadcast, \
             patch.object(crud, 'update_or_create_device', AsyncMock()) as write:
            for serial in ('LP_9896E5124B00', 'esp_9896E5124B00'):
                payload = json.dumps({'serial': serial, 'status': 'online', 'timestamp': time.time()}).encode()
                await mqtt.handle_message_async(f'pi/devices/{serial}/status', payload, is_retained=True)
            broadcast.assert_not_awaited()
            write.assert_not_awaited()

    async def test_mqtt_drops_status_and_raw_data_before_metadata_or_broadcast(self):
        with patch.object(mqtt, 'get_device_metadata', AsyncMock()) as metadata, \
             patch.object(mqtt.manager, 'broadcast', AsyncMock()) as broadcast, \
             patch.object(mqtt, 'status_broadcast_handler', AsyncMock()) as status:
            for serial in ('PI_0bd46c72e7be', 'PI_8893150dff03', 'pi_b5ca1be152f8'):
                for topic in ('status', 'raw_data'):
                    await mqtt.handle_message_async(f'pi/devices/{serial}/{topic}', b'{}')
            metadata.assert_not_awaited()
            broadcast.assert_not_awaited()
            status.assert_not_awaited()

    async def test_blocked_existing_device_cannot_open_agent_websocket(self):
        socket = SimpleNamespace(close=AsyncMock())
        with patch.object(mqtt, 'get_device_metadata', AsyncMock()) as metadata:
            await station_runtime.pi_websocket_endpoint(socket, 'PI_0bd46c72e7be', 'token')
        socket.close.assert_awaited_once_with(code=1008, reason='Blocked device serial')
        metadata.assert_not_awaited()

    async def test_blocked_device_never_reaches_database_write(self):
        db = AsyncMock()
        result = await crud.update_or_create_device(db, {'serial': 'PI_0bd46c72e7be'})
        self.assertIsNone(result)
        db.execute.assert_not_awaited()

    async def test_existing_rows_and_cached_map_exclude_pi_but_keep_lp_and_esp(self):
        devices = [models.Device(serial=s) for s in ('PI_0bd46c72e7be', 'LP_9896E5124B00', 'esp_9896E5124B00')]
        visible = _device_response_list(devices, SimpleNamespace(role='admin'))
        self.assertEqual([d.serial for d in visible], ['LP_9896E5124B00', 'esp_9896E5124B00'])
        with patch.object(map_routes, 'public_map_cache', [{'serial': d.serial} for d in devices]), \
             patch.object(map_routes, 'public_map_cache_updated_monotonic', time.monotonic()):
            db = MagicMock()
            items = await map_routes._get_map_station_items(Response(), db)
        self.assertEqual([d['serial'] for d in items], ['LP_9896E5124B00', 'esp_9896E5124B00'])
        db.execute.assert_not_called()

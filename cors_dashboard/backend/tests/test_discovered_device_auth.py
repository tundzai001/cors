"""MQTT discovery must not treat an agent's local token as DB registration."""
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app import crud, license_manager, mqtt


class DiscoveredDeviceAuthTests(unittest.IsolatedAsyncioTestCase):
    async def receive(self, metadata, fingerprint, expected_status):
        serial = 'PI_0bd46c72e7be'
        payload = {
            'serial': serial, 'status': 'online', 'timestamp': time.time(),
            'agent_token_fingerprint': fingerprint,
            'system_info': {'auth_license_bypass': True, 'auth_status': {
                'token_synced': True, 'server_token_present': True,
            }},
        }

        async def accept(data, **kwargs):
            return SimpleNamespace(accepted=True, payload=data)

        broadcast = AsyncMock()
        with patch.multiple(mqtt, IS_SHUTTING_DOWN=False,
                            status_broadcast_handler=broadcast,
                            device_telemetry_cache={}, device_sync_cache={},
                            device_write_cache={serial: time.time()},
                            device_status_cache={serial: expected_status},
                            device_name_cache={}), \
             patch.object(mqtt, 'get_device_metadata', AsyncMock(return_value=metadata)), \
             patch.object(mqtt.status_reconciler, 'accept', AsyncMock(side_effect=accept)), \
             patch.object(crud, 'update_or_create_device', AsyncMock()) as write:
            await mqtt.handle_message_async(f'pi/devices/{serial}/status', json.dumps(payload).encode())
        broadcast.assert_awaited_once()
        write.assert_not_awaited()
        return broadcast.await_args.args[0]['data']

    async def test_unknown_agent_with_local_token_requires_server_registration(self):
        result = await self.receive({'exists': False}, 'local-token-fingerprint', 'awaiting_license')
        self.assertEqual(result['status'], 'awaiting_license')
        auth = result['system_info']['auth_status']
        self.assertFalse(auth['server_token_present'])
        self.assertFalse(auth['token_synced'])
        self.assertTrue(auth['agent_token_present'])
        self.assertTrue(auth['needs_token_deploy'])
        self.assertFalse(result['system_info']['auth_license_bypass'])

    async def test_known_matching_token_stays_online(self):
        token = license_manager.generate_device_token()
        result = await self.receive({'exists': True, 'device_token': token}, mqtt._fingerprint_token(token), 'online')
        self.assertEqual(result['status'], 'online')
        self.assertTrue(result['system_info']['auth_status']['token_synced'])
        self.assertFalse(result['system_info']['auth_status']['needs_token_deploy'])

    async def test_partial_known_heartbeat_does_not_demand_new_token(self):
        token = license_manager.generate_device_token()
        result = await self.receive({'exists': True, 'device_token': token}, None, 'online')
        self.assertEqual(result['status'], 'online')
        self.assertFalse(result['system_info']['auth_status']['needs_token_deploy'])

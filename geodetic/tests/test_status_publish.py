"""Exercise heartbeat publishing without starting hardware or network services."""
import ast
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock


class StatusPublishTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_acknowledged_rejected_and_timed_out(self):
        source = Path(__file__).resolve().parents[1] / 'agent_universal.py'
        tree = ast.parse(source.read_text())
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                        and node.name == 'send_status')
        initialization = asyncio.Event()
        initialization.set()
        scope = dict(
            asyncio=asyncio, json=json, logging=logging, time=time,
            AgentManager=object, mqtt=SimpleNamespace(Client=object, MQTT_ERR_SUCCESS=0),
            initialization_complete=initialization, STATUS_SEQUENCE=0,
            STATUS_BOOT_ID='test-boot', MACHINE_SERIAL='test-device',
            active_websocket_connection=None, get_cached_system_info=lambda: {},
            transport_status={'mqtt_broker': 'test-broker'},
            get_device_lock_status=lambda: {},
        )
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), scope)
        agent = SimpleNamespace(
            get_full_status=lambda: {'status': 'awaiting_license'},
            control_plane_failover=SimpleNamespace(record_probe=Mock()),
        )
        for rc, acknowledged, expected in (
            (0, True, 'MQTT status acknowledged'),
            (4, False, 'rc=4'),
            (0, False, 'not acknowledged within 3s'),
        ):
            with self.subTest(rc=rc, acknowledged=acknowledged):
                info = SimpleNamespace(rc=rc, wait_for_publish=Mock(),
                                       is_published=lambda: acknowledged)
                client = SimpleNamespace(is_connected=lambda: True,
                                         publish=Mock(return_value=info))
                with self.assertLogs(level='INFO') as logs:
                    await scope['send_status'](agent, client)
                self.assertIn(expected, '\n'.join(logs.output))
                args, kwargs = client.publish.call_args
                self.assertEqual(args[0], 'pi/devices/test-device/status')
                self.assertEqual(json.loads(args[1])['status'], 'awaiting_license')
                self.assertEqual(kwargs, {'qos': 1, 'retain': True})
                if rc == 0:
                    info.wait_for_publish.assert_called_once_with(timeout=3.0)
                else:
                    info.wait_for_publish.assert_not_called()


if __name__ == '__main__':
    unittest.main()

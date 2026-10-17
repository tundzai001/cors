"""Mixed-version license and command checks without hardware or app startup."""
import ast
import asyncio
from collections import OrderedDict
import importlib.util
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / 'geodetic/agent_universal.py'


def load_functions(path, names, scope):
    tree = ast.parse(path.read_text())
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    return scope


class ProtocolCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def test_old_and_new_licenses_match_backend_and_agent(self):
        spec = importlib.util.spec_from_file_location('license_manager', ROOT / 'cors_dashboard/backend/app/license_manager.py')
        backend = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backend)
        scope = load_functions(AGENT, {'generate_pi_license_base', 'get_license_code_from_string',
                                      'is_valid_license_key', 'license_is_valid'}, dict(math=math, re=re))
        serial = 'LP_E051D80E3511'
        legacy = backend.get_license_code_from_string(backend.generate_pi_license_base(serial))
        modern = backend.generate_device_token()
        for token in (legacy, modern):
            self.assertTrue(scope['is_valid_license_key'](token, serial))
            self.assertTrue(backend.is_compatible_device_token(token, serial))
            self.assertEqual(backend.get_or_create_device_token(serial, token), token)
            with tempfile.TemporaryDirectory() as directory:
                license_path = Path(directory) / 'license.key'
                license_path.write_text(token)
                scope.update(LICENSE_PATH=license_path, get_machine_serial=lambda: serial)
                self.assertTrue(scope['license_is_valid']())
        for token in ('', 'dt_short', '1234567890', 'invalid'):
            self.assertFalse(scope['is_valid_license_key'](token, serial))
            self.assertFalse(backend.is_compatible_device_token(token, serial))
        self.assertFalse(scope['is_valid_license_key'](legacy, 'XYZ7654321'))
        self.assertTrue(backend.is_device_token(backend.get_or_create_device_token(serial, None)))

    def test_default_websocket_host_and_tls(self):
        tree = ast.parse(AGENT.read_text())
        names = {'BACKEND_HOST', 'BACKEND_WS_HOST', 'BACKEND_WS_SCHEME', 'BACKEND_WS_PORT'}
        nodes = [node for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)]
        scope = {'os': SimpleNamespace(getenv=lambda key, default=None: default), 'DEFAULT_BACKEND_HOST': 'aitogy.click'}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(AGENT), 'exec'), scope)
        self.assertEqual((scope['BACKEND_WS_HOST'], scope['BACKEND_WS_SCHEME'], scope['BACKEND_WS_PORT']),
                         ('cors.aitogy.com', 'wss', 443))
        self.assertEqual(scope['BACKEND_HOST'], 'aitogy.click')

    async def test_dispatch_envelope_fallback_and_freshness(self):
        class HTTPException(Exception):
            def __init__(self, status_code, detail):
                self.status_code = status_code
        mqtt = SimpleNamespace(is_connected=lambda: True)
        handler = SimpleNamespace(publish_message=Mock())
        manager = SimpleNamespace(send_personal_message=AsyncMock(return_value=False))
        scope = load_functions(ROOT / 'cors_dashboard/backend/app/command_dispatcher.py', {'send_command_to_pi'},
                               dict(json=json, logging=logging, secrets=secrets,
                                    time=SimpleNamespace(time=lambda: 1000), mqtt_client=mqtt,
                                    mqtt_handler=handler, pi_manager=manager, HTTPException=HTTPException))
        original = {'command': 'PROVISION_DEVICE', 'payload': {'name': 'New name'}}
        result = await scope['send_command_to_pi']('test-device', original)
        self.assertEqual(result['channel'], 'mqtt')
        envelope = json.loads(handler.publish_message.call_args.args[1])
        self.assertEqual(original, {'command': 'PROVISION_DEVICE', 'payload': {'name': 'New name'}})
        self.assertEqual(envelope['payload'], original['payload'])
        self.assertRegex(envelope['command_id'], r'^[A-Za-z0-9_-]{16,100}$')
        self.assertEqual((envelope['issued_at'], envelope['expires_at']), (1000, 1300))
        # Run the real agent's ingress validation up to payload validation.
        record = Mock()
        agent_scope = load_functions(AGENT, {'process_command', '_process_command'}, dict(
            asyncio=asyncio, command_execution_lock=asyncio.Lock(),
            AgentManager=object, GNSSReader=object, mqtt=SimpleNamespace(Client=object),
            time=SimpleNamespace(time=lambda: 1000), re=re, logging=logging,
            _record_command_result=record, send_status=AsyncMock(),
            seen_command_ids=OrderedDict(),
            _validate_command_payload=lambda *args: (False, 'stop after envelope validation'),
        ))
        await agent_scope['process_command']('mqtt', envelope, object(), None, mqtt)
        self.assertIn(envelope['command_id'], agent_scope['seen_command_ids'])
        record.reset_mock()
        await agent_scope['process_command']('mqtt', envelope, object(), None, mqtt)
        self.assertEqual(record.call_args.args[3], 'replayed command id')
        expired = {**envelope, 'command_id': secrets.token_urlsafe(18), 'expires_at': 999}
        await agent_scope['process_command']('mqtt', expired, object(), None, mqtt)
        self.assertNotIn(expired['command_id'], agent_scope['seen_command_ids'])
        handler.publish_message.side_effect = ConnectionError('publish failed')
        manager.send_personal_message.return_value = True
        result = await scope['send_command_to_pi']('test-device', original)
        self.assertEqual(result['channel'], 'websocket')
        manager.send_personal_message.return_value = False
        with self.assertRaises(HTTPException) as error:
            await scope['send_command_to_pi']('test-device', original)
        self.assertEqual(error.exception.status_code, 503)


if __name__ == '__main__':
    unittest.main()

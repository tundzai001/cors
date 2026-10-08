"""Configured legacy casters can control wake; arbitrary callers cannot."""
import ast
import asyncio
import hmac
import ipaddress
from pathlib import Path
import socket
import time
from types import SimpleNamespace
from typing import Optional
import unittest
from unittest.mock import AsyncMock

APP = Path(__file__).resolve().parents[1] / 'app'


class HTTPException(Exception):
    def __init__(self, status_code, detail): self.status_code, self.detail = status_code, detail


class LegacyCasterControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_configured_actual_peer_is_accepted_without_token(self):
        helper_tree = ast.parse((APP / 'caster_helpers.py').read_text())
        nodes = [n for n in helper_tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'is_configured_caster_peer']
        scope = dict(asyncio=asyncio, ipaddress=ipaddress, socket=socket, time=time,
                     configured_caster_hosts=lambda: ['192.0.2.73'], _caster_peer_cache={})
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'helpers.py', 'exec'), scope)
        self.assertTrue(await scope['is_configured_caster_peer']('192.0.2.73'))
        self.assertFalse(await scope['is_configured_caster_peer']('192.0.2.90'))
        self.assertFalse(await scope['is_configured_caster_peer']('unknown'))
        tree = ast.parse((APP / 'main.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_require_ntrip_control_token')
        scope.update(Request=object, Optional=Optional, Header=lambda *args, **kwargs: None,
                     settings=SimpleNamespace(NTRIP_CONTROL_TOKEN=''), os=SimpleNamespace(getenv=lambda *args: ''),
                     HTTPException=HTTPException, hmac=hmac)
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'main.py', 'exec'), scope)
        check = scope['_require_ntrip_control_token']
        await check(SimpleNamespace(client=SimpleNamespace(host='192.0.2.73')), None)
        with self.assertRaises(HTTPException) as error:
            await check(SimpleNamespace(client=SimpleNamespace(host='192.0.2.90'),
                                        headers={'x-forwarded-for': '192.0.2.73'}), None)
        self.assertEqual(error.exception.status_code, 503)
        scope['settings'].NTRIP_CONTROL_TOKEN = 'configured-token-' + 'x' * 32
        with self.assertRaises(HTTPException) as error:
            await check(SimpleNamespace(client=SimpleNamespace(host='192.0.2.73')), None)
        self.assertEqual(error.exception.status_code, 401)
        await check(SimpleNamespace(client=SimpleNamespace(host='192.0.2.90')), scope['settings'].NTRIP_CONTROL_TOKEN)


if __name__ == '__main__': unittest.main()

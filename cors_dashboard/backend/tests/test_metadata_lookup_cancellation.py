"""A timed-out handshake must not cancel the shared metadata DB query."""
import ast
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import time
from types import SimpleNamespace
import unittest


class MetadataLookupCancellationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gate = asyncio.Event()
        self.queries = 0
        self.cancelled = 0
        self.query_error = None

        async def lookup(session, serial):
            self.queries += 1
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
            if self.query_error:
                raise self.query_error
            return {"exists": True, "device_token": "stored-token", "cached_at": time.monotonic()}

        @asynccontextmanager
        async def session_scope():
            yield object()

        source = Path(__file__).resolve().parents[1] / 'app/mqtt.py'
        tree = ast.parse(source.read_text())
        names = {'get_device_metadata', '_refresh_device_metadata', '_metadata_lookup_done'}
        nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
        self.scope = dict(asyncio=asyncio, time=time, device_metadata_cache={}, device_metadata_locks={},
                          device_metadata_tasks={}, DEVICE_METADATA_CACHE_TTL_SECONDS=60,
                          device_session_scope=session_scope, crud=SimpleNamespace(get_device_by_serial=lookup),
                          _metadata_snapshot=lambda device: device)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), self.scope)
        self.get = self.scope['get_device_metadata']

    async def test_handshake_timeout_keeps_query_alive_and_populates_cache(self):
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(self.get('LP_TEST'), timeout=0.01)
        self.assertEqual(self.cancelled, 0, 'Handshake timeout must not cancel SQL or connection cleanup')
        self.gate.set()
        metadata = await self.get('LP_TEST')
        self.assertTrue(metadata['exists'])
        self.assertEqual(self.queries, 1, 'Retry must share the original lookup')
        self.assertEqual(await self.get('LP_TEST'), metadata)
        self.assertEqual(self.queries, 1, 'Next handshake must use the populated cache')
        await asyncio.sleep(0)
        self.assertEqual(self.scope['device_metadata_tasks'], {})

    async def test_failed_lookup_is_retried_without_caching_auth_success(self):
        self.query_error = RuntimeError('database unavailable')
        self.gate.set()
        with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
            await self.get('LP_TEST')
        self.assertEqual(self.scope['device_metadata_cache'], {})
        self.query_error = None
        self.assertTrue((await self.get('LP_TEST'))['exists'])
        self.assertEqual(self.queries, 2)

    async def test_expired_token_is_not_used_when_database_lookup_fails(self):
        self.scope['device_metadata_cache']['LP_TEST'] = {
            'exists': True, 'device_token': 'revoked-token', 'cached_at': time.monotonic() - 120,
        }
        self.query_error = RuntimeError('database unavailable')
        self.gate.set()
        with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
            await self.get('LP_TEST')
        self.assertEqual(self.queries, 1, 'Expired auth data must require a fresh lookup')


if __name__ == '__main__':
    unittest.main()

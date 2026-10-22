"""Regression checks for cache polling and stream deactivation without live services."""
import ast
import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import List, Optional
import unittest
from unittest.mock import AsyncMock, patch

from app.rtcm_stream_state import collect_reapable_server_streams

APP = Path(__file__).resolve().parents[1] / 'app'


def load_functions(filename, names, scope):
    tree = ast.parse((APP / filename).read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    if len(nodes) != len(names):
        raise AssertionError(f'Missing functions: {names}')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, 'exec'), scope)
    return scope


def keepalive_scope():
    return load_functions('ntrip_control.py', {'_fresh_rtcm_clienttable', '_record_rtcm_keepalive_miss'}, dict(
        Optional=Optional, RTCM_KEEPALIVE_FETCH_GRACE_SECONDS=30,
        RTCM_KEEPALIVE_NEW_SESSION_GRACE_SECONDS=30,
        _get_env_preferring_dotenv=lambda name, default: default,
        rtcm_keepalive_miss_counts={},
    ))


class KeepaliveSnapshotTests(unittest.TestCase):
    def test_five_polls_of_one_snapshot_are_only_one_miss(self):
        scope = keepalive_scope()
        check = scope['_record_rtcm_keepalive_miss']
        cache = dict(last_update=100, clients=[], error=None)
        self.assertEqual([check('lease', cache, now, 1) for now in range(100, 115, 3)], [1] * 5)
        for number, sampled_at in enumerate(range(130, 221, 30), 2):
            cache['last_update'] = sampled_at
            self.assertEqual(check('lease', cache, sampled_at, 1), number)

    def test_invalid_stale_and_preconnection_snapshots_do_not_count(self):
        scope = keepalive_scope()
        check = scope['_record_rtcm_keepalive_miss']
        for cache in (None, {}, dict(last_update=100, clients=[], error='failed'),
                      dict(last_update=1, clients=[], error=None),
                      dict(last_update=99, clients=[], error=None),
                      dict(last_update=300, clients=[], error=None)):
            scope['rtcm_keepalive_miss_counts']['lease'] = (1, 4)
            self.assertEqual(check('lease', cache, 200, 100), 0)
            self.assertNotIn('lease', scope['rtcm_keepalive_miss_counts'])

    def test_reconnect_has_independent_counter_and_grace(self):
        scope = keepalive_scope()
        scope['rtcm_keepalive_miss_counts']['old-lease'] = (100, 4)
        check = scope['_record_rtcm_keepalive_miss']
        cache = dict(last_update=200, clients=[], error=None)
        self.assertEqual(check('new-lease', cache, 200, 190), 0)
        cache['last_update'] = 230
        self.assertEqual(check('new-lease', cache, 230, 190), 1)


class MonitorSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_clienttable_is_not_a_successful_empty_table(self):
        tree = ast.parse((APP / 'caster_client.py').read_text())
        caster_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CasterClient')
        method = next(n for n in caster_class.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'get_clienttable')
        scope = dict(List=List, Optional=Optional, logger=logging.getLogger('test'), CLIENTTABLE_DEBUG=False)
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'caster_client.py', 'exec'), scope)
        caster = SimpleNamespace(admin_user='admin', admin_password='test',
                                 _send_command=AsyncMock(return_value='CLIENTTABLE 200 OK\r\n'))
        self.assertIsNone(await scope['get_clienttable'](caster))
        caster._send_command.return_value = 'CLIENTTABLE 200 OK\r\nENDCLIENTTABLE\r\n'
        self.assertEqual(await scope['get_clienttable'](caster), [])

    async def test_failed_query_is_not_a_healthy_empty_snapshot(self):
        fake_module = ModuleType('app.caster_client')
        caster = SimpleNamespace(get_users=AsyncMock(return_value=[]), get_clienttable=AsyncMock(return_value=None))
        fake_module.CasterClient = lambda *args, **kwargs: caster
        save = AsyncMock()
        history = AsyncMock()
        import time
        scope = load_functions('caster_monitor.py', {'_sync_single_caster'}, dict(
            asyncio=asyncio, time=time, logger=logging.getLogger('test'),
            global_caster_cache_lock=asyncio.Lock(), _set_caster_cache=save,
            _get_caster_cache=AsyncMock(return_value=None),
            _record_rover_session_snapshots=history, RTCM_KEEPALIVE_DEBUG=False,
            sync_caster_sources=AsyncMock(return_value={"synced_at": 100, "error": None}),
        ))
        with patch.dict(sys.modules, {'app.caster_client': fake_module}):
            await scope['_sync_single_caster']('caster', 2101)
            failed = save.call_args.args[1]
            self.assertTrue(failed['error'])
            history.assert_not_awaited()
            caster.get_clienttable.return_value = []
            await scope['_sync_single_caster']('caster', 2101)
            self.assertIsNone(save.call_args.args[1]['error'])
            history.assert_awaited_once()


class KeepaliveWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_miss_renews_lease_and_found_rover_clears_misses(self):
        class Expression:
            def __gt__(self, other): return self
            def __getattr__(self, name): return lambda *args: self
        class Query:
            changes = None
            def where(self, *args): return self
            def values(self, **changes): self.changes = changes; return self
        columns = {name: Expression() for name in (
            'session_id', 'client_id', 'serial', 'mountpoint', 'caster_host',
            'expires_at', 'created_at', 'last_heartbeat_at')}
        updates = []
        rows = [('lease', 'ntrip-mqtt:quanhp', 'station', 'HP.HaiAn', 'caster', 500, 1)]
        async def execute(query):
            if query.changes is not None: updates.append(query.changes)
            return SimpleNamespace(all=lambda: rows)
        db = SimpleNamespace(execute=execute, commit=AsyncMock())
        @asynccontextmanager
        async def session(): yield db
        cache = dict(last_update=100, clients=[], error=None)
        scope = keepalive_scope()
        scope.update(
            asyncio=SimpleNamespace(sleep=AsyncMock(side_effect=[None, asyncio.CancelledError()])),
            time=SimpleNamespace(time=lambda: 100), rtcm_session_lock=asyncio.Lock(),
            device_session_scope=session,
            models=SimpleNamespace(RTCMStreamSession=SimpleNamespace(**columns)),
            select=lambda *args: Query(), update=lambda *args: Query(),
            settings=SimpleNamespace(CASTER_HOST='caster', CASTER_PORT=2101),
            RTCM_NTRIP_CLIENTTABLE_CHECK_INTERVAL=3, RTCM_KEEPALIVE_MISS_THRESHOLD=5,
            RTCM_KEEPALIVE_DEBUG=False, logger=logging.getLogger('test'),
            _get_caster_cache=AsyncMock(side_effect=lambda key: cache),
            _normalize_mountpoint=lambda value: str(value).strip('/').lower(),
            _normalize_keepalive_client_id=lambda value: str(value or '').removeprefix('ntrip-mqtt:').lower(),
            _deactivate_idle_rtcm_mountpoint=AsyncMock(),
        )
        load_functions('ntrip_control.py', {'rtcm_ntrip_clienttable_keepalive_task'}, scope)
        with self.assertRaises(asyncio.CancelledError):
            await scope['rtcm_ntrip_clienttable_keepalive_task']()
        self.assertEqual(updates, [dict(expires_at=190, last_heartbeat_at=100)])
        self.assertEqual(scope['rtcm_keepalive_miss_counts']['lease'], (100, 1))
        cache['clients'] = [dict(username='quanhp', mountpoint='HP.HaiAn')]
        scope['asyncio'].sleep.side_effect = [None, asyncio.CancelledError()]
        with self.assertRaises(asyncio.CancelledError):
            await scope['rtcm_ntrip_clienttable_keepalive_task']()
        self.assertNotIn('lease', scope['rtcm_keepalive_miss_counts'])
        scope['_deactivate_idle_rtcm_mountpoint'].assert_not_awaited()


class StreamDeactivationTests(unittest.IsolatedAsyncioTestCase):
    async def run_deactivation(self, config, remaining=0):
        @asynccontextmanager
        async def session():
            yield object()
        device = SimpleNamespace(service_config=config)
        send = AsyncMock()
        scope = load_functions('ntrip_control.py', {'_deactivate_idle_rtcm_mountpoint'}, dict(
            rtcm_session_lock=asyncio.Lock(), device_session_scope=session,
            crud=SimpleNamespace(get_device_by_serial=AsyncMock(return_value=device)),
            _extract_reapable_server_streams=lambda device: collect_reapable_server_streams(device.service_config),
            _count_active_mountpoint_sessions=AsyncMock(return_value=remaining),
            _set_station_stream=send, time=SimpleNamespace(time=lambda: 1000),
            logger=logging.getLogger('test'),
        ))
        await scope['_deactivate_idle_rtcm_mountpoint']('station', 'hp.haian')
        return send

    async def test_always_on_with_stale_global_on_demand_never_stops(self):
        send = await self.run_deactivation(dict(
            stream_on_demand=True, stream_active=True, server1_enabled=True,
            server1_stream_on_demand=False, server1_stream_active=True, mountpoint1='HP.HaiAn',
        ))
        send.assert_not_awaited()

    async def test_new_rover_lease_cancels_pending_stop(self):
        send = await self.run_deactivation(dict(
            server1_enabled=True, server1_stream_on_demand=True,
            server1_stream_active=True, mountpoint1='HP.HaiAn',
        ), remaining=1)
        send.assert_not_awaited()

    async def test_stop_is_scoped_to_on_demand_server(self):
        send = await self.run_deactivation(dict(
            server1_enabled=True, server1_stream_on_demand=True,
            server1_stream_active=True, mountpoint1='HP.HaiAn',
            server2_enabled=True, server2_stream_on_demand=False, mountpoint2='OTHER',
        ))
        send.assert_awaited_once_with('station', active=False, mountpoint='hp.haian', server_id=1)


if __name__ == '__main__':
    unittest.main()

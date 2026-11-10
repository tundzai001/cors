"""Pure production status helpers; no DB, broker or physical station required."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

APP = Path(__file__).resolve().parents[1] / 'app'


def load_helpers(filename, names, namespace):
    tree = ast.parse((APP / filename).read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, 'exec'), namespace)


def status(device, payload):
    import dataclasses
    import re
    from typing import Any, Mapping, Optional
    namespace = dict(dataclass=dataclasses.dataclass, Any=Any, Mapping=Mapping, Optional=Optional,
                     models=SimpleNamespace(Device=object), re=re)
    load_helpers('schemas.py', {'service_server_ids'}, namespace)
    load_helpers('rtcm_stream_state.py', {'_as_bool', 'normalize_mountpoint',
                 'has_per_server_stream_state', 'ServerStreamState', 'get_server_stream_state'}, namespace)
    load_helpers('crud.py', {'_ensure_dict', 'normalize_service_config', 'normalize_ntrip_status_payload'}, namespace)
    load_helpers('caster_status.py', {'_extract_per_server_status'}, namespace)
    return namespace['_extract_per_server_status'](device, payload)


class CasterSleepSyncTests(unittest.TestCase):
    def device(self, **changes):
        values = dict(status='online', ntrip_connected=False, ntrip_status={}, service_config={
            'server1_enabled': True, 'mountpoint1': 'BASE',
            'server1_stream_on_demand': True, 'server1_stream_active': False})
        values.update(changes)
        return SimpleNamespace(**values)

    def test_agent_blocks_output_is_sleep_not_offline(self):
        info = dict(enabled=True, on_demand=True, active=False, connected=True, can_push=False)
        self.assertEqual(status(self.device(), {'ntrip_stream_state': {'server1': info}})['server1']['state'], 'sleep')

    def test_sleep_fallback_uses_per_server_config(self):
        self.assertEqual(status(self.device(), {})['server1']['state'], 'sleep')

    def test_wake_pending_is_starting_not_offline_or_sleep(self):
        info = dict(enabled=True, on_demand=True, active=True, connected=False)
        self.assertEqual(status(self.device(), {'ntrip_stream_state': {'server1': info}})['server1']['state'], 'starting')

    def test_new_online_payload_beats_old_offline_db(self):
        info = dict(enabled=True, on_demand=True, active=True, connected=True)
        result = status(self.device(status='offline'), {'status': 'online', 'ntrip_stream_state': {'server1': info}})
        self.assertEqual(result['server1']['state'], 'streaming')

    def test_offline_payload_beats_old_streaming_db(self):
        self.assertEqual(status(self.device(ntrip_connected=True), {'status': 'offline'})['server1']['state'], 'offline')

    def test_always_on_is_active_without_legacy_active_field(self):
        device = self.device(ntrip_connected=True, service_config={
            'server1_enabled': True, 'server1_stream_on_demand': False, 'mountpoint1': 'FIXED'})
        self.assertEqual(status(device, {})['server1']['state'], 'streaming')

    def test_mixed_servers_keep_sleep_and_streaming_separate(self):
        device = self.device()
        device.service_config.update(server2_enabled=True, server2_stream_on_demand=False)
        device.ntrip_status = {'server1': {'connected': False}, 'server2': {'connected': True}}
        result = status(device, {})
        self.assertEqual(result['server1']['state'], 'sleep')
        self.assertEqual(result['server2']['state'], 'streaming')

    def test_fail_open_uses_effective_output_without_changing_sleep_preference(self):
        info = dict(enabled=True, on_demand=True, active=False, connected=True,
                    configured_can_push=False, effective_can_push=True)
        self.assertEqual(status(self.device(), {'ntrip_stream_state': {'server1': info}})['server1']['state'], 'streaming')

    def test_disabled_server_stays_offline(self):
        device = self.device()
        device.service_config['server1_enabled'] = 'false'
        self.assertEqual(status(device, {})['server1']['state'], 'offline')


class StatusRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_unchanged_sleep_is_resent_after_refresh_interval(self):
        import asyncio
        from unittest.mock import AsyncMock
        clock = SimpleNamespace(now=100.0)
        snapshot = {'server1': {'enabled': True, 'state': 'sleep', 'mountpoint': 'BASE'},
                    'server2': {'enabled': False, 'state': 'offline', 'mountpoint': ''}}
        notify = AsyncMock()
        namespace = dict(models=SimpleNamespace(Device=object),
                         time=SimpleNamespace(monotonic=lambda: clock.now),
                         device_status_cache={}, device_status_cache_lock=asyncio.Lock(),
                         _status_last_notify={}, STATUS_REFRESH_SECONDS=30.0,
                         _extract_per_server_status=lambda device, payload: snapshot,
                         _notify_casters_on_status_change=notify)
        load_helpers('caster_status.py', {'_on_device_status_update', '_status_changed'}, namespace)
        update = namespace['_on_device_status_update']
        device = SimpleNamespace(serial='FIXED')
        await update(device, {})
        await update(device, {})
        self.assertEqual(notify.await_count, 1)
        clock.now += 31
        await update(device, {})
        self.assertEqual(notify.await_count, 2)
        self.assertEqual(notify.call_args.args[2]['server1']['new']['state'], 'sleep')
        self.assertTrue(notify.call_args.args[2]['server1']['changed'])


if __name__ == '__main__':
    unittest.main()

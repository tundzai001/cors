"""Numbered caster outputs keep legacy defaults and start independently."""
import ast
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / 'agent_universal.py'


class MultipleCasterTests(unittest.TestCase):
    def test_workers_and_telemetry_include_all_configured_outputs(self):
        tree = ast.parse(SOURCE.read_text())
        names = {'_get_ntrip_server_ids', '_to_bool', '_get_server_stream_switches',
                 '_compute_any_server_can_push', 'resolve_effective_stream_switch'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        manager = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AgentManager')
        restart = next(n for n in manager.body if isinstance(n, ast.FunctionDef) and n.name == 'restart_services')
        status_method = next(n for n in manager.body if isinstance(n, ast.FunctionDef) and n.name == 'get_full_status')
        telemetry = next(n for n in status_method.body if isinstance(n, ast.With)
                         and ast.unparse(n.items[0].context_expr) == 'self.stats_lock')
        worker = Mock(side_effect=lambda sid, *args: SimpleNamespace(server_id=sid, start=Mock()))
        scope = dict(NTRIPServerWorker=worker, is_remote_locked=lambda sid=None: False,
                     control_plane_fail_open_event=Event(), rtcm_input_stats_lock=Lock(), rtcm_input_bps=512)
        exec(compile(ast.Module(body=[*nodes, restart], type_ignores=[]), str(SOURCE), 'exec'), scope)
        ids = scope['_get_ntrip_server_ids']
        self.assertEqual(ids({}), [1, 2])
        config = {'stream_on_demand': False, 'server1_enabled': True, 'server2_enabled': True,
                  'server3_enabled': True, 'server5_enabled': False}
        self.assertEqual(ids(config), [1, 2, 3, 5])
        agent = SimpleNamespace(config={'is_provisioned': True, 'services': config},
                                service_workers=[], service_stats={}, ntrip_connection_status={},
                                log=Mock(), get_base_config=lambda: {}, get_service_config=lambda: config,
                                _normalize_mountpoint=lambda value: str(value or '').strip().lower(), stats_lock=Lock())
        scope['restart_services'](agent)
        self.assertEqual([w.server_id for w in agent.service_workers], [1, 2, 3])
        for w in agent.service_workers:
            w.start.assert_called_once()
        agent.ntrip_connection_status['server3'] = True
        scope.update(self=agent, status={})
        exec(compile(ast.Module(body=[telemetry], type_ignores=[]), str(SOURCE), 'exec'), scope)
        status = scope['status']
        self.assertTrue(status['ntrip_connected'])
        self.assertEqual(status['ntrip_stats']['server3_bps'], 512)
        self.assertEqual(status['ntrip_stats']['server1_bps'], 0)
        self.assertTrue(status['ntrip_stream_state']['server3']['connected'])
        config.update(server3_stream_on_demand=True, server3_stream_active=False)
        exec(compile(ast.Module(body=[telemetry], type_ignores=[]), str(SOURCE), 'exec'), scope)
        self.assertTrue(status['ntrip_stream_state']['server3']['sleep'])
        self.assertEqual(status['ntrip_stats']['server3_bps'], 0)


if __name__ == '__main__':
    unittest.main()

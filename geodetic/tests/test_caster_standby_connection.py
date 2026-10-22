"""Authenticated caster sessions survive RTCM sleep/wake transitions."""
import ast
import base64
from pathlib import Path
from queue import Queue, Empty
from threading import Event, Lock
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('Timed out waiting for worker state')
        time.sleep(0.01)


class CasterStandbyTests(unittest.TestCase):
    def test_sleep_keeps_authentication_and_wake_uses_same_socket(self):
        self.check_caster(1)

    def test_third_caster_keeps_authentication_and_wakes_independently(self):
        self.check_caster(3)

    def check_caster(self, sid):
        tree = ast.parse((ROOT / 'agent_universal.py').read_text())
        helpers = {'_get_ntrip_server_ids', '_to_bool', 'resolve_effective_stream_switch', '_get_server_stream_switches', '_compute_any_server_can_push'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in helpers
                 or isinstance(n, ast.ClassDef) and n.name == 'NTRIPServerWorker']
        manager = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AgentManager')
        nodes.append(next(n for n in manager.body if isinstance(n, ast.FunctionDef) and n.name == 'set_rtcm_stream_active'))
        class CasterSocket:
            def __init__(self):
                self.sent = []
                self.closed = False
            def setsockopt(self, *args): pass
            def sendall(self, data): self.sent.append(data)
            def recv(self, size): return b'HTTP/1.1 200 OK\r\n\r\n'
            def close(self): self.closed = True
        sock = CasterSocket()
        connect = Mock(return_value=sock)
        scope = dict(threading=threading, Queue=Queue, Empty=Empty, time=time, base64=base64,
                     socket=SimpleNamespace(create_connection=connect, SOL_SOCKET=1, SO_KEEPALIVE=9),
                     select=SimpleNamespace(select=lambda *args: ([], [], [])), subscriber_lock=Lock(),
                     rtcm_subscribers=[], control_plane_fail_open_event=Event(), is_remote_locked=lambda sid=None: False,
                     is_valid_rtcm3_packet=lambda data: data.startswith(b'\xd3'),
                     _telemetry_inc=Mock(), logging=SimpleNamespace(debug=Mock(), info=Mock()))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'agent.py', 'exec'), scope)
        cfg = dict(server1_enabled=True, serverhost1='caster', port1=2101, mountpoint1='BASE', password1='secret',
                   ntrip_version1=2, stream_on_demand=True, stream_active=False,
                   server1_stream_on_demand=True, server1_stream_active=False)
        cfg = {key.replace('1', str(sid)): value for key, value in cfg.items()}
        stats, connected = {}, {}
        worker = scope['NTRIPServerWorker'](sid, cfg, Mock(), stats, Lock(), connected)
        agent = SimpleNamespace(config={'services': cfg}, save_config=Mock(), restart_services=Mock(),
                                _normalize_mountpoint=lambda value: str(value or '').strip().lower())
        worker.start()
        try:
            wait_for(lambda: connected.get(f'server{sid}') is True)
            worker.queue.put(b'\xd3sleep')
            time.sleep(0.3)
            self.assertEqual(len(sock.sent), 1, 'Sleeping socket sends authentication only, no RTCM/end chunk')
            self.assertFalse(sock.closed)
            self.assertTrue(connected[f'server{sid}'])
            scope['set_rtcm_stream_active'](agent, True, server_id=sid)
            frame = b'\xd3wake'
            worker.queue.put(frame)
            wait_for(lambda: any(frame in packet for packet in sock.sent))
            scope['set_rtcm_stream_active'](agent, False, server_id=sid)
            worker.queue.put(b'\xd3sleep-again')
            time.sleep(0.3)
            self.assertEqual(connect.call_count, 1, 'Wake/sleep must reuse the authenticated socket')
            self.assertFalse(any(b'sleep' in packet for packet in sock.sent))
            self.assertTrue(connected[f'server{sid}'])
            agent.restart_services.assert_not_called()
        finally:
            worker.stop()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(sock.closed)

    def test_lte_sleep_keeps_target_enabled_but_blocks_rtcm(self):
        tree = ast.parse((ROOT / 'geotek_daithanh_aitogy.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MqttSourceWorker')
        names = {'_server_target_config', '_server_target_enabled', '_server_target_can_push', 'set_stream_active'}
        nodes = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
        runtime = {}
        scope = {'status_mgr': SimpleNamespace(get_runtime_config=lambda serial: dict(runtime),
                                               update_runtime_config=lambda serial, cfg: runtime.update(cfg))}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'lte.py', 'exec'), scope)
        agent = SimpleNamespace(serial='LTE', cfg=dict(server1_enabled=True, target_host='caster', target_port=2101,
                                target_mp='BASE', target_pass='secret', server1_stream_on_demand=True,
                                server1_stream_active=True), socket_lock=Lock(), _target_socks={'server1': Mock()},
                                current_bps_by_server={'server1': 100}, bytes_sent_by_server={'server1': 100},
                                log=Mock(), publish_status=Mock())
        for name in names:
            setattr(agent, name, scope[name].__get__(agent))
        agent.set_stream_active(False, 'BASE')
        self.assertTrue(agent._server_target_enabled(1))
        self.assertFalse(agent._server_target_can_push(1))
        agent._target_socks['server1'].close.assert_not_called()
        agent.set_stream_active(True, 'BASE')
        self.assertTrue(agent._server_target_can_push(1))
        self.assertTrue(agent.cfg['server1_stream_on_demand'])


if __name__ == '__main__': unittest.main()

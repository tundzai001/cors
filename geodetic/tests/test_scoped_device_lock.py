"""device_lock survives restart and gates individual hosts independently of enabled."""
import ast
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest


class ScopedDeviceLockTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        source = Path(__file__).parents[1] / 'agent_universal.py'
        tree = ast.parse(source.read_text())
        names = {'read_remote_lock', 'is_remote_locked', 'get_device_lock_status',
                 'create_remote_lock', 'remove_remote_lock', '_to_bool',
                 'resolve_effective_stream_switch', '_get_server_stream_switches'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        streamer = next(n for n in tree.body if isinstance(n, ast.ClassDef) and
                        any(isinstance(m, ast.FunctionDef) and m.name == '_can_push_rtcm' for m in n.body))
        nodes.append(next(m for m in streamer.body if isinstance(m, ast.FunctionDef) and m.name == '_can_push_rtcm'))
        self.scope = dict(os=os, json=json, time=time,
                          REMOTE_LOCK_PATH=os.path.join(self.directory.name, 'remote.lock'),
                          control_plane_fail_open_event=threading.Event())
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), self.scope)
        self.config = {'server1_enabled': True, 'server2_enabled': True,
                       'server1_stream_on_demand': False, 'server2_stream_on_demand': False}

    def test_selected_server_is_locked_even_enabled_and_fail_open(self):
        self.assertTrue(self.scope['create_remote_lock']([2]))
        for fail_open in (False, True):
            if fail_open:
                self.scope['control_plane_fail_open_event'].set()
            for sid, expected in ((1, True), (2, False)):
                with self.subTest(sid=sid, fail_open=fail_open):
                    switches = self.scope['_get_server_stream_switches'](self.config, sid, apply_fail_open=True)
                    self.assertTrue(switches['enabled'])
                    self.assertEqual(switches['can_push'], expected)
                    streamer = SimpleNamespace(config=self.config, server_id=sid)
                    self.assertEqual(self.scope['_can_push_rtcm'](streamer), expected)
        self.assertFalse(self.scope['is_remote_locked'](), 'Scoped locks must not stop GNSS')
        self.assertEqual(json.loads(Path(self.scope['REMOTE_LOCK_PATH']).read_text()), {'locked_servers': [2]})

    def test_switching_locks_and_unlock_preserves_enabled_configuration(self):
        original = dict(self.config)
        for locked in ([1], [1, 2], []):
            self.assertTrue(self.scope['create_remote_lock'](locked))
            self.assertEqual(self.scope['get_device_lock_status']()['locked_servers'], locked)
            for sid in (1, 2):
                self.assertEqual(self.scope['is_remote_locked'](sid), sid in locked)
            self.assertFalse(self.scope['is_remote_locked']())
        self.assertEqual(self.config, original)
        self.assertTrue(self.scope['create_remote_lock']([]), 'Unlock must be idempotent')

    def test_legacy_and_corrupt_locks_fail_closed_and_scoped_lock_replaces_legacy(self):
        self.assertTrue(self.scope['create_remote_lock']())
        self.assertTrue(self.scope['is_remote_locked']())
        self.assertTrue(self.scope['is_remote_locked'](1))
        self.assertTrue(self.scope['create_remote_lock']([2]))
        self.assertFalse(self.scope['is_remote_locked']())
        self.assertFalse(self.scope['is_remote_locked'](1))
        self.assertTrue(self.scope['is_remote_locked'](2))
        self.assertFalse(self.scope['create_remote_lock']([3]))
        self.assertTrue(self.scope['is_remote_locked'](2))
        Path(self.scope['REMOTE_LOCK_PATH']).write_text('{broken')
        self.assertTrue(self.scope['is_remote_locked']())

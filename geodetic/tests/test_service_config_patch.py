"""Deploy patches without erasing credentials or unrelated stream settings."""
import ast
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


def load_method(filename, name, scope, last=False):
    tree = ast.parse((ROOT / filename).read_text())
    methods = [node for cls in tree.body if isinstance(cls, ast.ClassDef)
               for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name]
    exec(compile(ast.Module(body=[methods[-1 if last else 0]], type_ignores=[]), filename, 'exec'), scope)
    return scope[name]


class ServiceConfigPatchTests(unittest.TestCase):
    def test_pi_patch_preserves_passwords_and_unrelated_settings(self):
        scope = {'_to_bool': lambda value, default: bool(value),
                 '_compute_any_server_can_push': lambda cfg, **kw: cfg['server1_stream_active']}
        apply = load_method('agent_universal.py', 'update_service_config', scope)
        stored = {'password1': 'secret', 'password2': 'backup', 'rtcmpassword1': 'rtcm',
                  'command_pass': 'command', 'username1': 'source', 'port1': 2101,
                  'stream_on_demand': True, 'server1_stream_on_demand': True,
                  'server1_stream_active': False}
        agent = SimpleNamespace(config={'services': dict(stored)}, save_config=Mock())
        patch = {'mountpoint1': 'NEW', 'password1': '', 'password2': None}
        apply(agent, patch)
        cfg = agent.config['services']
        for key, value in stored.items():
            self.assertEqual(cfg[key], value, key)
        self.assertEqual(cfg['mountpoint1'], 'NEW')
        self.assertEqual(patch, {'mountpoint1': 'NEW', 'password1': '', 'password2': None})
        apply(agent, {'password1': 'replacement', 'server1_stream_active': True})
        self.assertEqual(agent.config['services']['password1'], 'replacement')
        self.assertEqual(agent.config['services']['username1'], 'source')
        self.assertTrue(agent.rtcm_stream_active)

    def test_lte_patch_preserves_password_and_stream_mode(self):
        tree = ast.parse((ROOT / 'geotek_daithanh_aitogy.py').read_text())
        helpers = [node for node in tree.body if
                   isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'MQTT_SOURCE_CONFIG_FIELDS' for t in node.targets)
                   or isinstance(node, ast.FunctionDef) and node.name == '_mqtt_source_persistable_config']
        stored = {'target_pass': 'secret', 'password1': 'secret', 'password2': 'backup',
                  'server1_enabled': True, 'stream_on_demand': True, 'server1_stream_active': True,
                  'target_host': 'caster', 'target_port': 2101, 'username1': 'source'}
        status = SimpleNamespace(get_runtime_config=lambda serial: dict(stored), update_runtime_config=Mock())
        config = SimpleNamespace(update_mqtt_source_config=Mock(return_value=True))
        scope = {'datetime': datetime, 'timezone': timezone, 'status_mgr': status, 'config_mgr': config}
        exec(compile(ast.Module(body=helpers, type_ignores=[]), 'relay.py', 'exec'), scope)
        apply = load_method('geotek_daithanh_aitogy.py', '_persist_remote_config', scope, last=True)
        agent = SimpleNamespace(serial='LTE', cfg=dict(stored), log=Mock(), socket_lock=Lock(),
                                _target_socks={}, _target_sock=None)
        apply(agent, {'payload': {'service_config': {'password1': '', 'password2': None, 'mountpoint2': 'NEW'}}})
        for key, value in stored.items():
            self.assertEqual(agent.cfg[key], value, key)
        self.assertEqual(agent.cfg['mountpoint2'], 'NEW')
        apply(agent, {'payload': {'service_config': {'password1': 'replacement'}}})
        self.assertEqual(agent.cfg['target_pass'], 'replacement')
        self.assertEqual(agent.cfg['password1'], 'replacement')
        saved = config.update_mqtt_source_config.call_args.args[1]
        self.assertEqual(saved['password2'], 'backup')


if __name__ == '__main__':
    unittest.main()

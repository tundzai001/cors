"""Compare reported configuration without importing the running backend."""
import ast
from pathlib import Path
import unittest


path = Path(__file__).resolve().parents[1] / 'app/mqtt.py'
tree = ast.parse(path.read_text())
function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == '_compare_configs')
scope = {}
exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), scope)
compare = scope['_compare_configs']


class ConfigSyncRedactionTests(unittest.TestCase):
    def test_redacted_secrets_and_runtime_switches_are_not_config_errors(self):
        db = {'password1': 'private', 'password2': 'private', 'rtcmpassword1': 'private',
              'stream_active': False, 'server1_stream_active': False,
              'server2_stream_active': False, 'server1_enabled': True,
              'stream_on_demand': True, 'mountpoint1': 'TV869'}
        agent = {'stream_active': True, 'server1_stream_active': True,
                 'server2_stream_active': True, 'server1_enabled': True,
                 'stream_on_demand': True, 'mountpoint1': 'TV869'}
        self.assertEqual(compare(agent, db, 'Service'), (True, []))

    def test_real_config_mismatch_remains_visible_without_secret_values(self):
        ok, errors = compare({'mountpoint1': 'OLD', 'password1': 'agent-private'},
                             {'mountpoint1': 'NEW', 'password1': 'db-private'}, 'Service')
        self.assertFalse(ok)
        self.assertEqual(errors, ['[Service.mountpoint1] DB=NEW vs Agent=OLD'])
        self.assertNotIn('private', str(errors))

    def test_nested_secrets_and_missing_real_fields(self):
        ok, errors = compare({'nested': {}}, {'nested': {'password': 'private', 'port': 2101}}, 'Service')
        self.assertFalse(ok)
        self.assertEqual(errors, ['[Service.nested.port] Agent=None'])


if __name__ == '__main__':
    unittest.main()

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
    def test_province_lookup_echo_does_not_override_base_coordinate_comparison(self):
        target = {'coords': {'lat': '21.0849895913'},
                  'conversion_report': {'location': {'input_coord': {'lat': 21.084987718254386}}}}
        agent = {'coords': {'lat': '21.0849895913'},
                 'conversion_report': {'location': {'input_coord': {'lat': 21.08498772}}}}
        self.assertEqual(compare(agent, target, 'Base'), (True, []))
        agent['coords']['lat'] = '21.0849995913'
        self.assertIn('[Base.coords.lat]', compare(agent, target, 'Base')[1][0])

    def test_nested_list_objects_ignore_json_key_order(self):
        db = {'conversion_report': {'location': {'projection_options': [
            {'k0': 0.9999, 'l0_deg': 105, 'region': 'Hà Nội'}]}}}
        agent = {'conversion_report': {'location': {'projection_options': [
            {'region': 'Hà Nội', 'l0_deg': 105, 'k0': 0.9999}]}}}
        self.assertEqual(compare(agent, db, 'Base'), (True, []))
        agent['conversion_report']['location']['projection_options'][0]['l0_deg'] = 106
        ok, errors = compare(agent, db, 'Base')
        self.assertFalse(ok)
        self.assertIn('Base.conversion_report.location.projection_options', errors[0])
        self.assertFalse(compare({'ports': ['USB', 'UART1']}, {'ports': ['UART1', 'USB']})[0])

    def test_survey_ignores_retained_fixed_coordinates_and_accuracy(self):
        agent = {'base_setup_method': 'SURVEY_IN', 'survey_in_duration': 120,
                 'survey_in_accuracy': 5, 'gnss_options': {'output_mode': 'rtcm_only'}}
        target = {**agent, 'coords': {'lat': 21, 'lon': 105, 'alt': 10}, 'accuracy': 0.01}
        self.assertEqual(compare(agent, target, 'Base'), (True, []))
        self.assertEqual(compare(agent, {**target, 'altitude_reference': 'LEGACY'}, 'Base'), (True, []))
        for key, value in [('survey_in_duration', 300), ('survey_in_accuracy', 1),
                           ('gnss_options', {'output_mode': 'diagnostics_rtcm'}),
                           ('base_setup_method', 'FIXED_LLA')]:
            with self.subTest(key=key):
                self.assertFalse(compare(agent, {**target, key: value}, 'Base')[0])
        self.assertFalse(compare(agent, target, 'Service')[0])
        fixed = {**agent, 'base_setup_method': 'FIXED_LLA'}
        self.assertEqual(compare(fixed, {**target, 'base_setup_method': 'FIXED_LLA'}, 'Base'),
                         (False, ['[Base.coords] Agent=None', '[Base.accuracy] Agent=None']))

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

    def test_missing_legacy_height_reference_does_not_claim_ellipsoid(self):
        agent = {'base_setup_method': 'FIXED_LLA', 'coords': {'alt': '49.920000000'}}
        target = {**agent, 'altitude_reference': 'LEGACY'}
        self.assertEqual(compare(agent, target, 'Base'), (True, []))
        target['altitude_reference'] = 'ELLIPSOID'
        self.assertEqual(compare(agent, target, 'Base'),
                         (False, ['[Base.altitude_reference] Agent=None']))
        agent['altitude_reference'] = 'LEGACY'
        self.assertEqual(compare(agent, target, 'Base'),
                         (False, ['[Base.altitude_reference] DB=ELLIPSOID vs Agent=LEGACY']))
        agent['altitude_reference'] = 'ELLIPSOID'
        self.assertEqual(compare(agent, target, 'Base'), (True, []))
        agent['coords'] = {'alt': '50.0'}
        self.assertFalse(compare(agent, target, 'Base')[0])


if __name__ == '__main__':
    unittest.main()

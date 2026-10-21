"""Check ellipsoidal base height on each wire path without connecting a receiver."""
import ast
import asyncio
import importlib.util
import logging
from pathlib import Path
import re
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('height_command_builder', ROOT / 'cors_dashboard/backend/app/command_builder.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def relay_scope():
    names = {'_first_value', '_as_float', '_as_int', '_extract_remote_payload',
             'build_geotek_lte_unicore_commands', '_mqtt_source_persistable_config'}
    nodes = [n for n in ast.parse((ROOT / 'geodetic/geotek_daithanh_aitogy.py').read_text()).body
             if isinstance(n, ast.FunctionDef) and n.name in names
             or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'MQTT_SOURCE_CONFIG_FIELDS' for t in n.targets)]
    scope = {'re': re, 'logging': logging}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'relay', 'exec'), scope)
    return scope


class BaseHeightTests(unittest.TestCase):
    def assert_ellipsoid(self, commands):
        commands = [c.lower() for c in commands]
        undulation = commands.index(b'config undulation 0.0\r\n')
        fixed = next(i for i, c in enumerate(commands) if c.startswith(b'mode base ') and not c.startswith(b'mode base time'))
        self.assertLess(undulation, fixed)
        self.assertEqual(float(commands[fixed].split()[-1]), 30)
        # Model a receiver with a nonzero initial geoid separation. Only the
        # explicit undulation command makes its internal ellipsoidal height 30.
        separation = -25
        for command in commands:
            if command.startswith(b'config undulation '):
                separation = float(command.split()[-1])
            if command.startswith(b'mode base ') and not command.startswith(b'mode base time'):
                self.assertEqual(float(command.split()[-1]) + separation, 30)

    def test_backend_fixed_and_lte_legacy_and_ellipsoid(self):
        for make in [lambda ref: builder.build_base_fixed_lla_command('Unicorecomm', 20, 106, 30, .01, altitude_reference=ref),
                     lambda ref: builder.build_geotek_lte_unicore_config('FIXED_LLA', lat=20, lon=106, alt=30, altitude_reference=ref)]:
            self.assertEqual(make(None), make('LEGACY'))
            self.assertFalse(any(b'undulation' in c.lower() for c in make(None)))
            self.assert_ellipsoid(make('ELLIPSOID'))
            with self.assertRaises(ValueError):
                make('ORTHOMETRIC')
        packet = builder.build_base_fixed_lla_command('Ublox', 20, 106, 30, .01, altitude_reference='ELLIPSOID')[0]
        self.assertEqual(packet[8:10], b'\x02\x01')
        self.assertEqual(int.from_bytes(packet[18:22], 'little', signed=True) * .01 + int.from_bytes(packet[24:25], 'little', signed=True) * .0001, 30)

    def test_relay_preserves_reference_through_restart_and_legacy_payloads(self):
        scope = relay_scope()
        make = scope['build_geotek_lte_unicore_commands']
        runtime = {'setup_method': 'FIXED_LLA', 'lat': 20, 'lon': 106, 'alt': 30, 'altitude_reference': 'ELLIPSOID'}
        self.assert_ellipsoid(make(None, runtime))
        payload = {'method': 'FIXED_LLA', 'params': {'coords': {'lat': 20, 'lon': 106, 'alt': 30}, 'altitude_reference': 'ELLIPSOID'}}
        self.assert_ellipsoid(make({'payload': payload}, {}))
        self.assertEqual(scope['_mqtt_source_persistable_config'](runtime)['altitude_reference'], 'ELLIPSOID')
        del payload['params']['altitude_reference']
        self.assertFalse(any(b'undulation' in c.lower() for c in make(payload, runtime)))
        self.assertFalse(any(b'undulation' in c.lower() for c in make({'method': 'SURVEY_IN'}, runtime)))

    def test_auto_agents_set_undulation_before_fixed_position(self):
        for filename in ['agent_universal.py', 'agent_test.py']:
            with self.subTest(agent=filename):
                node = next(n for n in ast.parse((ROOT / 'geodetic' / filename).read_text()).body
                            if isinstance(n, ast.AsyncFunctionDef) and n.name == '_apply_base_mode_to_chip')
                receiver = Mock()
                receiver.__enter__ = Mock(return_value=receiver)
                receiver.__exit__ = Mock(return_value=False)
                async def no_wait(_):
                    pass
                scope = {'GNSSReader': object, 'DEFAULT_BAUDRATE': 115200, 'logging': logging,
                         'asyncio': SimpleNamespace(sleep=no_wait), 'serial_port_lock': threading.Lock(),
                         'serial': SimpleNamespace(Serial=Mock(return_value=receiver)),
                         '_runtime_serial_baud': lambda **kw: 115200}
                exec(compile(ast.Module(body=[node], type_ignores=[]), filename, 'exec'), scope)
                asyncio.run(scope[node.name]('Unicorecomm', 'TEST', 20, 106, 30, None))
                self.assert_ellipsoid([call.args[0] for call in receiver.write.call_args_list])


if __name__ == '__main__':
    unittest.main()

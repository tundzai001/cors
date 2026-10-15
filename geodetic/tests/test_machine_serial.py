"""Linux PCs without /proc/cpuinfo Serial must retain their saved device ID."""
import ast
import io
import logging
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class MachineSerialTests(unittest.TestCase):
    def load_serial_function(self, filename, directory):
        source = Path(__file__).parents[1] / filename
        function = next(node for node in ast.parse(source.read_text()).body
                        if isinstance(node, ast.FunctionDef) and node.name == 'get_machine_serial')
        uuid = SimpleNamespace(uuid4=Mock(return_value=SimpleNamespace(hex='0123456789abcdef0123456789abcdef')))

        def read_file(path, *args, **kwargs):
            if str(path) == '/proc/cpuinfo':
                return io.StringIO('processor : 0\nmodel name : Embedded PC\n')
            return open(path, *args, **kwargs)

        scope = dict(os=os, re=re, logging=logging, uuid=uuid, BASE_DIR=directory,
                     IS_WINDOWS=False, IS_RASPBERRY_PI=True, open=read_file)
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), scope)
        return scope['get_machine_serial'], uuid

    def test_linux_fallback_id_survives_agent_restart(self):
        for filename in ('agent_universal.py', 'agent_test.py'):
            with self.subTest(agent=filename), tempfile.TemporaryDirectory() as directory:
                get_serial, uuid = self.load_serial_function(filename, directory)
                self.assertEqual(get_serial(), 'PI_0123456789ab')
                uuid.uuid4.return_value = SimpleNamespace(hex='abcdef0123456789abcdef0123456789')
                self.assertEqual(get_serial(), 'PI_0123456789ab')
                uuid.uuid4.assert_called_once()

    def test_existing_ids_are_preserved_without_case_changes(self):
        for filename in ('agent_universal.py', 'agent_test.py'):
            for serial in ('PI_0bd46c72e7be', 'PI_AbCdEf123456', 'LP_ABCDEF123456', '10000000abcdef01'):
                with self.subTest(agent=filename, serial=serial), tempfile.TemporaryDirectory() as directory:
                    (Path(directory) / 'device_id.txt').write_text(serial)
                    get_serial, uuid = self.load_serial_function(filename, directory)
                    self.assertEqual(get_serial(), serial)
                    uuid.uuid4.assert_not_called()

    def test_corrupt_id_is_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'device_id.txt').write_text('PI_not-a-hardware-id')
            get_serial, _ = self.load_serial_function('agent_universal.py', directory)
            self.assertEqual(get_serial(), 'PI_0123456789ab')

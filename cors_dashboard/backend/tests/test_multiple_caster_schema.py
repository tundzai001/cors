"""Additional caster fields survive request parsing with legacy field types."""
import ast
import logging
from types import SimpleNamespace
from typing import Optional, List
import importlib.util
from pathlib import Path
import unittest
from pydantic import ValidationError

spec = importlib.util.spec_from_file_location('caster_schemas', Path(__file__).resolve().parents[1] / 'app/schemas.py')
schemas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schemas)


class MultipleCasterSchemaTests(unittest.TestCase):
    def test_third_caster_is_available_to_rover_routing(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'app/ntrip_control.py').read_text())
        names = {'_extract_station_mountpoints', '_device_has_enabled_mountpoint', '_resolve_preferred_server_id_for_mountpoint'}
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        scope = dict(models=SimpleNamespace(Device=object), Optional=Optional, List=List,
                     service_server_ids=schemas.service_server_ids,
                     _device_service_config=lambda device: device.service_config,
                     _normalize_mountpoint=lambda value: str(value or '').strip('/').lower(),
                     settings=SimpleNamespace(ENABLE_PREFERRED_HOST=False), logger=logging.getLogger(__name__))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'ntrip_control.py', 'exec'), scope)
        config = {'server3_enabled': True, 'serverhost3': 'third.test', 'mountpoint3': 'THIRD'}
        device = SimpleNamespace(service_config=config)
        self.assertEqual(scope['_extract_station_mountpoints'](device), ['THIRD'])
        self.assertTrue(scope['_device_has_enabled_mountpoint'](device, 'third'))
        self.assertEqual(scope['_resolve_preferred_server_id_for_mountpoint'](config, 'third'), 3)

    def test_extra_slots_are_typed_and_patch_fields_are_preserved(self):
        payload = schemas.ServiceConfig(server3_enabled=True, serverhost3='third.caster', port3='2101',
                                       ntrip_version3='2', password3='secret', server3_stream_on_demand=True,
                                       server12_enabled=False).model_dump(exclude_unset=True)
        self.assertEqual(payload['port3'], 2101)
        self.assertEqual(payload['ntrip_version3'], 2)
        self.assertEqual(schemas.service_server_ids(payload), [1, 2, 3, 12])
        self.assertNotIn('server1_enabled', payload)
        for patch in ({'port3': 0}, {'port3': 65536}, {'ntrip_version3': 3}, {'server3_enabled': 'bad'}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                schemas.ServiceConfig(**patch)


if __name__ == '__main__':
    unittest.main()

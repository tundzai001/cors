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
    def test_remove_added_host_clears_only_its_fields_for_patch_and_legacy_agents(self):
        config = {'server1_enabled': True, 'password1': 'keep', 'server3_enabled': True,
                  'serverhost3': 'third', 'password3': 'remove', 'port3': 2101,
                  'server3_stream_active': True, 'server4_enabled': True, 'password4': 'keep4'}
        changes = schemas.ServiceConfig(removed_servers=[3]).model_dump(exclude_unset=True)
        saved = schemas.remove_service_servers(config, changes['removed_servers'])
        self.assertEqual(saved, {'server1_enabled': True, 'password1': 'keep', 'server4_enabled': True, 'password4': 'keep4'})
        for version in ('V2.2.6', 'V2.0.2'):
            deployment = schemas.service_config_deployment_payload(version, saved, changes)
            self.assertEqual(deployment['removed_servers'], [3])
        for sid in (0, 1, 2, -1):
            with self.subTest(sid=sid), self.assertRaises(ValidationError):
                schemas.ServiceConfig(removed_servers=[sid])

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

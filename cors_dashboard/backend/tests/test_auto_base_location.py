"""Use real boundary/catalog snapshots; never infer a projection from longitude."""
import unittest
from app.auto_base_location import resolve_location, _in_ring


class AutoBaseLocationTests(unittest.TestCase):
    def test_hanoi_resolves_existing_projection(self):
        result = resolve_location(21.08499, 105.78666, -7.9815)
        self.assertEqual(result['province_code'], '01')
        self.assertEqual(result['l0_deg'], 105)
        self.assertEqual(result['k0'], 0.9999)
        self.assertEqual(result['zone_width_deg'], 3)
        self.assertEqual(result['input_coord']['alt'], -7.9815)

    def test_merged_province_requires_explicit_projection_when_old_regions_differ(self):
        result = resolve_location(10.7769, 106.7009, 10)
        self.assertEqual(result['province_code'], '79')
        self.assertEqual(result['state'], 'projection_ambiguous')
        self.assertIsNone(result['l0_deg'])
        self.assertGreater(len({p['l0_deg'] for p in result['projection_options']}), 1)
        self.assertTrue(all(p['k0'] == 0.9999 for p in result['projection_options']))

    def test_outside_country_and_invalid_llh(self):
        self.assertEqual(resolve_location(0, 0, 0)['state'], 'outside_boundaries')
        with self.assertRaises(ValueError):
            resolve_location(float('nan'), 105, 0)

    def test_boundary_inclusion(self):
        ring = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
        self.assertTrue(_in_ring(0, 0.5, ring))
        self.assertFalse(_in_ring(2, 0.5, ring))


class AutoBaseLocationApiTests(unittest.TestCase):
    def test_endpoint_returns_echoed_llh_and_rejects_invalid_values(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.coordinate_routes import router
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            point = dict(lat=21.08499, lon=105.78666, alt=-7.9815)
            response = client.post('/api/coordinates/auto-base-location', json=point)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['input_coord'], point)
            self.assertEqual(response.json()['province_code'], '01')
            self.assertEqual(client.post('/api/coordinates/auto-base-location', json={**point, 'lat': 91}).status_code, 422)


class AutoBaseConfigTests(unittest.TestCase):
    def test_projection_and_shift_options_reach_both_agents_without_host_policy(self):
        import ast
        import math
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / 'app/routers/devices.py'
        node = next(n for n in ast.parse(path.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == '_normalize_auto_cors_config')
        scope = {'math': math}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
        normalize = scope['_normalize_auto_cors_config']
        connection = dict(ip='any-caster.example', port=2101, user='user', password='pass', mountpoint='M3')
        self.assertTrue(normalize({'auto_cors': connection})['enable_itrf_vn2000_transform'])
        manual = dict(connection, l0_deg='105.75', k0='0.9999', resN=0.25, resE=-0.75, resHS=1.5)
        result = normalize({'auto_cors': manual})
        self.assertEqual((result['l0_deg'], result['k0'], result['resHS']), (105.75, 0.9999, 1.5))
        self.assertFalse(normalize({'auto_cors': dict(connection, enable_itrf_vn2000_transform=False)})['enable_itrf_vn2000_transform'])
        for invalid in ({'l0_deg': 105}, {'k0': 0.9996}, {'l0_deg': 105, 'k0': float('nan')},
                        {'resN': float('inf')}, {'enable_itrf_vn2000_transform': 'false'}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize({'auto_cors': dict(connection, **invalid)})

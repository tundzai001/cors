"""Use real boundary/catalog snapshots; never infer a projection from longitude."""
import unittest
from app.auto_base_location import resolve_location, _in_ring


class AutoBaseLocationTests(unittest.TestCase):
    def test_hanoi_resolves_existing_projection(self):
        result = resolve_location(21.08499, 105.78666, -7.9815)
        self.assertEqual(result['province_code'], '01')
        self.assertEqual(result['l0_deg'], 105)
        self.assertEqual(result['k0'], 0.9996)
        self.assertEqual(result['zone_width_deg'], 6)
        self.assertEqual(result['input_coord']['alt'], -7.9815)

    def test_merged_province_uses_six_degree_zone_not_old_tm3_regions(self):
        result = resolve_location(10.7769, 106.7009, 10)
        self.assertEqual(result['province_code'], '79')
        self.assertEqual(result['state'], 'resolved')
        self.assertEqual(result['l0_deg'], 105)
        self.assertEqual(result['k0'], 0.9996)
        east = resolve_location(16.0544, 108.2022, 10)
        self.assertEqual(east['utm_zone'], 49)
        self.assertEqual(east['l0_deg'], 111)

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

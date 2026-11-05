import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pyproj import CRS
from app.coordinate_routes import router, TM3


class CoordinateTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def convert(self, source, target, points):
        return self.client.post('/api/coordinates/convert', json={
            'source': source, 'target': target, 'points': points})

    def test_display_reprojection_changes_only_ne_and_keeps_original(self):
        from copy import deepcopy
        from pyproj import Proj
        source = dict(l0_deg=105, k0=0.9996, false_easting=500000, false_northing=0)
        east, north = Proj(proj='tmerc', ellps='WGS84', lon_0=105, k_0=.9996, x_0=500000)(105.8, 21.08)
        payload = dict(north=north, east=east, h=-7.993264563381672, source=source,
                       target_l0_deg=105.75, target_k0=.9999)
        original = deepcopy(payload)
        response = self.client.post('/api/coordinates/reproject-display', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        expected_east, expected_north = Proj(proj='tmerc', ellps='WGS84', lon_0=105.75, k_0=.9999, x_0=500000)(105.8, 21.08)
        self.assertAlmostEqual(result['projected_coord']['north'], expected_north, places=6)
        self.assertAlmostEqual(result['projected_coord']['east'], expected_east, places=6)
        self.assertEqual(result['projected_coord']['h'], payload['h'])
        self.assertEqual(payload, original)
        reverse = dict(**result['projected_coord'], source=dict(source, l0_deg=105.75, k0=.9999),
                       target_l0_deg=105, target_k0=.9996)
        restored = self.client.post('/api/coordinates/reproject-display', json=reverse).json()['projected_coord']
        self.assertAlmostEqual(restored['north'], north, places=6)
        self.assertAlmostEqual(restored['east'], east, places=6)
        self.assertEqual(restored['h'], payload['h'])
        for invalid in ({'target_k0': 1}, {'target_l0_deg': 0}, {'north': None}):
            self.assertEqual(self.client.post('/api/coordinates/reproject-display', json={**payload, **invalid}).status_code, 422)

    def test_catalog_and_parameters(self):
        data = self.client.get('/api/coordinates/catalog').json()
        self.assertEqual(len(data['provinces34']), 34)
        self.assertEqual(len(data['provinces63']), 63)
        self.assertEqual(len({p['name'] for p in data['provinces63']}), 63)
        self.assertEqual({p['code'] for p in data['provinces34']},
                         {p['current_code'] for p in data['provinces63']})
        ag = [p for p in data['provinces63'] if p['current_code'] == '91']
        self.assertEqual({p['central_meridian'] for p in ag}, {104.5, 104.75})
        systems = {s['id']: s for s in data['systems']}
        for name, meridian in [('Kiên Giang', 104.5), ('An Giang', 104.75), ('Bắc Ninh', 105.5), ('Bắc Giang', 107)]:
            province = next(p for p in data['provinces63'] if p['name'] == name)
            self.assertEqual(systems[province['crs']]['projection'],
                             dict(central_meridian=meridian, zone_width=3, scale_factor=.9999))
        self.assertEqual(systems['EPSG:32648']['projection'],
                         dict(central_meridian=105, zone_width=6, scale_factor=.9996))
        for meridian, code in TM3.items():
            params = {p.name: p.value for p in CRS(code).coordinate_operation.params}
            self.assertEqual(params['Longitude of natural origin'], meridian)
            self.assertEqual(params['Scale factor at natural origin'], .9999)
            self.assertEqual(params['False easting'], 500000)

    def test_hanoi_reference_and_inverse(self):
        # EPSG 6960 datum operation + EPSG 5897 TM-3, north/east UI order.
        response = self.convert('EPSG:4326', 'EPSG:5897', [[21.0285, 105.8542]])
        self.assertEqual(response.status_code, 200)
        data = response.json()
        north, east = data['points'][0]
        self.assertAlmostEqual(north, 2326345.3117, places=3)
        self.assertAlmostEqual(east, 588591.9440, places=3)
        self.assertEqual(data['accuracy_m'], 1)
        inverse = self.convert('EPSG:5897', 'EPSG:4326', [[north, east]]).json()['points'][0]
        self.assertAlmostEqual(inverse[0], 21.0285, places=7)
        self.assertAlmostEqual(inverse[1], 105.8542, places=7)
        datum = self.convert('EPSG:4326', 'EPSG:4756', [[21.0285, 105.8542]]).json()['points'][0]
        self.assertGreater(abs(datum[0] - 21.0285), .0001)

    def test_batch_roundtrip_every_system(self):
        points = [[21.0285, 105.8542], [10.776, 106.7]]
        for system in self.client.get('/api/coordinates/catalog').json()['systems']:
            if system['geocentric']:
                continue
            with self.subTest(system=system['id']):
                converted = self.convert('EPSG:4326', system['id'], points)
                self.assertEqual(converted.status_code, 200)
                restored = self.convert(system['id'], 'EPSG:4326', converted.json()['points'])
                self.assertEqual(restored.status_code, 200)
                for before, after in zip(points, restored.json()['points']):
                    for a, b in zip(before, after):
                        self.assertAlmostEqual(a, b, places=7)

    def test_invalid_inputs(self):
        for points in ([], [[91, 105]], [[21, 181]], [[21]], [[21, 105, 3, 4]], [[21, 105]] * 1001, [[21, 105], [21, 105, 30]]):
            self.assertEqual(self.convert('EPSG:4326', 'EPSG:5897', points).status_code, 422)
        self.assertEqual(self.convert('+proj=tmerc', 'EPSG:4326', [[1, 2]]).status_code, 422)
        response = self.client.post('/api/coordinates/convert', content='{"source":"EPSG:4326","target":"EPSG:5897","points":[["NaN",105]]}', headers={'content-type': 'application/json'})
        self.assertEqual(response.status_code, 422)

    def test_3d_matches_land_slide_reference_and_inverse(self):
        # Independent fixture from land_slide_web/backend/processors/geodetic.py:
        # DatumParams(l0_deg=105.75), WGS84 LLH(20.845,106.688,30).
        source = [[20.845, 106.688, 30]]
        data = self.convert('EPSG:4326', 'EPSG:9210', source).json()
        self.assertEqual(data['dimensions'], 3)
        for actual, expected in zip(data['points'][0], [2306078.7833471284, 597421.9707657371, 51.73330244515091]):
            self.assertAlmostEqual(actual, expected, places=3)
        restored = self.convert('EPSG:9210', 'EPSG:4326', data['points']).json()['points'][0]
        for actual, expected in zip(restored, source[0]):
            self.assertAlmostEqual(actual, expected, places=5)
        # Height must affect the actual 3D transformation, not just be echoed.
        high = self.convert('EPSG:4326', 'EPSG:9210', [[20.845, 106.688, 1000]]).json()['points'][0]
        self.assertGreater(abs(high[0] - data['points'][0][0]), .001)

    def test_3d_roundtrip_geocentric_and_projected(self):
        source = [[20.845, 106.688, 30], [10.776, 106.7, 100]]
        for target in ['EPSG:4978', 'VN2000:ECEF', 'EPSG:4756', 'EPSG:3405', 'EPSG:32649']:
            with self.subTest(target=target):
                converted = self.convert('EPSG:4326', target, source)
                self.assertEqual(converted.status_code, 200)
                restored = self.convert(target, 'EPSG:4326', converted.json()['points'])
                self.assertEqual(restored.status_code, 200)
                for before, after in zip(source, restored.json()['points']):
                    for a, b in zip(before, after):
                        self.assertAlmostEqual(a, b, places=5)
        self.assertEqual(self.convert('EPSG:4326', 'EPSG:4978', [[20, 106]]).status_code, 422)

    def test_explicit_geoid_height_conversion(self):
        payload = dict(source='EPSG:4326', target='EPSG:9210', points=[[20.845, 106.688, 10]],
                       source_height='orthometric', source_geoid=20,
                       target_height='orthometric', target_geoid=25)
        response = self.client.post('/api/coordinates/convert', json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertAlmostEqual(response.json()['points'][0][2], 26.7332876, places=4)
        del payload['source_geoid']
        self.assertEqual(self.client.post('/api/coordinates/convert', json=payload).status_code, 422)
        payload['source_geoid'] = 20
        payload['points'] = [[20.845, 106.688]]
        self.assertEqual(self.client.post('/api/coordinates/convert', json=payload).status_code, 422)


if __name__ == '__main__':
    unittest.main()

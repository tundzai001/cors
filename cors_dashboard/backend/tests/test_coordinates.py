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
            with self.subTest(system=system['id']):
                converted = self.convert('EPSG:4326', system['id'], points)
                self.assertEqual(converted.status_code, 200)
                restored = self.convert(system['id'], 'EPSG:4326', converted.json()['points'])
                self.assertEqual(restored.status_code, 200)
                for before, after in zip(points, restored.json()['points']):
                    for a, b in zip(before, after):
                        self.assertAlmostEqual(a, b, places=7)

    def test_invalid_inputs(self):
        for points in ([], [[91, 105]], [[21, 181]], [[21]], [[21, 105, 3]], [[21, 105]] * 1001):
            self.assertEqual(self.convert('EPSG:4326', 'EPSG:5897', points).status_code, 422)
        self.assertEqual(self.convert('+proj=tmerc', 'EPSG:4326', [[1, 2]]).status_code, 422)
        response = self.client.post('/api/coordinates/convert', content='{"source":"EPSG:4326","target":"EPSG:5897","points":[["NaN",105]]}', headers={'content-type': 'application/json'})
        self.assertEqual(response.status_code, 422)


if __name__ == '__main__':
    unittest.main()

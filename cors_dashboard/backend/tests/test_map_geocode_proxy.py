"""Tests for public geocode proxy and station map search resilience."""
import unittest
from unittest.mock import AsyncMock, Mock, patch
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.map_routes import _GEOCODE_CACHE, _fetch_geocode, get_public_map_stations_router


class MapGeocodeProxyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _GEOCODE_CACHE.clear()
        self.app = FastAPI()
        self.app.include_router(get_public_map_stations_router)
        self.client = TestClient(self.app)

    async def test_fetch_geocode_success_with_arcgis(self):
        fake_arcgis = {
            "candidates": [
                {
                    "address": "Tam Kỳ, Quảng Nam",
                    "score": 100.0,
                    "location": {"x": 108.4740, "y": 15.5736},
                    "attributes": {"PlaceName": "Tam Kỳ"}
                }
            ]
        }
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json = Mock(return_value=fake_arcgis)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            results = await _fetch_geocode("Tam Kỳ")
            self.assertEqual(len(results), 1)
            self.assertAlmostEqual(results[0]["lat"], 15.5736, places=3)
            self.assertAlmostEqual(results[0]["lon"], 108.4740, places=3)
            self.assertIn("Tam Kỳ", results[0]["display_name"])
            self.assertEqual(results[0]["source"], "arcgis")

    async def test_fetch_geocode_fallback_with_open_meteo(self):
        fake_response = {
            "results": [
                {
                    "name": "Tam Kỳ",
                    "latitude": 15.5736,
                    "longitude": 108.4740,
                    "country_code": "VN",
                    "admin1": "Đà Nẵng",
                    "admin2": "Thành Phố Tam Kỳ",
                }
            ]
        }

        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json = Mock(return_value=fake_response)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            results = await _fetch_geocode("Tam Kỳ")
            self.assertEqual(len(results), 1)
            self.assertAlmostEqual(results[0]["lat"], 15.5736, places=3)
            self.assertAlmostEqual(results[0]["lon"], 108.4740, places=3)
            self.assertIn("Tam Kỳ", results[0]["display_name"])
            self.assertEqual(results[0]["source"], "open-meteo")

    async def test_fetch_geocode_caching(self):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json = Mock(return_value={
            "candidates": [
                {
                    "address": "Hà Nội",
                    "score": 100.0,
                    "location": {"x": 105.8542, "y": 21.0285},
                }
            ]
        })

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            res1 = await _fetch_geocode("Hà Nội")
            self.assertGreaterEqual(len(res1), 1)
            initial_calls = mock_get.call_count
            # Second call should hit memory cache without any additional HTTP requests
            res2 = await _fetch_geocode("Hà Nội")
            self.assertEqual(len(res2), len(res1))
            self.assertEqual(mock_get.call_count, initial_calls)

    async def test_specific_ward_rejects_city_and_scattered_name_matches(self):
        async def response(url, **kwargs):
            if 'arcgis' in url:
                data = {'candidates': [{'address': 'Thành phố Ninh Bình', 'score': 100,
                                       'location': {'x': 105.975, 'y': 20.251}}]}
                self.assertIn('Hoa Lư', kwargs['params']['singleLine'])
            elif 'photon' in url:
                data = {'features': [{'geometry': {'coordinates': [105.89, 20.57]},
                                      'properties': {'name': 'Đường Đồng Văn', 'district': 'Phù Vân',
                                                     'state': 'Ninh Bình', 'country': 'Vietnam'}}]}
            elif 'open-meteo' in url:
                data = {'results': []}
            else:
                data = [{'name': 'Ninh Vân', 'display_name': 'Ninh Vân, Hoa Lư, Ninh Bình',
                         'lat': '20.15', 'lon': '105.98'}]
            return Mock(status_code=200, json=Mock(return_value=data))

        with patch('httpx.AsyncClient.get', side_effect=response):
            results = await _fetch_geocode('Ninh Vân, Ninh Bình')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['source'], 'nominatim')
        self.assertIn('Ninh Vân', results[0]['display_name'])

    async def test_fetch_geocode_handles_network_failure_gracefully(self):
        # When external service connection is refused or errors
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=httpx.ConnectError("Connection refused")):
            results = await _fetch_geocode("Unknown Place")
            self.assertEqual(results, [])

    def test_public_geocode_http_endpoint(self):
        fake_response = {
            "results": [
                {
                    "name": "Đà Nẵng",
                    "latitude": 16.0544,
                    "longitude": 108.2022,
                    "country_code": "VN",
                    "admin1": "Đà Nẵng",
                }
            ]
        }
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json = Mock(return_value=fake_response)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            response = self.client.get("/api/public/geocode?q=Đà+Nẵng")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIsInstance(data, list)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["name"], "Đà Nẵng")

    def test_get_vietnam_provinces_summary_endpoint(self):
        response = self.client.get("/api/public/vietnam-provinces")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 34)
        names = {p["name"] for p in data}
        self.assertIn("Hà Nội", names)
        self.assertIn("Đà Nẵng", names)
        self.assertIn("Hồ Chí Minh", names)

    def test_get_vietnam_provinces_geojson_endpoint(self):
        response = self.client.get("/api/public/vietnam-provinces-geojson")
        self.assertEqual(response.status_code, 200)
        fc = response.json()
        self.assertEqual(fc.get("type"), "FeatureCollection")
        self.assertEqual(len(fc.get("features", [])), 34)

    async def test_search_administrative_units_empty_query(self):
        from app.province_db_service import search_administrative_units
        mock_db = AsyncMock()
        results = await search_administrative_units(mock_db, "")
        self.assertEqual(results, [])

    async def test_seed_provinces_already_seeded(self):
        from app.province_db_service import seed_vietnam_provinces
        mock_db = AsyncMock()
        mock_scalar = Mock()
        mock_scalar.scalar.return_value = 34
        mock_db.execute.return_value = mock_scalar
        res = await seed_vietnam_provinces(mock_db)
        self.assertTrue(res.get("already_seeded"))
        self.assertEqual(res.get("provinces"), 34)



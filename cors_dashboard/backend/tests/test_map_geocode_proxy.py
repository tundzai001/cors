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

    async def test_fetch_geocode_success_with_open_meteo(self):
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
            "results": [
                {
                    "name": "Hà Nội",
                    "latitude": 21.0285,
                    "longitude": 105.8542,
                    "country_code": "VN",
                }
            ]
        })

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            res1 = await _fetch_geocode("Hà Nội")
            self.assertEqual(len(res1), 1)
            # Second call should hit memory cache without re-requesting
            res2 = await _fetch_geocode("Hà Nội")
            self.assertEqual(len(res2), 1)
            mock_get.assert_called_once()

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


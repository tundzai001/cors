"""Unit tests for NOAA GloTEC Ionosphere service and API endpoints."""

import unittest
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ionosphere_service import IonosphereService
from app.ionosphere_routes import router as ionosphere_router


class IonosphereServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = IonosphereService()
        self.app = FastAPI()
        self.app.include_router(ionosphere_router)
        self.client = TestClient(self.app)

    def _sample_glotec_geojson(self, time_tag=None):
        if time_tag is None:
            time_tag = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Create minimal 2x2 grid sample for fast testing
        return {
            "type": "FeatureCollection",
            "time_tag": time_tag,
            "product": "glotec",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-177.5, 88.75]},
                    "properties": {"tec": 25.4, "anomaly": 1.2, "hmF2": 320.0, "quality_flag": 0}
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [177.5, 88.75]},
                    "properties": {"tec": 30.1, "anomaly": -0.5, "hmF2": 310.0, "quality_flag": 0}
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-177.5, -88.75]},
                    "properties": {"tec": 10.2, "anomaly": 0.0, "hmF2": 290.0, "quality_flag": 0}
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [177.5, -88.75]},
                    "properties": {"tec": 15.6, "anomaly": 2.1, "hmF2": 280.0, "quality_flag": 0}
                }
            ]
        }

    async def test_parse_glotec_payload(self):
        sample = self._sample_glotec_geojson()
        compact, geojson = self.service._parse_glotec_payload(sample, "http://test.url/sample.geojson")

        self.assertIn("status", compact)
        self.assertFalse(compact["is_stale"])
        self.assertEqual(compact["time_tag"], sample["time_tag"])
        self.assertIn("UTC+7", compact["observation_time_vn"])
        self.assertAlmostEqual(compact["stats"]["min_tec"], 10.2, places=1)
        self.assertAlmostEqual(compact["stats"]["max_tec"], 30.1, places=1)

        # Check geojson features
        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertEqual(len(geojson["features"]), 4)

    async def test_stale_detection(self):
        # 24 hours ago
        stale_time = "2026-10-14T00:00:00Z"
        sample = self._sample_glotec_geojson(time_tag=stale_time)
        compact, _ = self.service._parse_glotec_payload(sample, "http://test.url/sample.geojson")
        self.assertTrue(compact["is_stale"])
        self.assertEqual(compact["status"], "STALE")

    async def test_get_latest_with_mock_client(self):
        sample_geo = self._sample_glotec_geojson()
        catalog_mock = [{"url": "/products/glotec/sample.geojson", "time_tag": sample_geo["time_tag"]}]

        resp_cat = Mock(status_code=200, json=Mock(return_value=catalog_mock), raise_for_status=Mock())
        resp_geo = Mock(status_code=200, json=Mock(return_value=sample_geo), raise_for_status=Mock())

        async def mock_get(url, *args, **kwargs):
            if "geojson_2d_urt.json" in url:
                return resp_cat
            return resp_geo

        with patch("httpx.AsyncClient.get", side_effect=mock_get):
            data = await self.service.get_latest(format_type="compact", force_refresh=True)
            self.assertEqual(data["time_tag"], sample_geo["time_tag"])
            self.assertEqual(data["stats"]["min_tec"], 10.2)

            geo = await self.service.get_latest(format_type="geojson")
            self.assertEqual(geo["type"], "FeatureCollection")

    def test_api_endpoint(self):
        sample = self._sample_glotec_geojson()
        compact, geojson = self.service._parse_glotec_payload(sample, "http://test.url/sample.geojson")

        with patch.object(self.service, "get_latest", new_callable=AsyncMock, return_value=compact):
            with patch("app.ionosphere_routes.ionosphere_service", self.service):
                res = self.client.get("/api/ionosphere/latest")
                self.assertEqual(res.status_code, 200)
                json_data = res.json()
                self.assertEqual(json_data["time_tag"], compact["time_tag"])
                self.assertIn("Cache-Control", res.headers)
                self.assertIn("public", res.headers["Cache-Control"])


if __name__ == "__main__":
    unittest.main()

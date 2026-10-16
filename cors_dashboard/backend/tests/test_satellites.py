"""Unit tests for GNSS satellite service and CelesTrak TLE parsing."""

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.satellite_service import SatelliteService
from app.satellite_routes import router as satellite_router


class SatelliteServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clock = patch("app.satellite_service.datetime", wraps=datetime)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.clock.now.return_value = datetime(2026, 10, 16, tzinfo=timezone.utc)
        self.service = SatelliteService()
        self.service._save_disk_cache = Mock()
        self.service._load_disk_cache = Mock(return_value=None)
        self.app = FastAPI()
        self.app.include_router(satellite_router)
        self.client = TestClient(self.app)

    def test_extract_satellite_code(self):
        # GPS
        code_gps = self.service._extract_satellite_code("gps", "GPS BIIR-5  (PRN 22)", "1 26407U...")
        self.assertEqual(code_gps, "G22")

        # BeiDou
        code_bds = self.service._extract_satellite_code("beidou", "BEIDOU-2 G4 (C04)", "1 37210U...")
        self.assertEqual(code_bds, "C?")  # Assignment has ended; the old name is not authoritative.

        # Galileo
        code_gal = self.service._extract_satellite_code("galileo", "GSAT0101 (GALILEO-PFM)", "1 37846U...")
        self.assertEqual(code_gal, "E11")
        self.assertEqual(self.service._extract_satellite_code("galileo", "GSAT0203 (GALILEO 7)", "1 40544U..."), "E26")
        self.assertEqual(self.service._extract_satellite_code("galileo", "GSAT0208 (GALILEO 11)", "1 41175U..."), "E08")

        # GLONASS
        code_glo = self.service._extract_satellite_code("glonass", "COSMOS 2433 (720)", "1 32275U...")
        self.assertEqual(code_glo, "R25")
        self.assertEqual(self.service._extract_satellite_code("glonass", "COSMOS 2456 (730)", "1 36111U..."), "R01")

        code_qzss = self.service._extract_satellite_code("qzss", "QZS-1R (QZSS/PRN 196)", "1 49336U...")
        self.assertEqual(code_qzss, "J196")

    def test_reassignment_uses_effective_time_and_repairs_cached_code(self):
        sat = {"name": "BEIDOU-3 IGSO-1 (C38)", "line1": "1 44204U...", "code": "C38"}
        payload = {"constellations": {"beidou": [sat]}}
        self.service._normalize_codes(payload)
        self.assertEqual(sat["code"], "C06")
        self.clock.now.return_value = datetime(2026, 4, 10, 13, tzinfo=timezone.utc)
        self.assertEqual(self.service._extract_satellite_code("beidou", sat["name"], sat["line1"]), "C?")
        self.clock.now.return_value = datetime(2026, 4, 9, tzinfo=timezone.utc)
        self.assertEqual(self.service._extract_satellite_code("beidou", sat["name"], sat["line1"]), "C38")

    def test_unknown_identity_does_not_invent_prn_from_name_or_norad(self):
        for constellation, name, prefix in [("glonass", "COSMOS (712)", "R"), ("galileo", "GSAT9999 (GALILEO 8)", "E")]:
            self.assertEqual(self.service._extract_satellite_code(constellation, name, "1 99999U..."), prefix + "?")

    def test_reported_missing_prns_resolve_by_norad(self):
        for constellation, norad, expected in [
            ("beidou", "44204", "C06"), ("galileo", "41175", "E08"),
            ("glonass", "36111", "R01"), ("glonass", "39155", "R02"),
            ("glonass", "37867", "R03"), ("glonass", "44299", "R12"),
            ("glonass", "32393", "R13"), ("glonass", "42939", "R14"),
            ("glonass", "39620", "R18"), ("glonass", "45358", "R24"),
        ]:
            with self.subTest(prn=expected):
                self.assertEqual(self.service._extract_satellite_code(constellation, "Former satellite name", f"1 {norad}U..."), expected)

    async def test_fresh_disk_cache_codes_are_repaired_without_network(self):
        payload = {"updated_at_utc": "2026-10-16T00:00:00+00:00", "constellations": {k: [] for k in ("gps", "galileo", "beidou", "glonass", "qzss")}}
        payload["constellations"]["galileo"] = [{"name": "GSAT0208 (GALILEO 11)", "line1": "1 41175U...", "code": "E11"}]
        self.service._load_disk_cache.return_value = payload
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as request:
            result = await self.service.get_satellites_tle()
            request.assert_not_called()
        self.assertEqual(result["constellations"]["galileo"][0]["code"], "E08")

    def test_parse_tle_text(self):
        sample_tle = (
            "GPS BIIR-5  (PRN 22)\n"
            "1 26407U 00040A   26287.19752126  .00000004  00000+0  00000+0 0  9993\n"
            "2 26407  54.8318 210.4400 0116657 304.4726 228.8000  2.00558672192336\n"
        )
        sats = self.service._parse_tle_text(sample_tle, "gps")
        self.assertEqual(len(sats), 1)
        self.assertEqual(sats[0]["code"], "G22")
        self.assertEqual(sats[0]["norad_id"], "26407")
        self.assertEqual(sats[0]["constellation"], "gps")

    def test_parse_gnss_group_filters_qzss_and_uses_rtcm_prn(self):
        gnss_tles = (
            "QZS-1R (QZSS/PRN 196)\n"
            "1 49336U 21096A   26287.19752126  .00000004  00000+0  00000+0 0  9993\n"
            "2 49336  41.0000 210.4400 0001000 304.4726 228.8000  1.00270000    01\n"
            "GPS BIIR-5 (PRN 22)\n"
            "1 26407U 00040A   26287.19752126  .00000004  00000+0  00000+0 0  9993\n"
            "2 26407  54.8318 210.4400 0116657 304.4726 228.8000  2.00558672192336\n"
        )
        sats = self.service._parse_tle_text(gnss_tles, "qzss")
        self.assertEqual([sat["code"] for sat in sats], ["J196"])

    async def test_get_satellites_tle_with_mock(self):
        sample_tle = (
            "GPS BIIR-5  (PRN 22)\n"
            "1 26407U 00040A   26287.19752126  .00000004  00000+0  00000+0 0  9993\n"
            "2 26407  54.8318 210.4400 0116657 304.4726 228.8000  2.00558672192336\n"
        )
        mock_resp = Mock(status_code=200, text=sample_tle)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            payload = await self.service.get_satellites_tle(force_refresh=True)
            self.assertEqual(payload["status"], "OK")
            self.assertFalse(payload["is_stale"])
            self.assertIn("gps", payload["constellations"])
            self.assertEqual(len(payload["constellations"]["gps"]), 1)
            self.assertEqual(payload["constellations"]["qzss"], [])

    def test_api_endpoints(self):
        dummy = {
            "status": "OK",
            "is_stale": False,
            "total_satellites": 143,
            "counts": {"gps": 32, "galileo": 31, "beidou": 53, "glonass": 27, "qzss": 5},
            "constellations": {"gps": [], "galileo": [], "beidou": [], "glonass": [], "qzss": []}
        }
        with patch.object(self.service, "get_satellites_tle", new_callable=AsyncMock, return_value=dummy):
            with patch("app.satellite_routes.satellite_service", self.service):
                res = self.client.get("/api/gnss/satellites/tle")
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.json()["total_satellites"], 143)

                res_sum = self.client.get("/api/gnss/satellites/summary")
                self.assertEqual(res_sum.status_code, 200)


if __name__ == "__main__":
    unittest.main()

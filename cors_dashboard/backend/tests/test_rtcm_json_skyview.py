import unittest
import time

from app.rtcm_signal_decoder import RTCMSignalDecoder, _rtcm_msm_prn


class RtcmJsonSkyviewTests(unittest.TestCase):
    def test_agent_constellation_names_are_case_insensitive(self):
        for nav_system, code in (
            ("GPS", "G"),
            ("GLONASS", "R"),
            ("GALILEO", "E"),
            ("BEIDOU", "C"),
        ):
            with self.subTest(nav_system=nav_system):
                decoded = RTCMSignalDecoder().decode_json(
                    "PI_test",
                    {"navSystem": nav_system, "satellites": [{"prn": 8, "snr": 42}]},
                )
                self.assertIsNotNone(decoded)
                self.assertFalse(decoded["satellites"][0]["has_geometry"])
                self.assertEqual(decoded["satellites"][0]["id"], f"RTCM:{code}:8")

    def test_geometry_is_reported_only_for_fresh_orbit_angles(self):
        decoder = RTCMSignalDecoder()
        packet = {"navSystem": "GPS", "satellites": [{"prn": 8, "snr": 42}]}
        decoder.decode_json("PI_test", packet)
        decoder._azel_cache["PI_test"]["RTCM:G:8"] = (time.time(), 90.0, 45.0)
        satellite = decoder.decode_json("PI_test", packet)["satellites"][0]
        self.assertTrue(satellite["has_geometry"])
        self.assertEqual(satellite["azimuth"], 90.0)
        decoder._azel_cache["PI_test"]["RTCM:G:8"] = (time.time() - 11, 90.0, 45.0)
        self.assertFalse(decoder.decode_json("PI_test", packet)["satellites"][0]["has_geometry"])

    def test_qzss_json_accepts_standard_public_prn(self):
        decoded = RTCMSignalDecoder().decode_json(
            "PI_test",
            {"navSystem": "QZSS", "satellites": [{"prn": 196, "snr": 42}]},
        )
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["satellites"][0]["id"], "RTCM:J:196")

    def test_qzss_msm_satellite_id_maps_to_public_prn(self):
        self.assertEqual(_rtcm_msm_prn("jp", 4), 196)
        self.assertEqual(_rtcm_msm_prn("us", 4), 4)


if __name__ == "__main__":
    unittest.main()

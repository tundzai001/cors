import unittest

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
                self.assertEqual(decoded["satellites"][0]["id"], f"RTCM:{code}:8")

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

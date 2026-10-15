import unittest

from app.rtcm_signal_decoder import RTCMSignalDecoder


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


if __name__ == "__main__":
    unittest.main()

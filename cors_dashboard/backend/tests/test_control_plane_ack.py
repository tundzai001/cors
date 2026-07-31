from __future__ import annotations

import unittest

from app.control_plane_ack import build_control_plane_ack


class ControlPlaneAckTests(unittest.TestCase):
    def test_builds_correlated_ack(self):
        ack = build_control_plane_ack(
            {"status_boot_id": "boot-1", "status_sequence": 42},
            channel="mqtt",
            serial="station-1",
            server_timestamp=123.5,
        )
        self.assertEqual(
            ack,
            {
                "type": "control_plane_ack",
                "channel": "mqtt",
                "serial": "station-1",
                "status_boot_id": "boot-1",
                "status_sequence": 42,
                "server_timestamp": 123.5,
            },
        )

    def test_rejects_invalid_probe(self):
        self.assertIsNone(build_control_plane_ack({}, channel="mqtt", serial="x"))
        self.assertIsNone(build_control_plane_ack(
            {"status_boot_id": "boot", "status_sequence": -1},
            channel="mqtt",
            serial="x",
        ))


if __name__ == "__main__":
    unittest.main()

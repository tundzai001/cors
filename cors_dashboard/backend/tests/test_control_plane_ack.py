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

    def test_location_reply_is_correlated_over_both_existing_channels(self):
        probe = {"status_boot_id": "boot-1", "status_sequence": 42,
                 "auto_base_progress": {"phase": "resolving_location", "location_request": {
                     "request_id": "boot-1:1000", "lat": "21.08499", "lon": "105.786656", "alt": "-8.0624"}}}
        for channel in ("mqtt", "websocket"):
            response = build_control_plane_ack(probe, channel=channel, serial="station-1")['auto_base_location']
            self.assertEqual(response['request_id'], 'boot-1:1000')
            self.assertEqual(response['location']['province_code'], '01')
            self.assertEqual(response['location']['input_coord']['alt'], -8.0624)
        probe['auto_base_progress']['phase'] = 'ready_for_review'
        self.assertNotIn('auto_base_location', build_control_plane_ack(probe, channel='mqtt', serial='station-1'))
        probe['auto_base_progress']['phase'] = 'resolving_location'
        probe['auto_base_progress']['location_request']['lat'] = float('nan')
        self.assertEqual(build_control_plane_ack(probe, channel='mqtt', serial='station-1')['auto_base_location']['location']['state'], 'invalid_request')

    def test_rejects_invalid_probe(self):
        self.assertIsNone(build_control_plane_ack({}, channel="mqtt", serial="x"))
        self.assertIsNone(build_control_plane_ack(
            {"status_boot_id": "boot", "status_sequence": -1},
            channel="mqtt",
            serial="x",
        ))


if __name__ == "__main__":
    unittest.main()

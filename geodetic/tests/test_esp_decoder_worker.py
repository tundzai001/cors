import unittest
from unittest.mock import MagicMock, patch
import json
import sys
import os

# Ensure geodetic directory is in sys.path
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from geotek_daithanh_aitogy import MonitorWorker, ACTIVE_WORKERS, WORKER_MAP_LOCK


class EspDecoderWorkerTests(unittest.TestCase):

    def test_monitor_worker_serial_naming(self):
        mqtt_mock = MagicMock()

        # 1. Explicit serial supplied
        cfg1 = {"mountpoint": "TEST_BASE", "serial": "esp_9896E5124B00"}
        worker1 = MonitorWorker(cfg1, mqtt_mock)
        self.assertEqual(worker1.serial, "esp_9896E5124B00")
        self.assertEqual(worker1.topic_status, "pi/devices/esp_9896E5124B00/status")

        # 2. Mountpoint starts with esp_
        cfg2 = {"mountpoint": "esp_112233445566"}
        worker2 = MonitorWorker(cfg2, mqtt_mock)
        self.assertEqual(worker2.serial, "esp_112233445566")

        # 3. Standard fallback mountpoint
        cfg3 = {"mountpoint": "HAIPHONG_CORS"}
        worker3 = MonitorWorker(cfg3, mqtt_mock)
        self.assertEqual(worker3.serial, "MONITOR_HAIPHONG_CORS")

    def test_monitor_worker_status_and_skyview_payload(self):
        mqtt_mock = MagicMock()
        mqtt_mock.is_connected.return_value = True

        cfg = {
            "mountpoint": "ESP_BASE_01",
            "serial": "esp_AABBCCDDEEFF",
            "rover_user": "ESP_BASE_01",
            "rover_pass": "12345",
            "lat": 21.0285,
            "lon": 105.8542,
            "alt": 10.0,
        }
        worker = MonitorWorker(cfg, mqtt_mock)
        worker.is_connected = True
        worker.current_bps = 512

        # Simulate decoded satellite
        worker.satellites = {
            "GPS:1": {"system": "GPS", "prn": 1, "snr": 42.0, "elevation": 60, "azimuth": 120, "last_seen": 9999999999}
        }

        worker.publish_status()

        # A decoder must not replace the physical ESP's presence/config/license.
        self.assertEqual(mqtt_mock.publish.call_count, 1)
        self.assertFalse(any(c.args[0].endswith('/status') for c in mqtt_mock.publish.call_args_list))

        # Call 2: raw_data skyview
        raw_call = [c for c in mqtt_mock.publish.call_args_list if c[0][0] == "pi/devices/esp_AABBCCDDEEFF/raw_data"][0]
        raw_data = json.loads(raw_call[0][1])
        self.assertEqual(raw_data["type"], "rtcm_skyview_calculated")
        self.assertEqual(len(raw_data["satellites"]), 1)
        self.assertEqual(raw_data["satellites"][0]["prn"], 1)

    def test_1005_only_is_published_without_presence_or_gga_fallback(self):
        import geotek_daithanh_aitogy as relay
        from test_prefix7_decoder import frame1005
        decoder = relay.RTCMSignalDecoder()
        worker = MonitorWorker({'mountpoint': 'changed_mp', 'serial': 'esp_TEST',
                                'lat': 1, 'lon': 2, 'alt': 3}, MagicMock())
        with patch.object(relay, 'rtcm_signal_decoder', decoder):
            worker.publish_status()
            worker.mqtt_client.publish.assert_not_called()
            decoder.decode_sync(worker.serial, frame1005())
            worker.publish_status()
        call = worker.mqtt_client.publish.call_args
        self.assertEqual(call.args[0], 'pi/devices/esp_TEST/raw_data')
        data = json.loads(call.args[1])
        self.assertIn('1005', data['packets'])
        self.assertAlmostEqual(data['base_coords']['lat'], 21, places=0)
        self.assertNotIn('status', data)
        self.assertNotIn('service_config', data)
        self.assertNotIn('rtk_fix_status', data)

    def test_monitor_worker_stop(self):
        mqtt_mock = MagicMock()
        cfg = {"mountpoint": "ESP_TEST", "serial": "esp_1234"}
        worker = MonitorWorker(cfg, mqtt_mock)
        self.assertFalse(worker.stop_event.is_set())
        worker.stop()
        self.assertTrue(worker.stop_event.is_set())
        self.assertTrue(worker.wakeup_event.is_set())

    def test_monitor_worker_send_rover_gga(self):
        mqtt_mock = MagicMock()
        cfg = {
            "mountpoint": "ESP_TEST",
            "serial": "esp_1234",
            "lat": 20.9712,
            "lon": 105.7831,
            "alt": 15.0,
        }
        worker = MonitorWorker(cfg, mqtt_mock)
        sock_mock = MagicMock()
        worker._send_rover_gga(sock_mock)
        
        sock_mock.sendall.assert_called_once()
        sent_data = sock_mock.sendall.call_args[0][0].decode("ascii")
        self.assertTrue(sent_data.startswith("$GPGGA,"))
        self.assertTrue(sent_data.endswith("\r\n"))
        self.assertIn("2058.", sent_data)  # 20 deg 58 min for lat 20.9712
        self.assertGreater(worker.last_gga_send_time, 0.0)

    def test_rtcm_signal_decoder_get_base_coordinates(self):
        from geotek_daithanh_aitogy import RTCMSignalDecoder
        decoder = RTCMSignalDecoder()
        # Hanoi rough ECEF coords (-1629864.0, 5747683.0, 2270967.0)
        decoder._base_positions["esp_1234"] = (-1629864.0, 5747683.0, 2270967.0)
        coords = decoder.get_base_coordinates("esp_1234")
        self.assertIsNotNone(coords)
        lat, lon, alt = coords
        self.assertAlmostEqual(lat, 21.0, places=0)
        self.assertAlmostEqual(lon, 105.8, places=0)


if __name__ == "__main__":
    unittest.main()

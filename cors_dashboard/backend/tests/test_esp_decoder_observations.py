"""Decoded RTCM enriches physical ESP telemetry without replacing its heartbeat."""
import json
import unittest
from unittest.mock import AsyncMock, patch
from app import mqtt


class EspDecoderObservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_packet_only_observation_keeps_presence_and_nmea(self):
        serial = "esp_OBSERVATION_TEST"
        previous = mqtt.device_telemetry_cache.pop(serial, None)
        config = {"mountpoint1": "changed_mp", "serverhost1": "caster.test"}
        mqtt.device_telemetry_cache[serial] = {
            "status": "online", "service_config": config,
            "nmea_data": {"gga": {"quality": 4}},
        }
        coords = {"lat": 21.123456789, "lon": 105.123456789, "alt": -9.976}
        packets = {"1005": {"count": 1, "interval": 0}}
        try:
            with patch.object(mqtt, "_expects_nmea_skyview", return_value=True), \
                 patch.object(mqtt.manager, "broadcast", new_callable=AsyncMock) as broadcast:
                await mqtt.handle_message_async(f"pi/devices/{serial}/raw_data", json.dumps({
                    "type": "rtcm_skyview_calculated", "packets": packets,
                    "satellites": [], "base_coords": coords,
                }).encode())
            live = mqtt.device_telemetry_cache[serial]
            self.assertEqual(live["status"], "online")
            self.assertEqual(live["service_config"], config)
            self.assertEqual(live["nmea_data"]["gga"]["quality"], 4)
            self.assertEqual(live["rtcm_stats"], packets)
            self.assertEqual(live["base_coords"], coords)
            self.assertEqual(broadcast.await_args.args[0]["data"]["packets"], packets)
        finally:
            mqtt.device_telemetry_cache.pop(serial, None)
            if previous is not None:
                mqtt.device_telemetry_cache[serial] = previous

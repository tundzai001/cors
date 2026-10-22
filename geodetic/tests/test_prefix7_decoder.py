"""Offline protocol/decoder proof: prefix-7 auth, CRC data, no license or fake FIX."""
import base64
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import geotek_daithanh_aitogy as relay


def frame1005():
    # RTCM 1005 ECEF reference position with valid CRC24Q.
    fields = [(1005, 12), (1, 12), (0, 6), (1, 1), (1, 1), (1, 1), (0, 1),
              (int(-1629864.0 * 10000), 38), (0, 1), (0, 1),
              (int(5747683.0 * 10000), 38), (0, 2), (int(2270967.0 * 10000), 38)]
    bits = ''.join(format(value & ((1 << width) - 1), f'0{width}b') for value, width in fields)
    payload = int(bits, 2).to_bytes(len(bits) // 8, 'big')
    data = b'\xd3' + len(payload).to_bytes(2, 'big') + payload
    return data + relay.crc24q(data).to_bytes(3, 'big')


class Prefix7DecoderTests(unittest.TestCase):
    def worker(self):
        mqtt = MagicMock()
        mqtt.is_connected.return_value = True
        return relay.MonitorWorker({'mountpoint': '70001', 'serial': 'MONITOR_70001',
                                    'host': 'caster.test', 'port': 2201,
                                    'rover_user': '70001', 'rover_pass': 'saved-password'}, mqtt)

    def test_response_parser_fragmentation_and_coalesced_rtcm(self):
        frame = frame1005()
        for chunks in ([b'ICY 200 OK\r\n' + frame],
                       [b'HTTP/1.0 200', b' OK\r\nContent-Type: gnss/data\r\n', b'\r\n' + frame]):
            sock = MagicMock()
            sock.recv.side_effect = chunks
            self.assertEqual(relay.MonitorWorker._read_ntrip_response(sock), frame)
        for response in (b'HTTP/1.0 401 Unauthorized\r\n\r\n', b'SOURCETABLE 200 OK\r\n', b'ICY 403 Forbidden\r\n'):
            sock = MagicMock()
            sock.recv.return_value = response
            with self.assertRaises(ConnectionError):
                relay.MonitorWorker._read_ntrip_response(sock)

    def test_rtcm_decodes_reference_coordinates_without_license(self):
        worker = self.worker()
        decoder = relay.RTCMSignalDecoder()
        valid = frame1005()
        frames = relay.extract_rtcm3_frames(bytearray(b'noise' + valid))
        self.assertEqual(frames, [valid])
        decoder.decode_sync(worker.serial, frames[0])
        worker.is_connected = True
        worker.current_bps = len(valid)
        worker.satellites = {'GPS:1': {'system': 'GPS', 'prn': 1, 'snr': 42, 'last_seen': 9999999999}}
        with patch.object(relay, 'rtcm_signal_decoder', decoder), \
             patch.object(relay, 'has_license') as license_check, \
             patch.object(relay, 'get_license_fingerprint') as fingerprint:
            worker.publish_status()
        license_check.assert_not_called()
        fingerprint.assert_not_called()
        status = json.loads(worker.mqtt_client.publish.call_args_list[0].args[1])
        self.assertEqual(status['status'], 'online')
        self.assertEqual(status['rtk_fix_status'], 'NO_FIX')
        self.assertEqual(status['detected_chip_type'], 'UNKNOWN')
        self.assertTrue(status['is_provisioned'])
        self.assertIn('1005', status['rtcm_stats'])
        self.assertAlmostEqual(status['nmea_data']['base_coords']['lat'], 21, places=0)
        self.assertEqual(status['nmea_data']['base_coords'], status['base_config']['coords'])
        corrupt = bytearray(valid)
        corrupt[-1] ^= 1
        self.assertEqual(relay.extract_rtcm3_frames(corrupt), [])

    def test_worker_uses_saved_password_and_endpoint_and_closes_before_retry(self):
        worker = self.worker()
        sock = MagicMock()
        sock.recv.side_effect = [b'ICY 200 OK\r\n' + frame1005(), b'']
        def retry(seconds):
            self.assertGreaterEqual(seconds, 60)
            sock.close.assert_called()
            worker.stop_event.set()
        worker.wakeup_event.wait = retry
        with patch.object(relay.socket, 'create_connection', return_value=sock) as connect:
            worker.run()
        connect.assert_called_once_with(('caster.test', 2201), timeout=10)
        request = sock.sendall.call_args_list[0].args[0]
        self.assertIn(b'GET /70001 HTTP/1.0', request)
        self.assertIn(base64.b64encode(b'70001:saved-password'), request)
        self.assertFalse(worker.is_connected)

    def test_sync_is_idempotent_and_password_rotation_restarts_worker(self):
        client = MagicMock()
        workers = {}
        worker = MagicMock()
        worker.is_alive.return_value = True
        def create(config, mqtt):
            worker.cfg = config
            return worker
        payload = {'action': 'start', 'serial': 'MONITOR_70001', 'mountpoint': '70001',
                   'rover_user': '70001', 'rover_pass': 'old-password', 'host': 'caster.test', 'port': 2201}
        def send():
            client.on_message(client, None, SimpleNamespace(topic='pi/decoder/esp_base',
                              payload=json.dumps(payload).encode(), retain=False))
        with patch.object(relay.mqtt, 'Client', return_value=client), \
             patch.object(relay, 'MonitorWorker', side_effect=create) as factory:
            relay.setup_mqtt(workers)
            send()
            send()
            self.assertEqual(factory.call_count, 1)
            worker.is_alive.side_effect = [True, False]
            payload['rover_pass'] = 'new-password'
            send()
            self.assertEqual(factory.call_count, 2)
            worker.stop.assert_called_once()
            self.assertEqual(worker.cfg['rover_pass'], 'new-password')

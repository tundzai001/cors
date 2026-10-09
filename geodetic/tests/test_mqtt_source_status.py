"""LTE source availability takes precedence over caster standby."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class MqttSourceStatusTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'geotek_daithanh_aitogy.py').read_text())
        worker = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'MqttSourceWorker')
        names = {'_target_enabled', '_server_target_config', '_server_target_enabled', '_server_target_can_push', '_derive_status', 'publish_status'}
        methods = [node for node in worker.body if isinstance(node, ast.FunctionDef) and node.name in names]
        self.manager = SimpleNamespace(update_worker_status=Mock())
        self.scope = dict(time=SimpleNamespace(time=lambda: 1000), json=json, RELAY_VERSION='test',
                          status_mgr=self.manager, get_license_fingerprint=lambda serial: None,
                          has_license=lambda serial: True, _status_safe_config=lambda cfg: cfg,
                          rtcm_signal_decoder=SimpleNamespace(get_stats=lambda serial: {'1006': {'last_seen': 995}, '1077': {'last_seen': 995}}))
        exec(compile(ast.Module(body=methods, type_ignores=[]), 'lte-status.py', 'exec'), self.scope)
        self.worker = SimpleNamespace(serial='LTE', name='HT.VuQuang', topic_status='test/status',
            cfg=dict(topic='source', server1_enabled=True, target_host='caster', target_port=2101,
                     target_mp='BASE', target_pass='secret', server1_stream_on_demand=True, server1_stream_active=False),
            last_mqtt_data_ts=990, data_timeout=2, _target_socks={}, current_bps_by_server={},
            current_mqtt_bps=0, is_connected=False, latest_gga={'satellites': 28, 'fix_status': 'RTK_FIXED'},
            latest_gsa={'hdop': 1}, satellites={}, mqtt_status_client=None, last_antenna_status=None)
        for name in names:
            setattr(self.worker, name, self.scope[name].__get__(self.worker))

    def publish(self):
        self.worker.publish_status()
        return self.manager.update_worker_status.call_args.args[1]

    def test_missing_source_is_offline_even_in_standby_or_disabled(self):
        for enabled in (True, False):
            self.worker.cfg['server1_enabled'] = enabled
            with self.subTest(enabled=enabled):
                self.assertEqual(self.publish()['status'], 'offline')

    def test_sleep_requires_live_source_and_wake_is_online(self):
        self.worker.last_mqtt_data_ts = 1000
        self.worker.current_mqtt_bps = 1500
        self.assertEqual(self.publish()['status'], 'sleep')
        self.worker.cfg['server1_stream_active'] = True
        self.assertEqual(self.publish()['status'], 'online')

    def test_offline_does_not_republish_stale_gnss(self):
        payload = self.publish()
        self.assertIsNone(payload['nmea_data']['gga'])
        self.assertIsNone(payload['nmea_data']['gsa'])
        self.assertEqual(payload['gnss_stats']['satellites'], 0)
        self.assertEqual(payload['rtk_fix_status'], 'NO_FIX')

    def test_assessment_survives_sleep_and_source_loss(self):
        self.worker.last_mqtt_data_ts = 1000
        self.worker.current_mqtt_bps = 1500
        self.worker.satellites = {'G01': {'snr': 42, 'last_seen': 1000}}
        assessment = self.publish()['antenna_status']
        self.assertEqual(assessment['state'], 'OK')
        self.worker.last_mqtt_data_ts = 990
        self.worker.current_mqtt_bps = 0
        payload = self.publish()
        self.assertEqual(payload['antenna_status'], assessment)
        self.assertEqual(payload['nmea_data']['satellites'], {})
        self.assertEqual(payload['ntrip_stats']['rtcm_input_bps'], 0)
        self.assertEqual(payload['status'], 'offline')

    def test_fault_assessment_is_published_while_sleeping(self):
        self.worker.last_mqtt_data_ts = 1000
        self.worker.current_mqtt_bps = 30
        payload = self.publish()
        self.assertEqual(payload['status'], 'sleep')
        self.assertEqual(payload['antenna_status']['state'], 'FAULT')
        self.worker.last_mqtt_data_ts = 990
        self.assertEqual(self.publish()['antenna_status'], payload['antenna_status'])

    def test_no_data_does_not_invent_antenna_assessment(self):
        self.assertNotIn('antenna_status', self.publish())


if __name__ == '__main__':
    unittest.main()

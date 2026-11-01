"""Automatic edge monitoring from distinct chip samples, without a browser."""
import base64
import unittest
from test_receiver_base_readback import load


class AutomaticRfMonitorTests(unittest.TestCase):
    def setUp(self):
        self.scope = load()
        self.monitor = self.scope['GnssRfMonitor']()

    def payload(self, severity=1, cno=45, age=0):
        rf = bytearray(52)
        rf[1] = 2
        rf[5] = rf[29] = severity
        sat = bytearray(8 + 6 * 12)
        sat[4:6] = bytes([1, 6])
        for i in range(6):
            offset = 8 + i * 12
            sat[offset:offset + 3] = bytes([0, i + 1, cno])
            sat[offset + 8] = 5
        return dict(status='online',
                    gnss_rf=dict(source='ubx_mon_rf', age_seconds=age, payload_b64=base64.b64encode(rf).decode()),
                    gnss_satellites=dict(source='ubx_nav_sat', age_seconds=age, payload_b64=base64.b64encode(sat).decode()))

    def test_distinct_samples_matched_cno_and_warning_episodes(self):
        result = self.monitor.observe(self.payload(), 100)
        self.assertEqual(result['sample_count'], 1)
        result = self.monitor.observe(self.payload(age=2), 102)
        self.assertEqual(result['sample_count'], 1)  # Same receiver sample, new heartbeat.
        result = self.monitor.observe(self.payload(2, 40), 130)
        self.assertEqual(result['observed_seconds'], 30)
        self.assertEqual(result['samples'], [[30, 0, None], [0, 1, -5]])
        self.assertEqual(result['coincident_cno_drops'], 1)
        self.assertEqual(result['cno_delta_dbhz'], -5)
        self.assertEqual(result['matched_satellites'], 6)
        result = self.monitor.observe(self.payload(3, 40), 160)
        self.assertEqual(result['episodes'], 1)
        self.assertEqual(result['critical_samples'], 1)
        self.monitor.observe(self.payload(), 190)
        result = self.monitor.observe(self.payload(2), 220)
        self.assertEqual(result['episodes'], 2)

    def test_missing_unknown_disabled_and_stale_never_count_as_clear(self):
        for payload in ({}, self.payload(0), self.payload(age=46),
                        dict(self.payload(), status='offline'),
                        dict(self.payload(), gnss_receiver=dict(age_seconds=0, itfm_enabled=False))):
            self.assertIsNone(self.monitor.observe(payload, 100))
        self.monitor.observe(self.payload(2), 100)
        result = self.monitor.observe({}, 130)
        self.assertIsNone(result['current_severity'])
        self.assertEqual(result['warning_samples'], 1)
        self.assertIsNone(self.monitor.observe({}, 701))

    def test_quality_gaps_and_different_satellites_are_not_correlations(self):
        self.monitor.observe(self.payload(), 100)
        result = self.monitor.observe(self.payload(2, 30), 200)
        self.assertEqual(result['coincident_cno_drops'], 0)
        packet = self.payload(2, 20)
        sat = bytearray(base64.b64decode(packet['gnss_satellites']['payload_b64']))
        for i in range(6):
            sat[9 + i * 12] += 10
        packet['gnss_satellites']['payload_b64'] = base64.b64encode(sat).decode()
        result = self.monitor.observe(packet, 230)
        self.assertEqual(result['matched_satellites'], 0)
        self.assertEqual(result['coincident_cno_drops'], 0)

    def test_um_bounded_history_and_status_transport(self):
        for i in range(200):
            now = 1000 + i * 5
            packet = dict(status='online', gnss_rf=dict(source='unicore_rf', jamming=dict(
                source='unicore_freqjamstatusa', age_seconds=0, flags=[0, 1, 0])))
            result = self.monitor.observe(packet, now)
        self.assertLessEqual(len(self.monitor.samples), 120)
        self.assertEqual(result['observed_seconds'], 600)
        self.assertEqual(len(result['samples']), result['sample_count'])
        self.assertEqual(result['samples'][-1][0], 0)
        self.assertTrue(all(0 <= sample[0] <= 600 for sample in result['samples']))
        self.assertEqual(result['warning_samples'], result['sample_count'])
        self.assertEqual(result['quality_comparisons'], 0)
        self.scope['AUTOMATIC_RF_REPORT'] = result
        report = self.scope['receiver_quality_status'](now + 10)['gnss_rf_monitor']
        self.assertEqual(report['age_seconds'], 10)
        self.assertEqual(report['source'], 'unicore_rf')

    def test_refresh_generates_report_on_edge_without_ui(self):
        self.scope['LAST_UBX_RF'] = self.payload()['gnss_rf']
        now = self.scope['time'].time()
        self.scope['LAST_UBX_RF_TS'] = now
        self.scope['_update_automatic_rf_monitor']()
        report = self.scope['receiver_quality_status'](now + 1)['gnss_rf_monitor']
        self.assertEqual(report['sample_count'], 1)
        self.assertLess(report['age_seconds'], 2)

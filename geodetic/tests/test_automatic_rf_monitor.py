"""Automatic edge monitoring from distinct chip samples, without a browser."""
import base64
import unittest
import tempfile
from pathlib import Path
from test_receiver_base_readback import load


class AutomaticRfMonitorTests(unittest.TestCase):
    def setUp(self):
        self.scope = load()
        self.monitor = self.scope['GnssRfMonitor']()
        self.epoch = 0

    def payload(self, severity=1, cno=45, age=0):
        rf = bytearray(52)
        rf[1] = 2
        rf[5] = rf[29] = severity
        sat = bytearray(8 + 6 * 12)
        self.epoch += 1000
        sat[:4] = self.epoch.to_bytes(4, 'little')
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
        self.assertEqual([row[:3] for row in result['samples']], [[30, 0, None], [0, 1, -5]])
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

    def test_zero_median_preserves_individual_changes_and_repeated_epoch_is_unknown(self):
        self.monitor.observe(self.payload(), 100)
        packet = self.payload(2)
        sat = bytearray(base64.b64decode(packet['gnss_satellites']['payload_b64']))
        for i, value in enumerate((42,44,45,45,46,48)):
            sat[10 + i * 12] = value
        packet['gnss_satellites']['payload_b64'] = base64.b64encode(sat).decode()
        result = self.monitor.observe(packet,130)
        self.assertEqual(result['samples'][-1][2:], [0,6,4,-3,3])
        result = self.monitor.observe(packet,160)
        self.assertIsNone(result['samples'][-1][2])

    def test_history_survives_restart_is_retained_24_hours_and_handles_disk_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'history.sqlite3')
            monitor = self.scope['GnssRfMonitor'](path)
            monitor.observe(self.payload(),100000)
            result = monitor.observe(self.payload(2,40),100030)
            self.assertEqual(sum(row[1] for row in result['history_24h']['hours']),2)
            restored = self.scope['GnssRfMonitor'](path)
            result = restored.observe({},100100)
            self.assertEqual(result['sample_count'],0)
            self.assertEqual(sum(row[2] for row in result['history_24h']['hours']),1)
            self.assertIsNone(result['current_severity'])
            self.assertIsNone(restored.observe({},200000))
            broken = self.scope['GnssRfMonitor'](directory)
            with self.assertLogs(level='WARNING'):
                result = broken.observe(self.payload(),100000)
            self.assertEqual(result['sample_count'],1)
            self.assertTrue(result['history_error'])

    def test_rf_ranges_keep_duplicate_ids_and_separate_warning_from_signal_change(self):
        packet = self.payload(2)
        raw = bytearray(base64.b64decode(packet['gnss_rf']['payload_b64']))
        for offset, noise, agc, jam in ((4,100,6600,22),(28,50,6300,6)):
            raw[offset+12:offset+14] = noise.to_bytes(2,'little')
            raw[offset+14:offset+16] = agc.to_bytes(2,'little')
            raw[offset+16] = jam
        raw[29] = 1
        packet['gnss_rf']['payload_b64'] = base64.b64encode(raw).decode()
        self.monitor.observe(packet,100)
        raw[5] = 3
        raw[16:18] = (104).to_bytes(2,'little')
        raw[20] = 26
        packet['gnss_rf']['payload_b64'] = base64.b64encode(raw).decode()
        result = self.monitor.observe(packet,130)
        self.assertEqual(result['block_ranges'],[
            [1,0,2,1,1,100,104,6600,6600,22,26],
            [2,0,2,0,0,50,50,6300,6300,6,6]])
        self.assertEqual(result['quality_comparisons'],0)  # Same NAV-SAT epoch.
        self.assertEqual(result['warning_samples'],2)

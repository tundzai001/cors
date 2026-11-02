"""HPG 1.51 security readback and fixed-duration edge monitoring."""
import asyncio
import base64
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from test_receiver_base_readback import load
from test_ublox_firmware_commands import monver


def signal(jam=1, spoof=1, enabled=9):
    return bytes([2, enabled | jam << 1 | spoof << 4, 0, 2]) + struct.pack('<II', 1575420 | (jam == 2) << 24, 1227600)


class SecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.s = load()
        self.parse = self.s['_ubx_security_payload']

    def reading(self, jam=1, spoof=1):
        return dict(status='online', gnss_receiver={'itfm_supported': False},
                    gnss_security=dict(self.parse(signal(jam, spoof), 9), age_seconds=0))

    def test_wire_layout_frequencies_events_and_rejection(self):
        data = self.parse(signal(2, 3), 9)
        self.assertTrue(data['jam_enabled'])
        self.assertTrue(data['spoof_enabled'])
        self.assertEqual((data['jam_state'], data['spoof_state']), (2, 3))
        self.assertEqual(data['frequencies'], [{'khz':1575420,'jammed':True}, {'khz':1227600,'jammed':False}])
        log = b'\x01\x02' + bytes(6) + struct.pack('<IBB2x', 12, 4, 0) + struct.pack('<IBB2x', 0xffffffff, 6, 1)
        self.assertEqual(self.parse(log, 16)['events'][1], {'elapsed_seconds':0xffffffff,'detection':6,'event':1})
        for body, mid in ((b'',9),(signal()[:-1],9),(bytes([2,7,0,0]),9),
                          (bytes([2,0x79,0,0]),9),(bytes([2,9,0,17]),9),(log[:-1],16),(bytes([1,17])+bytes(6),16)):
            with self.assertRaises(ValueError): self.parse(body, mid)
        pack = self.s['_ubx_packet']
        packet = pack(0x27,9,signal())
        self.assertEqual(self.s['_base_ubx_payload'](packet,(0x27,9)),signal())
        with self.assertRaises(ValueError): self.s['_base_ubx_payload'](packet[:-1]+bytes([packet[-1]^1]),(0x27,9))

    async def test_quality_polls_security_only_on_new_firmware_and_reports_timeout(self):
        pack = self.s['_ubx_packet']
        async def respond(wire, key):
            if key == (10,4): return pack(10,4,monver('1.51'))
            if key == (0x27,9): return pack(*key,signal(2))
            if key == (0x27,16): return pack(*key,b'\x01\0'+bytes(6))
            raise TimeoutError('test missing response')
        reader = SimpleNamespace(query_base=AsyncMock(side_effect=respond))
        agent = SimpleNamespace(detected_chip={'type':'Ublox'})
        await self.s['refresh_receiver_quality'](agent, reader)
        now = self.s['time'].time()
        status = self.s['receiver_quality_status'](now)
        self.assertEqual(status['gnss_security']['jam_state'],2)
        self.assertEqual(status['gnss_security_log']['events'],[])
        self.assertEqual(status['gnss_rf_monitor']['source'],'ubx_sec_sig')
        self.assertEqual(status['gnss_rf_monitor']['warning_samples'],1)
        reader.query_base.side_effect = TimeoutError('lost response')
        self.s['_refresh_ubx_receiver_info'] = AsyncMock()  # Retain known firmware while testing a failed SEC read.
        await self.s['refresh_receiver_quality'](agent, reader)
        self.assertIn('lost response',self.s['LAST_UBX_SECURITY']['error'])
        self.assertIsNone(self.s['AUTOMATIC_RF_REPORT']['current_severity'])
        self.assertIsNone(self.s['receiver_quality_status'](now+60)['gnss_security'])

    def test_security_states_and_no_legacy_fallback(self):
        for jam, spoof, expected in ((1,1,0),(2,1,1),(1,2,1),(1,3,2)):
            monitor = self.s['GnssRfMonitor']()
            report = monitor.observe(self.reading(jam,spoof),100)
            self.assertEqual(report['current_severity'],expected)
            self.assertEqual(report['jamming_samples'],int(jam==2))
            self.assertEqual(report['spoofing_samples'],int(spoof>=2))
        for data in (self.reading(0,0), dict(self.reading(),status='offline'),
                     dict(self.reading(),gnss_security={'source':'ubx_sec_sig','age_seconds':46}),
                     {'gnss_receiver':{'itfm_supported':False},'gnss_rf':{'source':'ubx_mon_rf'}}):
            self.assertIsNone(self.s['GnssRfMonitor']().observe(data,100))

    def test_next_period_resets_counts_preserves_archive_and_skips_empty_periods(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = self.s['GnssRfMonitor'](str(Path(directory)/'history.sqlite3'))
            monitor.observe(self.reading(2),1000)
            self.assertEqual(monitor.observe(self.reading(),1595)['observed_seconds'],595)
            report = monitor.observe(self.reading(),1600)
            self.assertEqual((report['window_started_at'],report['window_ends_at']), (1600,2200))
            self.assertEqual((report['sample_count'],report['warning_samples'],report['observed_seconds']), (1,0,0))
            self.assertEqual(sum(row[1] for row in report['history_24h']['hours']),3)
            report = monitor.observe(self.reading(),3500)
            self.assertEqual(report['window_started_at'],3400)
            self.assertEqual(report['observed_seconds'],100)
            self.assertEqual(report['sample_count'],1)

    def test_archive_survives_firmware_diagnostic_source_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/'history.sqlite3')
            monitor = self.s['GnssRfMonitor'](path)
            rf = bytearray(28)
            rf[1] = 1
            rf[5] = 2
            monitor.observe({'status':'online', 'gnss_rf':{
                'source':'ubx_mon_rf', 'age_seconds':0,
                'payload_b64':base64.b64encode(rf).decode()}},1000)
            restarted = self.s['GnssRfMonitor'](path)
            report = restarted.observe(self.reading(),1100)
            self.assertEqual(report['source'],'ubx_sec_sig')
            self.assertEqual(sum(row[1] for row in report['history_24h']['hours']),2)
            self.assertEqual(sum(row[2] for row in report['history_24h']['hours']),1)

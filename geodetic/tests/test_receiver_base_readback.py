"""Exercise the agent's real parser, UART query queue and UI status without hardware."""
import ast
import asyncio
import base64
import copy
import json
import logging
import math
import statistics
import sqlite3
from collections import deque
from pathlib import Path
from queue import Queue, Empty, Full
import re
import struct
import subprocess
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

SOURCE = Path(__file__).resolve().parents[1] / 'agent_universal.py'
NAMES = {'GNSSReader', '_ubx_packet', '_ecef_to_llh_rad', '_base_ubx_payload',
         '_base_ecef_position', '_base_ubx_position', '_base_unicore_fields',
         'refresh_receiver_base_status', 'receiver_base_system_info', '_crc24q',
         'is_valid_rtcm3_packet', '_ubx_navigation_quality', 'refresh_receiver_quality',
         'receiver_quality_status', '_ubx_dop', '_ubx_satellite_payload', '_ubx_rf_payload', '_unicore_rf_log', '_ubx_version_payload', '_refresh_ubx_receiver_info',
         'GnssRfMonitor', '_rf_monitor_age', '_rf_monitor_decode', '_update_automatic_rf_monitor'}


def load():
    scope = dict(asyncio=asyncio, threading=threading, Queue=Queue, Empty=Empty, Full=Full,
                 time=time, logging=logging, math=math, struct=struct, re=re, copy=copy, base64=base64, statistics=statistics, sqlite3=sqlite3, deque=deque,
                 PARSER_DEBUG_ENABLED=False, PARSER_DEBUG_INTERVAL_SECONDS=30,
                 dispatch_rtcm_data=Mock(), dispatch_nmea_data=Mock())
    nodes = [n for n in ast.parse(SOURCE.read_text()).body if getattr(n, 'name', None) in NAMES]
    assert len(nodes) == len(NAMES)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), scope)
    scope['AUTOMATIC_RF_MONITOR'] = scope['GnssRfMonitor']()
    return scope


def unicore(body, *, xor=False):
    crc = 0
    for byte in (body if xor else body[1:]).encode('ascii'):
        crc ^= byte
        if not xor:
            for _ in range(8):
                crc = (crc >> 1) ^ (0xedb88320 if crc & 1 else 0)
    return f'{body}*{crc:02X}'.encode() if xor else f'{body}*{crc:08x}'.encode()


class ReadbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.s = load()
        self.reader = self.s['GNSSReader'](Mock(), 'USB', 38400)
        self.reader.is_alive = lambda: True
        self.agent = SimpleNamespace(detected_chip={'type': 'Ublox'},
            config={'base_config': {'base_setup_method': 'SURVEY_IN'}}, save_config=Mock())
        self.agent.get_base_config = lambda: self.agent.config['base_config']
        self.mode = bytes.fromhex('0000010000000000000000000000000000000000000000007800000050C300000000000000000000')
        self.svin = bytes.fromhex('00000000605BDE1AEB0B0000C77A58F644EE25227C46970D1627F0004DC30000A20B000001000000')
        self.pack = self.s['_ubx_packet']

    async def test_receiver_version_and_running_itfm(self):
        payload = b'EXT CORE 1.00'.ljust(30, b'\0') + b'00190000'.ljust(10, b'\0') + b'FWVER=HPG 1.13'.ljust(30, b'\0') + b'PROTVER=27.12'.ljust(30, b'\0')
        for enabled in (0, 1):
            self.reader.query_base = AsyncMock(side_effect=[self.pack(10,4,payload), self.pack(6,0x8b,b'\x01\0\0\0' + struct.pack('<I',0x1041000d) + bytes([enabled]))])
            await self.s['_refresh_ubx_receiver_info'](self.reader)
            result = self.s['receiver_quality_status'](time.time())['gnss_receiver']
            self.assertEqual(base64.b64decode(result['payload_b64']), payload)
            self.assertEqual(result['itfm_enabled'], bool(enabled))
            self.assertEqual(self.reader.query_base.call_args_list[1].args[0], self.pack(6,0x8b,bytes(4)+struct.pack('<I',0x1041000d)))
            self.assertIsNone(self.s['receiver_quality_status'](time.time()+46)['gnss_receiver'])
        self.reader.query_base = AsyncMock(side_effect=[TimeoutError('MON-VER timeout'), ValueError('VALGET'), self.pack(6,0x39,struct.pack('<II',0x80000000,0))])
        await self.s['_refresh_ubx_receiver_info'](self.reader)
        result = self.s['LAST_UBX_RECEIVER_INFO']
        self.assertEqual(result['itfm_enabled'],True)
        self.assertIn('timeout',result['error'])
        self.reader.query_base = AsyncMock(side_effect=TimeoutError('timeout'))
        await self.s['_refresh_ubx_receiver_info'](self.reader)
        self.assertIsNone(self.s['LAST_UBX_RECEIVER_INFO']['itfm_enabled'])
        for bad in (b'', payload[:-1], bytes(40), b'\xff'+payload[1:]):
            with self.assertRaises(ValueError): self.s['_ubx_version_payload'](bad)

    async def refresh(self, packets):
        self.reader.query_base = AsyncMock(side_effect=packets)
        return await self.s['refresh_receiver_base_status'](self.agent, self.reader)

    def system(self):
        return self.s['receiver_base_system_info'](self.agent)

    async def test_rtcm_only_navigation_poll_units_and_expiry(self):
        pvt = bytearray(92)
        pvt[20:24] = bytes([5, 1, 0, 35])  # Time-only base, valid.
        struct.pack_into('<I', pvt, 40, 4080)
        struct.pack_into('<H', pvt, 76, 123)
        dop = bytearray(18)
        struct.pack_into('<H', dop, 6, 120)
        struct.pack_into('<H', dop, 12, 85)
        self.reader.query_base = AsyncMock(side_effect=[self.pack(1, 7, pvt), self.pack(1, 4, dop), self.pack(1, 0x35, b'\0\0\0\0\1\0\0\0')])
        await self.s['refresh_receiver_quality'](self.agent, self.reader)
        self.reader.query_base.assert_any_await(b'\xb5\x62\x01\x07\x00\x00\x08\x19', (1, 7))
        self.reader.query_base.assert_any_await(b'\xb5\x62\x01\x04\x00\x00\x05\x10', (1, 4))
        now = max(self.s['LAST_RECEIVER_QUALITY_TS'], self.s['LAST_UBX_DOP_TS'])
        status = self.s['receiver_quality_status'](now)
        self.assertAlmostEqual(status['gnss_dop']['hdop'], 0.85)
        self.assertAlmostEqual(status['gnss_dop']['pdop'], 1.20)
        self.assertEqual(status['hacc'], 4080)
        self.assertAlmostEqual(status['gnss_quality']['hacc_m'], 4.08)
        self.assertEqual(status['gnss_quality']['satellites'], 35)
        self.assertAlmostEqual(status['gnss_quality']['pdop'], 1.23)
        self.assertIsNone(self.s['receiver_quality_status'](now + 46)['gnss_quality'])
        self.assertIsNone(self.s['receiver_quality_status'](now + 46)['gnss_dop'])
        with self.assertRaises(ValueError):
            self.s['_ubx_dop'](dop[:-1])
        # Invalid fix actively clears the old good reading. Never replace it with HDOP.
        pvt[21] = 0
        self.reader._parse_buffer(bytearray(self.pack(1, 7, pvt)))
        self.assertIsNone(self.s['receiver_quality_status'](time.time())['hacc'])
        pvt[21] = 1; pvt[78] = 1
        self.assertIsNone(self.s['_ubx_navigation_quality'](pvt)['hacc_m'])
        with self.assertRaises(ValueError):
            self.s['_ubx_navigation_quality'](pvt[:-1])
        self.s.update(LAST_RECEIVER_QUALITY=None, LAST_GST_HACC_M=4.08, LAST_GST_TS=now)
        gst = self.s['receiver_quality_status'](now)
        self.assertEqual(gst['hacc'], 4080)
        self.assertAlmostEqual(gst['gnss_quality']['hacc_m'], 4.08)
        self.assertEqual(gst['gnss_quality']['source'], 'nmea_gst')
        self.assertIsNone(self.s['receiver_quality_status'](now + 16)['hacc'])

    async def test_navsat_poll_large_frame_and_expiry(self):
        pvt = bytearray(92); pvt[20:24] = bytes([5, 1, 0, 28])
        sat = bytearray(8 + 28 * 12)
        sat[4:6] = bytes([1, 28])
        for index in range(28):
            offset = 8 + index * 12
            struct.pack_into('<BBBbhhI', sat, offset, 0, index + 1, 40 + index % 10,
                             45, index * 12, 0, 0x80F)
        packets = [self.pack(1, 7, pvt), self.pack(1, 4, bytes(18)), self.pack(1, 0x35, sat)]
        self.reader.query_base = AsyncMock(side_effect=packets)
        await self.s['refresh_receiver_quality'](self.agent, self.reader)
        self.reader.query_base.assert_any_await(b'\xb5\x62\x01\x35\x00\x00\x36\xa3', (1, 0x35))
        now = self.s['LAST_UBX_SATELLITES_TS']
        snapshot = self.s['receiver_quality_status'](now)['gnss_satellites']
        self.assertEqual(base64.b64decode(snapshot['payload_b64']), sat)
        self.assertIsNone(self.s['receiver_quality_status'](now + 46)['gnss_satellites'])
        self.reader._parse_buffer(bytearray(self.pack(1, 7, pvt)))
        self.assertEqual(self.s['receiver_quality_status'](time.time())['gnss_satellites']['payload_b64'], snapshot['payload_b64'])
        self.assertEqual(self.reader._parse_buffer(bytearray(packets[-1])), b'')
        for invalid in (sat[:-1], bytes(8), bytes(8 + 129 * 12)):
            with self.assertRaises(ValueError):
                self.s['_ubx_satellite_payload'](invalid)
        sat[20:22] = sat[8:10]
        with self.assertRaises(ValueError):
            self.s['_ubx_satellite_payload'](sat)

    async def test_dop_failure_does_not_destroy_pvt(self):
        pvt = bytearray(92)
        pvt[20:24] = bytes([5, 1, 0, 28])
        struct.pack_into('<H', pvt, 76, 123)
        self.reader.query_base = AsyncMock(side_effect=[self.pack(1, 7, pvt), TimeoutError('DOP timeout'), self.pack(1, 0x35, b'\0\0\0\0\1\0\0\0')])
        await self.s['refresh_receiver_quality'](self.agent, self.reader)
        status = self.s['receiver_quality_status'](time.time())
        self.assertEqual(status['gnss_quality']['satellites'], 28)
        self.assertIsNone(status['gnss_dop'])
        corrupt = bytearray(self.pack(1, 4, bytes(18))); corrupt[-1] ^= 1
        with self.assertRaises(ValueError):
            self.s['_base_ubx_payload'](corrupt, (1, 4))

    async def test_unicore_rtcm_only_bestnav_accuracy(self):
        self.agent.detected_chip['type'] = 'Unicorecomm'
        mode = unicore('#MODE,81,GPS;MODE BASE 21 105 5', xor=True)
        best = unicore('#BESTNAVA,97,GPS;SOL_COMPUTED,FIXEDPOS,21,105,5,0,WGS84,0.3,0.4,0.6')
        await self.refresh([mode, best])
        status = self.s['receiver_quality_status'](time.time())
        self.assertEqual(status['gnss_quality']['source'], 'unicore_bestnav_sigma')
        self.assertAlmostEqual(status['gnss_quality']['hacc_m'], 0.5)
        self.assertEqual(status['hacc'], 500)
        self.reader.query_base.assert_any_await(b'\r\nBESTNAVA\r\n', b'#BESTNAVA')

    async def test_rf_poll_validation_expiry_and_partial_failure(self):
        rf = bytearray(4 + 2 * 24); rf[1] = 2
        for i in range(2):
            o = 4 + i * 24
            rf[o:o+4] = bytes([i, i+1, 2, 1])
            struct.pack_into('<HHB', rf, o + 12, 80+i, 4000+i, 12+i)
        span = bytearray(4 + 2 * 272); span[1] = 2
        for i in range(2):
            o = 4 + i * 272
            span[o:o+256] = bytes(range(256))
            struct.pack_into('<III', span, o + 256, 25600000, 100000, 1575420000-i*350000000)
            span[o+268] = 30
        self.reader.query_base = AsyncMock(side_effect=[TimeoutError('PVT'), TimeoutError('DOP'),
            TimeoutError('SAT'), self.pack(10, 0x38, rf), self.pack(10, 0x31, span)])
        await self.s['refresh_receiver_quality'](self.agent, self.reader)
        self.reader.query_base.assert_any_await(self.pack(10, 0x38), (10, 0x38))
        self.reader.query_base.assert_any_await(self.pack(10, 0x31), (10, 0x31))
        now = time.time()
        status = self.s['receiver_quality_status'](now)
        self.assertEqual(base64.b64decode(status['gnss_rf']['payload_b64']), rf)
        self.assertEqual(base64.b64decode(status['gnss_spectrum']['payload_b64']), span)
        self.assertIsNone(status['gnss_quality'])
        self.assertIsNone(self.s['receiver_quality_status'](now+46)['gnss_rf'])
        self.assertIsNone(self.s['receiver_quality_status'](now+46)['gnss_spectrum'])
        for message_id, payload in [(0x38, rf), (0x31, span)]:
            for invalid in [payload[:-1], bytes([1])+payload[1:], bytes([0, 5, 0, 0])]:
                with self.assertRaises(ValueError): self.s['_ubx_rf_payload'](invalid, message_id)
            corrupt = bytearray(self.pack(10, message_id, payload)); corrupt[-1] ^= 1
            with self.assertRaises(ValueError): self.s['_base_ubx_payload'](corrupt, (10, message_id))
        invalid = bytearray(rf); invalid[28] = 0
        self.assertEqual(self.s['_ubx_rf_payload'](invalid, 0x38)['source'],'ubx_mon_rf')
        legacy = bytearray(rf); legacy[28]=0;struct.pack_into('<H',legacy,18,9000)
        self.assertEqual(base64.b64decode(self.s['_ubx_rf_payload'](legacy,0x38)['payload_b64']),legacy)
        invalid = bytearray(span); invalid[260:264] = bytes(4)
        with self.assertRaises(ValueError): self.s['_ubx_rf_payload'](invalid, 0x31)

    async def test_rf_error_preserved_instead_of_silent_null(self):
        self.reader.query_base = AsyncMock(side_effect=[TimeoutError('PVT'),TimeoutError('DOP'),TimeoutError('SAT'),TimeoutError('MON-RF timeout'),TimeoutError('SPAN')])
        await self.s['refresh_receiver_quality'](self.agent,self.reader)
        failure=self.s['receiver_quality_status'](time.time())['gnss_rf']
        self.assertEqual(failure['state'],'read_error');self.assertEqual(failure['error_kind'],'timeout')
        self.assertIn('MON-RF timeout',failure['error'])
        self.assertIsNone(self.s['receiver_quality_status'](time.time()+46)['gnss_rf'])
        raw = bytearray(28);raw[1]=1;raw[0]=1
        packet=self.pack(10,0x38,raw)
        self.reader.query_base = AsyncMock(side_effect=[TimeoutError('PVT'),TimeoutError('DOP'),TimeoutError('SAT'),packet,TimeoutError('SPAN')])
        await self.s['refresh_receiver_quality'](self.agent,self.reader)
        failure=self.s['receiver_quality_status'](time.time())['gnss_rf']
        self.assertEqual(failure['error_kind'],'invalid_response');self.assertIn('Invalid RF payload',failure['error'])
        self.assertEqual(base64.b64decode(failure['frame_b64']),packet)
        raw[0]=0;struct.pack_into('<H',raw,18,4000)
        self.reader.query_base = AsyncMock(side_effect=[TimeoutError('PVT'),TimeoutError('DOP'),TimeoutError('SAT'),self.pack(10,0x38,raw),TimeoutError('SPAN')])
        await self.s['refresh_receiver_quality'](self.agent,self.reader)
        result=self.s['receiver_quality_status'](time.time())['gnss_rf']
        self.assertIn('payload_b64',result);self.assertNotIn('last_error',result)

    async def test_unicore_rf_agc_jamming_and_fallback(self):
        self.agent.detected_chip['type'] = 'Unicorecomm'
        agc = b'#AGCA,65,GPS,FINE,2190,375570000,0,0,18,37;44,46,63,-1,-1,41,1,0,-1,-1*634f1e4b'
        jam = unicore('#FREQJAMSTATUSA,97,GPS,FINE,2190,365412000,0,0,18,14;SINGLE,255,2,10,1,0,0,0,0')
        self.reader.query_base = AsyncMock(side_effect=[agc, jam])
        await self.s['refresh_receiver_quality'](self.agent, self.reader)
        self.reader.query_base.assert_any_await(b'\r\nAGCA\r\n', b'#AGCA')
        self.reader.query_base.assert_any_await(b'\r\nFREQJAMSTATUSA\r\n', b'#FREQJAMSTATUSA')
        result = self.s['receiver_quality_status'](time.time())['gnss_rf']
        self.assertEqual(result['agc']['values'],[44,46,63,-1,-1,41,1,0,-1,-1])
        self.assertEqual(result['jamming']['ratios'],[255,10,0])
        self.assertEqual(result['jamming']['flags'],[2,1,0])
        self.assertIsNone(self.s['receiver_quality_status'](time.time()+46)['gnss_rf'])
        overall = unicore('#JAMSTATUSA,97,GPS,FINE,2190,365412000,0,0,18,14;SINGLE,0,0,0,0')
        self.reader.query_base = AsyncMock(side_effect=[TimeoutError('AGC'), TimeoutError('per-band unsupported'), overall])
        await self.s['refresh_receiver_quality'](self.agent, self.reader)
        self.assertEqual(self.s['receiver_quality_status'](time.time())['gnss_rf']['jamming']['source'],'unicore_jamstatusa')
        for invalid in [agc[:-1]+b'0', unicore('#AGCA,65,GPS;44,46'), unicore('#AGCA,65,GPS;256,1,2,3,4,5,6,7,8,9'),
                        unicore('#JAMSTATUSA,65,GPS;SINGLE,1,3,0,0')]:
            with self.assertRaises(ValueError): self.s['_unicore_rf_log'](invalid)

    async def test_backend_compaction_keeps_quality_and_null_clear(self):
        from typing import Any, Optional
        source = SOURCE.parents[1] / 'cors_dashboard/backend/app/station_runtime.py'
        node = next(n for n in ast.parse(source.read_text()).body
                    if getattr(n, 'name', None) == '_compact_status_payload')
        scope = dict(Any=Any, Optional=Optional, STATUS_PAYLOAD_COMPACT_ENABLED=True,
                     STATUS_ALLOW_HEAVY_FIELDS=False, buffer_metrics={'status_payload_compacted_count': 0})
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), scope)
        for quality in ({'hacc_m': 4.08, 'source': 'ubx_nav_pvt', 'age_seconds': 0}, None):
            result = scope['_compact_status_payload']({'serial': 'PI', 'gnss_quality': quality, 'gnss_dop': quality, 'gnss_satellites': quality, 'gnss_rf': quality, 'gnss_spectrum': quality, 'gnss_receiver': quality})
            self.assertEqual(result['gnss_receiver'], quality)
            self.assertEqual(result['gnss_rf'], quality)
            self.assertEqual(result['gnss_spectrum'], quality)
            self.assertIn('gnss_satellites', result)
            self.assertEqual(result['gnss_satellites'], quality)
            self.assertIn('gnss_dop', result)
            self.assertEqual(result['gnss_dop'], quality)
            self.assertIn('gnss_quality', result)
            self.assertEqual(result['gnss_quality'], quality)

    async def test_checksum_and_hpposllh_offsets(self):
        pvt = bytearray(92)
        pvt[20:24] = bytes([3, 1, 0, 35])
        corrupt = bytearray(self.pack(1, 7, pvt)); corrupt[-1] ^= 1
        self.reader._parse_buffer(corrupt)
        self.assertNotIn('LAST_RECEIVER_QUALITY', self.s)
        self.assertEqual(self.reader.parse_errors, 1)
        hp = bytearray(36)
        struct.pack_into('<I', hp, 4, 123456000)  # iTOW must not become longitude.
        struct.pack_into('<iii', hp, 8, 1057866572, 210849638, -8115)
        struct.pack_into('<bbb', hp, 24, 4, -2, -4)
        self.reader._parse_buffer(bytearray(self.pack(1, 0x14, hp)))
        lat, lon, alt = self.s['LAST_HPPOSLLH_COORD']
        self.assertAlmostEqual(lat, 21.084963798, places=9)
        self.assertAlmostEqual(lon, 105.786657204, places=9)
        self.assertAlmostEqual(alt, -8.1154, places=4)
        before = self.s['LAST_HPPOSLLH_TS']
        hp[3] = 1  # invalidLlh
        self.reader._parse_buffer(bytearray(self.pack(1, 0x14, hp)))
        self.assertEqual(self.s['LAST_HPPOSLLH_TS'], before)

    async def test_real_device_survey_completion_and_persistence(self):
        result = await self.refresh([self.pack(6, 0x71, self.mode), self.pack(1, 0x3b, self.svin)])
        self.assertEqual(result['elapsed_seconds'], 3051)
        self.assertAlmostEqual(result['mean_accuracy_m'], 4.9997)
        self.assertEqual(self.system()['survey_in_status']['state'], 'base_ready')
        self.assertEqual(result['bestpos']['position_type'], 'FIXEDPOS')
        self.assertEqual(result['bestpos']['datum'], 'WGS84')
        self.agent.save_config.assert_called_once()
        await self.refresh([self.pack(6, 0x71, self.mode), self.pack(1, 0x3b, self.svin)])
        self.agent.save_config.assert_called_once()
        self.assertEqual(self.system()['receiver_base_readback']['revision'], 2)
        self.agent.receiver_base_readback_at -= 46
        self.assertEqual(self.system()['survey_in_status']['state'], 'awaiting_readback')
        self.assertIn('survey_in_result', self.system())

    async def test_active_survey_and_fixed_lla_and_rover(self):
        active = bytearray(self.svin)
        active[36:38] = b'\x00\x01'
        result = await self.refresh([self.pack(6, 0x71, self.mode), self.pack(1, 0x3b, active)])
        self.assertEqual(result['bestpos']['position_type'], 'SURVEY-IN-ESTIMATE')
        self.assertEqual(self.system()['survey_in_status']['state'], 'waiting_base')
        self.assertNotIn('survey_in_result', self.agent.config)
        fixed = bytearray(40)
        fixed[2:4] = b'\x02\x01'
        struct.pack_into('<iii', fixed, 4, 210849876, 1057867141, 291)
        fixed[16:19] = bytes([12, 88, 94])
        result = await self.refresh([self.pack(6, 0x71, fixed)])
        self.assertEqual(result['bestpos']['lat'], repr(21.084987612))
        self.assertEqual(result['bestpos']['alt'], '2.9194')
        self.assertEqual(result['mode_kind'], 'base')
        self.assertEqual(self.system()['survey_in_status']['state'], 'not_monitoring')
        result = await self.refresh([self.pack(6, 0x71, bytes(40))])
        self.assertEqual(result['mode_kind'], 'rover')
        self.assertNotIn('bestpos', result)

    async def test_bad_checksum_timeout_and_unsupported_chip_fail(self):
        for packets in ([self.pack(6, 0x71, self.mode)[:-1] + b'\xff'], [TimeoutError('UART timeout')]):
            with self.assertRaises((ValueError, TimeoutError)):
                await self.refresh(packets)
            self.assertIn('error', self.system()['receiver_base_readback'])
            self.assertNotEqual(self.system()['survey_in_status']['state'], 'base_ready')
        self.agent.detected_chip['type'] = 'UNKNOWN'
        with self.assertRaises(RuntimeError):
            await self.refresh([])

    async def test_unicore_single_is_not_completion_and_raw_keeps_precision(self):
        self.agent.detected_chip['type'] = 'Unicorecomm'
        mode = unicore('#MODE,81,GPS,FINE,2230,547967000,0,0,18,518;MODE BASE TIME 120 0 0,', xor=True)
        body = '#BESTNAVA,97,GPS,FINE,2294,472312000,0,0,18,16;SOL_COMPUTED,{},21.08496387579,105.78665721701,5.6879,0.0000,WGS84'
        single = unicore(body.format('SINGLE'))
        result = await self.refresh([mode, single])
        self.assertEqual(result['bestpos']['raw'], single.decode())
        self.assertEqual(self.system()['survey_in_status']['state'], 'waiting_base')
        self.assertNotIn('survey_in_result', self.agent.config)
        fixed = unicore(body.format('FIXEDPOS'))
        await self.refresh([mode, fixed])
        self.assertEqual(self.system()['survey_in_status']['state'], 'base_ready')
        self.agent.save_config.assert_called_once()
        changed_header = unicore(body.replace('472312000', '472313000').format('FIXEDPOS'))
        await self.refresh([mode, changed_header])
        self.agent.save_config.assert_called_once()  # Header time must not rewrite saved config.
        with self.assertRaises(TimeoutError):
            await self.refresh([mode, TimeoutError('BESTNAV timeout')])
        self.assertEqual(self.system()['receiver_base_readback']['mode_kind'], 'survey_in')
        self.assertEqual(self.system()['survey_in_status']['state'], 'awaiting_readback')
        self.assertIn('survey_in_result', self.system())

    async def test_uart_queue_interleaves_fragmented_ubx_unicore_and_rtcm(self):
        raw = b'\xd3\x00\x03\x3e\xd0\x00'
        rtcm = raw + self.s['_crc24q'](raw).to_bytes(3, 'big')
        for key, response in (((6, 0x71), self.pack(6, 0x71, self.mode)),
                              (b'#MODE', unicore('#MODE,81,GPS;MODE BASE TIME 120 0 0', xor=True) + b'\r\n')):
            with self.subTest(key=key):
                ser = SimpleNamespace(write=Mock(side_effect=lambda data: len(data)), flush=Mock())
                task = asyncio.create_task(self.reader.query_base(b'poll', key))
                await asyncio.sleep(0)
                self.reader._service_base_query(ser)
                buffer = bytearray()
                # Feed single bytes: UBX sync, partial headers and mixed streams must survive.
                for byte in rtcm + response + rtcm:
                    buffer.append(byte)
                    buffer = self.reader._parse_buffer(buffer)
                actual = await task
                self.assertEqual(actual, response.strip())
                self.assertEqual(buffer, b'')
                ser.write.assert_called_once_with(b'poll')
        self.assertEqual(self.s['dispatch_rtcm_data'].call_count, 4)
        self.s['dispatch_nmea_data'].assert_not_called()

    async def test_cancelled_query_never_writes_and_short_write_fails(self):
        task = asyncio.create_task(self.reader.query_base(b'poll', (6, 0x71)))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        ser = SimpleNamespace(write=Mock(return_value=0), flush=Mock())
        self.reader._service_base_query(ser)
        ser.write.assert_not_called()
        task = asyncio.create_task(self.reader.query_base(b'poll', (6, 0x71)))
        await asyncio.sleep(0)
        self.reader._service_base_query(ser)
        with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
            await task

    async def test_query_timeout_and_queue_limit(self):
        task = asyncio.create_task(self.reader.query_base(b'poll', (6, 0x71)))
        await asyncio.sleep(0)
        with self.assertRaisesRegex(RuntimeError, 'queue is busy'):
            await self.reader.query_base(b'poll2', (1, 0x3b))
        request = self.reader._base_queries.queue[0]
        request['deadline'] = time.monotonic() - 1
        with self.assertRaises(TimeoutError):
            await task
        ser = SimpleNamespace(write=Mock(), flush=Mock())
        self.reader._service_base_query(ser)
        ser.write.assert_not_called()

    async def test_existing_dashboard_accepts_real_readback_coordinates(self):
        ubx = await self.refresh([self.pack(6, 0x71, self.mode), self.pack(1, 0x3b, self.svin)])
        self.agent.detected_chip['type'] = 'Unicorecomm'
        mode = unicore('#MODE,81,GPS;MODE BASE TIME 120 0 0', xor=True)
        best = unicore('#BESTNAVA,97,GPS;SOL_COMPUTED,FIXEDPOS,21.08496387579,105.78665721701,5.6879,0.0,WGS84')
        um = await self.refresh([mode, best])
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const context = {window: {}};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const positions = JSON.parse(fs.readFileSync(0, 'utf8'));
for (const position of positions) {
    const result = context.window.CorsReceiverCoordinates.fromReceiver(position);
    assert(result && result.isBaseFixed);
    assert(Number.isFinite(Number(result.lat)) && Number.isFinite(Number(result.lon)));
    assert(Number.isFinite(Number(result.alt)));
}
assert.strictEqual(context.window.CorsReceiverCoordinates.fromReceiver(positions[1]).lat, '21.08496387579');
'''
        helper = SOURCE.parents[1] / 'cors_dashboard/src/js/receiver-coordinates.js'
        subprocess.run(['node', '-e', script, str(helper)],
                       input=json.dumps([ubx['bestpos'], um['bestpos']]), text=True, check=True)

    async def test_command_reports_error_instead_of_false_success(self):
        nodes = [node for node in ast.parse(SOURCE.read_text()).body
                 if getattr(node, 'name', None) == '_process_command']
        scope = dict(self.s, AgentManager=object, mqtt=SimpleNamespace(Client=object),
                     seen_command_ids={}, current_state='ONLINE', _record_command_result=Mock(),
                     send_status=AsyncMock(), is_remote_locked=lambda: False,
                     _validate_command_payload=lambda *args: (True, ''),
                     _runtime_serial_baud=lambda *args: 38400)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), scope)
        for failure in (True, False):
            scope['seen_command_ids'].clear()
            scope['_record_command_result'].reset_mock()
            scope['refresh_receiver_base_status'] = AsyncMock(side_effect=TimeoutError('readback failed') if failure else None)
            now = int(time.time())
            command = dict(command='CHECK_BASE_STATUS', command_id='a' * 20, issued_at=now,
                           expires_at=now + 60, payload={})
            await scope['_process_command']('mqtt', command, self.agent, self.reader, None)
            self.assertEqual(scope['_record_command_result'].call_args.args[2], 'error' if failure else 'success')
            scope['refresh_receiver_base_status'].assert_awaited_once_with(self.agent, self.reader)

    async def test_periodic_polling_respects_command_and_auto_base_ownership(self):
        node = next(node for node in ast.parse(SOURCE.read_text()).body
                    if getattr(node, 'name', None) == 'status_publisher_task')
        for blocked in ('none', 'command', 'auto_base', 'locked'):
            clock = [0]
            async def sleep(_):
                clock[0] += 5
                if clock[0] > 65:
                    raise asyncio.CancelledError
            lock = asyncio.Lock()
            if blocked == 'command':
                await lock.acquire()
            refresh = AsyncMock()
            quality = AsyncMock()
            status = AsyncMock()
            self.agent.gnss_reader = self.reader
            scope = dict(AgentManager=object, mqtt=SimpleNamespace(Client=object), logging=logging,
                         asyncio=SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError),
                         time=SimpleNamespace(monotonic=lambda: clock[0]),
                         STATUS_PUBLISH_INTERVAL_SECONDS=5, send_status=status,
                         command_execution_lock=lock, is_remote_locked=lambda: blocked == 'locked',
                         active_auto_base_task=SimpleNamespace(done=lambda: False) if blocked == 'auto_base' else None,
                         refresh_receiver_base_status=refresh, refresh_receiver_quality=quality)
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), scope)
            with self.assertRaises(asyncio.CancelledError):
                await scope['status_publisher_task'](self.agent, None)
            self.assertEqual(refresh.await_count, 3 if blocked == 'none' else 0)
            self.assertEqual(quality.await_count, 3 if blocked == 'none' else 0)
            self.assertEqual(status.await_count, 13)  # Heartbeats continue even while polling is skipped.


if __name__ == '__main__':
    unittest.main()

"""Read-only verification on universal agents for u-blox and Unicore receivers."""
import ast
import base64
import copy
import importlib.util
from pathlib import Path
import struct
import secrets
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
from test_receiver_base_readback import load, unicore, SOURCE

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('verification_builder', ROOT / 'cors_dashboard/backend/app/command_builder.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ConfigurationVerificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.s = load()
        names = {'_gnss_verification_expected', '_parse_unicore_loglist', '_verify_unicore_configuration', 'verify_receiver_configuration'}
        nodes = [node for node in ast.parse(SOURCE.read_text()).body if getattr(node, 'name', None) in names]
        self.s['base64'] = base64
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), self.s)
        self.config = {'base_setup_method': 'FIXED_LLA', 'coords': {'lat': '21.123456789', 'lon': '105.123456789', 'alt': '30'},
                       'accuracy': '0.01', 'altitude_reference': 'ELLIPSOID',
                       'gnss_options': {'ports': ['UART1', 'UART2', 'USB'], 'output_mode': 'rtcm_only', 'msm_level': 'both'}}
        self.agent = SimpleNamespace(detected_chip={'type': 'Ublox'}, config={'base_config': self.config}, save_config=Mock())
        self.agent.get_base_config = lambda: self.config
        self.pack = self.s['_ubx_packet']
        self.reader = SimpleNamespace(query_base=AsyncMock())

    async def verify(self, payload):
        original = copy.deepcopy(self.agent.config)
        result = await self.s['verify_receiver_configuration'](self.agent, self.reader, payload, 'a' * 24)
        self.assertEqual(self.agent.config, original)
        self.agent.save_config.assert_not_called()
        return result

    def ublox_responses(self, payload, *, mismatch=False, malformed=False):
        mode = base64.b64decode(payload['tmode_b64'])
        output = base64.b64decode(payload['output_b64'])
        rates = dict(struct.iter_unpack('<IB', output))
        for port in payload.get('readback', {}).get('ports', []):
            for key, _ in struct.iter_unpack('<IB', base64.b64decode(port['output_b64'])):
                rates.setdefault(key, int(key >> 16 in (0x1072, 0x107a)))
        rates[0x2091031C] = 3  # SPI Galileo MSM7: actual setting outside desired ports.
        rates.update({0x10510003: 1, 0x10520005: 1, 0x10530005: 1, 0x10650001: 1, 0x10640006: 0})
        async def respond(wire, key):
            if key == (6, 0x71):
                self.assertEqual(wire, self.pack(6, 0x71))
                return self.pack(6, 0x71, mode)
            if key == (6, 0):
                self.assertIn(wire[6:-2], (b'\x01', b'\x02'))
                body = bytearray(20)
                body[0] = wire[6]
                struct.pack_into('<I', body, 8, 115200 if body[0] == 1 else 57600)
                return self.pack(6, 0, body)
            self.assertEqual(key, (6, 0x8b))
            self.assertEqual(wire[6:10], b'\0\0\0\0')
            keys = [key for (key,) in struct.iter_unpack('<I', wire[10:-2])]
            self.assertLessEqual(len(keys), 40)
            body = b'\x01\0\0\0' + b''.join(struct.pack('<IB', key, rates[key] ^ int(mismatch)) for key in reversed(keys))
            packet = self.pack(6, 0x8b, body)
            return packet[:-1] + bytes([packet[-1] ^ 1]) if malformed else packet
        self.reader.query_base.side_effect = respond

    async def test_ublox_selected_ports_all_batches_and_temporary_status(self):
        payload = builder.build_ublox_verification_payload(self.config)
        self.assertEqual(len(base64.b64decode(payload['output_b64'])), 93 * 5)
        self.ublox_responses(payload)
        result = await self.verify(payload)
        self.assertEqual(result['state'], 'match')
        self.assertEqual(self.reader.query_base.await_count, 8)
        self.assertEqual(result['groups']['protocols']['checked'], 12)
        self.assertEqual(result['groups']['rtcm']['checked'], 30)
        details = result['details']
        self.assertEqual([port['name'] for port in details['ports']], ['I2C', 'UART1', 'UART2', 'USB', 'SPI'])
        self.assertEqual(details['ports'][1]['baud'], 115200)
        self.assertEqual(details['ports'][2]['baud'], 57600)
        self.assertEqual(details['ports'][4]['values'][details['fields'].index('RTCM 1097')], 3)
        self.assertEqual(details['ports'][4]['enabled'], 0)
        self.assertEqual(details['fixed']['actual']['self_position']['lat'], '21.123456789')
        status = self.s['receiver_base_system_info'](self.agent)
        self.assertTrue(status['gnss_verification_supported'])
        self.assertFalse(status['gnss_verification_uart1'])
        self.assertEqual(status['gnss_verification']['command_id'], 'a' * 24)
        self.agent.gnss_verification_at -= 61
        self.assertIsNone(self.s['receiver_base_system_info'](self.agent)['gnss_verification'])
        self.assertIsNone(self.agent.gnss_verification)

    async def test_ublox_mismatch_and_bad_checksum_never_pass(self):
        payload = builder.build_ublox_verification_payload(self.config)
        for mismatch, malformed, expected in ((True, False, 'mismatch'), (False, True, 'unread')):
            self.ublox_responses(payload, mismatch=mismatch, malformed=malformed)
            self.assertEqual((await self.verify(payload))['state'], expected)

    async def test_unread_optional_port_is_not_reported_as_disabled(self):
        payload = builder.build_ublox_verification_payload(self.config)
        self.ublox_responses(payload)
        respond = self.reader.query_base.side_effect
        async def missing_spi(wire, key):
            if key == (6, 0x8b) and wire[10:14] == bytes.fromhex('01007a10'):
                raise TimeoutError('SPI readback unavailable')
            return await respond(wire, key)
        self.reader.query_base.side_effect = missing_spi
        result = await self.verify(payload)
        self.assertEqual(result['state'], 'match')
        self.assertEqual(result['details']['ports'][-1]['state'], 'unread')
        self.assertIsNone(result['details']['ports'][-1]['values'])

    async def test_disabled_selected_interface_is_a_mismatch(self):
        payload = builder.build_ublox_verification_payload(self.config)
        self.ublox_responses(payload)
        respond = self.reader.query_base.side_effect
        async def disabled_uart2(wire, key):
            reply = await respond(wire, key)
            if key == (6, 0x8b):
                body = bytearray(reply[6:-2])
                for offset in range(4, len(body), 5):
                    if int.from_bytes(body[offset:offset + 4], 'little') == 0x10530005:
                        body[offset + 4] = 0
                return self.pack(6, 0x8b, body)
            return reply
        self.reader.query_base.side_effect = disabled_uart2
        result = await self.verify(payload)
        self.assertEqual(result['state'], 'mismatch')
        self.assertEqual(result['details']['ports'][2]['enabled'], 0)

    def unicore_responses(self, payload, *, extra=False, wrong_port=False, bad_count=False):
        self.agent.detected_chip['type'] = 'Unicorecomm'
        port = 'COM2' if wrong_port else 'COM3'
        names = payload['logs'] + (['GPGGA'] if extra else [])
        body = '#UNILOGLIST,66,GPS,FINE,2203,447089000,0,0,18,33;\r\n< ' + str(len(names) + int(bad_count))
        body += ''.join(f'\r\n< {name} {port} 1' for name in names)
        self.log_packet = unicore(body)
        responses = {
            b'#MODE': unicore('#MODE,81,GPS,FINE,2230,547967000,0,0,18,518;MODE BASE,', xor=True),
            b'#BESTNAVA': unicore('#BESTNAVA,97,GPS,FINE,2230,547967000,0,0,18,518;SOL_COMPUTED,FIXEDPOS,21.123456789,105.123456789,11.5,18.5,WGS84,0'),
            b'#UNILOGLIST': self.log_packet,
        }
        async def respond(wire, key):
            self.assertIn(wire, (b'\r\nMODE\r\n', b'\r\nBESTNAVA\r\n', b'\r\nUNILOGLIST\r\n'))
            return responses[key]
        self.reader.query_base.side_effect = respond

    async def test_unicore_mode_ellipsoid_and_output_list(self):
        payload = builder.build_unicore_verification_payload(self.config)
        self.unicore_responses(payload)
        result = await self.verify(payload)
        self.assertEqual(result['state'], 'match')
        self.assertNotIn('protocols', result['groups'])
        self.assertEqual(result['groups']['diagnostics']['mismatched'], 0)
        self.assertIn('RTCM1094', payload['logs'])
        self.assertIn('RTCM1097', payload['logs'])
        self.assertEqual(payload['port'], 'COM3')
        self.assertEqual(result['details']['logs_state'], 'ok')
        self.assertEqual(result['details']['logs'][0]['port'], 'COM3')
        self.assertEqual(result['details']['fixed']['actual']['self_position']['alt'], 30)

    async def test_unicore_extra_nmea_wrong_port_truncation_and_unknown_port(self):
        payload = builder.build_unicore_verification_payload(self.config)
        for flags, state in (({'extra': True}, 'mismatch'), ({'wrong_port': True}, 'mismatch'), ({'bad_count': True}, 'unread')):
            self.unicore_responses(payload, **flags)
            self.assertEqual((await self.verify(payload))['state'], state)
        payload['port'] = 'CURRENT'
        self.unicore_responses(payload)
        self.assertEqual((await self.verify(payload))['state'], 'unread')

    async def test_unicore_multiline_query_uses_existing_uart_owner(self):
        payload = builder.build_unicore_verification_payload(self.config)
        self.unicore_responses(payload)
        reader = self.s['GNSSReader'](Mock(), 'USB', 38400)
        request = dict(key=b'#UNILOGLIST', deadline=time.monotonic() + 5, cancelled=False, reply=None)
        reader._base_query = request
        buffer = bytearray()
        for part in (self.log_packet[:60], self.log_packet[60:120], self.log_packet[120:] + b'\r\n'):
            buffer.extend(part)
            buffer = reader._parse_buffer(buffer)
            self.assertLessEqual(len(request['response_preview']), 512)
        self.assertEqual(request['reply'], self.log_packet + b'\r\n')
        self.assertEqual(len(self.s['_parse_unicore_loglist'](request['reply'])), len(payload['logs']))
        corrupt = self.log_packet[:-1] + bytes([self.log_packet[-1] ^ 1])
        with self.assertRaises(ValueError):
            self.s['_parse_unicore_loglist'](corrupt)

    async def test_pq01_counted_reply_without_crc_preserves_following_rtcm(self):
        names = ['GPGGA', 'GPRMC', 'RTCM1006', 'RTCM1033', 'RTCM1074', 'RTCM1077', 'RTCM1019',
                 'RTCM1084', 'RTCM1087', 'RTCM1020', 'RTCM1094', 'RTCM1097', 'RTCM1045',
                 'RTCM1124', 'RTCM1127', 'RTCM1042', 'RTCM1114', 'RTCM1117', 'RTCM1044']
        packet = b'#UNILOGLIST,95,GPS,FINE,2443,64798000,0,0,18,73;\r\n<\t19\r\n'
        packet += ''.join(f'<\t{name} COM{2 if index < 2 else 3} 1 \r\n'
                           for index, name in enumerate(names)).encode() + b'\r\n'
        raw = b'\xd3\x00\x03\x3e\xd0\x00'
        rtcm = raw + self.s['_crc24q'](raw).to_bytes(3, 'big')
        for chunk_size in (1, 17, len(packet) + len(rtcm)):
            reader = self.s['GNSSReader'](Mock(), 'USB', 38400)
            request = dict(key=b'#UNILOGLIST', deadline=time.monotonic() + 5, cancelled=False, reply=None)
            reader._base_query = request
            buffer = bytearray()
            self.s['dispatch_rtcm_data'].reset_mock()
            stream = packet + rtcm
            for offset in range(0, len(stream), chunk_size):
                buffer.extend(stream[offset:offset + chunk_size])
                buffer = reader._parse_buffer(buffer)
            self.assertEqual(request['reply'], packet)
            self.s['dispatch_rtcm_data'].assert_called_once_with(rtcm)
            self.assertEqual(buffer, b'')
        parsed = self.s['_parse_unicore_loglist'](packet)
        self.assertEqual(len(parsed), 19)
        self.assertEqual(sum(port == 'COM3' for port, _ in parsed), 17)
        self.assertNotIn(('COM3', 'GPGGA'), parsed)
        for invalid in (packet[:-2], packet.replace(b'<\t19', b'<\t20'),
                        packet.replace(b'COM2', b'COM9'), packet + rtcm):
            with self.assertRaises((ValueError, UnicodeError)):
                self.s['_parse_unicore_loglist'](invalid)
        payload = builder.build_unicore_verification_payload(self.config)
        self.unicore_responses(payload)
        respond = self.reader.query_base.side_effect
        async def reply(wire, key):
            return packet if key == b'#UNILOGLIST' else await respond(wire, key)
        self.reader.query_base.side_effect = reply
        result = await self.verify(payload)
        self.assertEqual(result['details']['logs_state'], 'ok')
        self.assertEqual(result['groups']['diagnostics']['mismatched'], 0)
        self.assertEqual(result['groups']['rtcm']['mismatched'], 3)  # Extra QZSS, not an unread result.

    async def test_unicore_failed_log_query_reports_reason_and_bounded_reply(self):
        payload = builder.build_unicore_verification_payload(self.config)
        for failure in ('timeout', 'count', 'checksum'):
            self.unicore_responses(payload, bad_count=failure == 'count')
            respond = self.reader.query_base.side_effect
            async def reply(wire, key):
                if key == b'#UNILOGLIST' and failure == 'timeout':
                    raise TimeoutError('Receiver readback timeout: UNILOGLIST')
                packet = await respond(wire, key)
                return packet[:-1] + b'Z' if key == b'#UNILOGLIST' and failure == 'checksum' else packet
            self.reader.query_base.side_effect = reply
            with self.assertLogs(level='WARNING') as captured:
                result = await self.verify(payload)
            self.assertEqual(result['state'], 'unread')
            self.assertEqual(result['groups']['fixed']['unread'], 0)
            self.assertEqual(result['groups']['rtcm']['unread'], 1)
            self.assertTrue(any(result['output_error'] in line for line in captured.output))
            self.assertTrue(any('state=unread' in line for line in captured.output))
            if failure != 'timeout':
                self.assertIn(repr(self.log_packet[:512])[:50], captured.output[0])

    async def test_invalid_expectations_are_rejected_before_queries(self):
        for payload in ({}, {'tmode_b64': '!', 'output_b64': 'AA=='}, {'tmode_b64': 'A' * 57, 'output_b64': 'AA=='}):
            with self.assertRaises((ValueError, KeyError)):
                await self.verify(payload)
        self.reader.query_base.assert_not_awaited()

    async def test_api_uses_live_capabilities_saved_config_and_correct_agent_routing(self):
        source = ROOT / 'cors_dashboard/backend/app/routers/devices.py'
        node = next(node for node in ast.parse(source.read_text()).body if getattr(node, 'name', None) == 'send_generic_command')
        node.decorator_list = []; node.returns = None; node.args.defaults = []
        for arg in node.args.args:
            arg.annotation = None
        class HTTPException(Exception):
            def __init__(self, status_code, detail):
                self.status_code, self.detail = status_code, detail
        device = SimpleNamespace(detected_chip_type='Ublox', base_config=self.config, system_info={}, user_id=1)
        cache = {'test': {'system_info': {'gnss_verification_supported': True}}}
        auth = SimpleNamespace(Role=SimpleNamespace(COORDINATOR='coordinator', ADMIN='admin'),
            Permission=SimpleNamespace(EDIT_CHIP_CONFIG='edit'), has_permission=lambda *_: True)
        dispatch = AsyncMock(return_value={'status': 'command_sent'})
        scope = dict(auth=auth, crud=SimpleNamespace(get_device_by_serial=AsyncMock(return_value=device), log_system_event=AsyncMock()),
                     HTTPException=HTTPException, command_builder=builder, secrets=secrets,
                     mqtt_handler=SimpleNamespace(device_telemetry_cache=cache), send_command_to_pi=dispatch)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(source), 'exec'), scope)
        user = SimpleNamespace(role='admin', username='test', id=1)
        command = SimpleNamespace(command='VERIFY_GNSS_CONFIG', payload={'coords': {'lat': 0}, 'output_b64': 'attacker'})
        for chip in ('Ublox', 'Unicorecomm'):
            for embedded in (False, True):
                device.detected_chip_type = chip
                cache['test']['system_info']['gnss_verification_uart1'] = embedded
                result = await scope['send_generic_command']('test', command, None, user)
                sent = dispatch.await_args.args[1]
                expected = (builder.build_ublox_verification_payload(self.config, route_to_uart1=embedded) if chip == 'Ublox'
                            else builder.build_unicore_verification_payload(self.config, current_port=embedded))
                self.assertEqual(sent['payload'], expected)
                self.assertEqual(result['command_id'], sent['command_id'])
        dispatch.reset_mock()
        auth.has_permission = lambda *_: False
        with self.assertRaises(HTTPException) as error:
            await scope['send_generic_command']('test', command, None, user)
        self.assertEqual(error.exception.status_code, 403)
        dispatch.assert_not_awaited()

        auth.has_permission = lambda *_: True
        cache['test']['system_info']['gnss_verification_supported'] = False
        with self.assertRaises(HTTPException) as error:
            await scope['send_generic_command']('test', command, None, user)
        self.assertEqual(error.exception.status_code, 409)
        dispatch.assert_not_awaited()

    async def test_signed_command_dispatch_and_busy_workflow_guard(self):
        names = {'_process_command', '_validate_command_payload'}
        nodes = [node for node in ast.parse(SOURCE.read_text()).body if getattr(node, 'name', None) in names]
        scope = dict(self.s, AgentManager=object, mqtt=SimpleNamespace(Client=object),
                     seen_command_ids={}, current_state='ONLINE', _record_command_result=Mock(),
                     send_status=AsyncMock(), is_remote_locked=lambda: False,
                     active_auto_base_task=None, verify_receiver_configuration=AsyncMock())
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), scope)
        now = int(time.time())
        command = dict(command='VERIFY_GNSS_CONFIG', command_id='v' * 24, issued_at=now,
                       expires_at=now + 60, payload=builder.build_ublox_verification_payload(self.config))
        await scope['_process_command']('mqtt', command, self.agent, self.reader, None)
        scope['verify_receiver_configuration'].assert_awaited_once_with(self.agent, self.reader, command['payload'], command['command_id'])
        self.assertEqual(scope['_record_command_result'].call_args.args[2], 'success')
        scope['verify_receiver_configuration'].reset_mock()
        scope['active_auto_base_task'] = SimpleNamespace(done=lambda: False)
        command['command_id'] = 'b' * 24
        with self.assertLogs(level='ERROR'):
            await scope['_process_command']('mqtt', command, self.agent, self.reader, None)
        scope['verify_receiver_configuration'].assert_not_awaited()
        self.assertEqual(scope['_record_command_result'].call_args.args[2], 'error')


if __name__ == '__main__':
    unittest.main()

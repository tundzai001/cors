"""Exercise receiver configuration, correction routing and cancellation without hardware."""
import ast
import asyncio
import base64
import copy
from decimal import Decimal, ROUND_DOWN
from collections import OrderedDict
import importlib.util
import logging
import math
import statistics
from pathlib import Path
from queue import Queue, Empty, Full
import re
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / 'geodetic/agent_universal.py'


def load(names, scope):
    nodes = [node for node in ast.parse(AGENT.read_text()).body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(AGENT), 'exec'), scope)
    return scope


async def yield_only(seconds):
    await asyncio.sleep(0)


class Receiver:
    """Small wire-level TMODE3 emulator: only valid UBX packets change mode."""
    def __init__(self, packet, *, reject=False, fragment=4096):
        self.packet = packet
        self.reject = reject
        self.fragment = fragment
        self.writes = []
        self.buffer = bytearray()
        self.timeout = 2
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    @property
    def in_waiting(self):
        return len(self.buffer)

    def reset_input_buffer(self):
        self.buffer.clear()

    def write(self, data):
        data = bytes(data)
        self.writes.append(data)
        if data[:2] != b'\xb5\x62':
            return len(data)
        length = int.from_bytes(data[4:6], 'little')
        self.assert_valid(data, length)
        if data[2:4] == b'\x06\x71':
            if length == 40 and not self.reject:
                self.packet = data
            elif length == 0:
                # Mix NMEA and ACK traffic with the actual receiver readback.
                self.buffer.extend(b'$GNGGA,test\r\n' + self.packet)
        return len(data)

    @staticmethod
    def assert_valid(data, length):
        if len(data) != length + 8:
            raise ValueError(f'Invalid UBX packet length: {len(data)} != {length + 8}')
        a = b = 0
        for byte in data[2:-2]:
            a = (a + byte) & 255
            b = (b + a) & 255
        if data[-2:] != bytes([a, b]):
            raise ValueError('Invalid UBX checksum')

    def flush(self):
        pass

    def read(self, count):
        count = min(count, self.fragment)
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result


class AutoBaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = load({'_ubx_packet', '_verify_ublox_tmode', '_is_rtk_usable_status',
                           '_get_fresh_outbound_gga', '_apply_rover_mode_to_chip', '_apply_base_mode_to_chip'},
                          dict(Decimal=Decimal, ROUND_DOWN=ROUND_DOWN, asyncio=SimpleNamespace(sleep=yield_only), time=time, logging=logging,
                               GNSSReader=object, serial_port_lock=threading.Lock(),
                               _runtime_serial_baud=lambda **kwargs: 115200))
        self.packet = self.scope['_ubx_packet']
        self.rover = self.packet(6, 0x71, bytes(40))
        self.reader = SimpleNamespace(pause=Mock(), resume=Mock())

    def fixed(self):
        spec = importlib.util.spec_from_file_location('command_builder', ROOT / 'cors_dashboard/backend/app/command_builder.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        return builder.build_base_fixed_lla_command('Ublox', 20.232041239, 105.978597812, 2.0, 0.01)[0]

    async def test_rover_opens_correction_input_and_correct_usb_gga(self):
        receiver = Receiver(self.fixed())
        self.scope['serial'] = SimpleNamespace(Serial=lambda *args, **kwargs: receiver)
        await self.scope['_apply_rover_mode_to_chip']('Ublox', 'USB', self.reader)
        self.assertEqual(receiver.packet[8:10], b'\0\0')
        keys = {int.from_bytes(p[10:14], 'little'): p[14]
                for p in receiver.writes if p[2:4] == b'\x06\x8a'}
        for key in (0x10730004, 0x10750004, 0x10770004, 0x10780002, 0x209100BD):
            self.assertEqual(keys[key], 1)
        self.assertEqual(keys[0x209102C0], 0)
        self.assertEqual(keys[0x20030001], 0)  # CFG-TMODE-MODE, not navigation settings.
        self.assertNotIn(0x20110001, keys)
        self.assertTrue(any(p[2:4] == b'\x06\x01' and p[6:8] == b'\xf5\x05' for p in receiver.writes))
        self.reader.resume.assert_called_once()

    async def test_base_writes_valid_mode_and_checks_exact_coordinates(self):
        receiver = Receiver(self.rover, fragment=3)
        self.scope['serial'] = SimpleNamespace(Serial=lambda *args, **kwargs: receiver)
        await self.scope['_apply_base_mode_to_chip']('Ublox', 'USB', 20.232041239, 105.978597812, 2, self.reader)
        self.assertEqual(receiver.packet[8:10], b'\x02\x01')
        self.assertTrue(any(p[2:4] == b'\x06\x71' and len(p) == 8 for p in receiver.writes))

    async def test_receiver_staying_rover_is_failure_and_reader_resumes(self):
        receiver = Receiver(self.rover, reject=True)
        self.scope['serial'] = SimpleNamespace(Serial=lambda *args, **kwargs: receiver)
        with self.assertRaisesRegex(RuntimeError, 'TMODE mismatch'):
            await self.scope['_apply_base_mode_to_chip']('Ublox', 'USB', 20, 105, 2, self.reader)
        self.reader.resume.assert_called_once()

    def test_readback_timeout_wrong_coordinates_and_nak(self):
        expected = self.fixed()
        receiver = Receiver(self.rover)
        with self.assertRaises(TimeoutError):
            self.scope['_verify_ublox_tmode'](receiver, expected, timeout=0)
        self.assertEqual(receiver.timeout, 2)
        other = bytearray(expected[6:-2])
        other[4] ^= 1
        receiver = Receiver(self.packet(6, 0x71, bytes(other)))
        with self.assertRaisesRegex(RuntimeError, 'coordinates differ'):
            self.scope['_verify_ublox_tmode'](receiver, expected)
        corrupt = bytearray(expected)
        corrupt[-1] ^= 1
        receiver = Receiver(bytes(corrupt) + self.rover)
        with self.assertRaisesRegex(RuntimeError, 'TMODE mismatch'):
            self.scope['_verify_ublox_tmode'](receiver, expected)
        receiver = Receiver(self.packet(5, 0, b'\x06\x71'))
        with self.assertRaisesRegex(RuntimeError, 'rejected'):
            self.scope['_verify_ublox_tmode'](receiver, expected)

    def test_float_and_stale_gga_are_not_accepted_as_fixed(self):
        self.assertTrue(self.scope['_is_rtk_usable_status']('RTK_FIXED'))
        self.assertFalse(self.scope['_is_rtk_usable_status']('RTK_FLOAT'))
        self.scope.update(LAST_RAW_GGA='$GNGGA,test', LAST_RAW_GGA_TS=time.time() - 10)
        self.assertIsNone(self.scope['_get_fresh_outbound_gga'](3))

    async def test_overlapping_transport_commands_do_not_share_serial_session(self):
        events = []
        async def execute(source, data, *args):
            events.append(source + ' start')
            await asyncio.sleep(0)
            events.append(source + ' end')
        scope = load({'process_command'}, dict(asyncio=asyncio,
            command_execution_lock=asyncio.Lock(), _process_command=execute,
            AgentManager=object, GNSSReader=object, mqtt=SimpleNamespace(Client=object)))
        await asyncio.gather(scope['process_command']('mqtt', {}, None, None, None),
                             scope['process_command']('websocket', {}, None, None, None))
        self.assertEqual(events, ['mqtt start', 'mqtt end', 'websocket start', 'websocket end'])

    async def test_cancelling_boot_auto_base_keeps_main_workers_running(self):
        main = next(node for node in ast.parse(AGENT.read_text()).body
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == 'main')
        wrapper = next(node for node in main.body
                       if isinstance(node, ast.AsyncFunctionDef) and node.name == 'run_startup_auto_base')
        scope = dict(asyncio=asyncio, logging=logging)
        async def auto_setup():
            await asyncio.Event().wait()
        exec(compile(ast.Module(body=[wrapper], type_ignores=[]), str(AGENT), 'exec'), scope)
        for cancel_before_start in (False, True):
            with self.subTest(cancel_before_start=cancel_before_start):
                startup = asyncio.create_task(auto_setup())
                background = asyncio.create_task(asyncio.Event().wait())
                if cancel_before_start:
                    startup.cancel()
                main_workers = asyncio.gather(scope['run_startup_auto_base'](startup), background)
                await asyncio.sleep(0)
                if not cancel_before_start:
                    startup.cancel()
                await asyncio.gather(startup, return_exceptions=True)
                await asyncio.sleep(0)
                self.assertFalse(main_workers.done(), 'Stopping Auto Base must not shut down the agent')
                background.cancel()
                await asyncio.gather(main_workers, return_exceptions=True)

    async def test_stop_awaits_old_task_cleanup_and_disables_boot_restart(self):
        events = []
        async def old_task():
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                events.append('old task finished restoring receiver')
        task = asyncio.create_task(old_task())
        await asyncio.sleep(0)
        client = SimpleNamespace(stop=Mock(), is_alive=lambda: False)
        scope = load({'_stop_auto_base', '_cleanup_inject_queue'}, dict(
            asyncio=asyncio, active_auto_base_task=task, active_auto_base_client=client,
            subscriber_lock=threading.Lock(), rtcm_subscribers=[], Empty=Empty,
            _compute_any_server_can_push=lambda *args, **kwargs: False))
        agent = SimpleNamespace(config={'auto_base_setup': {'enabled': True}}, save_config=Mock(), get_service_config=lambda: {})
        reader = SimpleNamespace(rtcm_inject_queue=Queue(), resume=Mock())
        reader.rtcm_inject_queue.put(b'old corrections')
        await scope['_stop_auto_base'](agent, reader)
        events.append('ready for new fixed configuration')
        self.assertTrue(task.done())
        self.assertEqual(len(events), 2)
        self.assertFalse(agent.config['auto_base_setup']['enabled'])
        self.assertTrue(reader.rtcm_inject_queue.empty())
        self.assertIsNone(scope['active_auto_base_task'])

    async def test_manual_commit_requires_receiver_readback_after_old_task_stops(self):
        for reject in (False, True):
            with self.subTest(reject=reject):
                receiver = Receiver(self.rover, reject=reject)
                old = {'base_setup_method': 'AUTO_CORS', 'coords': {'lat': 1, 'lon': 2, 'alt': 3}}
                agent = SimpleNamespace(config={'base_config': copy.deepcopy(old)},
                    detected_chip={'port': 'USB'}, save_config=Mock(), restart_services=Mock())
                stopped = AsyncMock()
                scope = load({'_process_command', '_ubx_packet', '_verify_ublox_tmode'}, dict(
                    asyncio=SimpleNamespace(sleep=yield_only), AgentManager=object, GNSSReader=object,
                    mqtt=SimpleNamespace(Client=object), logging=logging, time=time, re=re, copy=copy,
                    base64=base64, seen_command_ids=OrderedDict(), current_state='AUTO_SETUP_WAIT_FIX',
                    _record_command_result=Mock(), send_status=AsyncMock(), is_remote_locked=lambda: False,
                    _validate_command_payload=lambda *args: (True, ''), create_lock_file=lambda: True,
                    remove_lock_file=Mock(), _stop_auto_base=stopped, _mark_gnss_data_stale=Mock(),
                    _remember_output_mode_from_original_config=Mock(), _delay_marker_seconds=lambda cmd: None,
                    _runtime_serial_baud=lambda *args: 115200, serial_port_lock=threading.Lock()))
                def open_port(*args, **kwargs):
                    self.assertEqual(stopped.await_count, 1, 'Stop must finish before opening serial')
                    return receiver
                scope['serial'] = SimpleNamespace(Serial=open_port)
                params = {'base_setup_method': 'FIXED_LLA', 'coords': {'lat': 20, 'lon': 105, 'alt': 2}}
                now = int(time.time())
                command = dict(command='EXECUTE_RAW_COMMANDS', command_id='a' * 20, issued_at=now, expires_at=now + 60,
                    payload={'commands_b64': [base64.b64encode(self.fixed()).decode()]},
                    original_config={'mode': 'BASE', 'params': params})
                with self.assertLogs(level='INFO'):
                    await scope['_process_command']('mqtt', command, agent, self.reader, None)
                self.assertEqual(agent.config['base_config'], old if reject else params)
                self.assertEqual(agent.config['gnss_config_pending'], reject)
                self.assertEqual(scope['_record_command_result'].call_args.args[2], 'error' if reject else 'success')

    def test_real_reader_loop_writes_queued_corrections_to_serial(self):
        scope = load({'GNSSReader'}, dict(threading=threading, time=time, Queue=Queue,
            Empty=Empty, Full=Full, IS_RASPBERRY_PI=False, is_remote_locked=lambda: False,
            serial_port_lock=threading.Lock(), _mark_gnss_data_stale=Mock(), _telemetry_inc=Mock()))
        reader = object.__new__(scope['GNSSReader'])
        reader.port = 'USB'
        reader.baudrate = 115200
        reader._stop_event = threading.Event()
        reader._pause_event = threading.Event()
        reader._base_queries = Queue(maxsize=1)
        reader._base_query = None
        reader.rtcm_inject_queue = Queue()
        correction = b'\xd3\x00\x03correction'
        reader.rtcm_inject_queue.put(correction)
        reader.log = Mock()
        reader._log_parser_debug_if_due = Mock()
        reader.get_statistics = Mock(return_value={})
        class SerialPort(Receiver):
            def write(self, data):
                self.writes.append(bytes(data))
                reader._stop_event.set()
                return len(data)
        receiver = SerialPort(self.rover)
        scope['serial'] = SimpleNamespace(Serial=lambda *args, **kwargs: receiver,
            SerialException=IOError, tools=SimpleNamespace(list_ports=SimpleNamespace(
                comports=lambda: [SimpleNamespace(device='USB')])))
        reader.run()
        self.assertEqual(receiver.writes, [correction])
        self.assertEqual(reader._inject_bytes_total, len(correction))
        self.assertEqual(reader._inject_total, 1)
        self.assertTrue(receiver.closed)

    async def test_auto_base_failure_never_saves_a_completed_coordinate(self):
        for apply_error, review_only, host, k0 in ((None, False, 'other.example', 0.9996), (RuntimeError('receiver still Rover'), False, 'other.example', 0.9996), (None, True, 'vngeonet.vn', 0.9996), (None, True, '14.238.1.125', 0.9996), (None, True, '14.238.1.125', 0.9999)):
            with self.subTest(apply_error=apply_error, review_only=review_only, host=host, k0=k0):
                expected_failure = apply_error or (host == '14.238.1.125' and k0 != 0.9996)
                now = [1000.0]
                reader = SimpleNamespace(pause=Mock(), resume=Mock(), inject_rtcm=Mock())
                client = SimpleNamespace(start=Mock(), stop=Mock(), join=Mock())
                old = {'base_setup_method': 'FIXED_LLA', 'coords': {'lat': 1, 'lon': 2, 'alt': 3}}
                agent = SimpleNamespace(config={'base_config': old, 'auto_base_setup': {
                    'enabled': True, 'review_only': review_only, 'port': 2101, 'ip': host, 'l0_deg': 105, 'k0': k0,
                    'enable_itrf_vn2000_transform': host != '14.238.1.125'}},
                    detected_chip={'type': 'Ublox', 'port': 'USB'}, save_config=Mock(), log=Mock())
                phases = []
                scope = dict(Decimal=Decimal, ROUND_DOWN=ROUND_DOWN, AgentManager=object, GNSSReader=object, mqtt=SimpleNamespace(Client=object),
                    time=SimpleNamespace(time=lambda: now[0]), math=math, logging=logging, copy=copy,
                    DATUM_LOCK=threading.RLock(), _resolve_auto_base_location=lambda *args: {},
                    RTCMDatum=lambda: SimpleNamespace(l0=math.radians(105), k0=0.9996,
                        rtcm1021_update=True, rtcm1023_update=False, rtcm1025_update=False,
                        **dict.fromkeys(("dX", "dY", "dZ", "rX", "rY", "rZ", "kT", "resB", "resL", "resH"), 0)),
                    _is_valid_vn2000_projection_params=lambda datum: True,
                    _transform_itrf_to_vn2000_neh_and_llh=Mock(return_value=(2300000, 580000, 25, 20.100000000123456, 105.10000000098765, 2.1000123456789)),
                    active_auto_base_client=None, _cleanup_inject_queue=Mock(),
                    _apply_rover_mode_to_chip=AsyncMock(),
                    _apply_base_mode_to_chip=AsyncMock(side_effect=apply_error),
                    _set_auto_base_progress=lambda phase, **kwargs: phases.append(phase),
                    send_status=AsyncMock(), NTRIPClientWorker=lambda *args, **kwargs: client,
                    _plain_mean=statistics.mean, _datum_snapshot=lambda: SimpleNamespace(
                        l0=math.radians(105), k0=0.9996, rtcm1021_update=False,
                        rtcm1023_update=False, rtcm1025_update=False),
                    LAST_RAW_GGA='$GNGGA,test', LAST_RAW_GGA_TS=now[0],
                    LAST_GGA_FIX_STATUS='RTK_FIXED', LAST_GGA_COORD=(20.0, 105.0, 2.0))
                async def advance(seconds):
                    now[0] += seconds
                    scope['LAST_RAW_GGA_TS'] = now[0]
                    await asyncio.sleep(0)
                scope['asyncio'] = SimpleNamespace(sleep=advance, CancelledError=asyncio.CancelledError,
                    to_thread=asyncio.to_thread)
                load({'auto_base_state_machine', '_run_auto_base_setup', '_get_fresh_outbound_gga',
                      '_is_rtk_usable_status'}, scope)
                with self.assertLogs(level='INFO'):
                    await scope['auto_base_state_machine'](agent, reader, None)
                self.assertEqual(phases[-1], 'failed' if expected_failure else 'ready_for_review')
                self.assertEqual(agent.config['gnss_config_pending'], apply_error is not None)
                self.assertIs(agent.config['base_config'], old)
                scope['_apply_base_mode_to_chip'].assert_awaited_once_with('Ublox', 'USB', 1.0, 2.0, 3.0, reader)
                if not expected_failure:
                    candidate = agent.config['auto_base_candidate']
                    converted = host == '14.238.1.125'
                    self.assertEqual(candidate['averaged_coord']['lat'], '20.100000000' if converted else '20.000000000')
                    self.assertEqual(candidate['transform_applied'], converted)
                    if converted:
                        self.assertEqual(candidate['conversion_report']['calculated_coord'],
                            {'lat': repr(20.100000000123456), 'lon': '105.10000000098765', 'alt': '2.1000123456789'})
                        self.assertEqual(candidate['averaged_coord']['lon'], '105.100000000')
                        self.assertEqual(candidate['averaged_coord']['alt'], '2.1000')
                    self.assertEqual(scope['_transform_itrf_to_vn2000_neh_and_llh'].called, converted)
                    self.assertEqual(candidate['conversion_report']['projected_coord'],
                        {'north': 2300000, 'east': 580000, 'h': 25} if converted else None)
                self.assertFalse(agent.config['auto_base_setup']['enabled'])
                self.assertIsNone(scope['active_auto_base_client'])
                self.assertTrue(client.stop.called)
                self.assertTrue(reader.resume.called)

    async def test_review_result_restores_old_base_without_installing_candidate(self):
        old = {'base_setup_method': 'FIXED_LLA', 'coords': {'lat': 21, 'lon': 105, 'alt': 30}}
        candidate = {'averaged_coord': {'lat': '22', 'lon': '106', 'alt': '40'}, 'fixed_samples_only': True}
        for failure in (None, RuntimeError('sampling failed'), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                agent = SimpleNamespace(config={'base_config': copy.deepcopy(old),
                    'auto_base_setup': {'enabled': True, 'review_only': True}},
                    detected_chip={'type': 'Ublox', 'port': 'USB'}, save_config=Mock())
                phases = []
                apply = AsyncMock()
                scope = load({'auto_base_state_machine'}, dict(AgentManager=object, GNSSReader=object,
                    mqtt=SimpleNamespace(Client=object), asyncio=asyncio, copy=copy, logging=logging,
                    _run_auto_base_setup=AsyncMock(return_value=candidate, side_effect=failure),
                    _apply_base_mode_to_chip=apply, _cleanup_inject_queue=Mock(),
                    _set_auto_base_progress=lambda phase, **kwargs: phases.append(phase), send_status=AsyncMock(),
                    active_auto_base_client=None))
                with self.assertLogs(level='ERROR') if isinstance(failure, RuntimeError) else __import__('contextlib').nullcontext():
                    if isinstance(failure, asyncio.CancelledError):
                        with self.assertRaises(asyncio.CancelledError):
                            await scope['auto_base_state_machine'](agent, None, None)
                    else:
                        await scope['auto_base_state_machine'](agent, None, None)
                apply.assert_awaited_once_with('Ublox', 'USB', 21.0, 105.0, 30.0, None)
                self.assertEqual(agent.config['base_config'], old)
                self.assertNotIn('pending_base_restore', agent.config)
                if failure is None:
                    self.assertEqual(phases[-1], 'ready_for_review')
                    self.assertEqual(agent.config['auto_base_candidate'], candidate)
                else:
                    self.assertNotIn('auto_base_candidate', agent.config)

    def test_ntrip_v1_corrections_reach_chip_while_outbound_stream_is_asleep(self):
        scope = load({'NTRIPClientWorker', 'GNSSReader', '_crc24q', 'is_valid_rtcm3_packet', 'extract_valid_rtcm3_packets'}, dict(
            threading=threading, time=time, base64=base64, socket=SimpleNamespace(timeout=TimeoutError),
            Queue=Queue, Empty=Empty, Full=Full, rtcm_stream_active_flag=False,
            dispatch_rtcm_data=Mock(side_effect=AssertionError('Auto corrections must bypass outbound gate')),
            is_remote_locked=lambda: False, _get_fresh_outbound_gga=lambda age: '$GNGGA,test',
            _telemetry_inc=Mock(), _update_datum_from_rtcm_packet=Mock()))
        raw = b'\xd3\x00\x03\x3e\xd0\x00'
        correction = raw + scope['_crc24q'](raw).to_bytes(3, 'big')
        reader = SimpleNamespace(rtcm_inject_queue=Queue())
        sink = lambda packet: scope['GNSSReader'].inject_rtcm(reader, packet)
        worker = scope['NTRIPClientWorker'](dict(rtcmserver1='caster', rtcmport1='2101',
            rtcmmountpoint1='VRS', rtcmusername1='user'), Mock(), correction_sink=sink)
        class Socket:
            def __init__(self):
                self.header = bytearray(b'ICY 200 OK\r\n')
            def settimeout(self, timeout): pass
            def sendall(self, data): pass
            def close(self): pass
            def recv(self, count):
                if self.header:
                    byte = bytes(self.header[:count]); del self.header[:count]; return byte
                if not reader.rtcm_inject_queue.empty():
                    worker.stop(); return b''
                return correction
        scope['socket'].create_connection = lambda *args, **kwargs: Socket()
        def log(level, message):
            if 'Connection error:' in message:
                worker.stop()
                raise AssertionError(message)
        worker.log = log
        worker.run()
        self.assertEqual(reader.rtcm_inject_queue.get_nowait(), correction)
        self.assertEqual(worker.total_rtcm_bytes, len(correction))
        self.assertEqual(worker.total_rtcm_pkts, 1)
        scope['dispatch_rtcm_data'].assert_not_called()


if __name__ == '__main__':
    unittest.main()

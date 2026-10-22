import asyncio
import unittest
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import Response
from app import reference_network as network
from app import reference_network_cache as shared_cache


LOGIN = '<title>Spider Business Center - SBC Login</title><form id="login-form"><input id="UserName"><input id="Password"></form>'
ERROR = '<title>Error</title><div id="handledErrorMessage">Sorry, an error occurred while processing your request -- Error Code: 500</div>'


class ReferenceNetworkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        cache_path = patch.object(shared_cache, 'CACHE_PATH', Path(directory.name) / 'polling.sqlite3')
        cache_path.start()
        self.addCleanup(cache_path.stop)
        self.config = {'rtcm_check_enabled': False, 'web_check_interval_seconds': 30, 'rtcm_check_interval_seconds': 900}
        config = patch.object(network, 'get_check_settings', side_effect=lambda: dict(self.config))
        config.start()
        self.addCleanup(config.stop)
        fallback = patch.object(network, 'probe_rtcm', AsyncMock(return_value=('offline', 'Không có RTCM')))
        self.rtcm = fallback.start()
        self.addCleanup(fallback.stop)

    def test_page_classification_uses_login_form_and_ignores_script_error_words(self):
        self.assertEqual(network.classify_page(LOGIN), 'online')
        self.assertEqual(network.classify_page(ERROR), 'offline')
        self.assertEqual(network.classify_page('<title>Maintenance</title>'), 'offline')
        self.assertEqual(network.classify_page('<title>OK</title><form><input id="UserName"></form>'), 'offline')
        self.assertEqual(network.classify_page(LOGIN + '<script>var error="error";</script>'), 'online')

    async def test_missing_login_form_is_offline_without_ntrip_connection(self):
        with patch.object(network, '_read_page', AsyncMock(return_value='<title>Site unavailable</title>')):
            result = await network.probe_network()
        self.assertEqual(result['status'], 'offline')
        self.assertFalse(result['station_status_available'])
        self.assertEqual(result['scope'], 'website')
        self.assertEqual(result['website_status'], 'offline')
        self.rtcm.assert_not_awaited()

    async def test_rtcm_diagnostic_does_not_override_failed_website(self):
        self.rtcm.return_value = ('online', 'Có RTCM hợp lệ')
        for page in (ERROR, '<title>Maintenance</title>'):
            with patch.object(network, '_read_page', AsyncMock(return_value=page)):
                result = await network.probe_network()
            self.assertEqual(result['status'], 'offline')
            self.assertEqual(result['website_status'], 'offline')
            self.assertEqual(result['rtcm_status'], 'not_checked')
            self.assertFalse(result['station_status_available'])
        self.rtcm.assert_not_awaited()

    async def test_healthy_website_is_online_without_ntrip_connection(self):
        with patch.object(network, '_read_page', AsyncMock(return_value=LOGIN)):
            result = await network.probe_network()
        self.assertEqual(result['website_status'], 'online')
        self.assertEqual(result['status'], 'online')
        self.assertEqual(result['rtcm_status'], 'not_checked')
        self.rtcm.assert_not_awaited()

    async def test_website_timeout_stays_unknown_without_ntrip_connection(self):
        self.rtcm.return_value = ('unknown', 'Không kiểm tra được RTCM')
        with patch.object(network, '_read_page', AsyncMock(side_effect=httpx.ReadTimeout('timeout'))):
            self.assertEqual((await network.probe_network())['status'], 'unknown')
        self.rtcm.assert_not_awaited()

    async def test_concurrent_viewers_share_probe_and_failure_replaces_online(self):
        network._cache = None
        network._cached_at = 0
        network._lock = asyncio.Lock()
        probe = AsyncMock(return_value={'status': 'online'})
        with patch.object(network, 'probe_network', probe):
            responses = await asyncio.gather(*(network.reference_network_status(Response()) for _ in range(10)))
        probe.assert_awaited_once()
        self.assertTrue(all(item['status'] == 'online' for item in responses))
        network._cached_at = 0
        with patch.object(network, 'probe_network', AsyncMock(return_value={'status': 'unknown'})):
            self.assertEqual((await network.reference_network_status(Response()))['status'], 'unknown')
        network._cache = None

    async def test_enabled_rtcm_decides_all_four_website_data_combinations(self):
        self.config['rtcm_check_enabled'] = True
        with patch.object(network, 'time', Mock(wraps=network.time)) as clock:
            for index, (website, rtcm) in enumerate((('online', 'online'), ('online', 'offline'), ('offline', 'online'), ('offline', 'offline'))):
                clock.time.return_value = 100000 + index * 1000
                self.rtcm.return_value = (rtcm, 'RTCM ' + rtcm)
                with patch.object(network, '_probe_website', AsyncMock(return_value=(website, 'Website ' + website))):
                    result = await network.probe_network()
                self.assertEqual(result['website_status'], website)
                self.assertEqual(result['status'], rtcm)
                self.assertEqual(result['rtcm_status'], rtcm)
                self.assertEqual(result['scope'], 'ntrip')
                self.assertEqual(result['checked_at'], clock.time.return_value)
                self.assertEqual(result['valid_until'], clock.time.return_value + 960)
        self.assertEqual(self.rtcm.await_count, 4)

    async def test_cooldowns_cache_failures_and_keep_web_and_rtcm_intervals_separate(self):
        self.config['rtcm_check_enabled'] = True
        website = AsyncMock(return_value=('online', 'Website online'))
        with patch.object(network, '_probe_website', website), patch.object(network, 'time', Mock(wraps=network.time)) as clock:
            clock.time.return_value = 100000
            self.assertEqual((await network.probe_network())['status'], 'offline')
            clock.time.return_value += 31
            self.assertEqual((await network.probe_network())['status'], 'offline')
            self.assertEqual(website.await_count, 2)
            self.rtcm.assert_awaited_once()
            clock.time.return_value = 100900
            self.rtcm.return_value = ('online', 'Có RTCM')
            self.assertEqual((await network.probe_network())['status'], 'online')
            self.assertEqual(self.rtcm.await_count, 2)

    async def test_live_disable_bypasses_local_cache_and_returns_to_website(self):
        self.config['rtcm_check_enabled'] = True
        network._cache = None
        network._lock = asyncio.Lock()
        with patch.object(network, '_probe_website', AsyncMock(return_value=('online', 'Website online'))):
            self.assertEqual((await network.get_network_status())['status'], 'offline')
            self.config['rtcm_check_enabled'] = False
            result = await network.get_network_status()
        self.assertEqual(result['status'], 'online')
        self.assertEqual(result['scope'], 'website')
        self.assertEqual(result['rtcm_status'], 'not_checked')
        self.rtcm.assert_awaited_once()
        self.config['rtcm_check_enabled'] = True
        result = await network.get_network_status()
        self.assertEqual(result['status'], 'offline')
        self.rtcm.assert_awaited_once()
        network._cache = None

    async def test_broken_cache_never_creates_upstream_connection(self):
        self.config['rtcm_check_enabled'] = True
        with patch.object(network, 'claim_probe', side_effect=sqlite3.OperationalError('unavailable')), patch.object(network, '_probe_website', AsyncMock()) as web:
            self.assertEqual((await network.probe_network())['status'], 'unknown')
        web.assert_not_awaited()
        self.rtcm.assert_not_awaited()

    def test_separate_worker_processes_reserve_only_one_poll(self):
        script = ('from pathlib import Path; from app import reference_network_cache as c; '
                  f'c.CACHE_PATH = Path({str(shared_cache.CACHE_PATH)!r}); '
                  'print(c.claim_probe("workers", 900, 100000)[1] is not None)')
        workers = [subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
        results = [worker.communicate(timeout=10) for worker in workers]
        self.assertTrue(all(worker.returncode == 0 for worker in workers), results)
        self.assertEqual(sum(output.strip() == 'True' for output, _ in results), 1)


class RtcmProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        values = {'CORS_CUC_NTRIP_USER': 'probe-user', 'CORS_CUC_NTRIP_PASSWORD': 'probe-password'}
        config = patch.object(network, '_get_env_preferring_dotenv', side_effect=lambda key, default: values.get(key, default))
        config.start()
        self.addCleanup(config.stop)
        self.reader = AsyncMock()
        self.reader.readline.return_value = b'ICY 200 OK\r\n'
        self.writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock())
        connection = patch.object(network.asyncio, 'open_connection', AsyncMock(return_value=(self.reader, self.writer)))
        self.connect = connection.start()
        self.addCleanup(connection.stop)

    async def test_login_sends_gga_and_requires_crc_valid_fragmented_rtcm(self):
        packet = bytes.fromhex('d300023ed0')
        packet += network.crc24q(packet).to_bytes(3, 'big')
        self.reader.read.side_effect = [b'\r\n' + packet[:4], packet[4:]]
        self.assertEqual((await network.probe_rtcm())[0], 'online')
        request, gga = [call.args[0] for call in self.writer.write.call_args_list]
        self.assertIn(b'GET /VRS.105M3 HTTP/1.0', request)
        self.assertIn(b'Authorization: Basic ', request)
        body, checksum = gga.strip()[1:].split(b'*')
        actual = 0
        for char in body:
            actual ^= char
        self.assertEqual(actual, int(checksum, 16))
        self.assertTrue(body.startswith(b'GPGGA,'))
        self.writer.close.assert_called_once()
        self.writer.wait_closed.assert_awaited_once()

    async def test_first_valid_frame_closes_socket_without_reading_more(self):
        packet = bytes.fromhex('d300023ed0')
        packet += network.crc24q(packet).to_bytes(3, 'big')
        self.reader.read.side_effect = [packet, AssertionError('Must not keep reading the stream')]
        self.assertEqual((await network.probe_rtcm())[0], 'online')
        self.reader.read.assert_awaited_once()
        self.writer.close.assert_called_once()
        self.writer.wait_closed.assert_awaited_once()

    async def test_http_success_without_valid_rtcm_is_offline(self):
        for payload in (b'<html>OK</html>', b'\xd3\x00\x02\x3e\xd0\x00\x00\x00', b''):
            self.reader.readline.return_value = b'HTTP/1.1 200 OK\r\n'
            self.reader.read.side_effect = [b'Content-Type: gnss/data\r\n\r\n' + payload, b'']
            self.assertEqual((await network.probe_rtcm())[0], 'offline')

    async def test_rejected_login_does_not_send_gga(self):
        for status, expected in ((b'HTTP/1.0 401 Unauthorized\r\n', 'unknown'),
                                 (b'HTTP/1.1 403 Forbidden\r\n', 'unknown'),
                                 (b'SOURCETABLE 200 OK\r\n', 'offline')):
            self.writer.reset_mock()
            self.reader.readline.return_value = status
            self.assertEqual((await network.probe_rtcm())[0], expected)
            self.assertEqual(self.writer.write.call_count, 1)
            self.writer.close.assert_called_once()

    async def test_connect_failure_is_unknown_but_stream_timeout_is_offline(self):
        self.connect.side_effect = OSError('unreachable')
        self.assertEqual((await network.probe_rtcm())[0], 'unknown')
        self.connect.side_effect = None
        self.reader.read.side_effect = TimeoutError()
        self.assertEqual((await network.probe_rtcm())[0], 'offline')
        self.writer.close.assert_called_once()

    async def test_missing_credentials_skips_connection(self):
        with patch.object(network, '_get_env_preferring_dotenv', return_value=''):
            self.assertEqual((await network.probe_rtcm())[0], 'unknown')
        self.connect.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()

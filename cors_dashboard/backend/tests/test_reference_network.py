import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import Response
from app import reference_network as network


LOGIN = '<title>Spider Business Center - SBC Login</title><form id="login-form"><input id="UserName"><input id="Password"></form>'
ERROR = '<title>Error</title><div id="handledErrorMessage">Sorry, an error occurred while processing your request -- Error Code: 500</div>'


class ReferenceNetworkTests(unittest.IsolatedAsyncioTestCase):
    def test_page_classification_uses_login_form_and_ignores_script_error_words(self):
        self.assertEqual(network.classify_page(LOGIN), 'online')
        self.assertEqual(network.classify_page(ERROR), 'offline')
        self.assertEqual(network.classify_page('<title>Maintenance</title>'), 'offline')
        self.assertEqual(network.classify_page('<title>OK</title><form><input id="UserName"></form>'), 'offline')
        self.assertEqual(network.classify_page(LOGIN + '<script>var error="error";</script>'), 'online')

    async def test_missing_login_form_is_error_even_with_http_success(self):
        with patch.object(network, '_read_page', AsyncMock(return_value='<title>Site unavailable</title>')):
            result = await network.probe_network()
        self.assertEqual(result['status'], 'offline')
        self.assertFalse(result['station_status_available'])
        self.assertEqual(result['scope'], 'website')

    async def test_healthy_response_and_failed_probe(self):
        with patch.object(network, '_read_page', AsyncMock(return_value=LOGIN)):
            self.assertEqual((await network.probe_network())['status'], 'online')
        with patch.object(network, '_read_page', AsyncMock(side_effect=httpx.ReadTimeout('timeout'))):
            self.assertEqual((await network.probe_network())['status'], 'unknown')

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


if __name__ == '__main__':
    unittest.main()

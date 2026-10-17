import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import Response
from app import reference_network as network


LOGIN = '<title>Spider Business Center - SBC Login</title><form id="login-form"><input id="UserName"><input id="Password"></form>'
MAP = '<title>Site Status</title><script>var Sites = [{"SiteCode":"HOCM","Status":0}];</script>'
ERROR = '<title>Error</title><div id="handledErrorMessage">Sorry, an error occurred while processing your request -- Error Code: 500</div>'


class ReferenceNetworkTests(unittest.IsolatedAsyncioTestCase):
    def test_page_classification_does_not_infer_individual_station_health(self):
        self.assertEqual(network.classify_page(LOGIN, 'login'), 'online')
        self.assertEqual(network.classify_page(MAP, 'map'), 'online')
        for kind in ('login', 'map'):
            self.assertEqual(network.classify_page(ERROR, kind), 'offline')
            self.assertEqual(network.classify_page('<title>Maintenance</title>', kind), 'unknown')
        self.assertEqual(network.classify_page(LOGIN + '<script>var error="error";</script>', 'login'), 'online')
        self.assertEqual(network.classify_page('<script>var Sites=[];</script>', 'map'), 'unknown')

    async def test_healthy_login_with_error_map_is_not_online(self):
        with patch.object(network, '_read_page', AsyncMock(side_effect=[LOGIN, ERROR])):
            result = await network.probe_network()
        self.assertEqual(result['status'], 'offline')
        self.assertFalse(result['station_status_available'])
        self.assertEqual(result['scope'], 'website')

    async def test_healthy_response_and_failed_probe(self):
        with patch.object(network, '_read_page', AsyncMock(side_effect=[LOGIN, MAP])):
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

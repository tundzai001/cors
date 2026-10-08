"""Security counters follow HTTP traffic and all enforcement sources."""
import ast
import asyncio
from collections import deque
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

APP = Path(__file__).resolve().parents[1] / 'app'


def methods(filename, classname, names, scope):
    tree = ast.parse((APP / filename).read_text(encoding='utf-8-sig'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == classname)
    cls.body = [node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    cls.bases = []
    exec(compile(ast.Module(body=[cls], type_ignores=[]), filename, 'exec'), scope)
    return scope[classname]


class SecurityObservabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = SimpleNamespace(time=lambda: 1000.0, monotonic=lambda: 1000.0)
        scope = {'time': self.clock}
        cls = methods('security.py', 'AdvancedRateLimiter', {'record_http_request','record_block_event','merge_runtime_stats'}, scope)
        self.limiter = cls()
        self.limiter.http_counts = {}
        self.limiter.http_ip_last_seen = {}
        self.limiter.runtime_blocks = {}
        self.limiter.recent_blocks = deque(maxlen=200)
        self.limiter._stats_cache = {}

    def test_http_counts_are_per_second_and_decay_to_zero(self):
        for _ in range(4): self.limiter.record_http_request('192.0.2.1', 999)
        self.assertEqual(self.limiter.merge_runtime_stats({}, [])['traffic_history'][-1], 4)
        self.clock.time = lambda: 1002
        self.assertEqual(self.limiter.merge_runtime_stats({}, [])['traffic_history'][-1], 0)
        self.clock.time = lambda: 1400
        self.assertEqual(self.limiter.merge_runtime_stats({}, [])['total_ips_tracked'], 0)
        self.assertEqual(sum(self.limiter.merge_runtime_stats({}, [])['traffic_history']), 0)

    def test_short_block_expires_but_history_remains(self):
        self.limiter.record_block_event('192.0.2.1', 'HTTP limit', 'http_rate_limit', blocked_until=1002)
        self.limiter.record_block_event('192.0.2.1', 'HTTP limit', 'http_rate_limit', blocked_until=1002)
        stats = self.limiter.merge_runtime_stats({}, [])
        self.assertEqual(stats['blocked_ips_count'], 1)
        self.assertEqual(stats['recent_blocks'][0]['count'], 2)
        self.clock.time = lambda: 1003
        stats = self.limiter.merge_runtime_stats({}, [])
        self.assertEqual(stats['blocked_ips_count'], 0)
        self.assertEqual(len(stats['recent_blocks']), 1)

    def test_redis_bans_are_visible_and_deduplicated(self):
        stats = self.limiter.merge_runtime_stats({'blacklisted_ips':[{'ip':'192.0.2.1'}],
            'temporary_blocked_ips':[{'ip':'192.0.2.1','blocked_until':1010},{'ip':'192.0.2.3','blocked_until':999}]}, ['192.0.2.1','192.0.2.2'])
        self.assertEqual(stats['permanent_blacklist_count'], 2)
        self.assertEqual(stats['blocked_ips_count'], 0)

    async def test_middleware_counts_and_records_short_rejections(self):
        redis = SimpleNamespace(check_rate_limit=AsyncMock(return_value=False), rate_limit_retry_after=AsyncMock(return_value=2))
        self.limiter._get_client_ip = lambda request: request.client.host
        scope = dict(time=self.clock, Request=object, rate_limiter=self.limiter, redis_client=redis,
                     health_monitor=SimpleNamespace(record_error=lambda *args: None),
                     status=SimpleNamespace(HTTP_429_TOO_MANY_REQUESTS=429), JSONResponse=lambda **kwargs: kwargs)
        middleware = methods('main.py','MonitoringMiddleware',{'dispatch'}, scope)()
        next_handler = AsyncMock()
        response = await middleware.dispatch(SimpleNamespace(client=SimpleNamespace(host='192.0.2.1')), next_handler)
        self.assertEqual(response['status_code'], 429)
        self.assertEqual(response['headers']['Retry-After'], '2')
        self.assertEqual(self.limiter.http_counts[1000], 1)
        self.assertEqual(self.limiter.merge_runtime_stats({}, [])['blocked_ips_count'], 1)
        next_handler.assert_not_awaited()

    async def test_unblock_clears_fallback_http_limit(self):
        cls = methods('redis_client.py','RedisClient',{'rate_limit_retry_after','clear_rate_limit'}, {'time':self.clock})
        redis = cls()
        redis._redis = None
        redis._fallback_limits = {'rate_limit:192.0.2.1':(1000, 1002)}
        self.assertEqual(await redis.rate_limit_retry_after('rate_limit:192.0.2.1'), 3)
        await redis.clear_rate_limit('rate_limit:192.0.2.1')
        self.assertNotIn('rate_limit:192.0.2.1', redis._fallback_limits)

if __name__ == '__main__': unittest.main()

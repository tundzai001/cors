"""Nginx bans cannot inject config and failed updates retain the previous include."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from app.nginx_security import NginxSecuritySync, blocklist_text

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('nginx_installer', ROOT / 'scripts/configure_nginx_security.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class NginxSecurityTests(unittest.IsolatedAsyncioTestCase):
    def test_exact_ips_only_and_no_proxy_loopback_block(self):
        self.assertIn('192.0.2.1 1;', blocklist_text(['192.0.2.1', '2001:db8::1']))
        for value in ('192.0.2.1; return 200;', '0.0.0.0/0', '127.0.0.1', '::', 'unknown'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                blocklist_text([value])

    def test_config_patch_scopes_to_both_base_servers_and_is_idempotent(self):
        source = 'events {}\nhttp {\nserver { server_name other.example; }\nserver { server_name base.aitogy.com; }\nserver { server_name base.aitogy.com; }\n}'
        result = installer.patch_config(source)
        self.assertEqual(result.count('if ($cors_blocked_ip) { return 444; }'), 2)
        self.assertEqual(result.count('geo $cors_blocked_ip'), 1)
        self.assertIn('server_name other.example; }', result)
        self.assertEqual(installer.patch_config(result), result)

    def test_base_only_domain_keeps_security_and_existing_backend(self):
        source = 'events {}\nhttp {\nserver { server_name base.aitogy.com; }\nserver { server_name base.aitogy.com; }\n}'
        result = installer.patch_config(source)
        self.assertEqual(result.count('if ($cors_blocked_ip) { return 444; }'), 2)
        self.assertEqual(installer.patch_config(result), result)
        deployed = (ROOT / 'deploy/nginx/nginx.conf').read_text()
        self.assertEqual(deployed.count('server_name base.aitogy.com;'), 2)
        self.assertNotIn('server_name base.aitogy.com ', deployed)
        self.assertIn('C:/Certbot/live/base.aitogy.com/fullchain.pem', deployed)
        self.assertIn('proxy_pass http://127.0.0.1:8000;', deployed)

    async def test_sync_test_reload_remove_and_no_repeated_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            prefix = Path(folder)
            (prefix/'conf').mkdir()
            path = prefix/'conf/cors-blocked-ips.conf'
            path.write_text('# initial\n')
            sync = NginxSecuritySync(folder)
            with patch.object(sync, '_run', Mock()) as run:
                await sync.sync({'192.0.2.1'})
                self.assertIn('192.0.2.1 1;', path.read_text())
                self.assertEqual(run.call_args_list[0].args, ('-t',))
                self.assertEqual(run.call_args_list[1].args, ('-s','reload'))
                self.assertEqual(sync.status['state'], 'reload_requested')
                await sync.sync({'192.0.2.1'})
                self.assertEqual(run.call_count, 2)
                await sync.sync(set())
                self.assertNotIn('192.0.2.1', path.read_text())
                self.assertEqual(sync.requested_ips, set())

    async def test_failed_test_or_reload_rolls_back_and_never_claims_success(self):
        for fail_at in ('-t', '-s'):
            with self.subTest(command=fail_at), tempfile.TemporaryDirectory() as folder:
                prefix = Path(folder)
                (prefix/'conf').mkdir()
                path = prefix/'conf/cors-blocked-ips.conf'
                path.write_text('# previous\n')
                sync = NginxSecuritySync(folder)
                def run(*args):
                    if args[0] == fail_at: raise RuntimeError('test failure')
                with patch.object(sync, '_run', side_effect=run):
                    await sync.sync({'192.0.2.1'})
                self.assertEqual(path.read_text(), '# previous\n')
                self.assertEqual(sync.status['state'], 'error')
                self.assertEqual(sync.requested_ips, set())

    async def test_startup_restores_gate_but_periodic_sync_cannot_resurrect_unblocked_ip(self):
        from contextlib import asynccontextmanager
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from app.nginx_security import sync_security_bans
        record = SimpleNamespace(ip_address='192.0.2.1')
        database = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda:SimpleNamespace(all=lambda:[record]))))
        @asynccontextmanager
        async def session():
            yield database
        redis = SimpleNamespace(is_ip_banned=AsyncMock(return_value=False), ban_ip=AsyncMock(), get_banned_ips=AsyncMock(return_value=[]))
        with patch('app.database.auth_session_scope', session), patch('app.redis_client.redis_client', redis), patch(
            'app.nginx_security.nginx_security.sync', AsyncMock()
        ) as sync:
            await sync_security_bans(restore_application=True)
            redis.ban_ip.assert_awaited_once_with('192.0.2.1')
            sync.assert_awaited_with({'192.0.2.1'})
            redis.ban_ip.reset_mock()
            await sync_security_bans()
            redis.ban_ip.assert_not_awaited()

    async def test_permanent_ban_saves_creation_reason_and_updates_http_gate(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from app.security import AdvancedRateLimiter
        limiter = AdvancedRateLimiter(5, 1, 1, 5)
        database = SimpleNamespace(execute=AsyncMock(), add=Mock(), commit=AsyncMock())
        with patch('app.redis_client.redis_client.ban_ip', AsyncMock()) as gate:
            await limiter.block_ip(database, '192.0.2.1', reason='Security Gate: 5 violations')
        stored = database.add.call_args.args[0]
        self.assertEqual(stored.block_type, 'permanent')
        self.assertGreater(stored.created_at, 0)
        self.assertEqual(stored.reason, 'Security Gate: 5 violations')
        database.commit.assert_awaited_once()
        gate.assert_awaited_once_with('192.0.2.1')

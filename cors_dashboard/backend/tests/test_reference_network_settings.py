"""Live .env settings and authorization for the RTCM switch."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from app import auth, reference_network_admin as admin, reference_network_settings as settings


class ReferenceNetworkSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.env = Path(directory.name) / '.env'
        self.env.write_text('CORS_CUC_RTCM_CHECK_ENABLED=true\nCORS_CUC_WEB_CHECK_INTERVAL_SECONDS=45\nCORS_CUC_RTCM_CHECK_INTERVAL_SECONDS=1200\n')
        path = patch.object(settings, 'ENV_FILE_PATH', self.env)
        path.start()
        self.addCleanup(path.stop)
        self.app = FastAPI()
        self.app.include_router(admin.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)

    async def test_anonymous_and_viewer_cannot_read_or_change_settings(self):
        url = '/api/admin/reference-network-settings'
        for method in ('GET', 'PUT'):
            response = await self.client.request(method, url, json={'rtcm_check_enabled': False} if method == 'PUT' else None)
            self.assertIn(response.status_code, (401, 403))
        self.app.dependency_overrides[auth.get_current_user] = lambda: SimpleNamespace(role='viewer')
        self.assertEqual((await self.client.get(url)).status_code, 403)
        self.assertEqual((await self.client.put(url, json={'rtcm_check_enabled': False})).status_code, 403)
        self.assertTrue(settings.get_check_settings()['rtcm_check_enabled'])

    async def test_admin_switch_persists_and_other_readers_see_change_without_restart(self):
        self.app.dependency_overrides[auth.get_current_user] = lambda: SimpleNamespace(role='admin')
        url = '/api/admin/reference-network-settings'
        response = await self.client.put(url, json={'rtcm_check_enabled': False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertFalse(settings.get_check_settings()['rtcm_check_enabled'])
        result = (await self.client.get(url)).json()
        self.assertEqual(result, {'rtcm_check_enabled': False, 'web_check_interval_seconds': 45, 'rtcm_check_interval_seconds': 1200})
        self.assertNotIn('PASSWORD', str(result))
        response = await self.client.put(url, json={'rtcm_check_enabled': True})
        self.assertTrue(response.json()['rtcm_check_enabled'])
        self.assertTrue(settings.get_check_settings()['rtcm_check_enabled'])

    async def test_switch_rejects_invalid_body_and_reports_save_failure(self):
        self.app.dependency_overrides[auth.get_current_user] = lambda: SimpleNamespace(role='admin')
        url = '/api/admin/reference-network-settings'
        for body in ({'rtcm_check_enabled': 'false'}, {'rtcm_check_enabled': False, 'password': 'ignored'}):
            self.assertEqual((await self.client.put(url, json=body)).status_code, 422)
        with patch.object(admin, 'set_rtcm_check_enabled', side_effect=OSError('read-only')):
            self.assertEqual((await self.client.put(url, json={'rtcm_check_enabled': False})).status_code, 503)

    async def test_interval_edits_are_live_and_invalid_intervals_use_defaults(self):
        self.env.write_text('CORS_CUC_RTCM_CHECK_ENABLED=false\nCORS_CUC_WEB_CHECK_INTERVAL_SECONDS=60\nCORS_CUC_RTCM_CHECK_INTERVAL_SECONDS=1800\n')
        self.assertEqual(settings.get_check_settings(), {'rtcm_check_enabled': False, 'web_check_interval_seconds': 60, 'rtcm_check_interval_seconds': 1800})
        for value in ('0', '-5', 'oops'):
            self.env.write_text(f'CORS_CUC_WEB_CHECK_INTERVAL_SECONDS={value}\nCORS_CUC_RTCM_CHECK_INTERVAL_SECONDS={value}\n')
            result = settings.get_check_settings()
            self.assertEqual(result['web_check_interval_seconds'], 30)
            self.assertEqual(result['rtcm_check_interval_seconds'], 900)


if __name__ == '__main__':
    unittest.main()

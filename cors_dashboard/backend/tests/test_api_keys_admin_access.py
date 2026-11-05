"""Actual HTTP/JWT authorization, including both page aliases."""
import ast
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import jwt
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app import auth, models
from app.database import get_auth_db, get_db
from app.routers.api_keys import router, _token_cipher, reveal_rate_limiter


class ApiKeysAdminAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reveal_rate_limiter.requests.clear()
        self.password_hash = await auth.get_password_hash('TestAdminPassword!123')
        self.app = FastAPI()
        self.app.include_router(router)
        self.db = SimpleNamespace(execute=AsyncMock(), get=AsyncMock(), commit=AsyncMock())
        self.app.dependency_overrides[get_auth_db] = lambda: self.db
        self.app.dependency_overrides[get_db] = lambda: self.db
        # Use production route definitions without starting MQTT or databases.
        source = Path(__file__).parents[1] / 'app' / 'frontend_routes.py'
        tree = ast.parse(source.read_text())
        definitions = [node for node in tree.body
                       if isinstance(node, ast.AsyncFunctionDef)
                       and node.name in {'serve_api_keys', 'serve_public_html'}]
        self.assertEqual(len(definitions), 2)
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), 'exec'),
             {'router': self.app, 'FileResponse': FileResponse, 'HTTPException': HTTPException})
        middleware_tree = ast.parse((source.parent / 'main.py').read_text())
        middleware = next(node for node in middleware_tree.body if isinstance(node, ast.ClassDef)
                          and node.name == 'FrontendCacheMiddleware')
        namespace = {'BaseHTTPMiddleware': BaseHTTPMiddleware, 'Request': object}
        exec(compile(ast.Module(body=[middleware], type_ignores=[]), str(source), 'exec'), namespace)
        self.app.add_middleware(namespace['FrontendCacheMiddleware'])
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)
        self.lookup = AsyncMock()
        lookup_patch = patch('app.auth.crud.get_user_by_username', self.lookup)
        lookup_patch.start()
        self.addCleanup(lookup_patch.stop)
        body = {'name': 'Test', 'expires_at': 0, 'all_stations': True, 'data_fields': ['name']}
        self.routes = [
            ('GET', '/api/admin/api-keys/page', None),
            ('GET', '/api/admin/api-keys', None),
            ('POST', '/api/admin/api-keys', body),
            ('POST', '/api/admin/api-keys/1/token', {'password': 'TestAdminPassword!123'}),
            ('PUT', '/api/admin/api-keys/1/token', {'token': 'cors_test_example', 'expires_in': 30}),
            ('PUT', '/api/admin/api-keys/1', body),
            ('DELETE', '/api/admin/api-keys/1', None),
        ]

    async def assert_denied(self, token, expected):
        for method, path, body in self.routes:
            with self.subTest(method=method, path=path):
                headers = {'Authorization': f'Bearer {token}'} if token else {}
                response = await self.client.request(method, path, headers=headers, json=body)
                self.assertIn(response.status_code, expected)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertNotIn('key-form', response.text)
                self.assertNotIn('cors_test_example', response.text)
        self.db.execute.assert_not_awaited()
        self.db.get.assert_not_awaited()
        self.db.commit.assert_not_awaited()

    async def test_missing_forged_expired_tokens_are_denied(self):
        await self.assert_denied(None, {401, 403})
        await self.assert_denied('forged.jwt.token', {401})
        forged = jwt.encode({'sub': 'test', 'role': 'admin'}, 'wrong-signing-secret', algorithm='HS256')
        await self.assert_denied(forged, {401})
        expired = auth.create_access_token({'sub': 'test'}, expires_delta=timedelta(seconds=-10))
        await self.assert_denied(expired, {401})
        self.lookup.assert_not_awaited()

    async def test_only_active_current_database_admin_can_access(self):
        token = auth.create_access_token({'sub': 'test', 'role': 'admin'})
        for role in ('viewer', 'coordinator'):
            self.lookup.return_value = SimpleNamespace(id=1, role=role, is_active=True)
            await self.assert_denied(token, {403})
        self.lookup.return_value = SimpleNamespace(id=1, role='admin', is_active=False)
        await self.assert_denied(token, {403})
        self.lookup.return_value = None
        await self.assert_denied(token, {401})

    async def test_admin_can_load_page_list_and_reveal(self):
        self.lookup.return_value = SimpleNamespace(id=1, role='admin', is_active=True, hashed_password=self.password_hash)
        headers = {'Authorization': 'Bearer ' + auth.create_access_token({'sub': 'test'})}
        response = await self.client.get('/api/admin/api-keys/page', headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="key-form"', response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['content-security-policy'], "frame-ancestors 'none'")
        self.db.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
        response = await self.client.get('/api/admin/api-keys', headers=headers)
        self.assertEqual(response.json(), [])
        self.db.get.return_value = models.ApiKey(revoked_at=None, token_encrypted=_token_cipher().encrypt(b'cors_test_example').decode())
        response = await self.client.post('/api/admin/api-keys/1/token', headers=headers, json={'password': 'TestAdminPassword!123'})
        self.assertEqual(response.json(), {'token': 'cors_test_example', 'expires_in': 30})
        self.assertEqual(response.headers['cache-control'], 'no-store')

    async def test_password_required_each_time_and_attempts_limited(self):
        self.lookup.return_value = SimpleNamespace(id=1, role='admin', is_active=True, hashed_password=self.password_hash)
        headers = {'Authorization': 'Bearer ' + auth.create_access_token({'sub': 'test'})}
        path = '/api/admin/api-keys/1/token'
        self.assertEqual((await self.client.get(path, headers=headers)).status_code, 405)
        for body in ({}, {'password': ''}):
            self.assertEqual((await self.client.post(path, headers=headers, json=body)).status_code, 422)
        for _ in range(10):
            response = await self.client.post(path, headers=headers, json={'password': 'wrong-password'})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('token', response.json())
        response = await self.client.post(path, headers=headers, json={'password': 'TestAdminPassword!123'})
        self.assertEqual(response.status_code, 429)
        self.db.get.assert_not_awaited()
        reveal_rate_limiter.requests.clear()
        self.db.get.return_value = models.ApiKey(revoked_at=None, token_encrypted=_token_cipher().encrypt(b'cors_test_example').decode())
        self.assertEqual((await self.client.post(path, headers=headers, json={'password': 'TestAdminPassword!123'})).status_code, 200)
        self.assertEqual((await self.client.post(path, headers=headers, json={'password': 'wrong-password'})).status_code, 400)

    async def test_public_aliases_only_return_gate(self):
        for path in ('/api-keys', '/api-keys.html'):
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('api-keys-gate.min.js', response.text)
            self.assertNotIn('id="key-form"', response.text)
            self.assertNotIn('Key đã cấp', response.text)
            self.assertEqual(response.headers['cache-control'], 'no-store')
        for path in ('/api-keys-gate.html', '/frontend/api-keys.html'):
            self.assertEqual((await self.client.get(path)).status_code, 404)


if __name__ == '__main__':
    unittest.main()

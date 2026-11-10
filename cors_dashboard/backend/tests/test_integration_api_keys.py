import ast
import hashlib
import logging
import sqlite3
import time
import unittest
from typing import Any, Optional
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi import HTTPException, Response
from app import auth, models
from app.status_reconciler import normalize_station_snapshot

from app.routers.api_keys import ApiKeyPolicy, _validate_policy, project_station_items, require_integration_key, require_station_edit, SavedApiKey, _key_metadata, create_api_key, reveal_api_key, save_existing_api_key, RevealApiKey, _token_cipher, reveal_rate_limiter


class IntegrationApiKeyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reveal_rate_limiter.requests.clear()
        self.admin = SimpleNamespace(role="admin", id=1, hashed_password=await auth.get_password_hash("TestAdminPassword!123"))
        self.password = RevealApiKey(password="TestAdminPassword!123")

    async def test_missing_invalid_and_revoked_keys_are_rejected(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)))
        for header in (None, "Bearer bad", "Bearer cors_revoked"):
            with self.subTest(header=header), self.assertRaises(HTTPException) as error:
                await require_integration_key(header, db)
            self.assertEqual(error.exception.status_code, 401)

    async def test_active_key_is_accepted_without_storing_plaintext(self):
        key = SimpleNamespace(id=4, revoked_at=None)
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: key)))
        self.assertIs(await require_integration_key("Bearer cors_test_token", db), key)
        statement = db.execute.call_args.args[0]
        self.assertIn(hashlib.sha256(b"cors_test_token").hexdigest(), str(statement.compile(compile_kwargs={"literal_binds": True})))
        self.assertNotIn("cors_test_token", str(statement.compile(compile_kwargs={"literal_binds": True})))
        self.assertIn("expires_at >", str(statement))

    async def test_policy_rejects_missing_scope_or_fields(self):
        db = SimpleNamespace(execute=AsyncMock())
        for policy in (
            ApiKeyPolicy(name="test", expires_at=4102444800, data_fields=["name"]),
            ApiKeyPolicy(name="test", expires_at=4102444800, all_stations=True, data_fields=["password"]),
            ApiKeyPolicy(name="test", expires_at=1, all_stations=True, data_fields=["name"]),
        ):
            with self.subTest(policy=policy), self.assertRaises(HTTPException) as error:
                await _validate_policy(policy, db)
            self.assertEqual(error.exception.status_code, 422)

    async def test_permanent_policy_and_expiry_authentication(self):
        policy = ApiKeyPolicy(name="permanent", expires_at=0, all_stations=True, data_fields=["name"])
        self.assertEqual((await _validate_policy(policy, SimpleNamespace()))["expires_at"], 0)
        now = int(time.time())
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE api_keys (key_hash TEXT, revoked_at INTEGER, expires_at INTEGER)")
        for expiry, revoked, accepted in ((0, None, True), (now + 3600, None, True), (now - 1, None, False), (0, now, False)):
            connection.execute("DELETE FROM api_keys")
            connection.execute("INSERT INTO api_keys VALUES (?, ?, ?)", (hashlib.sha256(b"cors_test").hexdigest(), revoked, expiry))
            key = SimpleNamespace(id=1)
            async def execute(statement):
                compiled = statement.compile(compile_kwargs={"literal_binds": True})
                # Evaluate the actual authentication predicate, independent of ORM column types.
                where = str(compiled).split("WHERE ", 1)[1]
                found = connection.execute("SELECT 1 FROM api_keys WHERE " + where).fetchone()
                return SimpleNamespace(scalar_one_or_none=lambda: key if found else None)
            db = SimpleNamespace(execute=execute)
            with self.subTest(expiry=expiry, revoked=revoked):
                if accepted:
                    self.assertIs(await require_integration_key("Bearer cors_test", db), key)
                else:
                    with self.assertRaises(HTTPException) as error:
                        await require_integration_key("Bearer cors_test", db)
                    self.assertEqual(error.exception.status_code, 401)

    def test_current_policy_controls_station_and_fields(self):
        items = [
            {"serial": "A", "name": "Alpha", "status": "online", "streams": [{"host": "private"}]},
            {"serial": "B", "name": "Beta", "status": "offline", "streams": []},
        ]
        key = SimpleNamespace(all_stations=False, station_serials=["A"], data_fields=["name"])
        self.assertEqual(project_station_items(items, key, {}), [{"serial": "A", "name": "Alpha"}])
        key.station_serials = ["B"]
        key.data_fields = ["status"]
        self.assertEqual(project_station_items(items, key, {}), [{"serial": "B", "status": "offline"}])

    def test_offline_snapshot_overrides_stale_license_and_stream_status(self):
        now = 1791516600
        for status in ("awaiting_license", "online", "sleep", "streaming"):
            cached = {"serial": "A", "status": status, "timestamp": now - 181, "bps": 1250, "ntrip_connected": True, "ntrip_status": {"server1": {"connected": True}}}
            snapshot = normalize_station_snapshot(cached, now=now)
            key = SimpleNamespace(all_stations=True, station_serials=[], data_fields=["status", "bps", "ntrip_connected", "offline_since"])
            self.assertEqual(project_station_items([snapshot], key, {}), [{"serial": "A", "status": "offline", "bps": 0, "ntrip_connected": False, "offline_since": now - 1}])
            self.assertEqual(cached["status"], status)
            self.assertEqual(normalize_station_snapshot({**cached, "timestamp": now - 180}, now=now)["status"], status)
        self.assertEqual(normalize_station_snapshot({"status": "awaiting_license", "timestamp": (now - 181) * 1000}, now=now)["status"], "offline")
        self.assertEqual(normalize_station_snapshot({"status": "awaiting_license", "timestamp": None}, now=now)["status"], "awaiting_license")
        self.assertEqual(normalize_station_snapshot({"status": "offline", "timestamp": now, "offline_since": now - 500}, now=now)["offline_since"], now - 500)

    async def test_map_cache_hit_and_miss_both_return_effective_offline(self):
        source = Path(__file__).parents[1] / "app" / "map_routes.py"
        function = next(node for node in ast.parse(source.read_text()).body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_get_map_station_items")
        namespace = {
            "Any": Any, "Optional": Optional, "Response": Response, "AsyncSession": object, "Depends": lambda _: None, "get_db": None,
            "time": time, "models": models, "select": __import__("sqlalchemy").select,
            "logging": logging, "logger": logging.getLogger(__name__),
            "schemas": SimpleNamespace(redact_config_secrets=lambda value: value),
            "normalize_station_snapshot": normalize_station_snapshot, "PUBLIC_MAP_CACHE_TTL_SECONDS": 30,
            "_device_total_bps": lambda device: device.bps,
            "crud": SimpleNamespace(is_blocked_device_serial=lambda serial: False),
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        old_timestamp = int(time.time()) - 1000
        item = {"serial": "A", "status": "awaiting_license", "timestamp": old_timestamp, "bps": 1250, "ntrip_connected": True}
        device = SimpleNamespace(**item, name="Alpha", base_config={"lat": 21, "lon": 105}, service_config={}, ntrip_status={}, offline_since=None, is_provisioned=False)
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [device]))))
        for cached in (True, False):
            namespace["public_map_cache"] = [dict(item)] if cached else None
            namespace["public_map_cache_updated_monotonic"] = time.monotonic()
            response = Response()
            result = await namespace["_get_map_station_items"](response, db)
            with self.subTest(cached=cached):
                self.assertEqual(response.headers["X-Data-Cache"], "HIT" if cached else "MISS")
                self.assertEqual(result[0]["status"], "offline")
                self.assertEqual(result[0]["offline_since"], old_timestamp + 180)
                self.assertEqual(namespace["public_map_cache"][0]["status"], "awaiting_license")

    async def test_saved_key_is_encrypted_and_reveal_is_admin_only(self):
        db = SimpleNamespace(add=Mock(), commit=AsyncMock(), refresh=AsyncMock())
        admin = self.admin
        policy = ApiKeyPolicy(name="Saved", expires_at=0, all_stations=True, data_fields=["name"])
        created = await create_api_key(policy, Response(), db, SimpleNamespace(), admin)
        key = db.add.call_args.args[0]
        self.assertNotIn("token", created)
        token = _token_cipher().decrypt(key.token_encrypted.encode()).decode()
        self.assertNotIn(token, key.token_encrypted)
        self.assertEqual(key.key_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertTrue(created["has_saved_token"])
        self.assertNotIn("token", _key_metadata(key))
        self.assertNotIn("token_encrypted", _key_metadata(key))
        db.get = AsyncMock(return_value=key)
        response = Response()
        self.assertEqual(await reveal_api_key(1, self.password, response, db, admin), {"token": token, "expires_in": 30})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        for call in (
            reveal_api_key(1, self.password, Response(), db, SimpleNamespace(role="viewer")),
            save_existing_api_key(1, SavedApiKey(token=token), Response(), db, SimpleNamespace(role="viewer")),
        ):
            with self.assertRaises(HTTPException) as error:
                await call
            self.assertEqual(error.exception.status_code, 403)
        key.revoked_at = 1
        response = Response()
        with self.assertRaises(HTTPException) as error:
            await reveal_api_key(1, self.password, response, db, admin)
        self.assertEqual(error.exception.status_code, 404)

    async def test_legacy_key_import_preserves_existing_token_and_policy(self):
        token = "cors_legacy_token"
        key = models.ApiKey(id=1, name="Legacy", key_hash=hashlib.sha256(token.encode()).hexdigest(), token_encrypted=None, created_at=1, created_by=1, expires_at=0, all_stations=True, station_serials=[], data_fields=["name"], edit_config=False)
        db = SimpleNamespace(get=AsyncMock(return_value=key), commit=AsyncMock())
        admin = self.admin
        before = _key_metadata(key)
        response = Response()
        with self.assertRaises(HTTPException) as error:
            await reveal_api_key(1, self.password, response, db, admin)
        self.assertEqual(error.exception.status_code, 409)
        request = SavedApiKey(token="cors_wrong_token")
        response = Response()
        with self.assertRaises(HTTPException) as error:
            await save_existing_api_key(1, request, response, db, admin)
        self.assertEqual(error.exception.status_code, 422)
        self.assertIsNone(key.token_encrypted)
        db.commit.assert_not_awaited()
        result = await save_existing_api_key(1, SavedApiKey(token=token), Response(), db, admin)
        self.assertEqual(result, {**before, "has_saved_token": True})
        self.assertEqual(key.key_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(await reveal_api_key(1, self.password, Response(), db, admin), {"token": token, "expires_in": 30})

    def test_edit_permission_is_station_scoped(self):
        key = SimpleNamespace(edit_config=True, all_stations=False, station_serials=["A"])
        require_station_edit(key, "A")
        with self.assertRaises(HTTPException) as error:
            require_station_edit(key, "B")
        self.assertEqual(error.exception.status_code, 403)
        key.edit_config = False
        with self.assertRaises(HTTPException):
            require_station_edit(key, "A")


if __name__ == "__main__":
    unittest.main()

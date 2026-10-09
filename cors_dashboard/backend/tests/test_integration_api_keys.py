import hashlib
import sqlite3
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException

from app.routers.api_keys import ApiKeyPolicy, _validate_policy, project_station_items, require_integration_key, require_station_edit


class IntegrationApiKeyTests(unittest.IsolatedAsyncioTestCase):
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

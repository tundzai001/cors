"""Tests for active session control and single-machine blocking."""
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace
from datetime import timedelta
import time
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app import auth, models, schemas


class SessionControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        auth.REVOKED_SESSIONS.clear()
        auth.BLOCKED_DEVICES.clear()
        auth.SESSION_LAST_ACTIVE_CACHE.clear()

    def test_in_memory_tracking_revoked_session(self):
        session_id = "sess_test_123"
        self.assertFalse(auth.is_session_revoked(session_id))
        auth.revoke_session_in_memory(session_id)
        self.assertTrue(auth.is_session_revoked(session_id))

    def test_in_memory_tracking_blocked_device(self):
        device_id = "dev_machine_a"
        self.assertFalse(auth.is_device_blocked(device_id))
        auth.block_device_in_memory(device_id)
        self.assertTrue(auth.is_device_blocked(device_id))
        auth.unblock_device_in_memory(device_id)
        self.assertFalse(auth.is_device_blocked(device_id))

    async def test_get_current_user_rejects_revoked_session(self):
        token = auth.create_access_token(
            data={"sub": "testuser", "role": "viewer", "sid": "sess_revoked_test", "did": "dev_1"},
            expires_delta=timedelta(minutes=30)
        )
        auth.revoke_session_in_memory("sess_revoked_test")

        db = SimpleNamespace(execute=AsyncMock())
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        with self.assertRaises(HTTPException) as ctx:
            await auth.get_current_user(credentials=credentials, db=db)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("bị hủy", ctx.exception.detail)

    async def test_get_current_user_rejects_blocked_device(self):
        token = auth.create_access_token(
            data={"sub": "testuser", "role": "viewer", "sid": "sess_valid_sid", "did": "dev_blocked_pc"},
            expires_delta=timedelta(minutes=30)
        )
        auth.block_device_in_memory("dev_blocked_pc")

        db = SimpleNamespace(execute=AsyncMock())
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        with self.assertRaises(HTTPException) as ctx:
            await auth.get_current_user(credentials=credentials, db=db)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("bị chặn", ctx.exception.detail)

    async def test_single_machine_blocked_does_not_affect_sibling_machine_on_same_network(self):
        # Both machines share the same public IP 113.161.20.10
        shared_public_ip = "113.161.20.10"
        
        # Machine 1
        dev_1 = "dev_office_pc_1"
        sid_1 = "sess_pc_1"
        token_1 = auth.create_access_token(
            data={"sub": "user1", "role": "viewer", "sid": sid_1, "did": dev_1},
            expires_delta=timedelta(minutes=30)
        )

        # Machine 2
        dev_2 = "dev_office_pc_2"
        sid_2 = "sess_pc_2"
        token_2 = auth.create_access_token(
            data={"sub": "user2", "role": "viewer", "sid": sid_2, "did": dev_2},
            expires_delta=timedelta(minutes=30)
        )

        # Admin blocks Machine 1 ONLY
        auth.block_device_in_memory(dev_1)
        auth.revoke_session_in_memory(sid_1)

        db = SimpleNamespace(execute=AsyncMock())
        user_2_obj = models.User(id=2, username="user2", role="viewer", is_active=True)
        # Mock crud lookup for user2
        from unittest.mock import patch
        with patch("app.crud.get_user_by_username", AsyncMock(return_value=user_2_obj)):
            # Machine 1 is blocked:
            cred_1 = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_1)
            with self.assertRaises(HTTPException) as ctx_1:
                await auth.get_current_user(credentials=cred_1, db=db)
            self.assertEqual(ctx_1.exception.status_code, 401) # Session revoked

            # Machine 2 on the SAME IP continues to work!
            cred_2 = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_2)
            user_res = await auth.get_current_user(credentials=cred_2, db=db)
            self.assertEqual(user_res.username, "user2")

    async def test_admin_revoke_and_block_endpoints(self):
        import os
        from pathlib import Path
        backend_dir = Path(__file__).resolve().parents[1]
        prev_cwd = os.getcwd()
        try:
            os.chdir(backend_dir)
            from app.main import revoke_session, block_device_endpoint, unblock_device_endpoint, get_admin_sessions
        finally:
            os.chdir(prev_cwd)

        admin_user = models.User(id=1, username="admin", role="admin", is_active=True)
        
        # Test 1: revoke_session
        test_sess = models.UserSession(
            session_id="sess_target_1",
            user_id=2,
            username="target_user",
            role="viewer",
            ip_address="1.2.3.4",
            local_ip="192.168.1.50",
            device_id="dev_target_1",
            device_name="Target PC",
            user_agent="Mozilla",
            created_at=int(time.time()),
            last_active_at=int(time.time()),
            expires_at=int(time.time()) + 3600,
            is_revoked=False
        )

        auth_db_mock = AsyncMock()
        auth_db_mock.execute.return_value = Mock(scalar_one_or_none=Mock(return_value=test_sess))
        devices_db_mock = AsyncMock()

        with patch("app.crud.log_system_event", AsyncMock()):
            res = await revoke_session(
                request_data={"session_id": "sess_target_1"},
                auth_db=auth_db_mock,
                devices_db=devices_db_mock,
                current_user=admin_user
            )
            self.assertEqual(res["status"], "ok")
            self.assertTrue(test_sess.is_revoked)
            self.assertTrue(auth.is_session_revoked("sess_target_1"))

        # Test 2: block_device_endpoint
        auth_db_mock_block = AsyncMock()
        auth_db_mock_block.add = Mock()
        # Mock first query: latest session
        # Second query: check_stmt
        # Third query: all_sess_stmt
        mock_res_sess = Mock(scalars=Mock(return_value=Mock(first=Mock(return_value=test_sess))))
        mock_res_check = Mock(scalar_one_or_none=Mock(return_value=None))
        mock_res_all_sess = Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=[test_sess]))))
        auth_db_mock_block.execute.side_effect = [mock_res_sess, mock_res_check, mock_res_all_sess]

        with patch("app.crud.log_system_event", AsyncMock()):
            block_res = await block_device_endpoint(
                request_data={"device_id": "dev_target_1", "reason": "Suspicious activity"},
                auth_db=auth_db_mock_block,
                devices_db=devices_db_mock,
                current_user=admin_user
            )
            self.assertEqual(block_res["status"], "ok")
            self.assertTrue(auth.is_device_blocked("dev_target_1"))

        # Test 3: unblock_device_endpoint
        auth_db_mock_unblock = AsyncMock()
        mock_blocked_entry = models.BlockedDevice(device_id="dev_target_1", blocked_at=123, blocked_by="admin")
        auth_db_mock_unblock.execute.return_value = Mock(scalar_one_or_none=Mock(return_value=mock_blocked_entry))

        with patch("app.crud.log_system_event", AsyncMock()):
            unblock_res = await unblock_device_endpoint(
                request_data={"device_id": "dev_target_1"},
                auth_db=auth_db_mock_unblock,
                devices_db=devices_db_mock,
                current_user=admin_user
            )
            self.assertEqual(unblock_res["status"], "ok")
            self.assertFalse(auth.is_device_blocked("dev_target_1"))


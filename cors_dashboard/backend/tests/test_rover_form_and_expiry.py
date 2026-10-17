"""Test rover form fields and expiry update logic."""
import unittest
from unittest.mock import AsyncMock, Mock, patch
import time

from app import models, schemas, auth


class RoverFormAndExpiryTests(unittest.IsolatedAsyncioTestCase):
    def test_rover_multi_create_schema_fields(self):
        req = schemas.RoverMultiCreate(
            username="test_rover",
            password="secret_password",
            expiry_days=30,
            selected_serials=["SN001"],
            mountpoint_override="TEST_MP",
            auto_save_sessions=True,
            auto_save_fixed_points=True
        )
        self.assertTrue(req.auto_save_sessions)
        self.assertTrue(req.auto_save_fixed_points)

    def test_rover_account_create_schema_fields(self):
        req = schemas.RoverAccountCreate(
            username="test_rover_single",
            password="password123",
            auto_save_sessions=False,
            auto_save_fixed_points=False
        )
        self.assertFalse(req.auto_save_sessions)
        self.assertFalse(req.auto_save_fixed_points)

    async def test_update_rover_with_expiry_days_does_not_raise_name_error(self):
        from app.routers.devices import update_rover

        mock_rover = models.RoverAccount(
            id=10,
            username="test_user",
            password="pwd",
            base_serial="SN01",
            mountpoints="MP1",
            max_connections=1,
            show_position=True,
            target_casters="host1",
            rtcm_cycle_enabled=False,
            rtcm_disconnect_interval_seconds=300,
            rtcm_disconnect_duration_seconds=10,
            expires_at=int(time.time()) + 86400,
            is_shared_with_managers=False,
            auto_save_sessions=True,
            auto_save_fixed_points=False,
            created_at=int(time.time()),
            is_active=True
        )

        auth_db_mock = AsyncMock()
        auth_db_mock.execute.return_value = Mock(scalar_one_or_none=Mock(return_value=mock_rover))
        devices_db_mock = AsyncMock()

        admin_user = models.User(id=1, username="admin", role="admin", is_active=True)
        update_req = schemas.RoverUpdate(
            expiry_days=365,
            show_position=True
        )

        with patch("app.crud.get_rover_by_id", AsyncMock(return_value=mock_rover)):
            with patch("app.crud.check_rover_management_permission", AsyncMock(return_value=True)):
                with patch("app.routers.devices._read_caster_config_from_env", return_value=(["host1"], {"host": "host1"})):
                    with patch("app.routers.devices.validate_caster_hosts", return_value=["host1"]):
                        with patch("app.routers.devices.CasterClient") as MockCaster:
                            mock_instance = AsyncMock()
                            MockCaster.return_value = mock_instance
                            # Should execute smoothly without NameError: name 'time' is not defined
                            result = await update_rover(
                                rover_id=10,
                                update_req=update_req,
                                db=auth_db_mock,
                                devices_db=devices_db_mock,
                                current_user=admin_user
                            )
                            self.assertEqual(result.username, "test_user")
                            self.assertGreater(mock_rover.expires_at, int(time.time()) + 364 * 86400)

    async def test_rover_password_preserved_for_coordinator(self):
        from app.routers.devices import _rover_response_list
        from app.rover_accounts_routes import get_all_rovers_global

        mock_rover = models.RoverAccount(
            id=10,
            username="test_rover",
            password="my_secret_pass",
            base_serial="SN01",
            mountpoints="MP1",
            max_connections=1,
            show_position=True,
            target_casters="host1",
            rtcm_cycle_enabled=False,
            rtcm_disconnect_interval_seconds=300,
            rtcm_disconnect_duration_seconds=10,
            expires_at=int(time.time()) + 86400,
            is_shared_with_managers=False,
            auto_save_sessions=True,
            auto_save_fixed_points=False,
            created_at=int(time.time()),
            is_active=True
        )

        coord_user = models.User(id=2, username="datnw", role=auth.Role.COORDINATOR, is_active=True, can_view_rovers=True)

        # 1. Test _rover_response_list
        items = _rover_response_list([mock_rover], coord_user)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["password"], "my_secret_pass")

        # 2. Test get_all_rovers_global
        auth_db_mock = AsyncMock()
        devices_db_mock = AsyncMock()
        device_result_mock = Mock()
        device_result_mock.scalars.return_value.all.return_value = []
        devices_db_mock.execute.return_value = device_result_mock

        with patch("app.auth.has_permission", return_value=True):
            with patch("app.crud.get_visible_rovers_for_user", AsyncMock(return_value=[mock_rover])):
                enriched = await get_all_rovers_global(
                    db=auth_db_mock,
                    devices_db=devices_db_mock,
                    current_user=coord_user
                )
                self.assertEqual(len(enriched), 1)
                self.assertEqual(enriched[0]["password"], "my_secret_pass")

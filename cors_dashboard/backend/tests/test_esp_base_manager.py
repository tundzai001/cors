import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import json
import time

from app import models
from app.esp_base_manager import (
    _extract_esp_mountpoint,
    auto_provision_esp_rover_account,
    auto_deprovision_esp_rover_account,
    sync_esp_base_rovers,
    OFFLINE_DELETE_THRESHOLD_SECONDS,
    DEFAULT_ROVER_PASSWORD,
)


class EspBaseManagerTests(unittest.IsolatedAsyncioTestCase):

    def test_extract_esp_mountpoint(self):
        dev = models.Device(serial="esp_1234567890AB")
        
        # Test dict service_config
        dev.service_config = {"mountpoint1": "ESP_TEST", "server1_enabled": True}
        self.assertEqual(_extract_esp_mountpoint(dev), "ESP_TEST")

        # Test active_mountpoint fallback
        dev.service_config = {"active_mountpoint": "/ESP_ACTIVE"}
        self.assertEqual(_extract_esp_mountpoint(dev), "ESP_ACTIVE")

        # Test json string
        dev.service_config = json.dumps({"mountpoint1": "JSON_MP"})
        self.assertEqual(_extract_esp_mountpoint(dev), "JSON_MP")

        # Test empty
        dev.service_config = {}
        self.assertIsNone(_extract_esp_mountpoint(dev))

    @patch("app.esp_base_manager._get_configured_casters")
    @patch("app.esp_base_manager.notify_decoder_worker", new_callable=AsyncMock)
    @patch("app.esp_base_manager.auth_session_scope")
    async def test_auto_provision_creates_rover_on_caster_and_db(
        self, mock_auth_scope, mock_notify, mock_casters
    ):
        mock_casters.return_value = [("127.0.0.1", 2101, "admin", "12345")]
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_auth_scope.return_value.__aenter__.return_value = mock_session
        
        # Existing query returns None (not yet in DB)
        mock_rs = MagicMock()
        mock_rs.scalars.return_value.first.return_value = None
        mock_session.execute.return_value = mock_rs

        dev = models.Device(serial="esp_1234567890AB", status="online")
        
        with patch("app.esp_base_manager.CasterClient") as MockCasterClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.create_user.return_value = True
            MockCasterClient.return_value = mock_client_instance

            ok = await auto_provision_esp_rover_account(dev, "ESP_TEST")
            self.assertTrue(ok)

            # Check Caster creation parameters
            mock_client_instance.create_user.assert_awaited_once_with(
                creator="system",
                username="ESP_TEST",
                password=DEFAULT_ROVER_PASSWORD,
                mountpoints="ESP_TEST",
                max_users=0,  # unlimited
                show_position=False,
                rtcm_cycle_enabled=False,
            )

            # Check DB persistence
            self.assertTrue(mock_session.add.called)
            added_rover = mock_session.add.call_args[0][0]
            self.assertEqual(added_rover.username, "ESP_TEST")
            self.assertEqual(added_rover.password, DEFAULT_ROVER_PASSWORD)
            self.assertEqual(added_rover.base_serial, "esp_1234567890AB")
            self.assertEqual(added_rover.max_connections, 0)
            self.assertFalse(added_rover.show_position)
            self.assertFalse(added_rover.auto_save_sessions)
            self.assertFalse(added_rover.auto_save_fixed_points)
            self.assertFalse(added_rover.rtcm_cycle_enabled)
            self.assertFalse(added_rover.is_shared_with_managers)
            self.assertIsNone(added_rover.expires_at)
            self.assertIsNone(added_rover.created_by_user_id)

            # Check decoder notified
            mock_notify.assert_awaited_once_with(
                "start", "esp_1234567890AB", "ESP_TEST", "ESP_TEST", DEFAULT_ROVER_PASSWORD, lat=0.0, lon=0.0, alt=0.0
            )

    @patch("app.esp_base_manager._get_configured_casters")
    @patch("app.esp_base_manager.notify_decoder_worker", new_callable=AsyncMock)
    @patch("app.esp_base_manager.auth_session_scope")
    async def test_auto_deprovision_deletes_from_caster_and_db(
        self, mock_auth_scope, mock_notify, mock_casters
    ):
        mock_casters.return_value = [("127.0.0.1", 2101, "admin", "12345")]
        mock_session = AsyncMock()
        mock_session.delete = AsyncMock()
        mock_auth_scope.return_value.__aenter__.return_value = mock_session

        rover = models.RoverAccount(
            username="ESP_TEST",
            base_serial="esp_1234567890AB",
            created_by_user_id=None,
        )
        mock_rs = MagicMock()
        mock_rs.scalars.return_value.all.return_value = [rover]
        mock_session.execute.return_value = mock_rs

        with patch("app.esp_base_manager.CasterClient") as MockCasterClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.delete_user.return_value = True
            MockCasterClient.return_value = mock_client_instance

            ok = await auto_deprovision_esp_rover_account("esp_1234567890AB", "ESP_TEST")
            self.assertTrue(ok)

            mock_client_instance.delete_user.assert_awaited_once_with(
                identifier="system", username="ESP_TEST"
            )
            mock_session.delete.assert_called_once_with(rover)
            mock_notify.assert_awaited_once_with(
                "stop", "esp_1234567890AB", "ESP_TEST", "ESP_TEST", DEFAULT_ROVER_PASSWORD
            )

    @patch("app.esp_base_manager.auto_deprovision_esp_rover_account", new_callable=AsyncMock)
    @patch("app.esp_base_manager.auto_provision_esp_rover_account", new_callable=AsyncMock)
    @patch("app.esp_base_manager.auth_session_scope")
    @patch("app.esp_base_manager.device_session_scope")
    async def test_sync_esp_base_rovers_offline_threshold(
        self, mock_dev_scope, mock_auth_scope, mock_prov, mock_deprov
    ):
        now = int(time.time())
        # Base 1: online with mountpoint -> should provision
        dev_online = models.Device(
            serial="esp_ONLINE1111",
            status="online",
            service_config={"mountpoint1": "ONLINE_MP"},
        )
        # Base 2: offline for 100s (< 300s) -> should NOT deprovision
        dev_recent_offline = models.Device(
            serial="esp_OFFLINE_REC",
            status="offline",
            timestamp=now - 100,
            offline_since=now - 100,
            service_config={"mountpoint1": "REC_MP"},
        )
        # Base 3: offline for 350s (>= 300s) -> should deprovision
        dev_old_offline = models.Device(
            serial="esp_OFFLINE_OLD",
            status="offline",
            timestamp=now - 350,
            offline_since=now - 350,
            service_config={"mountpoint1": "OLD_MP"},
        )

        mock_dev_session = AsyncMock()
        mock_dev_scope.return_value.__aenter__.return_value = mock_dev_session
        dev_rs = MagicMock()
        dev_rs.scalars.return_value.all.return_value = [
            dev_online, dev_recent_offline, dev_old_offline
        ]
        mock_dev_session.execute.return_value = dev_rs

        rover_old = models.RoverAccount(
            username="OLD_MP",
            base_serial="esp_OFFLINE_OLD",
            created_by_user_id=None,
        )
        mock_auth_session = AsyncMock()
        mock_auth_scope.return_value.__aenter__.return_value = mock_auth_session
        auth_rs = MagicMock()
        auth_rs.scalars.return_value.all.return_value = [rover_old]
        mock_auth_session.execute.return_value = auth_rs

        await sync_esp_base_rovers()

        # dev_online has no rover -> auto_provision called
        mock_prov.assert_awaited_once_with(dev_online, "ONLINE_MP")
        # dev_old_offline has been offline for 350s >= 300s -> auto_deprovision called
        mock_deprov.assert_awaited_once_with("esp_OFFLINE_OLD", "OLD_MP")


if __name__ == "__main__":
    unittest.main()

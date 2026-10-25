import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import json
import time

from app import models
from app import esp_base_manager as manager
from app.esp_base_manager import (
    _extract_esp_mountpoint,
    auto_provision_esp_rover_account,
    auto_deprovision_esp_rover_account,
    sync_esp_base_rovers,
    DEFAULT_ROVER_PASSWORD,
    notify_decoder_worker,
    MQTT_DECODER_TOPIC,
)


class EspBaseManagerTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.stopped_patch = patch.object(manager, "_stopped_decoder_serials", set())
        self.stopped_patch.start()
        self.addCleanup(self.stopped_patch.stop)

    async def test_duplicate_stops_suppressed_until_start_or_decoder_resync(self):
        client = MagicMock()
        client.is_connected.return_value = True
        client.publish.return_value.rc = 0
        with patch("app.mqtt.mqtt_client", client):
            for serial, mp in (("esp_112233445566", "test"), ("MONITOR_70001", "70001")):
                client.publish.reset_mock()
                await notify_decoder_worker("stop", serial, mp, mp, "")
                await notify_decoder_worker("stop", serial, mp, mp, "")
                client.publish.assert_called_once()
                await notify_decoder_worker("start", serial, mp, mp, "password")
                await notify_decoder_worker("stop", serial, mp, mp, "")
                self.assertEqual(client.publish.call_count, 3)
            with patch.object(manager, "sync_prefix7_bases", new_callable=AsyncMock) as prefix7, \
                 patch.object(manager, "sync_esp_base_rovers", new_callable=AsyncMock) as esp:
                await manager.resync_decoder_workers()
                prefix7.assert_awaited_once()
                esp.assert_awaited_once()
            client.publish.reset_mock()
            await notify_decoder_worker("stop", "esp_112233445566", "test", "test", "")
            client.publish.assert_called_once()

    async def test_failed_or_disconnected_stop_can_retry(self):
        client = MagicMock()
        client.is_connected.return_value = False
        with patch("app.mqtt.mqtt_client", client):
            await notify_decoder_worker("stop", "esp_test", "test", "test", "")
            client.publish.assert_not_called()
            client.is_connected.return_value = True
            client.publish.return_value.rc = 4
            with self.assertLogs("app.esp_base_manager", level="ERROR"):
                await notify_decoder_worker("stop", "esp_test", "test", "test", "")
            client.publish.return_value.rc = 0
            await notify_decoder_worker("stop", "esp_test", "test", "test", "")
            await notify_decoder_worker("stop", "esp_test", "test", "test", "")
            self.assertEqual(client.publish.call_count, 2)

    async def test_decoder_start_stop_use_shared_mqtt_client(self):
        client = MagicMock()
        client.is_connected.return_value = True
        client.publish.return_value.rc = 0
        with patch("app.mqtt.mqtt_client", client):
            for action in ("start", "stop"):
                client.publish.reset_mock()
                await notify_decoder_worker(action, "esp_test", "TEST", "rover", "test-password", lat=21.0, lon=105.0, alt=12.0)
                client.publish.assert_called_once()
                args, kwargs = client.publish.call_args
                self.assertEqual(args[0], MQTT_DECODER_TOPIC)
                payload = json.loads(args[1])
                self.assertEqual(payload["action"], action)
                self.assertEqual(payload["serial"], "esp_test")
                self.assertEqual(payload["mountpoint"], "TEST")
                self.assertEqual(payload["rover_user"], "rover")
                self.assertEqual(payload["rover_pass"], "test-password")
                self.assertEqual((payload["lat"], payload["lon"], payload["alt"]), (21.0, 105.0, 12.0))
                self.assertEqual(kwargs["qos"], 1)
            client.publish.reset_mock()
            client.is_connected.return_value = False
            await notify_decoder_worker("start", "esp_test", "TEST", "rover", "test-password")
            client.publish.assert_not_called()

    async def test_decoder_publish_failure_does_not_report_sent(self):
        client = MagicMock()
        client.is_connected.return_value = True
        client.publish.return_value.rc = 4
        with patch("app.mqtt.mqtt_client", client), self.assertLogs("app.esp_base_manager", level="INFO") as logs:
            await notify_decoder_worker("start", "esp_test", "TEST", "rover", "test-password")
        self.assertTrue(any("Failed to publish" in line for line in logs.output))
        self.assertFalse(any("Sent start" in line for line in logs.output))

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
                "start", "esp_1234567890AB", "ESP_TEST", "ESP_TEST", DEFAULT_ROVER_PASSWORD, lat=0.0, lon=0.0, alt=0.0, host=None, port=None
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

    async def test_deprovision_without_owned_system_account_never_deletes_caster_user(self):
        with patch('app.esp_base_manager.auth_session_scope') as auth_scope, \
             patch('app.esp_base_manager._get_configured_casters', new_callable=AsyncMock) as casters, \
             patch('app.esp_base_manager.notify_decoder_worker', new_callable=AsyncMock) as notify:
            db = AsyncMock()
            auth_scope.return_value.__aenter__.return_value = db
            result = MagicMock()
            result.scalars.return_value.all.return_value = []
            db.execute.return_value = result
            self.assertFalse(await auto_deprovision_esp_rover_account('esp_test', 'MANUAL'))
            casters.assert_not_awaited()
            db.delete.assert_not_awaited()
            notify.assert_not_awaited()

    @patch("app.esp_base_manager.auto_deprovision_esp_rover_account", new_callable=AsyncMock)
    @patch("app.esp_base_manager.auto_provision_esp_rover_account", new_callable=AsyncMock)
    @patch("app.esp_base_manager.auth_session_scope")
    @patch("app.esp_base_manager.device_session_scope")
    async def test_sync_esp_base_rovers_deletes_system_accounts_on_first_offline_sync(
        self, mock_dev_scope, mock_auth_scope, mock_prov, mock_deprov
    ):
        now = int(time.time())
        # Base 1: online with mountpoint -> should provision
        dev_online = models.Device(
            serial="esp_ONLINE1111",
            status="online",
            service_config={"mountpoint1": "ONLINE_MP"},
        )
        # Base 2: newly offline -> deprovision without a five-minute delay.
        dev_recent_offline = models.Device(
            serial="esp_OFFLINE_REC",
            status="offline",
            timestamp=now,
            offline_since=now,
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
        rover_recent = models.RoverAccount(username='REC_MP', base_serial='esp_OFFLINE_REC',
                                          created_by_user_id=None)
        manual_rover = models.RoverAccount(username='MANUAL', base_serial='esp_OFFLINE_REC',
                                          created_by_user_id=1)
        mock_auth_session = AsyncMock()
        mock_auth_scope.return_value.__aenter__.return_value = mock_auth_session
        auth_rs = MagicMock()
        auth_rs.scalars.return_value.all.return_value = [rover_old, rover_recent, manual_rover]
        mock_auth_session.execute.return_value = auth_rs

        source_rs = MagicMock()
        source_rs.scalars.return_value.all.return_value = [models.CasterSource(
            host='caster.test', port=2101, mountpoint='ONLINE_MP',
            source_data={'datacount': 50}, last_seen_at=now)]
        mock_auth_session.execute.side_effect = [auth_rs, source_rs]
        await sync_esp_base_rovers()

        # dev_online has no rover -> auto_provision called
        mock_prov.assert_awaited_once_with(dev_online, "ONLINE_MP")
        self.assertEqual(mock_deprov.await_count, 2)
        mock_deprov.assert_any_await('esp_OFFLINE_REC', 'REC_MP')
        mock_deprov.assert_any_await('esp_OFFLINE_OLD', 'OLD_MP')

    async def test_esp_decoder_stops_without_live_source_and_resumes_with_data(self):
        now = int(time.time())
        device = models.Device(serial='esp_TEST', status='online',
                               service_config={'mountpoint1': 'test', 'serverhost1': 'caster.test', 'port1': 2101})
        rover = models.RoverAccount(username='test', password='saved-password', base_serial=device.serial)
        source = models.CasterSource(host='caster.test', port=2101, mountpoint='test',
                                    source_data={'datacount': 50}, last_seen_at=now)
        def rows(items):
            result = MagicMock()
            result.scalars.return_value.all.return_value = items
            return result
        with patch('app.esp_base_manager.device_session_scope') as dev_scope, \
             patch('app.esp_base_manager.auth_session_scope') as auth_scope, \
             patch('app.esp_base_manager.notify_decoder_worker', new_callable=AsyncMock) as notify, \
             patch('app.esp_base_manager.auto_deprovision_esp_rover_account', new_callable=AsyncMock), \
             patch('app.esp_base_manager.auto_provision_esp_rover_account', new_callable=AsyncMock) as provision:
            dev_db, auth_db = AsyncMock(), AsyncMock()
            dev_scope.return_value.__aenter__.return_value = dev_db
            auth_scope.return_value.__aenter__.return_value = auth_db
            dev_db.execute.return_value = rows([device])
            for status, count, age, host, expected in (
                ('sleep', 50, 0, 'caster.test', 'start'),
                ('offline', 50, 0, 'caster.test', 'start'),
                ('offline', 0, 0, 'caster.test', 'stop'),
                ('online', 0, 0, 'caster.test', 'stop'),
                ('online', 50, 120, 'caster.test', 'stop'),
                ('online', 50, 0, 'other.test', 'stop'),
                ('online', 50, 0, 'caster.test', 'start'),
            ):
                device.status = status
                device.offline_since = now
                source.source_data = {'datacount': count}
                source.last_seen_at = now - age
                source.host = host
                auth_db.execute.side_effect = [rows([rover]), rows([source])]
                notify.reset_mock()
                await sync_esp_base_rovers()
                self.assertEqual(notify.await_args.args[0], expected, (status, count, age, host))
            self.assertEqual(notify.await_args.args[4], 'saved-password')
            self.assertEqual(notify.await_args.kwargs['host'], 'caster.test')
            self.assertEqual(notify.await_args.kwargs['port'], 2101)
            provision.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

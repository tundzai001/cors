"""Decoder input must not be gated by its own offline telemetry/wake leases."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from app import ntrip_control as control


class Prefix7NtripControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_mobile_wake_never_reads_decoder_status_or_creates_lease(self):
        with patch.object(control, '_check_rover_blocked', AsyncMock(return_value=None)), \
             patch.object(control, 'device_session_scope') as db, \
             patch.object(control, '_ntrip_mqtt_start', AsyncMock()) as start:
            response = await control._do_ntrip_wake('70717', '70717', 90, 'caster.test')
        body = response.body.decode()
        self.assertIn('WAKE_STATUS: ok', body)
        self.assertIn('CONTROL_MODE: passive', body)
        self.assertIn('STREAM_HINT: pending', body)
        self.assertNotIn('offline', body)
        db.assert_not_called()
        start.assert_not_awaited()

    async def test_passive_wake_still_enforces_rover_blocklist(self):
        with patch.object(control, '_check_rover_blocked', AsyncMock(return_value='blocked account')):
            response = await control._do_ntrip_wake('70717', '70717', 90)
        self.assertIn('WAKE_STATUS: blocked', response.body.decode())

    async def test_mobile_disconnect_does_not_stop_station(self):
        with patch.object(control, 'device_session_scope') as db, \
             patch.object(control, '_set_station_stream', AsyncMock()) as send:
            for force in (False, True):
                response = await control._do_ntrip_stop('70717', '70717', force=force)
                self.assertIn('CONTROL_MODE: passive', response.body.decode())
        db.assert_not_called()
        send.assert_not_awaited()

    async def test_old_mobile_leases_cannot_toggle_stream(self):
        with patch.object(control, 'device_session_scope') as db, \
             patch.object(control, 'send_command_to_pi', AsyncMock()) as send:
            for active in (False, True):
                await control._set_station_stream('MONITOR_70717', active, mountpoint='70717')
        db.assert_not_called()
        send.assert_not_awaited()

    async def test_fixed_station_keeps_offline_gate(self):
        with patch.object(control, '_check_rover_blocked', AsyncMock(return_value=None)), \
             patch.object(control, 'device_session_scope'), \
             patch.object(control, '_resolve_station_serial_by_mountpoint', AsyncMock(return_value='fixed')), \
             patch.object(control.crud, 'get_device_by_serial', AsyncMock(return_value=MagicMock(status='offline'))):
            response = await control._do_ntrip_wake('ATG_DT', 'rover', 90)
        self.assertIn('WAKE_STATUS: offline', response.body.decode())

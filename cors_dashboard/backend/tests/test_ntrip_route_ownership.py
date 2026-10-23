"""Route ownership must separate relay inputs from managed outputs."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from app import models, ntrip_control as control


def rows(items):
    result = MagicMock()
    result.all.return_value = items
    return result


class RouteOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = patch.multiple(control, mountpoint_serial_cache={}, command_mountpoints_cache=set(),
                                    mountpoint_cache_last_updated=0)
        self.state.start()
        self.addCleanup(self.state.stop)

    async def test_enabled_owner_wins_over_disabled_duplicate_in_either_row_order(self):
        old = ('old', {'mountpoint1': 'SinQuyen2', 'server1_enabled': False}, {})
        live = ('live', {'mountpoint1': 'SinQuyen2', 'server1_enabled': True}, {})
        for entries in ([old, live], [live, old]):
            control.mountpoint_cache_last_updated = 0
            db = AsyncMock()
            db.execute.return_value = rows(entries)
            self.assertEqual(await control._resolve_station_serial_by_mountpoint(db, 'SinQuyen2'), 'live')

    async def test_wake_refreshes_stale_owner_before_offline_gate(self):
        old = models.Device(serial='old', status='offline', service_config={'mountpoint1': 'SinQuyen2', 'server1_enabled': False})
        live = models.Device(serial='live', status='online', service_config={'mountpoint1': 'SinQuyen2', 'server1_enabled': True})
        with patch.object(control, '_check_rover_blocked', AsyncMock(return_value=None)), \
             patch.object(control, 'device_session_scope'), \
             patch.object(control, '_resolve_station_serial_by_mountpoint', AsyncMock(side_effect=['old', 'live'])) as resolve, \
             patch.object(control.crud, 'get_device_by_serial', AsyncMock(side_effect=[old, live])), \
             patch.object(control, '_ntrip_mqtt_start', AsyncMock(return_value={'wake_status': 'joined-existing', 'connected': True})) as start:
            response = await control._do_ntrip_wake('SinQuyen2', 'rover', 90)
        self.assertIn('WAKE_STATUS: joined-existing', response.body.decode())
        self.assertEqual(start.await_args.kwargs['serial'], 'live')
        self.assertTrue(resolve.await_args.kwargs['force_refresh'])

    async def test_registered_relay_input_is_passive_even_when_relay_is_offline(self):
        entry = ('RELAY_ATG_DT', {'server1_enabled': True, 'mountpoint1': 'ATG_DT',
                                'relay_source': {'mountpoint': 'ATGDT', 'host': 'caster.test'}}, {})
        db = AsyncMock()
        db.execute.return_value = rows([entry])
        with patch.object(control, '_check_rover_blocked', AsyncMock(return_value=None)), \
             patch.object(control, 'device_session_scope') as scope, \
             patch.object(control, '_ntrip_mqtt_start', AsyncMock()) as start, \
             patch.object(control, '_set_station_stream', AsyncMock()) as send:
            scope.return_value.__aenter__.return_value = db
            response = await control._do_ntrip_wake('ATGDT', 'atg', 90, 'caster.test')
            self.assertIn('CONTROL_MODE: passive', response.body.decode())
            self.assertIn('WAKE_STATUS: ok', response.body.decode())
            response = await control._do_ntrip_stop('ATGDT', 'atg', requested_address='caster.test')
            self.assertIn('CONTROL_MODE: passive', response.body.decode())
        start.assert_not_awaited()
        send.assert_not_awaited()

    async def test_unknown_mp_is_not_silently_accepted(self):
        db = AsyncMock()
        db.execute.return_value = rows([])
        with patch.object(control, '_check_rover_blocked', AsyncMock(return_value=None)), \
             patch.object(control, 'device_session_scope') as scope:
            scope.return_value.__aenter__.return_value = db
            response = await control._do_ntrip_wake('UNKNOWN', 'rover', 90)
        self.assertIn('WAKE_STATUS: error', response.body.decode())

    async def test_relay_input_does_not_override_managed_output_owner(self):
        for entries in (
            [('RELAY_A', {'relay_source': {'mountpoint': 'ATGDT'}}, {}),
             ('station', {'mountpoint1': 'ATGDT', 'server1_enabled': True}, {})],
            [('A', {'mountpoint1': 'ATGDT', 'server1_enabled': True}, {}),
             ('RELAY_Z', {'relay_source': {'mountpoint': 'ATGDT'}}, {})],
        ):
            db = AsyncMock()
            db.execute.return_value = rows(entries)
            serial = await control._resolve_station_serial_by_mountpoint(db, 'ATGDT', force_refresh=True)
            self.assertIn(serial, ('station', 'A'))

    async def test_disabled_output_is_still_rejected_after_refresh(self):
        device = models.Device(serial='station', status='online',
                               service_config={'mountpoint1': 'OTHER', 'server1_enabled': True,
                                               'mountpoint2': 'SinQuyen2', 'server2_enabled': False})
        with patch.object(control, '_check_rover_blocked', AsyncMock(return_value=None)), \
             patch.object(control, 'device_session_scope'), \
             patch.object(control, '_resolve_station_serial_by_mountpoint', AsyncMock(return_value='station')), \
             patch.object(control.crud, 'get_device_by_serial', AsyncMock(return_value=device)), \
             patch.object(control, '_set_station_stream', AsyncMock()) as send:
            response = await control._do_ntrip_wake('SinQuyen2', 'rover', 90)
        self.assertIn('WAKE_STATUS: error', response.body.decode())
        self.assertIn('not enabled', response.body.decode())
        send.assert_not_awaited()

    async def test_relay_source_wake_still_checks_blocklist(self):
        with patch.object(control, '_check_rover_blocked', AsyncMock(return_value='blocked')), \
             patch.object(control, 'device_session_scope') as scope:
            response = await control._do_ntrip_wake('ATGDT', 'atg', 90)
        self.assertIn('WAKE_STATUS: blocked', response.body.decode())
        scope.assert_not_called()

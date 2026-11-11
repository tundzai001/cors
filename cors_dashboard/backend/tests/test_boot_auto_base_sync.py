import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import crud, models
from app.crud import confirmed_boot_auto_base


class BootAutoBaseSyncTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            'serial': 'esp_test', 'status': 'online', 'status_boot_id': 'boot-1',
            'base_config': {'base_setup_method': 'FIXED_LLA', 'auto_base_started_boot_id': 'boot-1',
                            'coords': {'lat': 21.0, 'lon': 105.0, 'alt': 12.0}},
            'auto_base_progress': {'phase': 'completed', 'auto_applied': True,
                                   'receiver_write_completed': True, 'fixed_samples_only': True,
                                   'averaged_coord': {'lat': '21.0', 'lon': '105.0', 'alt': '12.0'}},
        }

    def test_fixed_result_requires_current_boot_and_confirmed_coordinates(self):
        self.assertTrue(confirmed_boot_auto_base(self.data))
        for mutation in ('boot', 'coordinates', 'incomplete'):
            data = copy.deepcopy(self.data)
            if mutation == 'boot': data['status_boot_id'] = 'boot-2'
            if mutation == 'coordinates': data['base_config']['coords']['lat'] = 22
            if mutation == 'incomplete': data['auto_base_progress']['receiver_write_completed'] = False
            self.assertFalse(confirmed_boot_auto_base(data))

    def test_survey_only_after_outside_radius_fallback(self):
        self.data['base_config']['base_setup_method'] = 'SURVEY_IN'
        self.data['auto_base_progress'] = {'phase': 'completed', 'auto_applied': True,
            'receiver_write_completed': True, 'mode': 'survey_in',
            'reason': 'no_fixed_station_within_20_km'}
        self.assertTrue(confirmed_boot_auto_base(self.data))
        self.data['auto_base_progress']['reason'] = 'connection_error'
        self.assertFalse(confirmed_boot_auto_base(self.data))


class BootAutoBaseDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsynced_survey_target_is_updated_after_confirmed_fixed(self):
        fixture = BootAutoBaseSyncTests(); fixture.setUp()
        data = fixture.data
        data['serial'] = 'esp_9896E5124B00'
        data['is_synced'] = False
        device = models.Device(serial=data['serial'], status='online', is_synced=False,
            base_config={'base_setup_method': 'SURVEY_IN'}, service_config={},
            detected_chip_type='Unicorecomm', system_info={}, is_locked=False,
            timestamp=1, ntrip_status={}, is_provisioned=True)
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())
        with patch.object(crud, 'get_device_for_update', AsyncMock(return_value=device)):
            await crud.update_or_create_device(db, data)
        params = db.execute.await_args.args[0].compile().params
        self.assertEqual(params['base_config']['base_setup_method'], 'FIXED_LLA')

    async def test_unsynced_auto_target_accepts_confirmed_survey_without_coordinates(self):
        data = {'serial': 'esp_9896E5124B00', 'status': 'online', 'status_boot_id': 'boot-1',
            'is_synced': False, 'base_config': {'base_setup_method': 'SURVEY_IN',
                'auto_base_started_boot_id': 'boot-1'},
            'auto_base_progress': {'phase': 'completed', 'auto_applied': True,
                'receiver_write_completed': True, 'mode': 'survey_in',
                'reason': 'no_fixed_station_within_20_km'}}
        device = models.Device(serial=data['serial'], status='online', is_synced=False,
            base_config={'base_setup_method': 'AUTO_CORS'}, service_config={},
            detected_chip_type='Unicorecomm', system_info={}, is_locked=False,
            timestamp=1, ntrip_status={}, is_provisioned=True)
        db = SimpleNamespace(execute=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())
        with patch.object(crud, 'get_device_for_update', AsyncMock(return_value=device)):
            await crud.update_or_create_device(db, data)
        params = db.execute.await_args.args[0].compile().params
        self.assertEqual(params['base_config']['base_setup_method'], 'SURVEY_IN')
        self.assertNotIn('coords', params['base_config'])


if __name__ == '__main__':
    unittest.main()

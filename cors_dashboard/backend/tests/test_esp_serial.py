import unittest

from app import crud


class EspSerialTests(unittest.TestCase):
    def test_accepts_mobile_base_serial_and_case_variants(self):
        for serial in ('esp_9896E5124B00', 'ESP_9896e5124b00', 'esp_000000000001'):
            with self.subTest(serial=serial):
                self.assertTrue(crud.is_valid_serial(serial))

    def test_rejects_malformed_esp_serials(self):
        for serial in ('esp_', 'esp_123', 'esp_9896E5124B000', 'esp_9896E5124B0G',
                       'esp_9896E5124B00/command', 'esp_9896E5 24B00',
                       'esp_9896E5124B00\nother', 'esp_9896E5124B00;DROP'):
            with self.subTest(serial=serial):
                self.assertFalse(crud.is_valid_serial(serial))

    def test_fixed_station_formats_remain_accepted(self):
        for serial in ('1000000037a7201b', 'LP_00E04C0CFB9E', 'RELAY_ATG_DT',
                       '860549070087338', 'MONITOR_TEST'):
            with self.subTest(serial=serial):
                self.assertTrue(crud.is_valid_serial(serial))

    def test_pi_prefix_is_blocked_in_all_cases(self):
        for serial in ('PI_0bd46c72e7be', 'PI_8893150dff03', 'PI_b5ca1be152f8',
                       'pi_112233445566', ' Pi_ABCDEF123456 ', 'PI_anything'):
            with self.subTest(serial=serial):
                self.assertTrue(crud.is_blocked_device_serial(serial))
                self.assertFalse(crud.is_valid_serial(serial))

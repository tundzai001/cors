import json
from pathlib import Path
import unittest
from app.client_device import client_device_name


class ClientDeviceTests(unittest.TestCase):
    def test_current_and_historical_mobile_labels(self):
        cases = json.loads((Path(__file__).resolve().parents[3] / 'scripts/fixtures/session-devices.json').read_text(encoding='utf-8'))
        for case in cases:
            with self.subTest(expected=case['expected']):
                self.assertEqual(client_device_name(case['ua'], case.get('fallback', '')), case['expected'])
        self.assertEqual(client_device_name('', 'My device'), 'My device')
        self.assertEqual(client_device_name(None), 'Thiết bị')

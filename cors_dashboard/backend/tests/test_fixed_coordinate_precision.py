"""Fixed coordinates preserve decimal text until receiver wire encoding."""
from decimal import Decimal
import unittest
from app import command_builder as builder, crud


class FixedCoordinatePrecisionTests(unittest.TestCase):
    def test_fixed_accuracy_is_decimal_meters_without_float_truncation(self):
        for meters, expected in (('0.01', 100), ('0.05', 500), ('0.0029', 29),
                                 ('0.001', 10), ('0.1234', 1234)):
            packet = builder.build_base_fixed_lla_command('Ublox', 20, 106, 30, meters)[0]
            self.assertEqual(int.from_bytes(packet[26:30], 'little'), expected, meters)

    def test_unicore_preserves_all_ten_digits_and_trailing_zeros(self):
        coords = {'lat': '20.1234567890', 'lon': '106.9876543210', 'alt': '-30.1234567890'}
        saved = crud.normalize_base_config({'coords': coords})
        self.assertEqual(saved['coords'], coords)
        commands = builder.build_base_fixed_lla_command('Unicorecomm', **saved['coords'], accuracy=.01)
        self.assertIn(b'mode base 20.1234567890 106.9876543210 -30.1234567890\r\n', commands)
        commands = builder.build_base_fixed_lla_command('Unicorecomm', '20,1234567890', '106,9876543210', '30,0000000000', .01)
        self.assertIn(b'mode base 20.1234567890 106.9876543210 30.0000000000\r\n', commands)

    def test_ublox_quantizes_exact_decimal_without_float_residue(self):
        for coords in (('20.1234567890', '106.9876543210', '-30.1234567890'),
                       ('-20.0000000010', '-106.0000000990', '30.0001000000'),
                       ('89.9999999999', '179.9999999999', '0.0000999999')):
            packet = builder.build_base_fixed_lla_command('Ublox', *coords, accuracy=.01)[0]
            for text, offset, hp_offset, scale in zip(coords, (10, 14, 18), (22, 23, 24), (10**9, 10**9, 10**4)):
                whole = int.from_bytes(packet[offset:offset+4], 'little', signed=True)
                hp = int.from_bytes(packet[hp_offset:hp_offset+1], 'little', signed=True)
                self.assertEqual(whole * 100 + hp, int(Decimal(text) * scale))
            a = b = 0
            for value in packet[2:46]:
                a = (a + value) & 255
                b = (b + a) & 255
            self.assertEqual(packet[46:48], bytes((a, b)))

    def test_coordinate_validation_still_rejects_invalid_values(self):
        for value in ('NaN', 'Infinity', '20\r\nreboot', None, True, '91'):
            with self.assertRaises(ValueError):
                builder.build_base_fixed_lla_command('Unicorecomm', value, 106, 30, .01)

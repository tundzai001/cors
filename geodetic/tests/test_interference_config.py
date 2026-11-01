"""Verify actual UBX frames without a receiver or backend dependencies."""
import base64
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('interference_builder', Path(__file__).resolve().parents[2] / 'cors_dashboard/backend/app/command_builder.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def packets(blob):
    offset = 0
    while offset < len(blob):
        size = int.from_bytes(blob[offset+4:offset+6], 'little') + 8
        packet = blob[offset:offset+size]
        assert len(packet) == size and packet[:2] == b'\xb5\x62'
        assert packet == builder._ubx_packet(packet[2], packet[3], packet[6:-2]), 'checksum'
        yield packet
        offset += size


class InterferenceConfigTests(unittest.TestCase):
    def test_default_and_boolean_validation(self):
        self.assertIs(builder.normalize_gnss_options(None)['interference_detection_enabled'], True)
        self.assertIs(builder.normalize_gnss_options({'interference_detection_enabled':False})['interference_detection_enabled'], False)
        for invalid in ('false', 'true', 0, 1, None, [], {}):
            with self.assertRaises(ValueError): builder.normalize_gnss_options({'interference_detection_enabled':invalid})

    def test_fixed_and_survey_wire_persistence_and_output_isolation(self):
        for mode in ('diagnostics_rtcm', 'rtcm_only'):
            for enabled in (True, False):
                options = {'output_mode':mode,'msm_level':'both','interference_detection_enabled':enabled}
                for make in (lambda:builder.build_base_fixed_lla_command('Ublox',21,105,10,.01,options),
                             lambda:builder.build_base_survey_in_command('Ublox',300,3,options)):
                    commands = make()
                    all_packets = [packet for command in commands for packet in packets(command)]
                    self.assertEqual(commands[-1],builder.UBLOX_SAVE_CONFIG)
                    self.assertEqual(commands[1],builder.build_ublox_output_config_command(options))
                    monitor = [p for p in all_packets if p[2:4] == b'\x06\x8a' and p[10:14] == bytes.fromhex('0d004110')]
                    self.assertEqual(len(monitor),1)
                    self.assertEqual(monitor[0][6:-2],b'\x00\x07\x00\x00'+bytes.fromhex('0d004110')+bytes([enabled]))
                config={'base_setup_method':'FIXED_LLA','coords':{'lat':21,'lon':105,'alt':10},'accuracy':.01,'gnss_options':options}
                verification=builder.build_ublox_verification_payload(config)
                output=base64.b64decode(verification['output_b64'])
                self.assertNotIn(bytes.fromhex('0d004110'),output)
                for port in verification['readback']['ports']:
                    self.assertNotIn(bytes.fromhex('0d004110'),base64.b64decode(port['output_b64']))
                other={**options,'interference_detection_enabled':not enabled}
                self.assertEqual(builder.build_ublox_output_config_command(options),builder.build_ublox_output_config_command(other))

    def test_unicore_is_unchanged(self):
        for make in (lambda opts:builder.build_base_fixed_lla_command('Unicorecomm',21,105,10,.01,opts),
                     lambda opts:builder.build_base_survey_in_command('Unicorecomm',300,3,opts)):
            self.assertEqual(make({'interference_detection_enabled':True}),make({'interference_detection_enabled':False}))

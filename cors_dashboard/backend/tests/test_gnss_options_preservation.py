import unittest
import sys
from unittest.mock import MagicMock

import types
class MockModule(types.ModuleType):
    def __getattr__(self, name):
        return MagicMock()

for mod in [
    "sqlalchemy", "sqlalchemy.exc", "sqlalchemy.ext", "sqlalchemy.ext.asyncio",
    "sqlalchemy.orm", "sqlalchemy.sql", "sqlalchemy.future", "sqlalchemy.dialects",
    "sqlalchemy.dialects.postgresql", "pydantic", "pydantic_settings",
]:
    if mod not in sys.modules:
        m = MockModule(mod)
        m.__path__ = []
        sys.modules[mod] = m

dummy_db = MockModule("app.database")
dummy_db.settings = MagicMock()
dummy_db.settings.DB_TOTAL_CONNECTION_BUDGET = 20
dummy_db.settings.DB_AUTH_CONNECTION_BUDGET = 5
dummy_db.settings.DB_DEVICE_CONNECTION_BUDGET = 15
sys.modules["app.database"] = dummy_db
sys.modules["app.models"] = MockModule("app.models")
sys.modules["app.schemas"] = MockModule("app.schemas")

from app import command_builder, crud


class TestGnssOptionsPreservation(unittest.TestCase):
    def test_boot_survey_config_is_accepted_without_fixed_coordinates(self):
        config = {"base_setup_method": "SURVEY_IN", "survey_in_duration": 120,
                  "survey_in_accuracy": 5, "gnss_options": {"output_mode": "rtcm_only", "msm_level": "both"}}
        self.assertEqual(crud.normalize_base_config(config), config)
        self.assertEqual(crud.normalize_base_config({**config, "coords": {"lat": 21, "lon": 105},
            "accuracy": 0.01, "altitude_reference": "LEGACY"}), config)
        for changes in ({"survey_in_duration": None}, {"survey_in_duration": "nan"},
                        {"survey_in_duration": 1.5}, {"survey_in_accuracy": 0}, {"survey_in_accuracy": "inf"}):
            self.assertEqual(crud.normalize_base_config({**config, **changes}), {})
        self.assertEqual(crud.normalize_base_config({"base_setup_method": "FIXED_LLA"}), {})

    def test_normalize_gnss_options_preserves_admin_rtcm_only_and_both(self):
        admin_opts = {
            "output_mode": "rtcm_only",
            "msm_level": "both",
            "constellations": {
                "gps": True,
                "glo": True,
                "gal": True,
                "bds": True,
                "qzss": False,
                "sbas": False,
            },
            "ports": ["USB"],
        }
        normalized = command_builder.normalize_gnss_options(admin_opts)
        self.assertEqual(normalized["output_mode"], "rtcm_only")
        self.assertEqual(normalized["msm_level"], "both")

    def test_ublox_output_commands_disable_nmea_and_enable_msm4_and_msm7(self):
        admin_opts = {
            "output_mode": "rtcm_only",
            "msm_level": "both",
            "constellations": {
                "gps": True,
                "glo": True,
                "gal": True,
                "bds": True,
                "qzss": False,
                "sbas": False,
            },
            "ports": ["USB"],
        }
        cmd = command_builder.build_ublox_output_config_command(admin_opts)
        self.assertIsInstance(cmd, bytes)
        self.assertTrue(len(cmd) > 0)

        # In rtcm_only mode, NMEA should be disabled in protocol keys (0x10780002 for USB)
        usb_nmea_key = command_builder.UBLOX_OUTPUT_PROTOCOL_KEYS["USB"]["NMEA"].to_bytes(4, "little")
        self.assertIn(usb_nmea_key + b"\x00", cmd)

        # RTCM3X protocol should be enabled (0x10780004 for USB)
        usb_rtcm_key = command_builder.UBLOX_OUTPUT_PROTOCOL_KEYS["USB"]["RTCM3X"].to_bytes(4, "little")
        self.assertIn(usb_rtcm_key + b"\x01", cmd)

        # Both MSM4 (1074) and MSM7 (1077) should be enabled
        usb_1074_key = command_builder.UBLOX_RTCM_KEYS["1074"]["USB"].to_bytes(4, "little")
        self.assertIn(usb_1074_key + b"\x01", cmd)

        usb_1077_key = command_builder.UBLOX_RTCM_KEYS["1077"]["USB"].to_bytes(4, "little")
        self.assertIn(usb_1077_key + b"\x01", cmd)

    def test_crud_normalize_base_config_preserves_gnss_options_when_present(self):
        params = {
            "base_setup_method": "FIXED_LLA",
            "coords": {"lat": 21.0, "lon": 105.0, "alt": 10.0},
            "accuracy": 0.015,
            "gnss_options": {
                "output_mode": "rtcm_only",
                "msm_level": "both",
            },
        }
        normalized = crud.normalize_base_config(params)
        self.assertIn("gnss_options", normalized)
        self.assertEqual(normalized["gnss_options"]["output_mode"], "rtcm_only")
        self.assertEqual(normalized["gnss_options"]["msm_level"], "both")

    def test_coordinator_save_inherits_admin_gnss_options(self):
        # Existing device base_config set by Admin
        existing_base_config = {
            "base_setup_method": "FIXED_LLA",
            "coords": {"lat": 21.0, "lon": 105.0, "alt": 10.0},
            "accuracy": 0.015,
            "gnss_options": {
                "output_mode": "rtcm_only",
                "msm_level": "both",
            },
        }

        # Coordinator submits new coordinates without gnss_options
        coordinator_params = {
            "base_setup_method": "FIXED_LLA",
            "coords": {"lat": 22.3444577, "lon": 105.8904931, "alt": 234.565},
            "accuracy": 0.015,
        }

        gnss_options = coordinator_params.get("gnss_options")
        if gnss_options is None and isinstance(existing_base_config, dict):
            gnss_options = existing_base_config.get("gnss_options")
        if gnss_options and "gnss_options" not in coordinator_params:
            coordinator_params["gnss_options"] = gnss_options

        normalized_params = crud.normalize_base_config(coordinator_params)
        if "gnss_options" not in normalized_params and "gnss_options" in existing_base_config:
            normalized_params["gnss_options"] = existing_base_config["gnss_options"]

        self.assertIn("gnss_options", normalized_params)
        self.assertEqual(normalized_params["gnss_options"]["output_mode"], "rtcm_only")
        self.assertEqual(normalized_params["gnss_options"]["msm_level"], "both")

        # Command generated must use the inherited gnss_options
        commands = command_builder.build_base_fixed_lla_command(
            "Ublox",
            coordinator_params["coords"]["lat"],
            coordinator_params["coords"]["lon"],
            coordinator_params["coords"]["alt"],
            coordinator_params["accuracy"],
            gnss_options,
        )
        self.assertTrue(len(commands) > 0)
        output_cmd = commands[1]
        usb_nmea_key = command_builder.UBLOX_OUTPUT_PROTOCOL_KEYS["USB"]["NMEA"].to_bytes(4, "little")
        self.assertIn(usb_nmea_key + b"\x00", output_cmd)
        usb_1077_key = command_builder.UBLOX_RTCM_KEYS["1077"]["USB"].to_bytes(4, "little")
        self.assertIn(usb_1077_key + b"\x01", output_cmd)


if __name__ == "__main__":
    unittest.main()

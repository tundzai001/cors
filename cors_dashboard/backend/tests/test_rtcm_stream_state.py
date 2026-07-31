import unittest

from app.rtcm_stream_state import (
    apply_stream_control_state,
    build_stream_control_payload,
    collect_reapable_server_streams,
    get_server_stream_state,
)


class RTCMStreamStateTests(unittest.TestCase):
    def test_mixed_sleeping_s1_and_always_on_s2_has_no_reaper_target(self):
        config = {
            "stream_on_demand": True,
            "stream_active": True,
            "server1_enabled": True,
            "server1_stream_on_demand": True,
            "server1_stream_active": False,
            "mountpoint1": "VP.VinhYen",
            "server2_enabled": True,
            "server2_stream_on_demand": False,
            "server2_stream_active": True,
            "mountpoint2": "VP.Backup",
        }

        self.assertEqual(
            collect_reapable_server_streams(config, legacy_active_mountpoint="VP.VinhYen"),
            (),
        )
        self.assertTrue(get_server_stream_state(config, 2).can_push)

    def test_only_awake_on_demand_server_is_reapable(self):
        config = {
            "stream_on_demand": True,
            "stream_active": True,
            "server1_enabled": True,
            "server1_stream_on_demand": True,
            "server1_stream_active": True,
            "mountpoint1": "/VP.VinhYen/",
            "server2_enabled": True,
            "server2_stream_on_demand": False,
            "server2_stream_active": True,
            "mountpoint2": "VP.Backup",
        }

        self.assertEqual(
            collect_reapable_server_streams(config, legacy_active_mountpoint="VP.VinhYen"),
            ((1, "vp.vinhyen"),),
        )

    def test_disabled_server_with_stale_active_flag_is_not_reapable(self):
        config = {
            "stream_on_demand": True,
            "stream_active": True,
            "server1_enabled": False,
            "server1_stream_on_demand": True,
            "server1_stream_active": True,
            "mountpoint1": "VP.VinhYen",
        }

        self.assertEqual(collect_reapable_server_streams(config), ())

    def test_modern_missing_active_flag_is_fail_safe_inactive(self):
        config = {
            "stream_on_demand": True,
            "stream_active": True,
            "server1_enabled": True,
            "server1_stream_on_demand": True,
            "mountpoint1": "VP.VinhYen",
            "server2_enabled": True,
            "server2_stream_on_demand": False,
            "server2_stream_active": True,
            "mountpoint2": "VP.Backup",
        }

        self.assertEqual(collect_reapable_server_streams(config), ())

    def test_legacy_global_flags_still_fall_back_to_server_one(self):
        config = {
            "stream_on_demand": "true",
            "stream_active": "1",
            "server1_enabled": "yes",
            "mountpoint1": "VP.VinhYen",
        }

        self.assertEqual(
            collect_reapable_server_streams(config, legacy_active_mountpoint="VP.VinhYen"),
            ((1, "vp.vinhyen"),),
        )

    def test_same_mountpoint_always_on_server_is_never_reaped(self):
        config = {
            "stream_on_demand": True,
            "stream_active": True,
            "server1_enabled": True,
            "server1_stream_on_demand": True,
            "server1_stream_active": True,
            "mountpoint1": "VP.VinhYen",
            "server2_enabled": True,
            "server2_stream_on_demand": False,
            "server2_stream_active": True,
            "mountpoint2": "VP.VinhYen",
        }

        self.assertEqual(collect_reapable_server_streams(config), ((1, "vp.vinhyen"),))

    def test_deactivation_is_idempotent_and_keeps_always_on_s2_active(self):
        config = {
            "stream_on_demand": True,
            "stream_active": True,
            "active_mountpoint": "VP.VinhYen",
            "server1_enabled": True,
            "server1_stream_on_demand": True,
            "server1_stream_active": True,
            "mountpoint1": "VP.VinhYen",
            "server2_enabled": True,
            "server2_stream_on_demand": False,
            "server2_stream_active": True,
            "mountpoint2": "VP.Backup",
        }

        first = apply_stream_control_state(
            config,
            active=False,
            mountpoint="VP.VinhYen",
            server_id=1,
        )
        second = apply_stream_control_state(
            first,
            active=False,
            mountpoint="VP.VinhYen",
            server_id=1,
        )

        self.assertFalse(first["server1_stream_active"])
        self.assertTrue(first["stream_on_demand"])
        self.assertTrue(first["stream_active"])
        self.assertNotIn("active_mountpoint", first)
        self.assertEqual(collect_reapable_server_streams(first), ())
        self.assertEqual(second, first)

    def test_control_payload_contains_canonical_and_legacy_aliases(self):
        payload = build_stream_control_payload(
            active=False,
            mountpoint="VP.VinhYen",
            address="aitogy.com.vn",
            server_id=1,
        )

        self.assertEqual(payload["server_id"], 1)
        self.assertEqual(payload["server_idx"], 1)
        self.assertEqual(payload["address"], "aitogy.com.vn")
        self.assertEqual(payload["caster_host"], "aitogy.com.vn")
        self.assertEqual(payload["mountpoint"], "VP.VinhYen")
        self.assertFalse(payload["active"])

    def test_invalid_server_id_is_omitted(self):
        payload = build_stream_control_payload(active=True, server_id=9)

        self.assertNotIn("server_id", payload)
        self.assertNotIn("server_idx", payload)


if __name__ == "__main__":
    unittest.main()

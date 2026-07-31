from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane_failover import ControlPlaneFailover, resolve_effective_stream_switch


class ControlPlaneFailoverTests(unittest.TestCase):
    def make_tracker(self) -> ControlPlaneFailover:
        return ControlPlaneFailover(
            "boot-a",
            stale_after_seconds=10,
            recovery_after_seconds=2,
            probe_ttl_seconds=30,
        )

    def test_startup_grace_and_both_channels_required(self):
        tracker = self.make_tracker()
        self.assertFalse(tracker.evaluate(now=100).active)
        tracker.record_probe(1, now=100)
        self.assertFalse(tracker.evaluate(now=109.9).active)

        tracker.record_ack(
            "websocket",
            {"status_boot_id": "boot-a", "status_sequence": 1},
            now=109,
        )
        self.assertFalse(tracker.evaluate(now=110.1).active)
        self.assertTrue(tracker.evaluate(now=119.1).active)

    def test_fresh_mqtt_alone_prevents_fail_open(self):
        tracker = self.make_tracker()
        tracker.record_probe(1, now=0)
        tracker.record_ack(
            "mqtt",
            {"status_boot_id": "boot-a", "status_sequence": 1},
            now=9,
        )
        self.assertFalse(tracker.evaluate(now=10.5).active)

    def test_old_wrong_boot_and_retained_ack_are_rejected(self):
        tracker = self.make_tracker()
        tracker.record_probe(7, now=0)
        self.assertFalse(tracker.record_ack(
            "mqtt",
            {"status_boot_id": "old-boot", "status_sequence": 7},
            now=1,
        ))
        self.assertFalse(tracker.record_ack(
            "mqtt",
            {"status_boot_id": "boot-a", "status_sequence": 7},
            retained=True,
            now=1,
        ))
        self.assertFalse(tracker.record_ack(
            "websocket",
            {"status_boot_id": "boot-a", "status_sequence": 999},
            now=1,
        ))

    def test_duplicate_out_of_order_and_delayed_ack_are_rejected(self):
        tracker = self.make_tracker()
        tracker.record_probe(1, now=0)
        tracker.record_probe(2, now=1)
        self.assertTrue(tracker.record_ack(
            "mqtt",
            {"status_boot_id": "boot-a", "status_sequence": 2},
            now=2,
        ))
        self.assertFalse(tracker.record_ack(
            "mqtt",
            {"status_boot_id": "boot-a", "status_sequence": 2},
            now=3,
        ))
        self.assertFalse(tracker.record_ack(
            "mqtt",
            {"status_boot_id": "boot-a", "status_sequence": 1},
            now=3,
        ))
        self.assertTrue(tracker.evaluate(now=12).active)

        delayed = self.make_tracker()
        delayed.record_probe(3, now=10)
        self.assertFalse(delayed.record_ack(
            "websocket",
            {"status_boot_id": "boot-a", "status_sequence": 3},
            now=20,
        ))

    def test_recovery_requires_probe_newer_than_failover_entry(self):
        tracker = self.make_tracker()
        tracker.record_probe(1, now=0)
        entered = tracker.evaluate(now=10)
        self.assertTrue(entered.changed)
        self.assertTrue(entered.active)

        self.assertFalse(tracker.record_ack(
            "mqtt",
            {"status_boot_id": "boot-a", "status_sequence": 1},
            now=11,
        ))
        self.assertTrue(tracker.evaluate(now=20).active)

        tracker.record_probe(2, now=20)
        self.assertTrue(tracker.record_ack(
            "mqtt",
            {"status_boot_id": "boot-a", "status_sequence": 2},
            now=20.1,
        ))
        self.assertTrue(tracker.evaluate(now=21).active)
        recovered = tracker.evaluate(now=22.1)
        self.assertTrue(recovered.changed)
        self.assertFalse(recovered.active)

    def test_tracker_never_mutates_service_config(self):
        config = {
            "server1_enabled": True,
            "server1_stream_on_demand": True,
            "server1_stream_active": False,
            "server2_enabled": False,
        }
        original = deepcopy(config)
        tracker = self.make_tracker()
        tracker.record_probe(1, now=0)
        tracker.evaluate(now=10)
        tracker.record_probe(2, now=11)
        tracker.record_ack(
            "websocket",
            {"status_boot_id": "boot-a", "status_sequence": 2},
            now=11,
        )
        tracker.evaluate(now=13)
        self.assertEqual(config, original)

    def test_fail_open_forces_only_enabled_sleeping_outputs(self):
        sleeping = resolve_effective_stream_switch(
            enabled=True,
            on_demand=True,
            active=False,
            fail_open=True,
        )
        disabled = resolve_effective_stream_switch(
            enabled=False,
            on_demand=True,
            active=False,
            fail_open=True,
        )
        always_on = resolve_effective_stream_switch(
            enabled=True,
            on_demand=False,
            active=True,
            fail_open=True,
        )
        self.assertTrue(sleeping["can_push"])
        self.assertTrue(sleeping["fail_open_forced"])
        self.assertFalse(disabled["can_push"])
        self.assertTrue(always_on["can_push"])
        self.assertFalse(always_on["fail_open_forced"])


if __name__ == "__main__":
    unittest.main()

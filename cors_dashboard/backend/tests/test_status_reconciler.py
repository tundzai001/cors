import unittest

from app.status_reconciler import StatusReconciler


class StatusReconcilerTests(unittest.IsolatedAsyncioTestCase):
    now = 1793268500

    def payload(self, boot, sequence, timestamp, status='online'):
        return dict(serial='esp_204340DD2568', status=status,
                    status_boot_id=boot, status_sequence=sequence,
                    agent_timestamp=timestamp, timestamp=timestamp)

    async def test_reboot_with_reset_uptime_recovers_online(self):
        reconciler = StatusReconciler()
        await reconciler.accept(self.payload('old', 100, 1036, 'offline'),
                                source='mqtt', received_at=self.now - 5)
        decision = await reconciler.accept(self.payload('new', 66, 430),
                                           source='mqtt', received_at=self.now)
        self.assertTrue(decision.accepted, decision.reason)
        self.assertEqual(decision.payload['status'], 'online')
        self.assertEqual(decision.payload['timestamp'], self.now)
        self.assertIsNone(decision.payload['agent_timestamp'])
        self.assertTrue(await reconciler.is_fresh('esp_204340DD2568', 180, now=self.now))
        old_boot = await reconciler.accept(self.payload('old', 101, 1040),
                                           source='mqtt', received_at=self.now + 1)
        self.assertEqual(old_boot.reason, 'retired_boot')

    async def test_synced_clock_can_reboot_before_time_sync(self):
        reconciler = StatusReconciler()
        await reconciler.accept(self.payload('old', 100, self.now - 5),
                                source='mqtt', received_at=self.now - 5)
        decision = await reconciler.accept(self.payload('new', 1, 20),
                                           source='mqtt', received_at=self.now)
        self.assertTrue(decision.accepted, decision.reason)

    async def test_uptime_retained_online_cannot_revive_offline_station(self):
        reconciler = StatusReconciler()
        decision = await reconciler.accept(self.payload('new', 66, 430),
                                           source='mqtt', retained=True, received_at=self.now)
        self.assertEqual(decision.reason, 'stale_retained_online')

    async def test_same_boot_still_rejects_duplicate_and_older_sequences(self):
        reconciler = StatusReconciler()
        await reconciler.accept(self.payload('new', 66, 430),
                                source='mqtt', received_at=self.now)
        for seq, reason in [(66, 'duplicate_sequence'), (65, 'out_of_order_sequence')]:
            decision = await reconciler.accept(self.payload('new', seq, 435),
                                               source='mqtt', received_at=self.now + 5)
            self.assertEqual(decision.reason, reason)

    async def test_unix_clock_still_rejects_older_boot_and_future_time(self):
        reconciler = StatusReconciler()
        await reconciler.accept(self.payload('current', 66, self.now),
                                source='mqtt', received_at=self.now)
        for boot, timestamp, reason in [
            ('older', self.now - 60, 'older_boot_timestamp'),
            ('future', self.now + 300, 'future_agent_timestamp'),
        ]:
            decision = await reconciler.accept(self.payload(boot, 1, timestamp),
                                               source='mqtt', received_at=self.now + 1)
            self.assertEqual(decision.reason, reason)

    async def test_fresh_epoch_retained_snapshot_still_accepted(self):
        reconciler = StatusReconciler()
        decision = await reconciler.accept(self.payload('new', 66, self.now - 5),
                                           source='mqtt', retained=True, received_at=self.now)
        self.assertTrue(decision.accepted, decision.reason)
        self.assertEqual(decision.payload['agent_timestamp'], self.now - 5)


if __name__ == '__main__':
    unittest.main()

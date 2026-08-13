import unittest

from app.license_sync import evaluate_license_sync


class LicenseSyncTests(unittest.TestCase):
    def test_partial_online_packet_does_not_invalidate_existing_license(self):
        state = evaluate_license_sync("expected", None)

        self.assertFalse(state.token_check_available)
        self.assertFalse(state.token_synced)
        self.assertFalse(state.needs_token_deploy)

    def test_matching_reported_fingerprint_is_synced(self):
        state = evaluate_license_sync("same", "same")

        self.assertTrue(state.token_check_available)
        self.assertTrue(state.token_synced)
        self.assertFalse(state.needs_token_deploy)

    def test_reported_mismatch_requires_deploy(self):
        state = evaluate_license_sync("server", "agent")

        self.assertTrue(state.token_check_available)
        self.assertFalse(state.token_synced)
        self.assertTrue(state.needs_token_deploy)

    def test_missing_server_token_still_requires_deploy(self):
        state = evaluate_license_sync(None, None)

        self.assertTrue(state.needs_token_deploy)

    def test_bypass_never_requires_deploy(self):
        state = evaluate_license_sync("server", "agent", license_bypass=True)

        self.assertFalse(state.needs_token_deploy)


if __name__ == "__main__":
    unittest.main()

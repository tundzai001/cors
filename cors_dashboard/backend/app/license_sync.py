from __future__ import annotations

from typing import NamedTuple


class LicenseSyncState(NamedTuple):
    token_check_available: bool
    token_synced: bool
    needs_token_deploy: bool


def evaluate_license_sync(
    expected_fingerprint: str | None,
    agent_fingerprint: str | None,
    *,
    license_bypass: bool = False,
) -> LicenseSyncState:
    """Evaluate only token evidence that is present in the current packet."""
    expected = str(expected_fingerprint or "").strip()
    reported = str(agent_fingerprint or "").strip()
    token_check_available = bool(reported)
    token_synced = bool(expected and reported and expected == reported)
    needs_token_deploy = False if license_bypass else (
        not expected or (token_check_available and not token_synced)
    )
    return LicenseSyncState(
        token_check_available=token_check_available,
        token_synced=token_synced,
        needs_token_deploy=needs_token_deploy,
    )

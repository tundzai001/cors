# Offline regression design for Device-controlled sync mismatch text reaches an HTML toast sink

This artifact is intentionally non-operational. It does not contact a live service, send a device command, render an active page, or modify persistent data.

1. Build a unit-test fixture for the nearest reviewed boundary.
2. Feed it the synthetic attacker-controlled value described in the finding.
3. Assert the secure invariant: identity binding, strict parsing, output encoding, bounded allocation, freshness, or fail-closed authorization as applicable.
4. Assert that the same value cannot select a sibling station/user/device or reach the dangerous sink.
5. Run the test against the vulnerable and fixed implementations and retain only pass/fail output.

Expected fixed behavior: the input is rejected or safely transformed before the sink, and no unauthorized state change or executable markup is produced.

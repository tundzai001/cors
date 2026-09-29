# Non-unique device-token index accepts deterministic cross-device bearer-token collisions

## Executive Summary

This report documents a `critical`-severity security finding in the reviewed repository. A credential collision or computed target token lets one device/attacker impersonate another station, poison its operational telemetry, occupy its socket identity, and potentially interfere with command delivery across a station boundary.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. device token generated deterministically without a secret
2. serial-derived base truncates at 12 digits
3. distinct serials dynamically produced identical tokens
4. database index is non-unique
5. application's IntegrityError duplicate defense cannot fire
6. WebSocket identity is path serial plus bearer-token equality

The source evidence recorded for this finding is:

- `cors_dashboard/backend/alembic_devices/versions/e7b9c4d1a2f3_add_device_runtime_columns.py` lines `21-29` — device_token is nullable and indexed with unique=False, so the database accepts the same authentication credential for multiple serials.`
- `cors_dashboard/backend/app/license_manager.py` lines `4-49` — The deployed token is computed without a secret from a base that truncates concatenated character codes at 12 digits, discarding much of the serial and making collisions practical.`
- `cors_dashboard/backend/app/routers/devices.py` lines `652-682` — Auto-deploy expects duplicate tokens to raise IntegrityError, but the migration creates no unique constraint, so this safeguard never detects a duplicate credential.`
- `cors_dashboard/backend/app/main.py` lines `7948-7989` — The agent WebSocket selects the target by attacker-supplied path serial and authenticates solely by equality with that row's device_token unless an explicit bypass is enabled.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: A credential collision or computed target token lets one device/attacker impersonate another station, poison its operational telemetry, occupy its socket identity, and potentially interfere with command delivery across a station boundary.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Validate the input at the trust boundary, bind it to the authenticated resource, fail closed, and add regression coverage.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

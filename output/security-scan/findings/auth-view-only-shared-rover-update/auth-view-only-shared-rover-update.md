# View-only station access authorizes updates to shared rover accounts

## Executive Summary

This report documents a `high`-severity security finding in the reviewed repository. A view-only coordinator can change another user's rover password, mountpoints, target casters, and sharing configuration.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. 1. Reach the Rover update API authorization through View-only allowed_bases mountpoint is counted as manager scope.
2. 2. Satisfy the bounded preconditions: Shared rover uses viewed mountpoint; Sharing flag enabled.
3. 3. Supply or cause the reviewed attacker-controlled input: Target shared rover and update fields.
4. 4. The nearest control fails or is incomplete, crossing View-only station scope -> caster/database credential mutation authority.
5. 5. The reviewed sink is reached, producing A view-only coordinator can change another user's rover password, mountpoints, target casters, and sharing configuration..

The source evidence recorded for this finding is:

- `cors_dashboard/backend/app/station_access.py` lines `{'start': 19, 'end': 37}` — extract_coordinator_station_access adds mountpoints for every visible device, although owned_serials is restricted to owner_id.`
- `cors_dashboard/backend/app/crud.py` lines `{'start': 756, 'end': 768}` — Coordinator-visible devices include both owned devices and allowed_bases view-only devices.`
- `cors_dashboard/backend/app/crud.py` lines `{'start': 818, 'end': 836}` — Shared-rover management is allowed when any rover mountpoint appears in the overbroad mountpoint set.`
- `cors_dashboard/backend/app/routers/devices.py` lines `{'start': 441, 'end': 601}` — The update route relies on that permission and mutates caster and database state.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: A view-only coordinator can change another user's rover password, mountpoints, target casters, and sharing configuration.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Derive editable mountpoints only from owned/edit-authorized stations.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

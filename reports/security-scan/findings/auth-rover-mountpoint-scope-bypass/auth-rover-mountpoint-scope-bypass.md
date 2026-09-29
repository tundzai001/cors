# Rover update accepts mountpoints outside the coordinator's station scope

## Executive Summary

This report documents a `high`-severity security finding in the reviewed repository. A coordinator can rebind a credential to arbitrary caster mountpoints outside their assigned/owned station scope.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. {'attacker': 'Authenticated coordinator', 'preconditions': ['Own or otherwise manage one rover account', 'Know a target mountpoint name'], 'entrypoint': 'Update rover account endpoint', 'source': 'mountpoints array', 'transformations': ['No station-scope validation', 'List forwarded to caster client'], 'sink': 'Caster authorization record and local ORM record', 'outcome': 'Unauthorized correction-stream entitlement', 'counterevidence': []}

The source evidence recorded for this finding is:

- `cors_dashboard/backend/app/routers/devices.py` lines `{'start': 441, 'end': 463}` — After account-level permission succeeds, new_mountpoints is taken directly from the update payload.`
- `cors_dashboard/backend/app/routers/devices.py` lines `{'start': 540, 'end': 550}` — Attacker-selected mountpoints are sent to every selected caster.`
- `cors_dashboard/backend/app/routers/devices.py` lines `{'start': 587, 'end': 601}` — The unscoped mountpoint list is persisted.`
- `cors_dashboard/backend/app/schemas.py` lines `262-273` — RoverUpdate accepts arbitrary mountpoints and selected_serials without station-scope validation or format/count limits.`
- `cors_dashboard/backend/app/crud.py` lines `818-838` — Management permission is evaluated against the rover's old targets, with no helper that verifies all proposed new serials/mountpoints are within the caller's managed set.`
- `cors_dashboard/backend/app/crud.py` lines `719-731` — Caller-prepared base_serial and mountpoints values are assigned directly to the RoverAccount.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: A coordinator can rebind a credential to arbitrary caster mountpoints outside their assigned/owned station scope.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Validate the input at the trust boundary, bind it to the authenticated resource, fail closed, and add regression coverage.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

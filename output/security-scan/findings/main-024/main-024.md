# Operations events endpoint permits cross-station log reads and arbitrary serial filtering

## Executive Summary

This report documents a `medium`-severity security finding in the reviewed repository. Targeted cross-tenant operational and IP-address disclosure.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. 1. Reach the Operations events API through Viewer-controlled filters query global ActivityLog without station intersection.
2. 2. Satisfy the bounded preconditions: Caller has basic view permission.
3. 3. Supply or cause the reviewed attacker-controlled input: device_serial and query parameters.
4. 4. The nearest control fails or is incomplete, crossing Station-scoped bearer identity -> fleet-wide logs/IP/user/station data.
5. 5. The reviewed sink is reached, producing Targeted cross-tenant operational and IP-address disclosure..

The source evidence recorded for this finding is:

- `cors_dashboard/backend/app/main.py` lines `7429-7438` — VIEW_DEVICES user controls device_serial and q and calls global _ops_events_query with no visibility check.`
- `cors_dashboard/backend/app/main.py` lines `7152-7182` — Returns actor_user_id, device_serial, IP address, and messages.`
- `cors_dashboard/frontend/js/operations.js` lines `{'start': 136, 'end': 143}` — The operations page automatically calls the events endpoint for any authenticated visitor who opens the static page.`
- `cors_dashboard/backend/app/main.py` lines `{'start': 7429, 'end': 7438}` — The endpoint requires only VIEW_DEVICES and passes no current-user station scope to its query.`
- `cors_dashboard/backend/app/main.py` lines `{'start': 7166, 'end': 7182}` — _ops_events_query selects ActivityLog globally; optional device_serial is caller-controlled rather than derived from authorized devices.`
- `cors_dashboard/backend/app/main.py` lines `{'start': 7152, 'end': 7163}` — Each row includes message, actor_user_id, device_serial, and ip_address.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: Targeted cross-tenant operational and IP-address disclosure.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Intersect query scope with allowed stations.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

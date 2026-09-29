# Authenticated UI WebSocket stop_rtcm action allows cross-station forced shutdown

## Executive Summary

This report documents a `medium`-severity security finding in the reviewed repository. Cross-tenant correction-service denial and eviction of legitimate sessions.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. 1. Reach the Authenticated UI WebSocket control channel through stop_rtcm with arbitrary target and force=true.
2. 2. Satisfy the bounded preconditions: Target mountpoint is known; Victim has active sessions.
3. 3. Supply or cause the reviewed attacker-controlled input: WebSocket mountpoint/address/force fields.
4. 4. The nearest control fails or is incomplete, crossing Authenticated socket with stored scope -> out-of-scope session deletion/device stop command.
5. 5. The reviewed sink is reached, producing Cross-tenant correction-service denial and eviction of legitimate sessions..

The source evidence recorded for this finding is:

- `cors_dashboard/backend/app/main.py` lines `7832-7882` — Any authenticated socket may submit action=stop_rtcm, arbitrary mountpoint/address, and force=true; context.allowed_serials is not checked.`
- `cors_dashboard/backend/app/main.py` lines `3972-4037` — force deletes station sessions and deactivates streaming.`
- `cors_dashboard/backend/app/websocket.py` lines `{'start': 16, 'end': 50}` — Connection context contains allowed_serials and outbound filtering uses it, establishing the intended tenant boundary.`
- `cors_dashboard/backend/app/main.py` lines `{'start': 7853, 'end': 7876}` — stop_rtcm accepts arbitrary mountpoint, force, client identifier, and address without role, permission, ownership, or destination validation.`
- `cors_dashboard/backend/app/main.py` lines `{'start': 3972, 'end': 4036}` — force=true deletes RTCM session rows for the resolved station and sends SET_RTCM_STREAM_ACTIVE false; the caller-supplied address participates in host matching and the device command payload.`
- `cors_dashboard/backend/app/main.py` lines `{'start': 790, 'end': 832}` — requested_address is placed in build_stream_control_payload and sent to the selected device.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: Cross-tenant correction-service denial and eviction of legitimate sessions.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Apply allowed_serials/role checks to inbound stop actions.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

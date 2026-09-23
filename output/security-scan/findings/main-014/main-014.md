# Authenticated UI WebSocket start_rtcm action ignores the socket's allowed station scope

## Executive Summary

This report documents a `medium`-severity security finding in the reviewed repository. Viewer or coordinator can activate a station outside scope.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. Valid viewer token
2. allowed_serials computed
3. inbound handler ignores it
4. mountpoint activates device

The source evidence recorded for this finding is:

- `cors_dashboard/backend/app/main.py` lines `7778-7822` — Authentication computes allowed_serials for coordinators/viewers.`
- `cors_dashboard/backend/app/main.py` lines `7832-7872` — start_rtcm uses attacker mountpoint but never consults context.allowed_serials or checks a permission beyond having any valid account.`
- `cors_dashboard/backend/app/websocket.py` lines `{'start': 16, 'end': 21}` — The manager retains the authenticated context, including allowed_serials, for each socket.`
- `cors_dashboard/backend/app/websocket.py` lines `{'start': 29, 'end': 50}` — _can_receive enforces allowed_serials for outbound station events but is never applied to inbound control messages.`
- `cors_dashboard/backend/app/main.py` lines `{'start': 7832, 'end': 7866}` — After authentication, start_rtcm accepts an arbitrary mountpoint and invokes _do_ntrip_wake without checking role, permission, or allowed_serials.`
- `cors_dashboard/backend/app/main.py` lines `{'start': 3897, 'end': 3934}` — The supplied mountpoint resolves a station and reaches _ntrip_mqtt_start, which activates its stream.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: Viewer or coordinator can activate a station outside scope.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Validate the input at the trust boundary, bind it to the authenticated resource, fail closed, and add regression coverage.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

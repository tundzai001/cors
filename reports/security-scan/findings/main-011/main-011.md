# Viewer can activate RTCM on any station serial outside assigned scope

## Executive Summary

This report documents a `medium`-severity security finding in the reviewed repository. Cross-station correction-stream activation and resource consumption against another operator's station.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. 1. Reach the Authenticated RTCM station activation REST route through Viewer supplies an out-of-scope station serial.
2. 2. Satisfy the bounded preconditions: Target station serial/mountpoint is known.
3. 3. Supply or cause the reviewed attacker-controlled input: Path serial and activation request.
4. 4. The nearest control fails or is incomplete, crossing Coarse view permission -> cross-station session insert and stream activation.
5. 5. The reviewed sink is reached, producing Cross-station correction-stream activation and resource consumption against another operator's station..

The source evidence recorded for this finding is:

- `cors_dashboard/backend/app/main.py` lines `5310-5394` — Only VIEW_DEVICES is required; the serial lookup is not followed by can_view_station or allowed_bases/ownership enforcement before stream activation.`
- `cors_dashboard/backend/app/auth.py` lines `122-144` — VIEW_DEVICES is granted to viewers and coordinators.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: Cross-station correction-stream activation and resource consumption against another operator's station.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Enforce target station scope before inserting sessions or activating.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

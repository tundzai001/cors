# One global NMEA parser mixes satellite state across independently authenticated stations

## Executive Summary

This report documents a `medium`-severity security finding in the reviewed repository. One station can inject, overwrite, or leak satellite observations into another station's dashboard, corrupting operational GNSS health and provenance.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. 1. Reach the Backend NMEA telemetry parser through Interleaved NMEA from independent station topics shares singleton mutable state.
2. 2. Satisfy the bounded preconditions: At least two stations emit interleaved NMEA; Victim dashboards consume broadcasts.
3. 3. Supply or cause the reviewed attacker-controlled input: Valid-looking GSA/GSV sentences.
4. 4. The nearest control fails or is incomplete, crossing Authenticated station telemetry -> another station-labeled GNSS health state.
5. 5. The reviewed sink is reached, producing One station can inject, overwrite, or leak satellite observations into another station's dashboard, corrupting operational GNSS health and provenance..

The source evidence recorded for this finding is:

- `cors_dashboard/backend/app/utils.py` lines `7-17,82-102,107-221` — GSA, GSV buffers, and the persistent satellites map have no serial/device namespace.`
- `cors_dashboard/backend/app/__init__.py` lines `1-5` — A single NMEAParser instance is exported for the whole backend.`
- `cors_dashboard/backend/app/mqtt.py` lines `708-731` — NMEA from any topic serial is parsed without passing serial, then the shared result is broadcast under the current topic serial.`
- `cors_dashboard/backend/app/main.py` lines `8060-8078` — Every connected device also uses the same package-level parser.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: One station can inject, overwrite, or leak satellite observations into another station's dashboard, corrupting operational GNSS health and provenance.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Namespace all parser state by station serial and test interleaved streams.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

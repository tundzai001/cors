# Public wake requests can accumulate duplicate RTCM lease rows without a client/station uniqueness bound

## Executive Summary

This report documents a `medium`-severity security finding in the reviewed repository. A short unauthenticated burst can grow the device database by one active lease per request and inflate keepalive/reaper work for up to ten minutes, degrading the public stream-control and shared backend/database availability.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. public GET and POST routes
2. attacker-controlled stable client identifier
3. new UUID for every request
4. no logical unique constraint
5. active duplicates retained up to 600 seconds
6. background work iterates/counts active session rows

The source evidence recorded for this finding is:

- `cors_dashboard/backend/alembic_devices/versions/c1a8e7f9b2d4_create_rtcm_stream_sessions_table.py` lines `21-38` — Only random session_id is unique; client_id, serial, and expiry are indexed but duplicate active leases for the same client/station are unrestricted.`
- `cors_dashboard/backend/alembic_devices/versions/d3f4a9b2c7e1_add_mountpoint_to_rtcm_stream_sessions.py` lines `21-38` — The later (serial,mountpoint,expires_at) index is also non-unique, leaving repeated identical lease instances valid.`
- `cors_dashboard/backend/app/main.py` lines `3511-3553` — GET and POST wake routes accept attacker-chosen mountpoint, client identifier, and TTL without an authentication dependency.`
- `cors_dashboard/backend/app/main.py` lines `973-1028,3877-3934` — Every accepted wake creates a fresh UUID session and commits it; expired rows for that serial are deleted, but active duplicate rows are neither updated nor deduplicated.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: A short unauthenticated burst can grow the device database by one active lease per request and inflate keepalive/reaper work for up to ten minutes, degrading the public stream-control and shared backend/database availability.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Validate the input at the trust boundary, bind it to the authenticated resource, fail closed, and add regression coverage.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

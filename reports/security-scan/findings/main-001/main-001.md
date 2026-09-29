# Public map endpoint discloses complete password-bearing station service configuration

## Executive Summary

This report documents a `high`-severity security finding in the reviewed repository. Disclosure of NTRIP source/client passwords can enable correction-stream impersonation, unauthorized caster access, telemetry manipulation, or lateral operational discovery.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. 1. Reach the Unauthenticated public map API through Anonymous response includes complete service_config for all stations.
2. 2. Satisfy the bounded preconditions: At least one station has configured reusable secrets.
3. 3. Supply or cause the reviewed attacker-controlled input: GET public map request.
4. 4. The nearest control fails or is incomplete, crossing Public data surface -> restricted station/caster credentials and topology.
5. 5. The reviewed sink is reached, producing Disclosure of NTRIP source/client passwords can enable correction-stream impersonation, unauthorized caster access, telemetry manipulation, or lateral operational discovery..

The source evidence recorded for this finding is:

- `cors_dashboard/backend/app/main.py` lines `4546-4667` — Unauthenticated GET selects every Device and returns dev.service_config verbatim.`
- `cors_dashboard/backend/app/schemas.py` lines `67-102` — ServiceConfig includes password1, password2, and rtcmpassword1 together with hosts, ports, usernames, and mountpoints.`
- `cors_dashboard/frontend/js/main.js` lines `{'start': 1660, 'end': 1699}` — A visitor without a token fetches /api/public/map-stations and preserves each response's service_config in global state.`
- `cors_dashboard/backend/app/main.py` lines `{'start': 4546, 'end': 4550}` — The map-stations route is intentionally unauthenticated.`
- `cors_dashboard/backend/app/main.py` lines `{'start': 4646, 'end': 4667}` — The public object includes dev.service_config without projection or redaction.`
- `cors_dashboard/backend/app/schemas.py` lines `{'start': 76, 'end': 102}` — Service configuration includes username1/password1, username2/password2, and RTCM username/password fields.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: Disclosure of NTRIP source/client passwords can enable correction-stream impersonation, unauthorized caster access, telemetry manipulation, or lateral operational discovery.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Return a dedicated public DTO with no service_config or secrets.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

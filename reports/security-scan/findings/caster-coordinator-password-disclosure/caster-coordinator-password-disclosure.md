# Caster usertable passwords are cached and returned to coordinators in plaintext

## Executive Summary

This report documents a `medium`-severity security finding in the reviewed repository. A coordinator can obtain plaintext NTRIP credentials for visible/shared rover accounts and reuse them outside the dashboard.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. 1. Reach the Caster users API and cache through Plaintext usertable password copied to coordinator response.
2. 2. Satisfy the bounded preconditions: Caster cache contains a functional password.
3. 3. Supply or cause the reviewed attacker-controlled input: GET caster users.
4. 4. The nearest control fails or is incomplete, crossing Caster administrator data -> scoped dashboard user -> reusable external credential.
5. 5. The reviewed sink is reached, producing A coordinator can obtain plaintext NTRIP credentials for visible/shared rover accounts and reuse them outside the dashboard..

The source evidence recorded for this finding is:

- `cors_dashboard/backend/app/caster_client.py` lines `{'start': 219, 'end': 236}` — get_users parses and retains the plaintext password column from each untrusted caster usertable row.`
- `cors_dashboard/backend/app/main.py` lines `{'start': 5614, 'end': 5627}` — The complete user dictionaries are placed into global and Redis caster caches.`
- `cors_dashboard/backend/app/main.py` lines `{'start': 4790, 'end': 4806}` — The caster-users route is available to MANAGE_CASTER users, including coordinators.`
- `cors_dashboard/backend/app/main.py` lines `{'start': 4922, 'end': 4925}` — The route explicitly copies the cached password into CasterRoverItem responses.`
- `cors_dashboard/backend/app/schemas.py` lines `403-427` — CasterRoverItem makes password a required response field, so the aggregator cannot return normal user/status data without disclosing it.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: A coordinator can obtain plaintext NTRIP credentials for visible/shared rover accounts and reuse them outside the dashboard.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Remove passwords from normal cache/response projection.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

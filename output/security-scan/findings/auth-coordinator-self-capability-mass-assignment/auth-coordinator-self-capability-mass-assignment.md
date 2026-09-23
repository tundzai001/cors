# Coordinator can self-assign rover and mountpoint capabilities through generic user update

## Executive Summary

This report documents a `high`-severity security finding in the reviewed repository. A coordinator can grant themselves rover-creation and extra-mountpoint powers not assigned by an administrator and provision additional caster credentials.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. {'attacker': 'Authenticated coordinator', 'preconditions': ['can_create_users=true, permitting entry to user update', 'Target own user id'], 'entrypoint': 'PUT/PATCH user update endpoint', 'source': 'can_create_rovers=true and attacker-selected extra_mountpoints', 'transformations': ['exclude_unset retains fields', 'self-update branch leaves them intact', 'generic ORM setter persists them'], 'sink': 'Rover creation/mountpoint authorization', 'outcome': 'Persistent privilege expansion', 'counterevidence': ['Role escalation and assigned-device/base changes receive additional checks, but these fields do not.']}

The source evidence recorded for this finding is:

- `cors_dashboard/backend/app/routers/users.py` lines `{'start': 239, 'end': 269}` — The update endpoint accepts all UserUpdate fields; the non-admin self-update branch removes assigned_devices and allowed_bases only.`
- `cors_dashboard/backend/app/routers/users.py` lines `{'start': 263, 'end': 333}` — can_create_rovers and extra_mountpoints survive filtering and reach crud.update_user.`
- `cors_dashboard/backend/app/schemas.py` lines `{'start': 167, 'end': 177}` — UserUpdate exposes can_create_rovers, can_create_users, and extra_mountpoints.`
- `cors_dashboard/backend/app/crud.py` lines `{'start': 631, 'end': 640}` — Every surviving field is set on the database user.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: A coordinator can grant themselves rover-creation and extra-mountpoint powers not assigned by an administrator and provision additional caster credentials.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Validate the input at the trust boundary, bind it to the authenticated resource, fail closed, and add regression coverage.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

# Coordinator-controlled rover fields produce persistent XSS in the global rover table

## Executive Summary

This report documents a `high`-severity security finding in the reviewed repository. Persistent same-origin script execution across a coordinator-to-admin trust boundary, leading to token theft and privileged caster, rover, user, or device operations.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. coordinator rover-management permission
2. unconstrained strings
3. caster failure is non-fatal
4. database commit
5. admin reads global rover set
6. auto-refreshing raw innerHTML

The source evidence recorded for this finding is:

- `cors_dashboard/frontend/js/rovers-global.js` lines `891-1162` — Username, plaintext password, base name, mountpoints, caster hosts, connection mountpoint/client ID, and data attributes are concatenated into tr.innerHTML.`
- `cors_dashboard/backend/app/schemas.py` lines `217-273` — Rover username, password, mountpoints and target_casters are unrestricted strings/lists with no output-safe character validation.`
- `cors_dashboard/backend/app/routers/devices.py` lines `440-463,539-572,592-613` — A coordinator who manages a rover can submit arbitrary password/mountpoint/host strings. Failed caster confirmation is only logged; the values are still committed locally and returned.`
- `cors_dashboard/backend/app/main.py` lines `5928-5970` — Administrators receive every visible rover, including the attacker-created fields, and the frontend automatically renders them during initialization and polling.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: Persistent same-origin script execution across a coordinator-to-admin trust boundary, leading to token theft and privileged caster, rover, user, or device operations.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Validate the input at the trust boundary, bind it to the authenticated resource, fail closed, and add regression coverage.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

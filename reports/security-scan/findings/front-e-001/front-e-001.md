# Unauthenticated User-Agent values become stored script in the admin VPN-intelligence table

## Executive Summary

This report documents a `high`-severity security finding in the reviewed repository. Same-origin script execution in an administrator session, enabling theft of Web Storage bearer tokens and authenticated user, device, caster, and security-control actions.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. 1. Reach the Public login metadata and privileged security dashboard through Unauthenticated User-Agent persistence/grouping to admin innerHTML table.
2. 2. Satisfy the bounded preconditions: Fingerprint is observed from enough IP strings; Administrator opens security page.
3. 3. Supply or cause the reviewed attacker-controlled input: User-Agent plus repeated apparent source IPs.
4. 4. The nearest control fails or is incomplete, crossing Public request metadata -> privileged security telemetry DOM.
5. 5. The reviewed sink is reached, producing Same-origin script execution in an administrator session, enabling theft of Web Storage bearer tokens and authenticated user, device, caster, and security-control actions..

The source evidence recorded for this finding is:

- `cors_dashboard/frontend/js/security.js` lines `250-272` — item.user_agent from the security-stats API is interpolated directly into tr.innerHTML without text encoding.`
- `cors_dashboard/backend/app/security.py` lines `82-142,326-341` — Each failed login records the caller-controlled User-Agent as fingerprint metadata; the value is retained in process memory.`
- `cors_dashboard/backend/app/security.py` lines `445-525,535-559` — When one fingerprint is seen from more than two IP strings, up to 100 characters of its raw User-Agent are returned as vpn_suspects[].user_agent.`
- `cors_dashboard/backend/app/main.py` lines `4140-4159,5772-5792` — The public login route calls the recorder before authentication; the resulting stats are exposed to MANAGE_USERS principals and polled by the security page.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: Same-origin script execution in an administrator session, enabling theft of Web Storage bearer tokens and authenticated user, device, caster, and security-control actions.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Encode User-Agent output and test the VPN-intelligence table with inert markup.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

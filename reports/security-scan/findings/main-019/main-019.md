# Sync-by-username accepts arbitrary caster hosts, enabling authenticated SSRF

## Executive Summary

This report documents a `high`-severity security finding in the reviewed repository. Internal-network reachability probing and protocol writes to services on the caster port; possible interaction with otherwise unreachable caster/admin services.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. Request host list
2. no allowlist
3. backend TCP connect
4. 10-second timeout side channel

The source evidence recorded for this finding is:

- `cors_dashboard/backend/app/main.py` lines `6037-6069` — update_req.target_casters is copied into target_hosts with only strip/dedup, then passed to CasterClient and asyncio.open_connection.`
- `cors_dashboard/backend/app/caster_client.py` lines `21-47` — CasterClient connects to arbitrary self.host on configured caster port.`
- `cors_dashboard/backend/app/main.py` lines `{'start': 5971, 'end': 5987}` — Coordinator rover managers can reach sync-by-username.`
- `cors_dashboard/backend/app/main.py` lines `{'start': 6042, 'end': 6074}` — RoverUpdate.target_casters is accepted without allowlisting and receives update_req.password or the stored rover password.`
- `cors_dashboard/backend/app/caster_client.py` lines `{'start': 39, 'end': 47}` — The selected host receives the constructed management command.`
- `cors_dashboard/backend/app/caster_client.py` lines `{'start': 240, 'end': 265}` — create_user interpolates and transmits the rover username/password.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: Internal-network reachability probing and protocol writes to services on the caster port; possible interaction with otherwise unreachable caster/admin services.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Validate the input at the trust boundary, bind it to the authenticated resource, fail closed, and add regression coverage.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

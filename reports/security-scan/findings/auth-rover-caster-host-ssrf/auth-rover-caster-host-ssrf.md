# Rover update permits SSRF and credential exfiltration through arbitrary caster hosts

## Executive Summary

This report documents a `high`-severity security finding in the reviewed repository. Blind SSRF into reachable networks and exfiltration of the existing rover username/password to an attacker-controlled listener.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. 1. Reach the Rover update API and backend caster client through Attacker-selected target_casters receives existing rover password.
2. 2. Satisfy the bounded preconditions: Attacker controls/reaches TCP listener; Manager has one rover.
3. 3. Supply or cause the reviewed attacker-controlled input: target_casters host.
4. 4. The nearest control fails or is incomplete, crossing Tenant-scoped dashboard input -> backend network -> reusable credential disclosure.
5. 5. The reviewed sink is reached, producing Blind SSRF into reachable networks and exfiltration of the existing rover username/password to an attacker-controlled listener..

The source evidence recorded for this finding is:

- `cors_dashboard/backend/app/routers/devices.py` lines `{'start': 501, 'end': 504}` — target_casters from RoverUpdate replaces the caster host list without allowlist validation.`
- `cors_dashboard/backend/app/routers/devices.py` lines `{'start': 540, 'end': 550}` — CasterClient is instantiated for each attacker-selected host and receives the rover username/password.`
- `cors_dashboard/backend/app/caster_client.py` lines `{'start': 39, 'end': 47}` — CasterClient opens a TCP connection to the provided host on its configured port.`
- `cors_dashboard/backend/app/caster_client.py` lines `{'start': 240, 'end': 267}` — create_user transmits the supplied username, plaintext password, and mountpoints.`
- `cors_dashboard/backend/app/routers/devices.py` lines `{'start': 501, 'end': 504}` — RoverUpdate.target_casters replaces the host list without validation.`
- `cors_dashboard/backend/app/routers/devices.py` lines `{'start': 540, 'end': 550}` — Each selected host receives the current or replacement rover credential.`
- `cors_dashboard/backend/app/caster_client.py` lines `{'start': 39, 'end': 47}` — The generic client opens TCP to the supplied host and writes the command.`
- `cors_dashboard/backend/app/schemas.py` lines `262-273` — RoverUpdate.target_casters is an unconstrained list of strings with no host grammar, approved-caster membership, IP classification, or item/count limit.`
- `cors_dashboard/backend/app/crud.py` lines `719-731` — update_rover_account applies any caller dictionary key that names a model attribute; it does not enforce a destination allowlist or ownership invariant.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: Blind SSRF into reachable networks and exfiltration of the existing rover username/password to an attacker-controlled listener.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Allowlist caster destinations and never send existing secrets to unapproved hosts.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

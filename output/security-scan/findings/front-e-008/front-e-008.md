# Compromised device telemetry can inject script through satellite PRN signal bars

## Executive Summary

This report documents a `medium`-severity security finding in the reviewed repository. A compromised station can execute script in any operator session viewing its live satellite panel, steal that user's bearer token, and pivot from device-level compromise into dashboard authority and sibling-station actions allowed to the victim.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. device controls rtcm_signal_update JSON
2. backend shallow-copies satellite dict
3. valid SNR passes filter
4. nonnumeric PRN returned verbatim
5. innerHTML sink
6. operator live-view victim

The source evidence recorded for this finding is:

- `cors_dashboard/frontend/js/satellite-sky-view.js` lines `291-367` — A satellite with positive numeric SNR has getNormalizedPrn(sat) inserted directly into itemDiv.innerHTML.`
- `cors_dashboard/frontend/js/satellite-sky-view.js` lines `50-68` — If prn is not an optional-uppercase-letter plus digits and Number(prn) is NaN, the function returns the original attacker string.`
- `cors_dashboard/backend/app/main.py` lines `8079-8130` — rtcm_signal_update accepts an arbitrary dict from an authenticated/accepted field-device socket, copies each satellite dict, and broadcasts it without validating prn/system/snr field types or grammar.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: A compromised station can execute script in any operator session viewing its live satellite panel, steal that user's bearer token, and pivot from device-level compromise into dashboard authority and sibling-station actions allowed to the victim.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Validate the input at the trust boundary, bind it to the authenticated resource, fail closed, and add regression coverage.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

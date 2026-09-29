# Stored station names execute HTML in permanent nearest-station tooltips

## Executive Summary

This report documents a `high`-severity security finding in the reviewed repository. Stored script execution in an authenticated map user's origin, with access to bearer tokens and privileged API calls.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. 1. Reach the Authenticated nearest-station Leaflet map through Stored station name parsed in permanent tooltip HTML.
2. 2. Satisfy the bounded preconditions: Malicious station is selected as nearest; Victim uses feature.
3. 3. Supply or cause the reviewed attacker-controlled input: Persisted station name.
4. 4. The nearest control fails or is incomplete, crossing Station edit scope -> another user dashboard DOM/token boundary.
5. 5. The reviewed sink is reached, producing Stored script execution in an authenticated map user's origin, with access to bearer tokens and privileged API calls..

The source evidence recorded for this finding is:

- `cors_dashboard/frontend/js/main.js` lines `{'start': 905, 'end': 923}` — Station editors can persist arbitrary names.`
- `cors_dashboard/frontend/js/react-components.js` lines `{'start': 1043, 'end': 1088}` — The nearest-station workflow selects a device object containing the stored name.`
- `cors_dashboard/frontend/js/react-components.js` lines `{'start': 1122, 'end': 1144}` — stationName is interpolated into raw HTML passed to Leaflet bindTooltip and opened permanently.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: Stored script execution in an authenticated map user's origin, with access to bearer tokens and privileged API calls.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Encode stationName or use a DOM text node in tooltip content.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

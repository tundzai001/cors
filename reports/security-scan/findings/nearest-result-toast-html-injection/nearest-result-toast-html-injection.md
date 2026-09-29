# Nearest-station result interpolates station and geocoder data into an unsafe HTML toast

## Executive Summary

This report documents a `high`-severity security finding in the reviewed repository. Script execution in an authenticated user's dashboard session when nearest-search results are shown.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. 1. Reach the Nearest-station result notification through Stored station name or geocoder label enters CorsUI innerHTML toast.
2. 2. Satisfy the bounded preconditions: Victim runs nearest search; Attacker-controlled value is selected.
3. 3. Supply or cause the reviewed attacker-controlled input: Station name or result label.
4. 4. The nearest control fails or is incomplete, crossing Lower-privilege/third-party data -> authenticated dashboard DOM/token boundary.
5. 5. The reviewed sink is reached, producing Script execution in an authenticated user's dashboard session when nearest-search results are shown..

The source evidence recorded for this finding is:

- `cors_dashboard/frontend/js/react-components.js` lines `{'start': 1095, 'end': 1100}` — External locationName and stored station names are combined into the toast message.`
- `cors_dashboard/frontend/js/react-components.js` lines `{'start': 1193, 'end': 1207}` — Nominatim response display_name is passed as locationName.`
- `cors_dashboard/frontend/js/main.js` lines `{'start': 1609, 'end': 1615}` — showSuccess routes arbitrary message text to CorsUI.showToast.`
- `cors_dashboard/frontend/js/ops-ui.js` lines `{'start': 55, 'end': 70}` — CorsUI.showToast interpolates message into toastEl.innerHTML; ops-ui is bundled in core.min.js loaded by index.html.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: Script execution in an authenticated user's dashboard session when nearest-search results are shown.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Render notification text safely and test stored station names.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

# Viewer device listing serializes raw NTRIP service credentials

## Executive Summary

This report documents a `medium`-severity security finding in the reviewed repository. Disclosure of NTRIP source/client credentials can permit unauthorized caster access, correction-stream use, or source impersonation.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. 1. Reach the Viewer device list and station detail payloads through Raw password-bearing service_config serialized to view-only users.
2. 2. Satisfy the bounded preconditions: Station has configured NTRIP/command secrets.
3. 3. Supply or cause the reviewed attacker-controlled input: Normal device list/detail request.
4. 4. The nearest control fails or is incomplete, crossing Station visibility -> possession of reusable external-service credentials.
5. 5. The reviewed sink is reached, producing Disclosure of NTRIP source/client credentials can permit unauthorized caster access, correction-stream use, or source impersonation..

The source evidence recorded for this finding is:

- `cors_dashboard/backend/app/routers/devices.py` lines `{'start': 60, 'end': 123}` — Viewer-scoped listing filters which devices are visible but returns full Device ORM objects.`
- `cors_dashboard/backend/app/schemas.py` lines `{'start': 35, 'end': 45}` — The Device response schema includes raw service_config.`
- `cors_dashboard/backend/app/schemas.py` lines `{'start': 78, 'end': 102}` — Service configuration schemas contain NTRIP usernames and passwords.`
- `cors_dashboard/backend/app/schemas.py` lines `37-58` — schemas.Device serializes the entire service_config dictionary without a public/redacted output model.`
- `cors_dashboard/backend/app/schemas.py` lines `67-103` — The same configuration shape contains password1, password2, and rtcmpassword1 alongside caster usernames and destinations.`
- `cors_dashboard/backend/app/models.py` lines `9-29` — Device.service_config is stored and returned as unrestricted JSONB.`
- `cors_dashboard/frontend/js/dashboard-detail.js` lines `806-820, 839-867, 1109-1114` — canEdit only determines the disabled prop. The service tab remains visible when station state permits it, and ServiceConfigForm receives the complete device object even for a read-only viewer.`
- `cors_dashboard/frontend/js/dashboard-forms.js` lines `693-727, 753-833` — ServiceConfigForm spreads every service_config property, including password1/password2/rtcmpassword1/command_pass, into formState before applying the disabled UI state.`
- `cors_dashboard/frontend/js/dashboard-forms.js` lines `1099-1108, 1258-1267, 1408-1417, 1551-1560` — Command, server 1, server 2 and RTCM passwords are bound as input values. type=password masks pixels only; disabled controls and React state remain readable through DOM/DevTools and page scripts.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: Disclosure of NTRIP source/client credentials can permit unauthorized caster access, correction-stream use, or source impersonation.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Return role-specific redacted service_config DTOs.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

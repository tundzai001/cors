# Rover dashboard discloses reusable plaintext caster passwords to viewer-scoped sessions

## Executive Summary

This report documents a `medium`-severity security finding in the reviewed repository. A lower-privilege dashboard account can copy rover credentials for direct correction-stream access, share them outside dashboard controls, and retain access independently of its dashboard session until the rover password is rotated.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. 1. Reach the Global rover list/table/export through Viewer-scoped API response includes reusable rover passwords and UI displays/exports them.
2. 2. Satisfy the bounded preconditions: Caller can see at least one rover; Password remains active.
3. 3. Supply or cause the reviewed attacker-controlled input: Normal rover list request.
4. 4. The nearest control fails or is incomplete, crossing Dashboard record visibility -> possession of downstream caster credential.
5. 5. The reviewed sink is reached, producing A lower-privilege dashboard account can copy rover credentials for direct correction-stream access, share them outside dashboard controls, and retain access independently of its dashboard session until the rover password is rotated..

The source evidence recorded for this finding is:

- `cors_dashboard/backend/app/schemas.py` lines `217-246` — RoverAccountResponse inherits RoverAccountBase.password, making the secret a normal output field on every endpoint using this response model.`
- `cors_dashboard/backend/app/crud.py` lines `777-792` — A viewer's allowed_rovers match returns the complete RoverAccount rather than a redacted view.`
- `cors_dashboard/backend/app/models.py` lines `36-54` — The response is populated from the plaintext RoverAccount.password column.`
- `cors_dashboard/frontend/rovers-global.html` lines `146-169` — The main multi-account management table explicitly reserves a Password column. Supporting referenced bundle review confirms each returned rover.password is rendered in this column.`
- `cors_dashboard/frontend/rovers-global.html` lines `225-240, 368-378` — Both creation and edit password controls use type=text rather than a masked or one-time-secret workflow.`
- `cors_dashboard/frontend/js/rovers-global.js` lines `1129-1162` — Every rendered rover row prints r.password in clear text; the viewer branch only hides mutation controls.`
- `cors_dashboard/frontend/js/rovers-global.js` lines `1290-1304` — The browser export explicitly includes the password column for selected rover objects.`
- `cors_dashboard/backend/app/schemas.py` lines `217-246` — RoverAccountResponse inherits RoverAccountBase.password, so list/history responses serialize the reusable secret.`
- `cors_dashboard/backend/app/main.py` lines `5928-5970` — The list uses get_current_user and only specially rejects coordinators lacking rover-management delegation; it does not require a password-view permission and returns passwords for the caller's visible rover set.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: A lower-privilege dashboard account can copy rover credentials for direct correction-stream access, share them outside dashboard controls, and retain access independently of its dashboard session until the rover password is rotated.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Remove passwords from standard DTOs and require explicit audited reveal/rotation.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

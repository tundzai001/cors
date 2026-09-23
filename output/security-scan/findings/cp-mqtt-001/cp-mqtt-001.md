# MQTT status payload serial is not bound to the authenticated topic serial

## Executive Summary

This report documents a `high`-severity security finding in the reviewed repository. A device restricted by broker ACL to its own topic can impersonate a sibling station, poison its online/version/configuration state, create misleading UI telemetry, and potentially overwrite target configuration when that target is currently marked synced.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. 1. Reach the Backend MQTT status ingestion through Own authorized status topic carrying another station serial in JSON.
2. 2. Satisfy the bounded preconditions: Publisher may write its own status topic; Target station serial is known.
3. 3. Supply or cause the reviewed attacker-controlled input: payload.serial.
4. 4. The nearest control fails or is incomplete, crossing Authenticated topic identity -> victim station reconciler/database/UI identity.
5. 5. The reviewed sink is reached, producing A device restricted by broker ACL to its own topic can impersonate a sibling station, poison its online/version/configuration state, create misleading UI telemetry, and potentially overwrite target configuration when that target is currently marked synced..

The source evidence recorded for this finding is:

- `cors_dashboard/backend/app/mqtt.py` lines `671-678` — The handler derives serial from topic_parts[2] but does not overwrite or compare data.serial.`
- `cors_dashboard/backend/app/mqtt.py` lines `757-812` — JSON is accepted and passed to status_reconciler, which keys its state by payload.serial, while later metadata/cache work remains keyed by the topic serial.`
- `cors_dashboard/backend/app/mqtt.py` lines `923-979` — db_data preserves data.serial and is passed to crud.update_or_create_device, allowing the payload identity to select the persisted target.`
- `cors_dashboard/backend/app/status_reconciler.py` lines `71-97` — accept derives its state key and normalized serial solely from payload['serial'].`
- `cors_dashboard/backend/app/mqtt.py` lines `667-678,800-812` — The caller extracts serial from the topic but passes unmodified JSON to accept and does not overwrite payload serial first.`
- `cors_dashboard/backend/app/mqtt.py` lines `885-899,923-979` — Normalized payload serial is broadcast and copied into DB data, while metadata/cache decisions use the topic serial.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: A device restricted by broker ACL to its own topic can impersonate a sibling station, poison its online/version/configuration state, create misleading UI telemetry, and potentially overwrite target configuration when that target is currently marked synced.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Bind payload serial to the topic serial before all processing and add a two-device regression test.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

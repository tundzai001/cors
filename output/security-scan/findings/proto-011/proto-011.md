# Unvalidated GLONASS ephemeris can poison or heavily amplify satellite-position decoding

## Executive Summary

This report documents a `medium`-severity security finding in the reviewed repository. Targeted loss of signal telemetry and potential thread-pool CPU exhaustion when repeated across satellites/serials.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. 1. Reach the RTCM GLONASS ephemeris/satellite propagation through CRC-valid but physically invalid ephemeris drives unsafe or expensive integration.
2. 2. Satisfy the bounded preconditions: Target decoder processes crafted valid frames; Sufficient message rate/satellite mix.
3. 3. Supply or cause the reviewed attacker-controlled input: RTCM 1020 ephemeris plus MSM frames.
4. 4. The nearest control fails or is incomplete, crossing GNSS protocol trust -> persistent ephemeris -> shared thread-pool CPU/telemetry.
5. 5. The reviewed sink is reached, producing Targeted loss of signal telemetry and potential thread-pool CPU exhaustion when repeated across satellites/serials..

The source evidence recorded for this finding is:

- `cors_dashboard/backend/app/rtcm_signal_decoder.py` lines `347-384` — RTCM 1020 position/velocity/acceleration values are stored without physical sanity checks.`
- `cors_dashboard/backend/app/rtcm_signal_decoder.py` lines `106-170,692-712` — Each selected satellite can run up to about 720 RK4 steps (four derivative evaluations each); zero-radius state divides by zero and is not handled locally.`
- `cors_dashboard/backend/app/rtcm_signal_decoder.py` lines `593-616` — decode_sync catches only IndexError and ValueError, not ZeroDivisionError/OverflowError from orbital propagation.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: Targeted loss of signal telemetry and potential thread-pool CPU exhaustion when repeated across satellites/serials.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Validate physical ranges/finiteness, cap propagation, and catch arithmetic failures.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

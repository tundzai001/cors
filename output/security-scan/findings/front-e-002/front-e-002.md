# Trusted forwarding headers allow unauthenticated stored XSS in admin IP-security widgets

## Executive Summary

This report documents a `high`-severity security finding in the reviewed repository. Persistent or process-lifetime same-origin script execution when an administrator monitors suspicious or blocked IPs, with access to the administrator bearer token and privileged APIs.

I reviewed revision `13546e7b992aefb3237e9e56d5e6c70c529db3ff` and the recorded source locations directly. This is a static, source-backed assessment; I did not connect to production, a broker, a caster, a receiver, or a live station. The affected-version statement is limited to the reviewed revision because no fixing revision was supplied.

## Background

The relevant surface crosses the product trust boundary described in the scan threat model. The nearest control is the authenticated request, topic, parser, browser sink, or persistence boundary identified by the finding. The security invariant is that attacker-controlled data must remain bound to the authorized identity and must be validated before it reaches a privileged operation or rendering sink.

## Vulnerability Details

We first reach the reviewed path through the documented preconditions:

1. public failed login
2. unconditional X-Forwarded-For trust
3. no ipaddress validation
4. raw string used as security key
5. raw value returned in stats
6. HTML and JavaScript attribute sinks

The source evidence recorded for this finding is:

- `cors_dashboard/frontend/js/security.js` lines `365-405` — Persisted block.ip and block.reason are inserted into table markup and a data-ip attribute without encoding.`
- `cors_dashboard/frontend/js/security.js` lines `409-429` — Suspicious item.ip is inserted into text and into onclick=handleBlockSuspiciousIP('${item.ip}'), giving the string a JavaScript/HTML attribute context.`
- `cors_dashboard/backend/app/security.py` lines `429-433` — The security subsystem unconditionally trusts the first X-Forwarded-For value, or X-Real-IP, without proxy trust or IP-address validation.`
- `cors_dashboard/backend/app/security.py` lines `180-210,387-414,485-494,535-558` — The raw derived string keys suspicious_patterns and is written unchanged into IpSecurityLog when rate/threat thresholds block it, then returned to the admin stats API.`

The failure is that the nearest control does not enforce the final resource identity, input grammar, output encoding, freshness, or failure-closed behavior required at this boundary. The resulting state is described conservatively in the validated attack-path record: Persistent or process-lifetime same-origin script execution when an administrator monitors suspicious or blocked IPs, with access to the administrator bearer token and privileged APIs.

## Exploitability Analysis

The attack path is bounded by the preconditions recorded in the scan. A practical attacker would need to satisfy those preconditions and then supply the reviewed input. Reliability depends on deployment configuration, authorization scope, data shape, and whether compensating controls such as broker ACLs, CSP, database constraints, or network isolation are active. Those environmental facts were not exercised against a live system. We therefore report the source-proven primitive and its plausible impact without claiming a live exploit or a stronger outcome than the evidence supports.

## Proof of Concept

The accompanying `poc/README.md` is a non-operational regression-test design. It uses synthetic values or test doubles only and intentionally does not contact any service or perform a destructive action. A test harness should assert the secure invariant at the boundary and verify that the original unsafe value is rejected, canonicalized, encoded, scoped, or bounded as appropriate.

## Remediation

Restore the invariant at the narrowest shared boundary before the value is persisted, dispatched, parsed, or rendered. Validate the input at the trust boundary, bind it to the authenticated resource, fail closed, and add regression coverage.

Add regression coverage for the original trigger, missing and malformed inputs, cross-tenant or cross-station identities, repeated requests, failure paths, and the relevant authorization or output-encoding policy. During migration, retain the tactical guard and add telemetry for rejected values; do not silently preserve a legacy insecure mode.

## Summary

The reviewed revision contains a source-backed path from the stated attacker-controlled input to the documented security consequence. I validated the evidence statically and kept the proof-of-concept offline. The recommended fix is to make the security invariant explicit in one owned boundary and to prove it with focused regression tests and deployment checks.

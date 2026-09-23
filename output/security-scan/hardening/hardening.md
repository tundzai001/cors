# Security Hardening Review: cors repository

## Evidence Basis

We reviewed the complete 110-file worklist at the recorded revision and consolidated 145 canonical candidates. The final set contains 100 reportable findings, with additional validated items explicitly deferred, suppressed, or not applicable.

## Constraints

No live services or production devices were contacted. Performance and deployment budgets were not supplied, so tradeoffs below are source-derived and must be measured during implementation.

## Opportunity Portfolio

| Opportunity | Evidence | Options | Recommendation | Proposal |
| --- | --- | --- | --- | --- |
| Centralize identity and authorization | MQTT/WebSocket scope, device-token, station-scope and command findings | Local guards; shared authorization boundary; capability-based device enrollment | Begin with shared boundary plus tactical guards; move device enrollment toward unique capabilities | proposals/identity-boundaries.md |
| Make untrusted data safe by construction | HTML sinks, command injection, parser/resource findings | Per-sink fixes; shared encoders/parsers and bounded ingress | Adopt shared safe wrappers while retaining local regression tests | proposals/untrusted-data-boundaries.md |
| Remove ambient secrets and fail-open behavior | credential, logging, Redis and fallback findings | Rotation/local fixes; secret broker and fail-closed policy | Rotate immediately, then centralize secret issuance and failure policy | proposals/secret-and-failure-boundaries.md |

## Recommendation Summary

I recommend the incremental shared-boundary options under the current constraints: they reduce recurrence without requiring an immediate service split. We should preserve tactical fixes during migration and measure latency, memory, rejection rates, and operator recovery before selecting a more isolated architecture.

## Next Decisions

Confirm credential rotation ownership, choose the device enrollment protocol, define station-scope policy centrally, and establish regression/benchmark gates before implementation. Proposed work is not a remediation claim until the source paths are changed and revalidated.

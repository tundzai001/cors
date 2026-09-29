# Security Review: cors

## Scope

The scan reviewed the canonical include paths and exclusions listed below.

- Scan mode: repository
- Target kind: git_worktree
- Target ID: target_sha256_a933a0c003ae1cb9aa57d91b1e587af1bdd7bd54df1a885effd58bb3451fb5eb
- Revision: 13546e7b992aefb3237e9e56d5e6c70c529db3ff
- Snapshot digest: codex-security-snapshot/v1:sha256:32c72b8edad7b25495bf771955ed951c46bc67644dec918876744c2d10a6fad0
- Inventory strategy: repository
- Included paths: .
- Excluded paths: none
- Runtime or test status: not recorded

### Scan Summary

| Field | Value |
| --- | --- |
| Reportable findings | 100 |
| Severity mix | critical: 4, high: 40, medium: 54, low: 2 |
| Confidence mix | high: 84, medium: 16 |
| Coverage | partial |
| Validation mode | not recorded |

Canonical artifacts: `scan-manifest.json`, `findings.json`, and `coverage.json`. This report is a deterministic projection of those files.

## Threat Model

No explicit canonical threat-model summary was recorded.

## Findings

| Finding | Severity | Confidence | Detailed write-up |
| --- | --- | --- | --- |
| [Non-unique device-token index accepts deterministic cross-device bearer-token collisions](#finding-1) | critical | high | [Open report](findings/migdev-b-001/migdev-b-001.md) |
| [CRLF injection in Unicore survey duration reaches raw receiver command execution](#finding-2) | critical | high | [Open report](findings/cp-cmd-001/cp-cmd-001.md) |
| [Device WebSocket authentication token is deterministically derivable from public serial](#finding-3) | critical | high | [Open report](findings/auth-predictable-device-token/auth-predictable-device-token.md) |
| [Hard-coded shared MQTT credentials can expose the fleet control plane](#finding-4) | critical | high | [Open report](findings/geo-001/geo-001.md) |
| [Trusted forwarding headers allow unauthenticated stored XSS in admin IP-security widgets](#finding-5) | high | medium | [Open report](findings/front-e-002/front-e-002.md) |
| [Coordinator-supplied block reasons create persistent XSS in the admin caster blocklist tab](#finding-6) | high | high | [Open report](findings/front-e-006/front-e-006.md) |
| [Coordinator can self-assign rover and mountpoint capabilities through generic user update](#finding-7) | high | high | [Open report](findings/auth-coordinator-self-capability-mass-assignment/auth-coordinator-self-capability-mass-assignment.md) |
| [Stored activity-log messages and serials execute HTML in the operations event table](#finding-8) | high | high | [Open report](findings/ops-event-table-stored-xss/ops-event-table-stored-xss.md) |
| [Persistent same-origin XSS in public station SEO page](#finding-9) | high | high | [Open report](findings/main-002/main-002.md) |
| [Hard-coded NTRIP source and target credentials expose correction-stream access](#finding-10) | high | medium | [Open report](findings/geo-002/geo-002.md) |
| [Public map endpoint discloses complete password-bearing station service configuration](#finding-11) | high | high | [Open report](findings/main-001/main-001.md) |
| [RTCM NTRIP start wrapper independently exposes the same cross-station activation BOLA](#finding-12) | high | high | [Open report](findings/main-012/main-012.md) |
| [Sync-by-username accepts arbitrary caster hosts, enabling authenticated SSRF](#finding-13) | high | high | [Open report](findings/main-019/main-019.md) |
| [Unauthenticated device-name staging reaches a Leaflet nearest-station tooltip HTML sink](#finding-14) | high | medium | [Open report](findings/front-e-010/front-e-010.md) |
| [Every viewer can download global device templates containing service credentials](#finding-15) | high | high | [Open report](findings/device-config-templates-disclose-global-service-secrets/device-config-templates-disclose-global-service-secrets.md) |
| [View-only station access authorizes updates to shared rover accounts](#finding-16) | high | high | [Open report](findings/auth-view-only-shared-rover-update/auth-view-only-shared-rover-update.md) |
| [Status-topic publishers can invoke privileged Geotek commands](#finding-17) | high | high | [Open report](findings/geo-010/geo-010.md) |
| [Stored station names execute HTML in the authenticated all-stations map popup](#finding-18) | high | high | [Open report](findings/station-name-xss-authenticated-leaflet-popup/station-name-xss-authenticated-leaflet-popup.md) |
| [Malicious caster sourcetable fields execute as HTML in the admin mountpoint view](#finding-19) | high | medium | [Open report](findings/front-e-007/front-e-007.md) |
| [New caster sources default to the public password 12345](#finding-20) | high | high | [Open report](findings/data-006/data-006.md) |
| [Create-multi accepts arbitrary target caster hosts, enabling authenticated SSRF](#finding-21) | high | high | [Open report](findings/main-021/main-021.md) |
| [Rover update permits SSRF and credential exfiltration through arbitrary caster hosts](#finding-22) | high | high | [Open report](findings/auth-rover-caster-host-ssrf/auth-rover-caster-host-ssrf.md) |
| [Coordinator-controlled rover fields produce persistent XSS in the global rover table](#finding-23) | high | high | [Open report](findings/front-e-003/front-e-003.md) |
| [Unauthenticated User-Agent values become stored script in the admin VPN-intelligence table](#finding-24) | high | high | [Open report](findings/front-e-001/front-e-001.md) |
| [Any one-station overlap grants whole shared-rover credentials and mutation authority](#finding-25) | high | high | [Open report](findings/data-002/data-002.md) |
| [Editable station names and serials execute in user-assignment checklists](#finding-26) | high | high | [Open report](findings/fef-004/fef-004.md) |
| [Coordinator-created viewer fields execute as HTML in the user table](#finding-27) | high | high | [Open report](findings/fef-003/fef-003.md) |
| [Stored station names execute HTML in the public map popup](#finding-28) | high | high | [Open report](findings/station-name-xss-public-map-popup/station-name-xss-public-map-popup.md) |
| [Device-controlled sync mismatch text reaches an HTML toast sink](#finding-29) | high | high | [Open report](findings/feb-002/feb-002.md) |
| [Nearest-station result interpolates station and geocoder data into an unsafe HTML toast](#finding-30) | high | high | [Open report](findings/nearest-result-toast-html-injection/nearest-result-toast-html-injection.md) |
| [Coordinator delegated only user creation can overwrite global system settings](#finding-31) | high | high | [Open report](findings/main-025/main-025.md) |
| [Rover update accepts mountpoints outside the coordinator's station scope](#finding-32) | high | high | [Open report](findings/auth-rover-mountpoint-scope-bypass/auth-rover-mountpoint-scope-bypass.md) |
| [Stored station names execute HTML in permanent nearest-station tooltips](#finding-33) | high | high | [Open report](findings/station-name-xss-nearest-station-tooltip/station-name-xss-nearest-station-tooltip.md) |
| [Caster table queries use publicly known fallback credentials](#finding-34) | high | medium | [Open report](findings/caster-hardcoded-table-credentials/caster-hardcoded-table-credentials.md) |
| [MQTT status payload serial is not bound to the authenticated topic serial](#finding-35) | high | high | [Open report](findings/cp-mqtt-001/cp-mqtt-001.md) |
| [Security settings can be clickjacked because the privileged page is frameable](#finding-36) | high | medium | [Open report](findings/security-page-clickjacking-security-switches/security-page-clickjacking-security-switches.md) |
| [Device WebSocket authentication fails open for unknown or tokenless serials and reaches a device UPSERT](#finding-37) | high | high | [Open report](findings/main-003/main-003.md) |
| [Coordinator can block an arbitrary caster account absent from the local rover database](#finding-38) | high | high | [Open report](findings/main-016/main-016.md) |
| [Replayed EXECUTE commands spawn unbounded concurrent receiver-reset workers](#finding-39) | high | high | [Open report](findings/geo-011/geo-011.md) |
| [Device, caster, and rover alert fields execute HTML in the operations alert list](#finding-40) | high | high | [Open report](findings/ops-alert-list-stored-xss/ops-alert-list-stored-xss.md) |
| [MQTT commands lack freshness and replay protection before destructive actions](#finding-41) | high | high | [Open report](findings/geo-006/geo-006.md) |
| [CRLF injection in Unicore fixed coordinates reaches raw receiver command execution](#finding-42) | high | high | [Open report](findings/cp-cmd-002/cp-cmd-002.md) |
| [rtcm_port configuration permits CRLF injection of additional receiver commands](#finding-43) | high | high | [Open report](findings/geo-012/geo-012.md) |
| [Station records are interpolated into Leaflet popup HTML without encoding](#finding-44) | high | high | [Open report](findings/fef-001/fef-001.md) |
| [Unvalidated GLONASS ephemeris can poison or heavily amplify satellite-position decoding](#finding-45) | medium | medium | [Open report](findings/proto-011/proto-011.md) |
| [Compromised device telemetry can inject script through satellite PRN signal bars](#finding-46) | medium | high | [Open report](findings/front-e-008/front-e-008.md) |
| [One global NMEA parser mixes satellite state across independently authenticated stations](#finding-47) | medium | high | [Open report](findings/proto-001/proto-001.md) |
| [CRC-valid short RTCM frames cause large result-object amplification](#finding-48) | medium | high | [Open report](findings/proto-004/proto-004.md) |
| [Space-form /configset credentials are retained in Zalo diagnostics and application logs](#finding-49) | medium | high | [Open report](findings/zalo-configset-space-password-log-disclosure/zalo-configset-space-password-log-disclosure.md) |
| [Geocoder display names execute HTML in the searched-location popup](#finding-50) | medium | medium | [Open report](findings/nominatim-display-name-xss-leaflet-popup/nominatim-display-name-xss-leaflet-popup.md) |
| [Active RTCM station listing ignores station scope and exposes all client IDs](#finding-51) | medium | high | [Open report](findings/main-013/main-013.md) |
| [Space-form /roverupdate passwords are retained in Zalo diagnostics and application logs](#finding-52) | medium | high | [Open report](findings/zalo-roverupdate-space-password-log-disclosure/zalo-roverupdate-space-password-log-disclosure.md) |
| [Unauthenticated GET stop-listen with force=true deletes RTCM sessions and stops station streaming](#finding-53) | medium | high | [Open report](findings/main-008/main-008.md) |
| [Configured Redis URL is ignored and security controls silently fail open](#finding-54) | medium | medium | [Open report](findings/redis-url-ignored-security-failopen/redis-url-ignored-security-failopen.md) |
| [View-only station access authorizes deletion of shared rover accounts](#finding-55) | medium | high | [Open report](findings/auth-view-only-shared-rover-delete/auth-view-only-shared-rover-delete.md) |
| [Coordinator delegated user creation can read backend server logs](#finding-56) | medium | high | [Open report](findings/main-026/main-026.md) |
| [Activity feed exposes global logs to every authenticated user](#finding-57) | medium | high | [Open report](findings/main-022/main-022.md) |
| [Unauthenticated POST stop-station can force-stop correction streaming](#finding-58) | medium | high | [Open report](findings/main-010/main-010.md) |
| [Rover CSV export permits spreadsheet-formula injection from persisted account fields](#finding-59) | medium | medium | [Open report](findings/front-e-005/front-e-005.md) |
| [Full WebSocket URI logging exposes the device bearer token](#finding-60) | medium | medium | [Open report](findings/geo-005/geo-005.md) |
| [Caster clienttable fields are interpolated into rover popup HTML](#finding-61) | medium | high | [Open report](findings/fef-002/fef-002.md) |
| [Caster usertable passwords are cached and returned to coordinators in plaintext](#finding-62) | medium | high | [Open report](findings/caster-coordinator-password-disclosure/caster-coordinator-password-disclosure.md) |
| [Caster sourcetable mountpoint reaches an HTML toast sink in the admin session](#finding-63) | medium | high | [Open report](findings/feb-001/feb-001.md) |
| [Status reconciler retains an unbounded number of attacker-selected serial states](#finding-64) | medium | high | [Open report](findings/proto-009/proto-009.md) |
| [NTRIP response headers can grow without bound while bytes continue arriving](#finding-65) | medium | high | [Open report](findings/geo-009/geo-009.md) |
| [A reusable MQTT credential and device topic are committed in source](#finding-66) | medium | medium | [Open report](findings/sdm-001/sdm-001.md) |
| [JSON RTCM skyview accepts an unbounded satellites collection](#finding-67) | medium | high | [Open report](findings/proto-005/proto-005.md) |
| [Edited-message workflow secrets bypass fail-closed Zalo payload redaction](#finding-68) | medium | high | [Open report](findings/zalo-edited-message-workflow-secret-diagnostic-disclosure/zalo-edited-message-workflow-secret-diagnostic-disclosure.md) |
| [Unauthenticated POST wake-station can start a station correction stream](#finding-69) | medium | high | [Open report](findings/main-007/main-007.md) |
| [NTRIP blocklist authorization fails open on any database error](#finding-70) | medium | high | [Open report](findings/main-027/main-027.md) |
| [Rover dashboard discloses reusable plaintext caster passwords to viewer-scoped sessions](#finding-71) | medium | high | [Open report](findings/front-e-004/front-e-004.md) |
| [Viewer can activate RTCM on any station serial outside assigned scope](#finding-72) | medium | high | [Open report](findings/main-011/main-011.md) |
| [New caster mountpoints default to the public password 12345](#finding-73) | medium | high | [Open report](findings/fef-005/fef-005.md) |
| [Caster-controlled rover fields execute HTML in rover map popups](#finding-74) | medium | high | [Open report](findings/caster-client-xss-rover-leaflet-popup/caster-client-xss-rover-leaflet-popup.md) |
| [Future agent timestamps can permanently retire legitimate boot state](#finding-75) | medium | high | [Open report](findings/proto-008/proto-008.md) |
| [Public visitor WebSocket has no admission limit and stores every live socket](#finding-76) | medium | high | [Open report](findings/main-004/main-004.md) |
| [A valid base-position frame poisons JSON RTCM decoding through a nonexistent method call](#finding-77) | medium | high | [Open report](findings/proto-006/proto-006.md) |
| [Authenticated UI WebSocket start_rtcm action ignores the socket's allowed station scope](#finding-78) | medium | high | [Open report](findings/main-014/main-014.md) |
| [NMEA parser accepts sentences with absent or incorrect checksums](#finding-79) | medium | high | [Open report](findings/proto-002/proto-002.md) |
| [Admin username bypasses login rate limiting before password verification](#finding-80) | medium | high | [Open report](findings/auth-admin-rate-limit-bypass/auth-admin-rate-limit-bypass.md) |
| [Coordinator can remove block rules for an arbitrary unknown-local caster account](#finding-81) | medium | high | [Open report](findings/main-017/main-017.md) |
| [Operations events endpoint permits cross-station log reads and arbitrary serial filtering](#finding-82) | medium | high | [Open report](findings/main-024/main-024.md) |
| [Unauthenticated GET stop-station alias can force-stop correction streaming](#finding-83) | medium | high | [Open report](findings/main-009/main-009.md) |
| [Device-controlled system_info can enable the backend-managed license bypass](#finding-84) | medium | medium | [Open report](findings/data-001/data-001.md) |
| [All-stations map discloses protected station areas to Google tiles](#finding-85) | medium | medium | [Open report](findings/fef-008/fef-008.md) |
| [Public wake requests can accumulate duplicate RTCM lease rows without a client/station uniqueness bound](#finding-86) | medium | high | [Open report](findings/migdev-b-002/migdev-b-002.md) |
| [Unauthenticated GET wake-listen can start a station correction stream](#finding-87) | medium | high | [Open report](findings/main-005/main-005.md) |
| [Unauthenticated GET wake-station alias can start a station correction stream](#finding-88) | medium | high | [Open report](findings/main-006/main-006.md) |
| [Station-scoped viewers receive global caster host and error alerts](#finding-89) | medium | high | [Open report](findings/ops-alerts-exposes-global-caster-errors/ops-alerts-exposes-global-caster-errors.md) |
| [Caster table responses are accumulated without a byte or duration limit](#finding-90) | medium | high | [Open report](findings/caster-unbounded-table-response-dos/caster-unbounded-table-response-dos.md) |
| [WebSocket bearer token is transmitted over plaintext ws://](#finding-91) | medium | high | [Open report](findings/geo-004/geo-004.md) |
| [Authenticated UI WebSocket stop_rtcm action allows cross-station forced shutdown](#finding-92) | medium | high | [Open report](findings/main-015/main-015.md) |
| [Operations summary leaks global recent events and caster topology despite scoped device counts](#finding-93) | medium | high | [Open report](findings/main-023/main-023.md) |
| [View-only station access exposes plaintext rover credentials](#finding-94) | medium | high | [Open report](findings/auth-view-only-rover-credential-disclosure/auth-view-only-rover-credential-disclosure.md) |
| [Negative GSV PRNs bypass bounds and create an unbounded persistent satellite dictionary](#finding-95) | medium | high | [Open report](findings/proto-003/proto-003.md) |
| [Stored station names execute HTML in the delete-device confirmation dialog](#finding-96) | medium | high | [Open report](findings/station-name-xss-delete-confirmation/station-name-xss-delete-confirmation.md) |
| [Viewer device listing serializes raw NTRIP service credentials](#finding-97) | medium | high | [Open report](findings/auth-viewer-ntrip-secret-disclosure/auth-viewer-ntrip-secret-disclosure.md) |
| [Retained device status publishes reusable caster and command passwords](#finding-98) | medium | high | [Open report](findings/geo-013/geo-013.md) |
| [NTRIP MQTT control flood schedules unbounded coroutines outside the bounded ingress queue](#finding-99) | low | medium | [Open report](findings/cp-mqtt-005/cp-mqtt-005.md) |
| [RTCM decoder retains per-serial buffers and navigation caches without eviction](#finding-100) | low | medium | [Open report](findings/proto-010/proto-010.md) |

### Confidence Scale

| Label | Meaning |
| --- | --- |
| high | Direct evidence supports the finding with no material unresolved blocker. |
| medium | Evidence supports a plausible issue, but material runtime or reachability proof remains. |
| low | Evidence is incomplete and the item is retained only for explicit follow-up. |

<a id="finding-1"></a>

### [1] Non-unique device-token index accepts deterministic cross-device bearer-token collisions

| Field | Value |
| --- | --- |
| Severity | critical |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/alembic_devices/versions/e7b9c4d1a2f3_add_device_runtime_columns.py:21-29, cors_dashboard/backend/app/license_manager.py:4-49, cors_dashboard/backend/app/routers/devices.py:652-682, cors_dashboard/backend/app/main.py:7948-7989 |

#### Summary

See the [detailed technical write-up](findings/migdev-b-001/migdev-b-001.md).

#### Validation

See the [detailed technical write-up](findings/migdev-b-001/migdev-b-001.md).

#### Dataflow

See the [detailed technical write-up](findings/migdev-b-001/migdev-b-001.md).

#### Reachability

See the [detailed technical write-up](findings/migdev-b-001/migdev-b-001.md).

#### Severity

See the [detailed technical write-up](findings/migdev-b-001/migdev-b-001.md).

#### Remediation

See the [detailed technical write-up](findings/migdev-b-001/migdev-b-001.md).

<a id="finding-2"></a>

### [2] CRLF injection in Unicore survey duration reaches raw receiver command execution

| Field | Value |
| --- | --- |
| Severity | critical |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/command_builder.py:273-282 |

#### Summary

See the [detailed technical write-up](findings/cp-cmd-001/cp-cmd-001.md).

#### Validation

See the [detailed technical write-up](findings/cp-cmd-001/cp-cmd-001.md).

#### Dataflow

See the [detailed technical write-up](findings/cp-cmd-001/cp-cmd-001.md).

#### Reachability

See the [detailed technical write-up](findings/cp-cmd-001/cp-cmd-001.md).

#### Severity

See the [detailed technical write-up](findings/cp-cmd-001/cp-cmd-001.md).

#### Remediation

See the [detailed technical write-up](findings/cp-cmd-001/cp-cmd-001.md).

<a id="finding-3"></a>

### [3] Device WebSocket authentication token is deterministically derivable from public serial

| Field | Value |
| --- | --- |
| Severity | critical |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/routers/devices.py:653-668, cors_dashboard/backend/app/license_manager.py:4-49, cors_dashboard/backend/app/main.py:4650-4657, cors_dashboard/backend/app/main.py:7948-7989, geodetic/agent_universal.py:733-768, geodetic/agent_universal.py:1075-1087, geodetic/agent_universal.py:5274-5296, cors_dashboard/backend/app/license_manager.py:4-28, cors_dashboard/backend/app/license_manager.py:32-49, cors_dashboard/backend/app/routers/devices.py:652-668, cors_dashboard/backend/app/main.py:7948-7989 |

#### Summary

See the [detailed technical write-up](findings/auth-predictable-device-token/auth-predictable-device-token.md).

#### Validation

See the [detailed technical write-up](findings/auth-predictable-device-token/auth-predictable-device-token.md).

#### Dataflow

See the [detailed technical write-up](findings/auth-predictable-device-token/auth-predictable-device-token.md).

#### Reachability

See the [detailed technical write-up](findings/auth-predictable-device-token/auth-predictable-device-token.md).

#### Severity

See the [detailed technical write-up](findings/auth-predictable-device-token/auth-predictable-device-token.md).

#### Remediation

See the [detailed technical write-up](findings/auth-predictable-device-token/auth-predictable-device-token.md).

<a id="finding-4"></a>

### [4] Hard-coded shared MQTT credentials can expose the fleet control plane

| Field | Value |
| --- | --- |
| Severity | critical |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | geodetic/agent_universal.py:317-328, geodetic/agent_universal.py:536-5268, geodetic/geotek_daithanh_aitogy.py:88-360, geodetic/geotek_daithanh_aitogy.py:3233-3250 |

#### Summary

See the [detailed technical write-up](findings/geo-001/geo-001.md).

#### Validation

See the [detailed technical write-up](findings/geo-001/geo-001.md).

#### Dataflow

See the [detailed technical write-up](findings/geo-001/geo-001.md).

#### Reachability

See the [detailed technical write-up](findings/geo-001/geo-001.md).

#### Severity

See the [detailed technical write-up](findings/geo-001/geo-001.md).

#### Remediation

See the [detailed technical write-up](findings/geo-001/geo-001.md).

<a id="finding-5"></a>

### [5] Trusted forwarding headers allow unauthenticated stored XSS in admin IP-security widgets

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | medium |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/security.js:365-405, cors_dashboard/frontend/js/security.js:409-429, cors_dashboard/backend/app/security.py:429-433, cors_dashboard/backend/app/security.py:180-558 |

#### Summary

See the [detailed technical write-up](findings/front-e-002/front-e-002.md).

#### Validation

See the [detailed technical write-up](findings/front-e-002/front-e-002.md).

#### Dataflow

See the [detailed technical write-up](findings/front-e-002/front-e-002.md).

#### Reachability

See the [detailed technical write-up](findings/front-e-002/front-e-002.md).

#### Severity

See the [detailed technical write-up](findings/front-e-002/front-e-002.md).

#### Remediation

See the [detailed technical write-up](findings/front-e-002/front-e-002.md).

<a id="finding-6"></a>

### [6] Coordinator-supplied block reasons create persistent XSS in the admin caster blocklist tab

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/rovers-global.js:2147-2220, cors_dashboard/backend/app/schemas.py:392-400, cors_dashboard/backend/app/main.py:5088-5140, cors_dashboard/backend/app/main.py:5171-5187 |

#### Summary

See the [detailed technical write-up](findings/front-e-006/front-e-006.md).

#### Validation

See the [detailed technical write-up](findings/front-e-006/front-e-006.md).

#### Dataflow

See the [detailed technical write-up](findings/front-e-006/front-e-006.md).

#### Reachability

See the [detailed technical write-up](findings/front-e-006/front-e-006.md).

#### Severity

See the [detailed technical write-up](findings/front-e-006/front-e-006.md).

#### Remediation

See the [detailed technical write-up](findings/front-e-006/front-e-006.md).

<a id="finding-7"></a>

### [7] Coordinator can self-assign rover and mountpoint capabilities through generic user update

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/routers/users.py:239-269, cors_dashboard/backend/app/routers/users.py:263-333, cors_dashboard/backend/app/schemas.py:167-177, cors_dashboard/backend/app/crud.py:631-640 |

#### Summary

See the [detailed technical write-up](findings/auth-coordinator-self-capability-mass-assignment/auth-coordinator-self-capability-mass-assignment.md).

#### Validation

See the [detailed technical write-up](findings/auth-coordinator-self-capability-mass-assignment/auth-coordinator-self-capability-mass-assignment.md).

#### Dataflow

See the [detailed technical write-up](findings/auth-coordinator-self-capability-mass-assignment/auth-coordinator-self-capability-mass-assignment.md).

#### Reachability

See the [detailed technical write-up](findings/auth-coordinator-self-capability-mass-assignment/auth-coordinator-self-capability-mass-assignment.md).

#### Severity

See the [detailed technical write-up](findings/auth-coordinator-self-capability-mass-assignment/auth-coordinator-self-capability-mass-assignment.md).

#### Remediation

See the [detailed technical write-up](findings/auth-coordinator-self-capability-mass-assignment/auth-coordinator-self-capability-mass-assignment.md).

<a id="finding-8"></a>

### [8] Stored activity-log messages and serials execute HTML in the operations event table

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/operations.js:136-143, cors_dashboard/frontend/js/operations.js:91-109, cors_dashboard/backend/app/main.py:7152-7182, cors_dashboard/backend/app/main.py:7700-7727 |

#### Summary

See the [detailed technical write-up](findings/ops-event-table-stored-xss/ops-event-table-stored-xss.md).

#### Validation

See the [detailed technical write-up](findings/ops-event-table-stored-xss/ops-event-table-stored-xss.md).

#### Dataflow

See the [detailed technical write-up](findings/ops-event-table-stored-xss/ops-event-table-stored-xss.md).

#### Reachability

See the [detailed technical write-up](findings/ops-event-table-stored-xss/ops-event-table-stored-xss.md).

#### Severity

See the [detailed technical write-up](findings/ops-event-table-stored-xss/ops-event-table-stored-xss.md).

#### Remediation

See the [detailed technical write-up](findings/ops-event-table-stored-xss/ops-event-table-stored-xss.md).

<a id="finding-9"></a>

### [9] Persistent same-origin XSS in public station SEO page

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:3299-3382, cors_dashboard/backend/app/routers/users.py:84-102, cors_dashboard/backend/app/main.py:7948-8046 |

#### Summary

See the [detailed technical write-up](findings/main-002/main-002.md).

#### Validation

See the [detailed technical write-up](findings/main-002/main-002.md).

#### Dataflow

See the [detailed technical write-up](findings/main-002/main-002.md).

#### Reachability

See the [detailed technical write-up](findings/main-002/main-002.md).

#### Severity

See the [detailed technical write-up](findings/main-002/main-002.md).

#### Remediation

See the [detailed technical write-up](findings/main-002/main-002.md).

<a id="finding-10"></a>

### [10] Hard-coded NTRIP source and target credentials expose correction-stream access

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | medium |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | geodetic/geotek_daithanh_aitogy.py:95-360, geodetic/geotek_daithanh_aitogy.py:1622-1906, geodetic/geotek_daithanh_aitogy.py:2152-2162 |

#### Summary

See the [detailed technical write-up](findings/geo-002/geo-002.md).

#### Validation

See the [detailed technical write-up](findings/geo-002/geo-002.md).

#### Dataflow

See the [detailed technical write-up](findings/geo-002/geo-002.md).

#### Reachability

See the [detailed technical write-up](findings/geo-002/geo-002.md).

#### Severity

See the [detailed technical write-up](findings/geo-002/geo-002.md).

#### Remediation

See the [detailed technical write-up](findings/geo-002/geo-002.md).

<a id="finding-11"></a>

### [11] Public map endpoint discloses complete password-bearing station service configuration

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:4546-4667, cors_dashboard/backend/app/schemas.py:67-102, cors_dashboard/frontend/js/main.js:1660-1699, cors_dashboard/backend/app/main.py:4546-4550, cors_dashboard/backend/app/main.py:4646-4667, cors_dashboard/backend/app/schemas.py:76-102 |

#### Summary

See the [detailed technical write-up](findings/main-001/main-001.md).

#### Validation

See the [detailed technical write-up](findings/main-001/main-001.md).

#### Dataflow

See the [detailed technical write-up](findings/main-001/main-001.md).

#### Reachability

See the [detailed technical write-up](findings/main-001/main-001.md).

#### Severity

See the [detailed technical write-up](findings/main-001/main-001.md).

#### Remediation

See the [detailed technical write-up](findings/main-001/main-001.md).

<a id="finding-12"></a>

### [12] RTCM NTRIP start wrapper independently exposes the same cross-station activation BOLA

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:5397-5414 |

#### Summary

See the [detailed technical write-up](findings/main-012/main-012.md).

#### Validation

See the [detailed technical write-up](findings/main-012/main-012.md).

#### Dataflow

See the [detailed technical write-up](findings/main-012/main-012.md).

#### Reachability

See the [detailed technical write-up](findings/main-012/main-012.md).

#### Severity

See the [detailed technical write-up](findings/main-012/main-012.md).

#### Remediation

See the [detailed technical write-up](findings/main-012/main-012.md).

<a id="finding-13"></a>

### [13] Sync-by-username accepts arbitrary caster hosts, enabling authenticated SSRF

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:6037-6069, cors_dashboard/backend/app/caster_client.py:21-47, cors_dashboard/backend/app/main.py:5971-5987, cors_dashboard/backend/app/main.py:6042-6074, cors_dashboard/backend/app/caster_client.py:39-47, cors_dashboard/backend/app/caster_client.py:240-265 |

#### Summary

See the [detailed technical write-up](findings/main-019/main-019.md).

#### Validation

See the [detailed technical write-up](findings/main-019/main-019.md).

#### Dataflow

See the [detailed technical write-up](findings/main-019/main-019.md).

#### Reachability

See the [detailed technical write-up](findings/main-019/main-019.md).

#### Severity

See the [detailed technical write-up](findings/main-019/main-019.md).

#### Remediation

See the [detailed technical write-up](findings/main-019/main-019.md).

<a id="finding-14"></a>

### [14] Unauthenticated device-name staging reaches a Leaflet nearest-station tooltip HTML sink

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | medium |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/stations-map-search.js:216-229, cors_dashboard/backend/app/crud.py:126-227, cors_dashboard/backend/app/main.py:7948-8058 |

#### Summary

See the [detailed technical write-up](findings/front-e-010/front-e-010.md).

#### Validation

See the [detailed technical write-up](findings/front-e-010/front-e-010.md).

#### Dataflow

See the [detailed technical write-up](findings/front-e-010/front-e-010.md).

#### Reachability

See the [detailed technical write-up](findings/front-e-010/front-e-010.md).

#### Severity

See the [detailed technical write-up](findings/front-e-010/front-e-010.md).

#### Remediation

See the [detailed technical write-up](findings/front-e-010/front-e-010.md).

<a id="finding-15"></a>

### [15] Every viewer can download global device templates containing service credentials

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/operations.js:218-227, cors_dashboard/backend/app/main.py:7688-7697, cors_dashboard/backend/app/main.py:7711-7721, cors_dashboard/backend/app/schemas.py:76-102 |

#### Summary

See the [detailed technical write-up](findings/device-config-templates-disclose-global-service-secrets/device-config-templates-disclose-global-service-secrets.md).

#### Validation

See the [detailed technical write-up](findings/device-config-templates-disclose-global-service-secrets/device-config-templates-disclose-global-service-secrets.md).

#### Dataflow

See the [detailed technical write-up](findings/device-config-templates-disclose-global-service-secrets/device-config-templates-disclose-global-service-secrets.md).

#### Reachability

See the [detailed technical write-up](findings/device-config-templates-disclose-global-service-secrets/device-config-templates-disclose-global-service-secrets.md).

#### Severity

See the [detailed technical write-up](findings/device-config-templates-disclose-global-service-secrets/device-config-templates-disclose-global-service-secrets.md).

#### Remediation

See the [detailed technical write-up](findings/device-config-templates-disclose-global-service-secrets/device-config-templates-disclose-global-service-secrets.md).

<a id="finding-16"></a>

### [16] View-only station access authorizes updates to shared rover accounts

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/station_access.py:19-37, cors_dashboard/backend/app/crud.py:756-768, cors_dashboard/backend/app/crud.py:818-836, cors_dashboard/backend/app/routers/devices.py:441-601 |

#### Summary

See the [detailed technical write-up](findings/auth-view-only-shared-rover-update/auth-view-only-shared-rover-update.md).

#### Validation

See the [detailed technical write-up](findings/auth-view-only-shared-rover-update/auth-view-only-shared-rover-update.md).

#### Dataflow

See the [detailed technical write-up](findings/auth-view-only-shared-rover-update/auth-view-only-shared-rover-update.md).

#### Reachability

See the [detailed technical write-up](findings/auth-view-only-shared-rover-update/auth-view-only-shared-rover-update.md).

#### Severity

See the [detailed technical write-up](findings/auth-view-only-shared-rover-update/auth-view-only-shared-rover-update.md).

#### Remediation

See the [detailed technical write-up](findings/auth-view-only-shared-rover-update/auth-view-only-shared-rover-update.md).

<a id="finding-17"></a>

### [17] Status-topic publishers can invoke privileged Geotek commands

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | geodetic/geotek_daithanh_aitogy.py:3243-3250, geodetic/geotek_daithanh_aitogy.py:3261-3277, geodetic/geotek_daithanh_aitogy.py:3279-3357 |

#### Summary

See the [detailed technical write-up](findings/geo-010/geo-010.md).

#### Validation

See the [detailed technical write-up](findings/geo-010/geo-010.md).

#### Dataflow

See the [detailed technical write-up](findings/geo-010/geo-010.md).

#### Reachability

See the [detailed technical write-up](findings/geo-010/geo-010.md).

#### Severity

See the [detailed technical write-up](findings/geo-010/geo-010.md).

#### Remediation

See the [detailed technical write-up](findings/geo-010/geo-010.md).

<a id="finding-18"></a>

### [18] Stored station names execute HTML in the authenticated all-stations map popup

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/main.js:905-923, cors_dashboard/frontend/js/react-components.js:813-836, cors_dashboard/frontend/js/react-components.js:838-845, cors_dashboard/backend/app/routers/users.py:84-108 |

#### Summary

See the [detailed technical write-up](findings/station-name-xss-authenticated-leaflet-popup/station-name-xss-authenticated-leaflet-popup.md).

#### Validation

See the [detailed technical write-up](findings/station-name-xss-authenticated-leaflet-popup/station-name-xss-authenticated-leaflet-popup.md).

#### Dataflow

See the [detailed technical write-up](findings/station-name-xss-authenticated-leaflet-popup/station-name-xss-authenticated-leaflet-popup.md).

#### Reachability

See the [detailed technical write-up](findings/station-name-xss-authenticated-leaflet-popup/station-name-xss-authenticated-leaflet-popup.md).

#### Severity

See the [detailed technical write-up](findings/station-name-xss-authenticated-leaflet-popup/station-name-xss-authenticated-leaflet-popup.md).

#### Remediation

See the [detailed technical write-up](findings/station-name-xss-authenticated-leaflet-popup/station-name-xss-authenticated-leaflet-popup.md).

<a id="finding-19"></a>

### [19] Malicious caster sourcetable fields execute as HTML in the admin mountpoint view

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | medium |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/rovers-global.js:2256-2385, cors_dashboard/frontend/js/rovers-global.js:367-404, cors_dashboard/backend/app/caster_client.py:403-485, cors_dashboard/backend/app/main.py:4950-5005 |

#### Summary

See the [detailed technical write-up](findings/front-e-007/front-e-007.md).

#### Validation

See the [detailed technical write-up](findings/front-e-007/front-e-007.md).

#### Dataflow

See the [detailed technical write-up](findings/front-e-007/front-e-007.md).

#### Reachability

See the [detailed technical write-up](findings/front-e-007/front-e-007.md).

#### Severity

See the [detailed technical write-up](findings/front-e-007/front-e-007.md).

#### Remediation

See the [detailed technical write-up](findings/front-e-007/front-e-007.md).

<a id="finding-20"></a>

### [20] New caster sources default to the public password 12345

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/schemas.py:114-129 |

#### Summary

See the [detailed technical write-up](findings/data-006/data-006.md).

#### Validation

See the [detailed technical write-up](findings/data-006/data-006.md).

#### Dataflow

See the [detailed technical write-up](findings/data-006/data-006.md).

#### Reachability

See the [detailed technical write-up](findings/data-006/data-006.md).

#### Severity

See the [detailed technical write-up](findings/data-006/data-006.md).

#### Remediation

See the [detailed technical write-up](findings/data-006/data-006.md).

<a id="finding-21"></a>

### [21] Create-multi accepts arbitrary target caster hosts, enabling authenticated SSRF

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:6180-6204, cors_dashboard/backend/app/caster_client.py:21-47, cors_dashboard/backend/app/main.py:6113-6123, cors_dashboard/backend/app/main.py:6180-6208, cors_dashboard/backend/app/caster_client.py:39-47 |

#### Summary

See the [detailed technical write-up](findings/main-021/main-021.md).

#### Validation

See the [detailed technical write-up](findings/main-021/main-021.md).

#### Dataflow

See the [detailed technical write-up](findings/main-021/main-021.md).

#### Reachability

See the [detailed technical write-up](findings/main-021/main-021.md).

#### Severity

See the [detailed technical write-up](findings/main-021/main-021.md).

#### Remediation

See the [detailed technical write-up](findings/main-021/main-021.md).

<a id="finding-22"></a>

### [22] Rover update permits SSRF and credential exfiltration through arbitrary caster hosts

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/routers/devices.py:501-504, cors_dashboard/backend/app/routers/devices.py:540-550, cors_dashboard/backend/app/caster_client.py:39-47, cors_dashboard/backend/app/caster_client.py:240-267, cors_dashboard/backend/app/routers/devices.py:501-504, cors_dashboard/backend/app/routers/devices.py:540-550, cors_dashboard/backend/app/caster_client.py:39-47, cors_dashboard/backend/app/schemas.py:262-273, cors_dashboard/backend/app/crud.py:719-731 |

#### Summary

See the [detailed technical write-up](findings/auth-rover-caster-host-ssrf/auth-rover-caster-host-ssrf.md).

#### Validation

See the [detailed technical write-up](findings/auth-rover-caster-host-ssrf/auth-rover-caster-host-ssrf.md).

#### Dataflow

See the [detailed technical write-up](findings/auth-rover-caster-host-ssrf/auth-rover-caster-host-ssrf.md).

#### Reachability

See the [detailed technical write-up](findings/auth-rover-caster-host-ssrf/auth-rover-caster-host-ssrf.md).

#### Severity

See the [detailed technical write-up](findings/auth-rover-caster-host-ssrf/auth-rover-caster-host-ssrf.md).

#### Remediation

See the [detailed technical write-up](findings/auth-rover-caster-host-ssrf/auth-rover-caster-host-ssrf.md).

<a id="finding-23"></a>

### [23] Coordinator-controlled rover fields produce persistent XSS in the global rover table

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/rovers-global.js:891-1162, cors_dashboard/backend/app/schemas.py:217-273, cors_dashboard/backend/app/routers/devices.py:440-613, cors_dashboard/backend/app/main.py:5928-5970 |

#### Summary

See the [detailed technical write-up](findings/front-e-003/front-e-003.md).

#### Validation

See the [detailed technical write-up](findings/front-e-003/front-e-003.md).

#### Dataflow

See the [detailed technical write-up](findings/front-e-003/front-e-003.md).

#### Reachability

See the [detailed technical write-up](findings/front-e-003/front-e-003.md).

#### Severity

See the [detailed technical write-up](findings/front-e-003/front-e-003.md).

#### Remediation

See the [detailed technical write-up](findings/front-e-003/front-e-003.md).

<a id="finding-24"></a>

### [24] Unauthenticated User-Agent values become stored script in the admin VPN-intelligence table

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/security.js:250-272, cors_dashboard/backend/app/security.py:82-341, cors_dashboard/backend/app/security.py:445-559, cors_dashboard/backend/app/main.py:4140-5792 |

#### Summary

See the [detailed technical write-up](findings/front-e-001/front-e-001.md).

#### Validation

See the [detailed technical write-up](findings/front-e-001/front-e-001.md).

#### Dataflow

See the [detailed technical write-up](findings/front-e-001/front-e-001.md).

#### Reachability

See the [detailed technical write-up](findings/front-e-001/front-e-001.md).

#### Severity

See the [detailed technical write-up](findings/front-e-001/front-e-001.md).

#### Remediation

See the [detailed technical write-up](findings/front-e-001/front-e-001.md).

<a id="finding-25"></a>

### [25] Any one-station overlap grants whole shared-rover credentials and mutation authority

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/crud.py:777-816, cors_dashboard/backend/app/crud.py:818-838, cors_dashboard/backend/app/schemas.py:217-246, cors_dashboard/backend/app/models.py:36-54 |

#### Summary

See the [detailed technical write-up](findings/data-002/data-002.md).

#### Validation

See the [detailed technical write-up](findings/data-002/data-002.md).

#### Dataflow

See the [detailed technical write-up](findings/data-002/data-002.md).

#### Reachability

See the [detailed technical write-up](findings/data-002/data-002.md).

#### Severity

See the [detailed technical write-up](findings/data-002/data-002.md).

#### Remediation

See the [detailed technical write-up](findings/data-002/data-002.md).

<a id="finding-26"></a>

### [26] Editable station names and serials execute in user-assignment checklists

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/users.js:154-159, cors_dashboard/frontend/js/users.js:205-233 |

#### Summary

See the [detailed technical write-up](findings/fef-004/fef-004.md).

#### Validation

See the [detailed technical write-up](findings/fef-004/fef-004.md).

#### Dataflow

See the [detailed technical write-up](findings/fef-004/fef-004.md).

#### Reachability

See the [detailed technical write-up](findings/fef-004/fef-004.md).

#### Severity

See the [detailed technical write-up](findings/fef-004/fef-004.md).

#### Remediation

See the [detailed technical write-up](findings/fef-004/fef-004.md).

<a id="finding-27"></a>

### [27] Coordinator-created viewer fields execute as HTML in the user table

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/users.js:101-139, cors_dashboard/frontend/js/users.js:395-508 |

#### Summary

See the [detailed technical write-up](findings/fef-003/fef-003.md).

#### Validation

See the [detailed technical write-up](findings/fef-003/fef-003.md).

#### Dataflow

See the [detailed technical write-up](findings/fef-003/fef-003.md).

#### Reachability

See the [detailed technical write-up](findings/fef-003/fef-003.md).

#### Severity

See the [detailed technical write-up](findings/fef-003/fef-003.md).

#### Remediation

See the [detailed technical write-up](findings/fef-003/fef-003.md).

<a id="finding-28"></a>

### [28] Stored station names execute HTML in the public map popup

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:4649-4667, cors_dashboard/frontend/js/main.js:2130-2144, cors_dashboard/frontend/js/main.js:2153-2159, cors_dashboard/backend/app/routers/users.py:84-108 |

#### Summary

See the [detailed technical write-up](findings/station-name-xss-public-map-popup/station-name-xss-public-map-popup.md).

#### Validation

See the [detailed technical write-up](findings/station-name-xss-public-map-popup/station-name-xss-public-map-popup.md).

#### Dataflow

See the [detailed technical write-up](findings/station-name-xss-public-map-popup/station-name-xss-public-map-popup.md).

#### Reachability

See the [detailed technical write-up](findings/station-name-xss-public-map-popup/station-name-xss-public-map-popup.md).

#### Severity

See the [detailed technical write-up](findings/station-name-xss-public-map-popup/station-name-xss-public-map-popup.md).

#### Remediation

See the [detailed technical write-up](findings/station-name-xss-public-map-popup/station-name-xss-public-map-popup.md).

<a id="finding-29"></a>

### [29] Device-controlled sync mismatch text reaches an HTML toast sink

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/dashboard-detail.js:700-706, cors_dashboard/frontend/js/dashboard-detail.js:964-973 |

#### Summary

See the [detailed technical write-up](findings/feb-002/feb-002.md).

#### Validation

See the [detailed technical write-up](findings/feb-002/feb-002.md).

#### Dataflow

See the [detailed technical write-up](findings/feb-002/feb-002.md).

#### Reachability

See the [detailed technical write-up](findings/feb-002/feb-002.md).

#### Severity

See the [detailed technical write-up](findings/feb-002/feb-002.md).

#### Remediation

See the [detailed technical write-up](findings/feb-002/feb-002.md).

<a id="finding-30"></a>

### [30] Nearest-station result interpolates station and geocoder data into an unsafe HTML toast

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/react-components.js:1095-1100, cors_dashboard/frontend/js/react-components.js:1193-1207, cors_dashboard/frontend/js/main.js:1609-1615, cors_dashboard/frontend/js/ops-ui.js:55-70 |

#### Summary

See the [detailed technical write-up](findings/nearest-result-toast-html-injection/nearest-result-toast-html-injection.md).

#### Validation

See the [detailed technical write-up](findings/nearest-result-toast-html-injection/nearest-result-toast-html-injection.md).

#### Dataflow

See the [detailed technical write-up](findings/nearest-result-toast-html-injection/nearest-result-toast-html-injection.md).

#### Reachability

See the [detailed technical write-up](findings/nearest-result-toast-html-injection/nearest-result-toast-html-injection.md).

#### Severity

See the [detailed technical write-up](findings/nearest-result-toast-html-injection/nearest-result-toast-html-injection.md).

#### Remediation

See the [detailed technical write-up](findings/nearest-result-toast-html-injection/nearest-result-toast-html-injection.md).

<a id="finding-31"></a>

### [31] Coordinator delegated only user creation can overwrite global system settings

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:5759-5770, cors_dashboard/backend/app/auth.py:191-196 |

#### Summary

See the [detailed technical write-up](findings/main-025/main-025.md).

#### Validation

See the [detailed technical write-up](findings/main-025/main-025.md).

#### Dataflow

See the [detailed technical write-up](findings/main-025/main-025.md).

#### Reachability

See the [detailed technical write-up](findings/main-025/main-025.md).

#### Severity

See the [detailed technical write-up](findings/main-025/main-025.md).

#### Remediation

See the [detailed technical write-up](findings/main-025/main-025.md).

<a id="finding-32"></a>

### [32] Rover update accepts mountpoints outside the coordinator's station scope

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/routers/devices.py:441-463, cors_dashboard/backend/app/routers/devices.py:540-550, cors_dashboard/backend/app/routers/devices.py:587-601, cors_dashboard/backend/app/schemas.py:262-273, cors_dashboard/backend/app/crud.py:818-838, cors_dashboard/backend/app/crud.py:719-731 |

#### Summary

See the [detailed technical write-up](findings/auth-rover-mountpoint-scope-bypass/auth-rover-mountpoint-scope-bypass.md).

#### Validation

See the [detailed technical write-up](findings/auth-rover-mountpoint-scope-bypass/auth-rover-mountpoint-scope-bypass.md).

#### Dataflow

See the [detailed technical write-up](findings/auth-rover-mountpoint-scope-bypass/auth-rover-mountpoint-scope-bypass.md).

#### Reachability

See the [detailed technical write-up](findings/auth-rover-mountpoint-scope-bypass/auth-rover-mountpoint-scope-bypass.md).

#### Severity

See the [detailed technical write-up](findings/auth-rover-mountpoint-scope-bypass/auth-rover-mountpoint-scope-bypass.md).

#### Remediation

See the [detailed technical write-up](findings/auth-rover-mountpoint-scope-bypass/auth-rover-mountpoint-scope-bypass.md).

<a id="finding-33"></a>

### [33] Stored station names execute HTML in permanent nearest-station tooltips

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/main.js:905-923, cors_dashboard/frontend/js/react-components.js:1043-1088, cors_dashboard/frontend/js/react-components.js:1122-1144 |

#### Summary

See the [detailed technical write-up](findings/station-name-xss-nearest-station-tooltip/station-name-xss-nearest-station-tooltip.md).

#### Validation

See the [detailed technical write-up](findings/station-name-xss-nearest-station-tooltip/station-name-xss-nearest-station-tooltip.md).

#### Dataflow

See the [detailed technical write-up](findings/station-name-xss-nearest-station-tooltip/station-name-xss-nearest-station-tooltip.md).

#### Reachability

See the [detailed technical write-up](findings/station-name-xss-nearest-station-tooltip/station-name-xss-nearest-station-tooltip.md).

#### Severity

See the [detailed technical write-up](findings/station-name-xss-nearest-station-tooltip/station-name-xss-nearest-station-tooltip.md).

#### Remediation

See the [detailed technical write-up](findings/station-name-xss-nearest-station-tooltip/station-name-xss-nearest-station-tooltip.md).

<a id="finding-34"></a>

### [34] Caster table queries use publicly known fallback credentials

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | medium |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/caster_client.py:22-30, cors_dashboard/backend/app/caster_client.py:210-217, cors_dashboard/backend/app/caster_helpers.py:41-47 |

#### Summary

See the [detailed technical write-up](findings/caster-hardcoded-table-credentials/caster-hardcoded-table-credentials.md).

#### Validation

See the [detailed technical write-up](findings/caster-hardcoded-table-credentials/caster-hardcoded-table-credentials.md).

#### Dataflow

See the [detailed technical write-up](findings/caster-hardcoded-table-credentials/caster-hardcoded-table-credentials.md).

#### Reachability

See the [detailed technical write-up](findings/caster-hardcoded-table-credentials/caster-hardcoded-table-credentials.md).

#### Severity

See the [detailed technical write-up](findings/caster-hardcoded-table-credentials/caster-hardcoded-table-credentials.md).

#### Remediation

See the [detailed technical write-up](findings/caster-hardcoded-table-credentials/caster-hardcoded-table-credentials.md).

<a id="finding-35"></a>

### [35] MQTT status payload serial is not bound to the authenticated topic serial

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/mqtt.py:671-678, cors_dashboard/backend/app/mqtt.py:757-812, cors_dashboard/backend/app/mqtt.py:923-979, cors_dashboard/backend/app/status_reconciler.py:71-97, cors_dashboard/backend/app/mqtt.py:667-812, cors_dashboard/backend/app/mqtt.py:885-979 |

#### Summary

See the [detailed technical write-up](findings/cp-mqtt-001/cp-mqtt-001.md).

#### Validation

See the [detailed technical write-up](findings/cp-mqtt-001/cp-mqtt-001.md).

#### Dataflow

See the [detailed technical write-up](findings/cp-mqtt-001/cp-mqtt-001.md).

#### Reachability

See the [detailed technical write-up](findings/cp-mqtt-001/cp-mqtt-001.md).

#### Severity

See the [detailed technical write-up](findings/cp-mqtt-001/cp-mqtt-001.md).

#### Remediation

See the [detailed technical write-up](findings/cp-mqtt-001/cp-mqtt-001.md).

<a id="finding-36"></a>

### [36] Security settings can be clickjacked because the privileged page is frameable

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | medium |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/security.html:100-123, cors_dashboard/frontend/security.html:1-10, cors_dashboard/frontend/js/security.js:438-467, cors_dashboard/backend/app/main.py:5759-5769, cors_dashboard/backend/app/main.py:1714-1725 |

#### Summary

See the [detailed technical write-up](findings/security-page-clickjacking-security-switches/security-page-clickjacking-security-switches.md).

#### Validation

See the [detailed technical write-up](findings/security-page-clickjacking-security-switches/security-page-clickjacking-security-switches.md).

#### Dataflow

See the [detailed technical write-up](findings/security-page-clickjacking-security-switches/security-page-clickjacking-security-switches.md).

#### Reachability

See the [detailed technical write-up](findings/security-page-clickjacking-security-switches/security-page-clickjacking-security-switches.md).

#### Severity

See the [detailed technical write-up](findings/security-page-clickjacking-security-switches/security-page-clickjacking-security-switches.md).

#### Remediation

See the [detailed technical write-up](findings/security-page-clickjacking-security-switches/security-page-clickjacking-security-switches.md).

<a id="finding-37"></a>

### [37] Device WebSocket authentication fails open for unknown or tokenless serials and reaches a device UPSERT

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:7948-7989, cors_dashboard/backend/app/main.py:7991-8058, cors_dashboard/backend/app/crud.py:126-234 |

#### Summary

See the [detailed technical write-up](findings/main-003/main-003.md).

#### Validation

See the [detailed technical write-up](findings/main-003/main-003.md).

#### Dataflow

See the [detailed technical write-up](findings/main-003/main-003.md).

#### Reachability

See the [detailed technical write-up](findings/main-003/main-003.md).

#### Severity

See the [detailed technical write-up](findings/main-003/main-003.md).

#### Remediation

See the [detailed technical write-up](findings/main-003/main-003.md).

<a id="finding-38"></a>

### [38] Coordinator can block an arbitrary caster account absent from the local rover database

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:5088-5167, cors_dashboard/backend/app/auth.py:137-141 |

#### Summary

See the [detailed technical write-up](findings/main-016/main-016.md).

#### Validation

See the [detailed technical write-up](findings/main-016/main-016.md).

#### Dataflow

See the [detailed technical write-up](findings/main-016/main-016.md).

#### Reachability

See the [detailed technical write-up](findings/main-016/main-016.md).

#### Severity

See the [detailed technical write-up](findings/main-016/main-016.md).

#### Remediation

See the [detailed technical write-up](findings/main-016/main-016.md).

<a id="finding-39"></a>

### [39] Replayed EXECUTE commands spawn unbounded concurrent receiver-reset workers

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | geodetic/geotek_daithanh_aitogy.py:1554-1585, geodetic/geotek_daithanh_aitogy.py:2575-2837, geodetic/geotek_daithanh_aitogy.py:3279-3297 |

#### Summary

See the [detailed technical write-up](findings/geo-011/geo-011.md).

#### Validation

See the [detailed technical write-up](findings/geo-011/geo-011.md).

#### Dataflow

See the [detailed technical write-up](findings/geo-011/geo-011.md).

#### Reachability

See the [detailed technical write-up](findings/geo-011/geo-011.md).

#### Severity

See the [detailed technical write-up](findings/geo-011/geo-011.md).

#### Remediation

See the [detailed technical write-up](findings/geo-011/geo-011.md).

<a id="finding-40"></a>

### [40] Device, caster, and rover alert fields execute HTML in the operations alert list

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/operations.js:122-127, cors_dashboard/frontend/js/operations.js:49-89, cors_dashboard/backend/app/main.py:6994-7078, cors_dashboard/backend/app/main.py:7314-7328 |

#### Summary

See the [detailed technical write-up](findings/ops-alert-list-stored-xss/ops-alert-list-stored-xss.md).

#### Validation

See the [detailed technical write-up](findings/ops-alert-list-stored-xss/ops-alert-list-stored-xss.md).

#### Dataflow

See the [detailed technical write-up](findings/ops-alert-list-stored-xss/ops-alert-list-stored-xss.md).

#### Reachability

See the [detailed technical write-up](findings/ops-alert-list-stored-xss/ops-alert-list-stored-xss.md).

#### Severity

See the [detailed technical write-up](findings/ops-alert-list-stored-xss/ops-alert-list-stored-xss.md).

#### Remediation

See the [detailed technical write-up](findings/ops-alert-list-stored-xss/ops-alert-list-stored-xss.md).

<a id="finding-41"></a>

### [41] MQTT commands lack freshness and replay protection before destructive actions

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | geodetic/agent_universal.py:4768-4787, geodetic/agent_universal.py:4840-5138, geodetic/agent_universal.py:5221-5241 |

#### Summary

See the [detailed technical write-up](findings/geo-006/geo-006.md).

#### Validation

See the [detailed technical write-up](findings/geo-006/geo-006.md).

#### Dataflow

See the [detailed technical write-up](findings/geo-006/geo-006.md).

#### Reachability

See the [detailed technical write-up](findings/geo-006/geo-006.md).

#### Severity

See the [detailed technical write-up](findings/geo-006/geo-006.md).

#### Remediation

See the [detailed technical write-up](findings/geo-006/geo-006.md).

<a id="finding-42"></a>

### [42] CRLF injection in Unicore fixed coordinates reaches raw receiver command execution

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/command_builder.py:359-372 |

#### Summary

See the [detailed technical write-up](findings/cp-cmd-002/cp-cmd-002.md).

#### Validation

See the [detailed technical write-up](findings/cp-cmd-002/cp-cmd-002.md).

#### Dataflow

See the [detailed technical write-up](findings/cp-cmd-002/cp-cmd-002.md).

#### Reachability

See the [detailed technical write-up](findings/cp-cmd-002/cp-cmd-002.md).

#### Severity

See the [detailed technical write-up](findings/cp-cmd-002/cp-cmd-002.md).

#### Remediation

See the [detailed technical write-up](findings/cp-cmd-002/cp-cmd-002.md).

<a id="finding-43"></a>

### [43] rtcm_port configuration permits CRLF injection of additional receiver commands

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | geodetic/geotek_daithanh_aitogy.py:1554-1583, geodetic/geotek_daithanh_aitogy.py:2674-2752, geodetic/geotek_daithanh_aitogy.py:2780-2820 |

#### Summary

See the [detailed technical write-up](findings/geo-012/geo-012.md).

#### Validation

See the [detailed technical write-up](findings/geo-012/geo-012.md).

#### Dataflow

See the [detailed technical write-up](findings/geo-012/geo-012.md).

#### Reachability

See the [detailed technical write-up](findings/geo-012/geo-012.md).

#### Severity

See the [detailed technical write-up](findings/geo-012/geo-012.md).

#### Remediation

See the [detailed technical write-up](findings/geo-012/geo-012.md).

<a id="finding-44"></a>

### [44] Station records are interpolated into Leaflet popup HTML without encoding

| Field | Value |
| --- | --- |
| Severity | high |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/stations-map.js:57-94, cors_dashboard/frontend/js/stations-map.js:296-337 |

#### Summary

See the [detailed technical write-up](findings/fef-001/fef-001.md).

#### Validation

See the [detailed technical write-up](findings/fef-001/fef-001.md).

#### Dataflow

See the [detailed technical write-up](findings/fef-001/fef-001.md).

#### Reachability

See the [detailed technical write-up](findings/fef-001/fef-001.md).

#### Severity

See the [detailed technical write-up](findings/fef-001/fef-001.md).

#### Remediation

See the [detailed technical write-up](findings/fef-001/fef-001.md).

<a id="finding-45"></a>

### [45] Unvalidated GLONASS ephemeris can poison or heavily amplify satellite-position decoding

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | medium |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/rtcm_signal_decoder.py:347-384, cors_dashboard/backend/app/rtcm_signal_decoder.py:106-712, cors_dashboard/backend/app/rtcm_signal_decoder.py:593-616 |

#### Summary

See the [detailed technical write-up](findings/proto-011/proto-011.md).

#### Validation

See the [detailed technical write-up](findings/proto-011/proto-011.md).

#### Dataflow

See the [detailed technical write-up](findings/proto-011/proto-011.md).

#### Reachability

See the [detailed technical write-up](findings/proto-011/proto-011.md).

#### Severity

See the [detailed technical write-up](findings/proto-011/proto-011.md).

#### Remediation

See the [detailed technical write-up](findings/proto-011/proto-011.md).

<a id="finding-46"></a>

### [46] Compromised device telemetry can inject script through satellite PRN signal bars

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/satellite-sky-view.js:291-367, cors_dashboard/frontend/js/satellite-sky-view.js:50-68, cors_dashboard/backend/app/main.py:8079-8130 |

#### Summary

See the [detailed technical write-up](findings/front-e-008/front-e-008.md).

#### Validation

See the [detailed technical write-up](findings/front-e-008/front-e-008.md).

#### Dataflow

See the [detailed technical write-up](findings/front-e-008/front-e-008.md).

#### Reachability

See the [detailed technical write-up](findings/front-e-008/front-e-008.md).

#### Severity

See the [detailed technical write-up](findings/front-e-008/front-e-008.md).

#### Remediation

See the [detailed technical write-up](findings/front-e-008/front-e-008.md).

<a id="finding-47"></a>

### [47] One global NMEA parser mixes satellite state across independently authenticated stations

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/utils.py:7-221, cors_dashboard/backend/app/__init__.py:1-5, cors_dashboard/backend/app/mqtt.py:708-731, cors_dashboard/backend/app/main.py:8060-8078 |

#### Summary

See the [detailed technical write-up](findings/proto-001/proto-001.md).

#### Validation

See the [detailed technical write-up](findings/proto-001/proto-001.md).

#### Dataflow

See the [detailed technical write-up](findings/proto-001/proto-001.md).

#### Reachability

See the [detailed technical write-up](findings/proto-001/proto-001.md).

#### Severity

See the [detailed technical write-up](findings/proto-001/proto-001.md).

#### Remediation

See the [detailed technical write-up](findings/proto-001/proto-001.md).

<a id="finding-48"></a>

### [48] CRC-valid short RTCM frames cause large result-object amplification

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/rtcm_signal_decoder.py:562-630, cors_dashboard/backend/app/rtcm_signal_decoder.py:631-645, cors_dashboard/backend/app/mqtt.py:322-754 |

#### Summary

See the [detailed technical write-up](findings/proto-004/proto-004.md).

#### Validation

See the [detailed technical write-up](findings/proto-004/proto-004.md).

#### Dataflow

See the [detailed technical write-up](findings/proto-004/proto-004.md).

#### Reachability

See the [detailed technical write-up](findings/proto-004/proto-004.md).

#### Severity

See the [detailed technical write-up](findings/proto-004/proto-004.md).

#### Remediation

See the [detailed technical write-up](findings/proto-004/proto-004.md).

<a id="finding-49"></a>

### [49] Space-form /configset credentials are retained in Zalo diagnostics and application logs

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/zalo_bot_service.py:320-350, cors_dashboard/backend/app/zalo_bot_service.py:669-722, cors_dashboard/backend/app/zalo_bot_service.py:1250-1289, cors_dashboard/backend/app/zalo_bot_service.py:2271-2297 |

#### Summary

See the [detailed technical write-up](findings/zalo-configset-space-password-log-disclosure/zalo-configset-space-password-log-disclosure.md).

#### Validation

See the [detailed technical write-up](findings/zalo-configset-space-password-log-disclosure/zalo-configset-space-password-log-disclosure.md).

#### Dataflow

See the [detailed technical write-up](findings/zalo-configset-space-password-log-disclosure/zalo-configset-space-password-log-disclosure.md).

#### Reachability

See the [detailed technical write-up](findings/zalo-configset-space-password-log-disclosure/zalo-configset-space-password-log-disclosure.md).

#### Severity

See the [detailed technical write-up](findings/zalo-configset-space-password-log-disclosure/zalo-configset-space-password-log-disclosure.md).

#### Remediation

See the [detailed technical write-up](findings/zalo-configset-space-password-log-disclosure/zalo-configset-space-password-log-disclosure.md).

<a id="finding-50"></a>

### [50] Geocoder display names execute HTML in the searched-location popup

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | medium |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/react-components.js:1193-1207, cors_dashboard/frontend/js/react-components.js:1113-1120 |

#### Summary

See the [detailed technical write-up](findings/nominatim-display-name-xss-leaflet-popup/nominatim-display-name-xss-leaflet-popup.md).

#### Validation

See the [detailed technical write-up](findings/nominatim-display-name-xss-leaflet-popup/nominatim-display-name-xss-leaflet-popup.md).

#### Dataflow

See the [detailed technical write-up](findings/nominatim-display-name-xss-leaflet-popup/nominatim-display-name-xss-leaflet-popup.md).

#### Reachability

See the [detailed technical write-up](findings/nominatim-display-name-xss-leaflet-popup/nominatim-display-name-xss-leaflet-popup.md).

#### Severity

See the [detailed technical write-up](findings/nominatim-display-name-xss-leaflet-popup/nominatim-display-name-xss-leaflet-popup.md).

#### Remediation

See the [detailed technical write-up](findings/nominatim-display-name-xss-leaflet-popup/nominatim-display-name-xss-leaflet-popup.md).

<a id="finding-51"></a>

### [51] Active RTCM station listing ignores station scope and exposes all client IDs

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:5705-5737 |

#### Summary

See the [detailed technical write-up](findings/main-013/main-013.md).

#### Validation

See the [detailed technical write-up](findings/main-013/main-013.md).

#### Dataflow

See the [detailed technical write-up](findings/main-013/main-013.md).

#### Reachability

See the [detailed technical write-up](findings/main-013/main-013.md).

#### Severity

See the [detailed technical write-up](findings/main-013/main-013.md).

#### Remediation

See the [detailed technical write-up](findings/main-013/main-013.md).

<a id="finding-52"></a>

### [52] Space-form /roverupdate passwords are retained in Zalo diagnostics and application logs

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/zalo_bot_service.py:320-350, cors_dashboard/backend/app/zalo_bot_service.py:669-722, cors_dashboard/backend/app/zalo_bot_service.py:1250-1289, cors_dashboard/backend/app/zalo_bot_service.py:2328-2355 |

#### Summary

See the [detailed technical write-up](findings/zalo-roverupdate-space-password-log-disclosure/zalo-roverupdate-space-password-log-disclosure.md).

#### Validation

See the [detailed technical write-up](findings/zalo-roverupdate-space-password-log-disclosure/zalo-roverupdate-space-password-log-disclosure.md).

#### Dataflow

See the [detailed technical write-up](findings/zalo-roverupdate-space-password-log-disclosure/zalo-roverupdate-space-password-log-disclosure.md).

#### Reachability

See the [detailed technical write-up](findings/zalo-roverupdate-space-password-log-disclosure/zalo-roverupdate-space-password-log-disclosure.md).

#### Severity

See the [detailed technical write-up](findings/zalo-roverupdate-space-password-log-disclosure/zalo-roverupdate-space-password-log-disclosure.md).

#### Remediation

See the [detailed technical write-up](findings/zalo-roverupdate-space-password-log-disclosure/zalo-roverupdate-space-password-log-disclosure.md).

<a id="finding-53"></a>

### [53] Unauthenticated GET stop-listen with force=true deletes RTCM sessions and stops station streaming

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:3972-4060 |

#### Summary

See the [detailed technical write-up](findings/main-008/main-008.md).

#### Validation

See the [detailed technical write-up](findings/main-008/main-008.md).

#### Dataflow

See the [detailed technical write-up](findings/main-008/main-008.md).

#### Reachability

See the [detailed technical write-up](findings/main-008/main-008.md).

#### Severity

See the [detailed technical write-up](findings/main-008/main-008.md).

#### Remediation

See the [detailed technical write-up](findings/main-008/main-008.md).

<a id="finding-54"></a>

### [54] Configured Redis URL is ignored and security controls silently fail open

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | medium |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/database.py:15-93, cors_dashboard/backend/app/redis_client.py:19-32, cors_dashboard/backend/app/redis_client.py:45-88, cors_dashboard/backend/app/main.py:1642-1658, cors_dashboard/backend/app/main.py:8566-8579 |

#### Summary

See the [detailed technical write-up](findings/redis-url-ignored-security-failopen/redis-url-ignored-security-failopen.md).

#### Validation

See the [detailed technical write-up](findings/redis-url-ignored-security-failopen/redis-url-ignored-security-failopen.md).

#### Dataflow

See the [detailed technical write-up](findings/redis-url-ignored-security-failopen/redis-url-ignored-security-failopen.md).

#### Reachability

See the [detailed technical write-up](findings/redis-url-ignored-security-failopen/redis-url-ignored-security-failopen.md).

#### Severity

See the [detailed technical write-up](findings/redis-url-ignored-security-failopen/redis-url-ignored-security-failopen.md).

#### Remediation

See the [detailed technical write-up](findings/redis-url-ignored-security-failopen/redis-url-ignored-security-failopen.md).

<a id="finding-55"></a>

### [55] View-only station access authorizes deletion of shared rover accounts

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/station_access.py:19-37, cors_dashboard/backend/app/crud.py:818-836, cors_dashboard/backend/app/routers/devices.py:616-650 |

#### Summary

See the [detailed technical write-up](findings/auth-view-only-shared-rover-delete/auth-view-only-shared-rover-delete.md).

#### Validation

See the [detailed technical write-up](findings/auth-view-only-shared-rover-delete/auth-view-only-shared-rover-delete.md).

#### Dataflow

See the [detailed technical write-up](findings/auth-view-only-shared-rover-delete/auth-view-only-shared-rover-delete.md).

#### Reachability

See the [detailed technical write-up](findings/auth-view-only-shared-rover-delete/auth-view-only-shared-rover-delete.md).

#### Severity

See the [detailed technical write-up](findings/auth-view-only-shared-rover-delete/auth-view-only-shared-rover-delete.md).

#### Remediation

See the [detailed technical write-up](findings/auth-view-only-shared-rover-delete/auth-view-only-shared-rover-delete.md).

<a id="finding-56"></a>

### [56] Coordinator delegated user creation can read backend server logs

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:8450-8477, cors_dashboard/backend/app/auth.py:191-196 |

#### Summary

See the [detailed technical write-up](findings/main-026/main-026.md).

#### Validation

See the [detailed technical write-up](findings/main-026/main-026.md).

#### Dataflow

See the [detailed technical write-up](findings/main-026/main-026.md).

#### Reachability

See the [detailed technical write-up](findings/main-026/main-026.md).

#### Severity

See the [detailed technical write-up](findings/main-026/main-026.md).

#### Remediation

See the [detailed technical write-up](findings/main-026/main-026.md).

<a id="finding-57"></a>

### [57] Activity feed exposes global logs to every authenticated user

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:6957-6963, cors_dashboard/backend/app/crud.py:886-900, cors_dashboard/backend/app/crud.py:924-960, cors_dashboard/backend/app/schemas.py:333-348, cors_dashboard/backend/app/models.py:190-202 |

#### Summary

See the [detailed technical write-up](findings/main-022/main-022.md).

#### Validation

See the [detailed technical write-up](findings/main-022/main-022.md).

#### Dataflow

See the [detailed technical write-up](findings/main-022/main-022.md).

#### Reachability

See the [detailed technical write-up](findings/main-022/main-022.md).

#### Severity

See the [detailed technical write-up](findings/main-022/main-022.md).

#### Remediation

See the [detailed technical write-up](findings/main-022/main-022.md).

<a id="finding-58"></a>

### [58] Unauthenticated POST stop-station can force-stop correction streaming

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:3972-4074 |

#### Summary

See the [detailed technical write-up](findings/main-010/main-010.md).

#### Validation

See the [detailed technical write-up](findings/main-010/main-010.md).

#### Dataflow

See the [detailed technical write-up](findings/main-010/main-010.md).

#### Reachability

See the [detailed technical write-up](findings/main-010/main-010.md).

#### Severity

See the [detailed technical write-up](findings/main-010/main-010.md).

#### Remediation

See the [detailed technical write-up](findings/main-010/main-010.md).

<a id="finding-59"></a>

### [59] Rover CSV export permits spreadsheet-formula injection from persisted account fields

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | medium |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/rovers-global.js:1290-1304, cors_dashboard/backend/app/schemas.py:217-273, cors_dashboard/backend/app/routers/devices.py:440-613 |

#### Summary

See the [detailed technical write-up](findings/front-e-005/front-e-005.md).

#### Validation

See the [detailed technical write-up](findings/front-e-005/front-e-005.md).

#### Dataflow

See the [detailed technical write-up](findings/front-e-005/front-e-005.md).

#### Reachability

See the [detailed technical write-up](findings/front-e-005/front-e-005.md).

#### Severity

See the [detailed technical write-up](findings/front-e-005/front-e-005.md).

#### Remediation

See the [detailed technical write-up](findings/front-e-005/front-e-005.md).

<a id="finding-60"></a>

### [60] Full WebSocket URI logging exposes the device bearer token

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | medium |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | geodetic/agent_universal.py:5274-5297 |

#### Summary

See the [detailed technical write-up](findings/geo-005/geo-005.md).

#### Validation

See the [detailed technical write-up](findings/geo-005/geo-005.md).

#### Dataflow

See the [detailed technical write-up](findings/geo-005/geo-005.md).

#### Reachability

See the [detailed technical write-up](findings/geo-005/geo-005.md).

#### Severity

See the [detailed technical write-up](findings/geo-005/geo-005.md).

#### Remediation

See the [detailed technical write-up](findings/geo-005/geo-005.md).

<a id="finding-61"></a>

### [61] Caster clienttable fields are interpolated into rover popup HTML

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/stations-map.js:160-193, cors_dashboard/frontend/js/stations-map.js:339-418 |

#### Summary

See the [detailed technical write-up](findings/fef-002/fef-002.md).

#### Validation

See the [detailed technical write-up](findings/fef-002/fef-002.md).

#### Dataflow

See the [detailed technical write-up](findings/fef-002/fef-002.md).

#### Reachability

See the [detailed technical write-up](findings/fef-002/fef-002.md).

#### Severity

See the [detailed technical write-up](findings/fef-002/fef-002.md).

#### Remediation

See the [detailed technical write-up](findings/fef-002/fef-002.md).

<a id="finding-62"></a>

### [62] Caster usertable passwords are cached and returned to coordinators in plaintext

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/caster_client.py:219-236, cors_dashboard/backend/app/main.py:5614-5627, cors_dashboard/backend/app/main.py:4790-4806, cors_dashboard/backend/app/main.py:4922-4925, cors_dashboard/backend/app/schemas.py:403-427 |

#### Summary

See the [detailed technical write-up](findings/caster-coordinator-password-disclosure/caster-coordinator-password-disclosure.md).

#### Validation

See the [detailed technical write-up](findings/caster-coordinator-password-disclosure/caster-coordinator-password-disclosure.md).

#### Dataflow

See the [detailed technical write-up](findings/caster-coordinator-password-disclosure/caster-coordinator-password-disclosure.md).

#### Reachability

See the [detailed technical write-up](findings/caster-coordinator-password-disclosure/caster-coordinator-password-disclosure.md).

#### Severity

See the [detailed technical write-up](findings/caster-coordinator-password-disclosure/caster-coordinator-password-disclosure.md).

#### Remediation

See the [detailed technical write-up](findings/caster-coordinator-password-disclosure/caster-coordinator-password-disclosure.md).

<a id="finding-63"></a>

### [63] Caster sourcetable mountpoint reaches an HTML toast sink in the admin session

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/caster-modals.js:91-119, cors_dashboard/frontend/js/caster-modals.js:172-183 |

#### Summary

See the [detailed technical write-up](findings/feb-001/feb-001.md).

#### Validation

See the [detailed technical write-up](findings/feb-001/feb-001.md).

#### Dataflow

See the [detailed technical write-up](findings/feb-001/feb-001.md).

#### Reachability

See the [detailed technical write-up](findings/feb-001/feb-001.md).

#### Severity

See the [detailed technical write-up](findings/feb-001/feb-001.md).

#### Remediation

See the [detailed technical write-up](findings/feb-001/feb-001.md).

<a id="finding-64"></a>

### [64] Status reconciler retains an unbounded number of attacker-selected serial states

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/status_reconciler.py:58-173, cors_dashboard/backend/app/status_reconciler.py:213-229, cors_dashboard/backend/app/mqtt.py:800-812 |

#### Summary

See the [detailed technical write-up](findings/proto-009/proto-009.md).

#### Validation

See the [detailed technical write-up](findings/proto-009/proto-009.md).

#### Dataflow

See the [detailed technical write-up](findings/proto-009/proto-009.md).

#### Reachability

See the [detailed technical write-up](findings/proto-009/proto-009.md).

#### Severity

See the [detailed technical write-up](findings/proto-009/proto-009.md).

#### Remediation

See the [detailed technical write-up](findings/proto-009/proto-009.md).

<a id="finding-65"></a>

### [65] NTRIP response headers can grow without bound while bytes continue arriving

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | geodetic/agent_universal.py:3339-3344 |

#### Summary

See the [detailed technical write-up](findings/geo-009/geo-009.md).

#### Validation

See the [detailed technical write-up](findings/geo-009/geo-009.md).

#### Dataflow

See the [detailed technical write-up](findings/geo-009/geo-009.md).

#### Reachability

See the [detailed technical write-up](findings/geo-009/geo-009.md).

#### Severity

See the [detailed technical write-up](findings/geo-009/geo-009.md).

#### Remediation

See the [detailed technical write-up](findings/geo-009/geo-009.md).

<a id="finding-66"></a>

### [66] A reusable MQTT credential and device topic are committed in source

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | medium |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | geodetic/track_rtcm_mqtt.py:5-10, geodetic/track_rtcm_mqtt.py:100-113 |

#### Summary

See the [detailed technical write-up](findings/sdm-001/sdm-001.md).

#### Validation

See the [detailed technical write-up](findings/sdm-001/sdm-001.md).

#### Dataflow

See the [detailed technical write-up](findings/sdm-001/sdm-001.md).

#### Reachability

See the [detailed technical write-up](findings/sdm-001/sdm-001.md).

#### Severity

See the [detailed technical write-up](findings/sdm-001/sdm-001.md).

#### Remediation

See the [detailed technical write-up](findings/sdm-001/sdm-001.md).

<a id="finding-67"></a>

### [67] JSON RTCM skyview accepts an unbounded satellites collection

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/rtcm_signal_decoder.py:501-560, cors_dashboard/backend/app/mqtt.py:681-704 |

#### Summary

See the [detailed technical write-up](findings/proto-005/proto-005.md).

#### Validation

See the [detailed technical write-up](findings/proto-005/proto-005.md).

#### Dataflow

See the [detailed technical write-up](findings/proto-005/proto-005.md).

#### Reachability

See the [detailed technical write-up](findings/proto-005/proto-005.md).

#### Severity

See the [detailed technical write-up](findings/proto-005/proto-005.md).

#### Remediation

See the [detailed technical write-up](findings/proto-005/proto-005.md).

<a id="finding-68"></a>

### [68] Edited-message workflow secrets bypass fail-closed Zalo payload redaction

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/zalo_bot_service.py:372-389, cors_dashboard/backend/app/zalo_bot_service.py:1464-1473, cors_dashboard/backend/app/zalo_bot_service.py:1475-1481, cors_dashboard/backend/app/zalo_bot_service.py:669-697 |

#### Summary

See the [detailed technical write-up](findings/zalo-edited-message-workflow-secret-diagnostic-disclosure/zalo-edited-message-workflow-secret-diagnostic-disclosure.md).

#### Validation

See the [detailed technical write-up](findings/zalo-edited-message-workflow-secret-diagnostic-disclosure/zalo-edited-message-workflow-secret-diagnostic-disclosure.md).

#### Dataflow

See the [detailed technical write-up](findings/zalo-edited-message-workflow-secret-diagnostic-disclosure/zalo-edited-message-workflow-secret-diagnostic-disclosure.md).

#### Reachability

See the [detailed technical write-up](findings/zalo-edited-message-workflow-secret-diagnostic-disclosure/zalo-edited-message-workflow-secret-diagnostic-disclosure.md).

#### Severity

See the [detailed technical write-up](findings/zalo-edited-message-workflow-secret-diagnostic-disclosure/zalo-edited-message-workflow-secret-diagnostic-disclosure.md).

#### Remediation

See the [detailed technical write-up](findings/zalo-edited-message-workflow-secret-diagnostic-disclosure/zalo-edited-message-workflow-secret-diagnostic-disclosure.md).

<a id="finding-69"></a>

### [69] Unauthenticated POST wake-station can start a station correction stream

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:3533-3964 |

#### Summary

See the [detailed technical write-up](findings/main-007/main-007.md).

#### Validation

See the [detailed technical write-up](findings/main-007/main-007.md).

#### Dataflow

See the [detailed technical write-up](findings/main-007/main-007.md).

#### Reachability

See the [detailed technical write-up](findings/main-007/main-007.md).

#### Severity

See the [detailed technical write-up](findings/main-007/main-007.md).

#### Remediation

See the [detailed technical write-up](findings/main-007/main-007.md).

<a id="finding-70"></a>

### [70] NTRIP blocklist authorization fails open on any database error

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:5258-5307 |

#### Summary

See the [detailed technical write-up](findings/main-027/main-027.md).

#### Validation

See the [detailed technical write-up](findings/main-027/main-027.md).

#### Dataflow

See the [detailed technical write-up](findings/main-027/main-027.md).

#### Reachability

See the [detailed technical write-up](findings/main-027/main-027.md).

#### Severity

See the [detailed technical write-up](findings/main-027/main-027.md).

#### Remediation

See the [detailed technical write-up](findings/main-027/main-027.md).

<a id="finding-71"></a>

### [71] Rover dashboard discloses reusable plaintext caster passwords to viewer-scoped sessions

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/schemas.py:217-246, cors_dashboard/backend/app/crud.py:777-792, cors_dashboard/backend/app/models.py:36-54, cors_dashboard/frontend/rovers-global.html:146-169, cors_dashboard/frontend/rovers-global.html:225-378, cors_dashboard/frontend/js/rovers-global.js:1129-1162, cors_dashboard/frontend/js/rovers-global.js:1290-1304, cors_dashboard/backend/app/schemas.py:217-246, cors_dashboard/backend/app/main.py:5928-5970 |

#### Summary

See the [detailed technical write-up](findings/front-e-004/front-e-004.md).

#### Validation

See the [detailed technical write-up](findings/front-e-004/front-e-004.md).

#### Dataflow

See the [detailed technical write-up](findings/front-e-004/front-e-004.md).

#### Reachability

See the [detailed technical write-up](findings/front-e-004/front-e-004.md).

#### Severity

See the [detailed technical write-up](findings/front-e-004/front-e-004.md).

#### Remediation

See the [detailed technical write-up](findings/front-e-004/front-e-004.md).

<a id="finding-72"></a>

### [72] Viewer can activate RTCM on any station serial outside assigned scope

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:5310-5394, cors_dashboard/backend/app/auth.py:122-144 |

#### Summary

See the [detailed technical write-up](findings/main-011/main-011.md).

#### Validation

See the [detailed technical write-up](findings/main-011/main-011.md).

#### Dataflow

See the [detailed technical write-up](findings/main-011/main-011.md).

#### Reachability

See the [detailed technical write-up](findings/main-011/main-011.md).

#### Severity

See the [detailed technical write-up](findings/main-011/main-011.md).

#### Remediation

See the [detailed technical write-up](findings/main-011/main-011.md).

<a id="finding-73"></a>

### [73] New caster mountpoints default to the public password 12345

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/rovers-global.html:490-518 |

#### Summary

See the [detailed technical write-up](findings/fef-005/fef-005.md).

#### Validation

See the [detailed technical write-up](findings/fef-005/fef-005.md).

#### Dataflow

See the [detailed technical write-up](findings/fef-005/fef-005.md).

#### Reachability

See the [detailed technical write-up](findings/fef-005/fef-005.md).

#### Severity

See the [detailed technical write-up](findings/fef-005/fef-005.md).

#### Remediation

See the [detailed technical write-up](findings/fef-005/fef-005.md).

<a id="finding-74"></a>

### [74] Caster-controlled rover fields execute HTML in rover map popups

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/react-components.js:679-710, cors_dashboard/frontend/js/react-components.js:929-945, cors_dashboard/frontend/js/react-components.js:947-975, cors_dashboard/backend/app/main.py:4732-4748 |

#### Summary

See the [detailed technical write-up](findings/caster-client-xss-rover-leaflet-popup/caster-client-xss-rover-leaflet-popup.md).

#### Validation

See the [detailed technical write-up](findings/caster-client-xss-rover-leaflet-popup/caster-client-xss-rover-leaflet-popup.md).

#### Dataflow

See the [detailed technical write-up](findings/caster-client-xss-rover-leaflet-popup/caster-client-xss-rover-leaflet-popup.md).

#### Reachability

See the [detailed technical write-up](findings/caster-client-xss-rover-leaflet-popup/caster-client-xss-rover-leaflet-popup.md).

#### Severity

See the [detailed technical write-up](findings/caster-client-xss-rover-leaflet-popup/caster-client-xss-rover-leaflet-popup.md).

#### Remediation

See the [detailed technical write-up](findings/caster-client-xss-rover-leaflet-popup/caster-client-xss-rover-leaflet-popup.md).

<a id="finding-75"></a>

### [75] Future agent timestamps can permanently retire legitimate boot state

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/status_reconciler.py:18-94, cors_dashboard/backend/app/status_reconciler.py:103-145 |

#### Summary

See the [detailed technical write-up](findings/proto-008/proto-008.md).

#### Validation

See the [detailed technical write-up](findings/proto-008/proto-008.md).

#### Dataflow

See the [detailed technical write-up](findings/proto-008/proto-008.md).

#### Reachability

See the [detailed technical write-up](findings/proto-008/proto-008.md).

#### Severity

See the [detailed technical write-up](findings/proto-008/proto-008.md).

#### Remediation

See the [detailed technical write-up](findings/proto-008/proto-008.md).

<a id="finding-76"></a>

### [76] Public visitor WebSocket has no admission limit and stores every live socket

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:4114-4137, cors_dashboard/backend/app/websocket.py:89-108 |

#### Summary

See the [detailed technical write-up](findings/main-004/main-004.md).

#### Validation

See the [detailed technical write-up](findings/main-004/main-004.md).

#### Dataflow

See the [detailed technical write-up](findings/main-004/main-004.md).

#### Reachability

See the [detailed technical write-up](findings/main-004/main-004.md).

#### Severity

See the [detailed technical write-up](findings/main-004/main-004.md).

#### Remediation

See the [detailed technical write-up](findings/main-004/main-004.md).

<a id="finding-77"></a>

### [77] A valid base-position frame poisons JSON RTCM decoding through a nonexistent method call

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/rtcm_signal_decoder.py:290-305, cors_dashboard/backend/app/rtcm_signal_decoder.py:501-540, cors_dashboard/backend/app/mqtt.py:681-706 |

#### Summary

See the [detailed technical write-up](findings/proto-006/proto-006.md).

#### Validation

See the [detailed technical write-up](findings/proto-006/proto-006.md).

#### Dataflow

See the [detailed technical write-up](findings/proto-006/proto-006.md).

#### Reachability

See the [detailed technical write-up](findings/proto-006/proto-006.md).

#### Severity

See the [detailed technical write-up](findings/proto-006/proto-006.md).

#### Remediation

See the [detailed technical write-up](findings/proto-006/proto-006.md).

<a id="finding-78"></a>

### [78] Authenticated UI WebSocket start_rtcm action ignores the socket's allowed station scope

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:7778-7822, cors_dashboard/backend/app/main.py:7832-7872, cors_dashboard/backend/app/websocket.py:16-21, cors_dashboard/backend/app/websocket.py:29-50, cors_dashboard/backend/app/main.py:7832-7866, cors_dashboard/backend/app/main.py:3897-3934 |

#### Summary

See the [detailed technical write-up](findings/main-014/main-014.md).

#### Validation

See the [detailed technical write-up](findings/main-014/main-014.md).

#### Dataflow

See the [detailed technical write-up](findings/main-014/main-014.md).

#### Reachability

See the [detailed technical write-up](findings/main-014/main-014.md).

#### Severity

See the [detailed technical write-up](findings/main-014/main-014.md).

#### Remediation

See the [detailed technical write-up](findings/main-014/main-014.md).

<a id="finding-79"></a>

### [79] NMEA parser accepts sentences with absent or incorrect checksums

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/utils.py:19-40, cors_dashboard/backend/app/mqtt.py:708-731 |

#### Summary

See the [detailed technical write-up](findings/proto-002/proto-002.md).

#### Validation

See the [detailed technical write-up](findings/proto-002/proto-002.md).

#### Dataflow

See the [detailed technical write-up](findings/proto-002/proto-002.md).

#### Reachability

See the [detailed technical write-up](findings/proto-002/proto-002.md).

#### Severity

See the [detailed technical write-up](findings/proto-002/proto-002.md).

#### Remediation

See the [detailed technical write-up](findings/proto-002/proto-002.md).

<a id="finding-80"></a>

### [80] Admin username bypasses login rate limiting before password verification

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/security.py:82-92, cors_dashboard/backend/app/main.py:4140-4159, cors_dashboard/backend/app/main.py:4177-4183 |

#### Summary

See the [detailed technical write-up](findings/auth-admin-rate-limit-bypass/auth-admin-rate-limit-bypass.md).

#### Validation

See the [detailed technical write-up](findings/auth-admin-rate-limit-bypass/auth-admin-rate-limit-bypass.md).

#### Dataflow

See the [detailed technical write-up](findings/auth-admin-rate-limit-bypass/auth-admin-rate-limit-bypass.md).

#### Reachability

See the [detailed technical write-up](findings/auth-admin-rate-limit-bypass/auth-admin-rate-limit-bypass.md).

#### Severity

See the [detailed technical write-up](findings/auth-admin-rate-limit-bypass/auth-admin-rate-limit-bypass.md).

#### Remediation

See the [detailed technical write-up](findings/auth-admin-rate-limit-bypass/auth-admin-rate-limit-bypass.md).

<a id="finding-81"></a>

### [81] Coordinator can remove block rules for an arbitrary unknown-local caster account

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:5181-5254 |

#### Summary

See the [detailed technical write-up](findings/main-017/main-017.md).

#### Validation

See the [detailed technical write-up](findings/main-017/main-017.md).

#### Dataflow

See the [detailed technical write-up](findings/main-017/main-017.md).

#### Reachability

See the [detailed technical write-up](findings/main-017/main-017.md).

#### Severity

See the [detailed technical write-up](findings/main-017/main-017.md).

#### Remediation

See the [detailed technical write-up](findings/main-017/main-017.md).

<a id="finding-82"></a>

### [82] Operations events endpoint permits cross-station log reads and arbitrary serial filtering

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:7429-7438, cors_dashboard/backend/app/main.py:7152-7182, cors_dashboard/frontend/js/operations.js:136-143, cors_dashboard/backend/app/main.py:7429-7438, cors_dashboard/backend/app/main.py:7166-7182, cors_dashboard/backend/app/main.py:7152-7163 |

#### Summary

See the [detailed technical write-up](findings/main-024/main-024.md).

#### Validation

See the [detailed technical write-up](findings/main-024/main-024.md).

#### Dataflow

See the [detailed technical write-up](findings/main-024/main-024.md).

#### Reachability

See the [detailed technical write-up](findings/main-024/main-024.md).

#### Severity

See the [detailed technical write-up](findings/main-024/main-024.md).

#### Remediation

See the [detailed technical write-up](findings/main-024/main-024.md).

<a id="finding-83"></a>

### [83] Unauthenticated GET stop-station alias can force-stop correction streaming

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:3972-4060 |

#### Summary

See the [detailed technical write-up](findings/main-009/main-009.md).

#### Validation

See the [detailed technical write-up](findings/main-009/main-009.md).

#### Dataflow

See the [detailed technical write-up](findings/main-009/main-009.md).

#### Reachability

See the [detailed technical write-up](findings/main-009/main-009.md).

#### Severity

See the [detailed technical write-up](findings/main-009/main-009.md).

#### Remediation

See the [detailed technical write-up](findings/main-009/main-009.md).

<a id="finding-84"></a>

### [84] Device-controlled system_info can enable the backend-managed license bypass

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | medium |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/crud.py:134-210, cors_dashboard/backend/app/crud.py:334-560, cors_dashboard/backend/app/models.py:20-26 |

#### Summary

See the [detailed technical write-up](findings/data-001/data-001.md).

#### Validation

See the [detailed technical write-up](findings/data-001/data-001.md).

#### Dataflow

See the [detailed technical write-up](findings/data-001/data-001.md).

#### Reachability

See the [detailed technical write-up](findings/data-001/data-001.md).

#### Severity

See the [detailed technical write-up](findings/data-001/data-001.md).

#### Remediation

See the [detailed technical write-up](findings/data-001/data-001.md).

<a id="finding-85"></a>

### [85] All-stations map discloses protected station areas to Google tiles

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | medium |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/stations-map.js:195-294, cors_dashboard/frontend/js/stations-map.js:296-337 |

#### Summary

See the [detailed technical write-up](findings/fef-008/fef-008.md).

#### Validation

See the [detailed technical write-up](findings/fef-008/fef-008.md).

#### Dataflow

See the [detailed technical write-up](findings/fef-008/fef-008.md).

#### Reachability

See the [detailed technical write-up](findings/fef-008/fef-008.md).

#### Severity

See the [detailed technical write-up](findings/fef-008/fef-008.md).

#### Remediation

See the [detailed technical write-up](findings/fef-008/fef-008.md).

<a id="finding-86"></a>

### [86] Public wake requests can accumulate duplicate RTCM lease rows without a client/station uniqueness bound

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/alembic_devices/versions/c1a8e7f9b2d4_create_rtcm_stream_sessions_table.py:21-38, cors_dashboard/backend/alembic_devices/versions/d3f4a9b2c7e1_add_mountpoint_to_rtcm_stream_sessions.py:21-38, cors_dashboard/backend/app/main.py:3511-3553, cors_dashboard/backend/app/main.py:973-3934 |

#### Summary

See the [detailed technical write-up](findings/migdev-b-002/migdev-b-002.md).

#### Validation

See the [detailed technical write-up](findings/migdev-b-002/migdev-b-002.md).

#### Dataflow

See the [detailed technical write-up](findings/migdev-b-002/migdev-b-002.md).

#### Reachability

See the [detailed technical write-up](findings/migdev-b-002/migdev-b-002.md).

#### Severity

See the [detailed technical write-up](findings/migdev-b-002/migdev-b-002.md).

#### Remediation

See the [detailed technical write-up](findings/migdev-b-002/migdev-b-002.md).

<a id="finding-87"></a>

### [87] Unauthenticated GET wake-listen can start a station correction stream

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:3511-3964 |

#### Summary

See the [detailed technical write-up](findings/main-005/main-005.md).

#### Validation

See the [detailed technical write-up](findings/main-005/main-005.md).

#### Dataflow

See the [detailed technical write-up](findings/main-005/main-005.md).

#### Reachability

See the [detailed technical write-up](findings/main-005/main-005.md).

#### Severity

See the [detailed technical write-up](findings/main-005/main-005.md).

#### Remediation

See the [detailed technical write-up](findings/main-005/main-005.md).

<a id="finding-88"></a>

### [88] Unauthenticated GET wake-station alias can start a station correction stream

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:3512-3964 |

#### Summary

See the [detailed technical write-up](findings/main-006/main-006.md).

#### Validation

See the [detailed technical write-up](findings/main-006/main-006.md).

#### Dataflow

See the [detailed technical write-up](findings/main-006/main-006.md).

#### Reachability

See the [detailed technical write-up](findings/main-006/main-006.md).

#### Severity

See the [detailed technical write-up](findings/main-006/main-006.md).

#### Remediation

See the [detailed technical write-up](findings/main-006/main-006.md).

<a id="finding-89"></a>

### [89] Station-scoped viewers receive global caster host and error alerts

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/operations.js:122-127, cors_dashboard/backend/app/main.py:7314-7328, cors_dashboard/backend/app/main.py:7336-7365 |

#### Summary

See the [detailed technical write-up](findings/ops-alerts-exposes-global-caster-errors/ops-alerts-exposes-global-caster-errors.md).

#### Validation

See the [detailed technical write-up](findings/ops-alerts-exposes-global-caster-errors/ops-alerts-exposes-global-caster-errors.md).

#### Dataflow

See the [detailed technical write-up](findings/ops-alerts-exposes-global-caster-errors/ops-alerts-exposes-global-caster-errors.md).

#### Reachability

See the [detailed technical write-up](findings/ops-alerts-exposes-global-caster-errors/ops-alerts-exposes-global-caster-errors.md).

#### Severity

See the [detailed technical write-up](findings/ops-alerts-exposes-global-caster-errors/ops-alerts-exposes-global-caster-errors.md).

#### Remediation

See the [detailed technical write-up](findings/ops-alerts-exposes-global-caster-errors/ops-alerts-exposes-global-caster-errors.md).

<a id="finding-90"></a>

### [90] Caster table responses are accumulated without a byte or duration limit

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/caster_client.py:32-43, cors_dashboard/backend/app/caster_client.py:49-78, cors_dashboard/backend/app/main.py:5607-5627 |

#### Summary

See the [detailed technical write-up](findings/caster-unbounded-table-response-dos/caster-unbounded-table-response-dos.md).

#### Validation

See the [detailed technical write-up](findings/caster-unbounded-table-response-dos/caster-unbounded-table-response-dos.md).

#### Dataflow

See the [detailed technical write-up](findings/caster-unbounded-table-response-dos/caster-unbounded-table-response-dos.md).

#### Reachability

See the [detailed technical write-up](findings/caster-unbounded-table-response-dos/caster-unbounded-table-response-dos.md).

#### Severity

See the [detailed technical write-up](findings/caster-unbounded-table-response-dos/caster-unbounded-table-response-dos.md).

#### Remediation

See the [detailed technical write-up](findings/caster-unbounded-table-response-dos/caster-unbounded-table-response-dos.md).

<a id="finding-91"></a>

### [91] WebSocket bearer token is transmitted over plaintext ws://

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | geodetic/agent_universal.py:5274-5296 |

#### Summary

See the [detailed technical write-up](findings/geo-004/geo-004.md).

#### Validation

See the [detailed technical write-up](findings/geo-004/geo-004.md).

#### Dataflow

See the [detailed technical write-up](findings/geo-004/geo-004.md).

#### Reachability

See the [detailed technical write-up](findings/geo-004/geo-004.md).

#### Severity

See the [detailed technical write-up](findings/geo-004/geo-004.md).

#### Remediation

See the [detailed technical write-up](findings/geo-004/geo-004.md).

<a id="finding-92"></a>

### [92] Authenticated UI WebSocket stop_rtcm action allows cross-station forced shutdown

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:7832-7882, cors_dashboard/backend/app/main.py:3972-4037, cors_dashboard/backend/app/websocket.py:16-50, cors_dashboard/backend/app/main.py:7853-7876, cors_dashboard/backend/app/main.py:3972-4036, cors_dashboard/backend/app/main.py:790-832 |

#### Summary

See the [detailed technical write-up](findings/main-015/main-015.md).

#### Validation

See the [detailed technical write-up](findings/main-015/main-015.md).

#### Dataflow

See the [detailed technical write-up](findings/main-015/main-015.md).

#### Reachability

See the [detailed technical write-up](findings/main-015/main-015.md).

#### Severity

See the [detailed technical write-up](findings/main-015/main-015.md).

#### Remediation

See the [detailed technical write-up](findings/main-015/main-015.md).

<a id="finding-93"></a>

### [93] Operations summary leaks global recent events and caster topology despite scoped device counts

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/main.py:7205-7263, cors_dashboard/backend/app/main.py:7152-7182, cors_dashboard/frontend/js/operations.js:119-121, cors_dashboard/backend/app/main.py:7205-7216, cors_dashboard/backend/app/main.py:7217-7229, cors_dashboard/backend/app/main.py:7239-7263 |

#### Summary

See the [detailed technical write-up](findings/main-023/main-023.md).

#### Validation

See the [detailed technical write-up](findings/main-023/main-023.md).

#### Dataflow

See the [detailed technical write-up](findings/main-023/main-023.md).

#### Reachability

See the [detailed technical write-up](findings/main-023/main-023.md).

#### Severity

See the [detailed technical write-up](findings/main-023/main-023.md).

#### Remediation

See the [detailed technical write-up](findings/main-023/main-023.md).

<a id="finding-94"></a>

### [94] View-only station access exposes plaintext rover credentials

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/routers/devices.py:301-315, cors_dashboard/backend/app/station_access.py:50-60, cors_dashboard/backend/app/schemas.py:217-246 |

#### Summary

See the [detailed technical write-up](findings/auth-view-only-rover-credential-disclosure/auth-view-only-rover-credential-disclosure.md).

#### Validation

See the [detailed technical write-up](findings/auth-view-only-rover-credential-disclosure/auth-view-only-rover-credential-disclosure.md).

#### Dataflow

See the [detailed technical write-up](findings/auth-view-only-rover-credential-disclosure/auth-view-only-rover-credential-disclosure.md).

#### Reachability

See the [detailed technical write-up](findings/auth-view-only-rover-credential-disclosure/auth-view-only-rover-credential-disclosure.md).

#### Severity

See the [detailed technical write-up](findings/auth-view-only-rover-credential-disclosure/auth-view-only-rover-credential-disclosure.md).

#### Remediation

See the [detailed technical write-up](findings/auth-view-only-rover-credential-disclosure/auth-view-only-rover-credential-disclosure.md).

<a id="finding-95"></a>

### [95] Negative GSV PRNs bypass bounds and create an unbounded persistent satellite dictionary

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/utils.py:126-177, cors_dashboard/backend/app/utils.py:179-219 |

#### Summary

See the [detailed technical write-up](findings/proto-003/proto-003.md).

#### Validation

See the [detailed technical write-up](findings/proto-003/proto-003.md).

#### Dataflow

See the [detailed technical write-up](findings/proto-003/proto-003.md).

#### Reachability

See the [detailed technical write-up](findings/proto-003/proto-003.md).

#### Severity

See the [detailed technical write-up](findings/proto-003/proto-003.md).

#### Remediation

See the [detailed technical write-up](findings/proto-003/proto-003.md).

<a id="finding-96"></a>

### [96] Stored station names execute HTML in the delete-device confirmation dialog

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/frontend/js/main.js:956-963, cors_dashboard/frontend/js/ops-ui.js:73-92, cors_dashboard/backend/app/routers/users.py:84-108 |

#### Summary

See the [detailed technical write-up](findings/station-name-xss-delete-confirmation/station-name-xss-delete-confirmation.md).

#### Validation

See the [detailed technical write-up](findings/station-name-xss-delete-confirmation/station-name-xss-delete-confirmation.md).

#### Dataflow

See the [detailed technical write-up](findings/station-name-xss-delete-confirmation/station-name-xss-delete-confirmation.md).

#### Reachability

See the [detailed technical write-up](findings/station-name-xss-delete-confirmation/station-name-xss-delete-confirmation.md).

#### Severity

See the [detailed technical write-up](findings/station-name-xss-delete-confirmation/station-name-xss-delete-confirmation.md).

#### Remediation

See the [detailed technical write-up](findings/station-name-xss-delete-confirmation/station-name-xss-delete-confirmation.md).

<a id="finding-97"></a>

### [97] Viewer device listing serializes raw NTRIP service credentials

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/routers/devices.py:60-123, cors_dashboard/backend/app/schemas.py:35-45, cors_dashboard/backend/app/schemas.py:78-102, cors_dashboard/backend/app/schemas.py:37-58, cors_dashboard/backend/app/schemas.py:67-103, cors_dashboard/backend/app/models.py:9-29, cors_dashboard/frontend/js/dashboard-detail.js:806-1114, cors_dashboard/frontend/js/dashboard-forms.js:693-833, cors_dashboard/frontend/js/dashboard-forms.js:1099-1560 |

#### Summary

See the [detailed technical write-up](findings/auth-viewer-ntrip-secret-disclosure/auth-viewer-ntrip-secret-disclosure.md).

#### Validation

See the [detailed technical write-up](findings/auth-viewer-ntrip-secret-disclosure/auth-viewer-ntrip-secret-disclosure.md).

#### Dataflow

See the [detailed technical write-up](findings/auth-viewer-ntrip-secret-disclosure/auth-viewer-ntrip-secret-disclosure.md).

#### Reachability

See the [detailed technical write-up](findings/auth-viewer-ntrip-secret-disclosure/auth-viewer-ntrip-secret-disclosure.md).

#### Severity

See the [detailed technical write-up](findings/auth-viewer-ntrip-secret-disclosure/auth-viewer-ntrip-secret-disclosure.md).

#### Remediation

See the [detailed technical write-up](findings/auth-viewer-ntrip-secret-disclosure/auth-viewer-ntrip-secret-disclosure.md).

<a id="finding-98"></a>

### [98] Retained device status publishes reusable caster and command passwords

| Field | Value |
| --- | --- |
| Severity | medium |
| Confidence | high |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | geodetic/geotek_daithanh_aitogy.py:2367-2402, geodetic/geotek_daithanh_aitogy.py:2404-2456 |

#### Summary

See the [detailed technical write-up](findings/geo-013/geo-013.md).

#### Validation

See the [detailed technical write-up](findings/geo-013/geo-013.md).

#### Dataflow

See the [detailed technical write-up](findings/geo-013/geo-013.md).

#### Reachability

See the [detailed technical write-up](findings/geo-013/geo-013.md).

#### Severity

See the [detailed technical write-up](findings/geo-013/geo-013.md).

#### Remediation

See the [detailed technical write-up](findings/geo-013/geo-013.md).

<a id="finding-99"></a>

### [99] NTRIP MQTT control flood schedules unbounded coroutines outside the bounded ingress queue

| Field | Value |
| --- | --- |
| Severity | low |
| Confidence | medium |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/mqtt.py:531-546 |

#### Summary

See the [detailed technical write-up](findings/cp-mqtt-005/cp-mqtt-005.md).

#### Validation

See the [detailed technical write-up](findings/cp-mqtt-005/cp-mqtt-005.md).

#### Dataflow

See the [detailed technical write-up](findings/cp-mqtt-005/cp-mqtt-005.md).

#### Reachability

See the [detailed technical write-up](findings/cp-mqtt-005/cp-mqtt-005.md).

#### Severity

See the [detailed technical write-up](findings/cp-mqtt-005/cp-mqtt-005.md).

#### Remediation

See the [detailed technical write-up](findings/cp-mqtt-005/cp-mqtt-005.md).

<a id="finding-100"></a>

### [100] RTCM decoder retains per-serial buffers and navigation caches without eviction

| Field | Value |
| --- | --- |
| Severity | low |
| Confidence | medium |
| Confidence rationale | Direct source trace and centralized validation/attack-path review. |
| Category | security-boundary |
| CWE | none |
| Affected lines | cors_dashboard/backend/app/rtcm_signal_decoder.py:98-633, cors_dashboard/backend/app/mqtt.py:667-754 |

#### Summary

See the [detailed technical write-up](findings/proto-010/proto-010.md).

#### Validation

See the [detailed technical write-up](findings/proto-010/proto-010.md).

#### Dataflow

See the [detailed technical write-up](findings/proto-010/proto-010.md).

#### Reachability

See the [detailed technical write-up](findings/proto-010/proto-010.md).

#### Severity

See the [detailed technical write-up](findings/proto-010/proto-010.md).

#### Remediation

See the [detailed technical write-up](findings/proto-010/proto-010.md).

## Structural Hardening

The scan also produced derived, unsealed design guidance based on the complete finding collection. These proposals describe options and tradeoffs; they do not indicate that any finding has been remediated.

[Open the structural hardening portfolio](hardening/hardening.md)

## Reviewed Surfaces

| Surface | Risk Area | Outcome | Notes |
| --- | --- | --- | --- |
| Full repository worklist (110 files) | not recorded | Reported | No additional canonical notes were recorded. |

## Open Questions And Follow Up

- Validated but attack-path evidence remains bounded or needs environment confirmation.
  - Follow-up prompt: Review deferred unit deferred-candidates and close its stated proof gap.
- Suppressed or not applicable after centralized validation.
  - Follow-up prompt: Review deferred unit suppressed-candidates and close its stated proof gap.

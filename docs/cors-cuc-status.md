# CORS Cục status

The station map separates local bases from the VNGEONET reference layer. Local
bases retain their device status and their own online count. CORS Cục markers
remain purple and carry a separate website-status ring and a `Cục` label.

`GET /api/public/reference-network-status` checks the public
[login page](https://vngeonet.vn/Account/Index?returnUrl=%2F), served by
`14.238.1.125`. A successful page must contain the expected login form and its
username/password fields. A page without that form fails the website check, even
with HTTP 200; the provider's Error title or `handledErrorMessage` also fails.
Timeouts, TLS failures and responses that cannot be inspected produce an `unknown` website status.
The provider uses a certificate for `vngeonet.vn`; verification stays enabled.

Polling is configured in `backend/.env`:

```dotenv
CORS_CUC_RTCM_CHECK_ENABLED=true
CORS_CUC_WEB_CHECK_INTERVAL_SECONDS=30
CORS_CUC_RTCM_CHECK_INTERVAL_SECONDS=900
```

Intervals are positive integer seconds; invalid values fall back to 30 and 900.
Changes are read live without a restart. When RTCM checking is disabled, status
uses the original login-form rule and no automatic NTRIP connection is made.
The default for installations without the setting is disabled.

When enabled, RTCM determines the overall status regardless of the website:

| Website | RTCM | Overall status |
| --- | --- | --- |
| Online | Valid frames | Online |
| Online | Accepted login, no valid frames | Offline |
| Failed | Valid frames | Online |
| Failed | Accepted login, no valid frames | Offline |

An authentication/permission rejection (401/403), missing credentials, or an
unreachable caster returns unknown, rather than claiming a data outage.
The probe uses `CORS_CUC_NTRIP_HOST`, `CORS_CUC_NTRIP_PORT`,
`CORS_CUC_NTRIP_MOUNTPOINT`, `CORS_CUC_NTRIP_USER` and
`CORS_CUC_NTRIP_PASSWORD`. It sends a current GGA for a reference position in
Hanoi and checks `VRS.105M3`. It closes the socket immediately after the first
CRC24Q-valid RTCM3 frame, or on failure; the entire probe is bounded to six
seconds and 64 KiB. HTTP/ICY success alone does not count as valid data.

Admins have a **Kiểm tra CORS Cục** section immediately below the Zalo system
controls on `/zalo`, as well as a **Kiểm tra dữ liệu RTCM Cục** switch in the
station-map filters. Both control the same setting independently of bot power.
`GET/PUT /api/admin/reference-network-settings` requires an authenticated admin;
PUT saves only `rtcm_check_enabled` to the same `.env`. It neither triggers a
probe directly nor bypasses the cooldown. Viewers and anonymous users cannot
change this setting. Switching off invalidates the combined process cache at
the next request, so an old RTCM error does not override the website result.

Probe results and attempt timestamps persist in
`backend/app/data/reference_network_cache.sqlite3`. Atomic reservations are
committed before contacting the provider; workers and restarts on the same
server share one cooldown, including failures. A failed local cache skips the
upstream request. All backend workers must use this same writable data directory;
separate hosts require a shared coordinator before enabling probes on each host.
Changing credentials or the endpoint starts a separate RTCM cache entry; existing
credential settings are loaded at startup, so restart after those changes.

The browser reads our endpoint every 30 seconds, cancels requests on unmount and
refreshes when the tab becomes visible. This does not create an upstream request
until that probe's configured interval has elapsed. `checked_at` is the actual
selected probe time; `website_checked_at` and `rtcm_checked_at` preserve separate
observations. `valid_until` follows the configured interval with a 60-second
grace period; expired observations become unknown. `scope` is `website` when
disabled and `ntrip` when enabled. Individual station status remains unavailable.

A valid VRS stream confirms that sampled service, not every station or rover FIX
quality. Web availability alone does not prove RTCM quality in legacy mode.
The public station list inspected on 2026-10-17 returned `Status: 0` and no live
station timestamps. An online website therefore does not mark local bases or
individual reference stations as broadcasting corrections.

Validation:

```sh
# Run from cors_dashboard/backend with the usual test database configuration.
python3 -m unittest tests.test_reference_network tests.test_reference_network_settings tests.test_zalo_cors_report tests.test_zalo_public_cors
# Run from the repository root.
node scripts/stations_map_lifecycle_selftest.js
node scripts/mapbox_globe_selftest.js
```

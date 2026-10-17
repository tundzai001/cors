# CORS Cục website status

The station map separates local bases from the VNGEONET reference layer. Local
bases retain their device status and their own online count. CORS Cục markers
remain purple and carry a separate website-status ring and a `Cục` label.

`GET /api/public/reference-network-status` checks the public
[login page](https://vngeonet.vn/Account/Index?returnUrl=%2F) and
[station overview](https://vngeonet.vn/User/SiteMap/SiteMapPublic). It requires
the expected login form and a nonempty station list. The provider's Error title
or `handledErrorMessage` is treated as a failure even with HTTP 200. Unexpected
HTML, timeouts and TLS failures produce `unknown`, never a retained green state.
The provider uses a certificate for `vngeonet.vn`; verification stays enabled.

Checks share a 30-second cache per backend worker, including failed results.
Requests have timeouts and a 1 MiB page limit. The browser refreshes every 30
seconds, cancels requests on unmount and treats data older than 75 seconds as
unknown. Returning to a visible tab triggers a refresh.

This measures website availability, not individual station or RTCM availability.
The public station list inspected on 2026-10-17 returned `Status: 0` and no live
station timestamps. An online website therefore does not mark local bases or
individual reference stations as broadcasting corrections.

Validation:

```sh
# Run from cors_dashboard/backend with the usual test database configuration.
python3 -m unittest tests.test_reference_network tests.test_session_control
# Run from the repository root.
node scripts/stations_map_lifecycle_selftest.js
node scripts/mapbox_globe_selftest.js
```

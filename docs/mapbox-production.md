# Mapbox in the production station map

The existing station map opens Mapbox GL JS 3.9.3 through its globe button or when zooming out to world view. Mapbox keeps a single canvas from globe to street scale. Station pins use the dashboard's current station list and filters, not sample data.

The globe dock contains one control per action: global overview, Vietnam, automatic rotation, and GNSS orbit view. The orbit view retains the existing Three.js satellite, RTCM and IPP tools. Selecting a Mapbox station and choosing **Theo dõi vệ tinh** selects that station for GNSS tracking. The toolbar's 2D button returns to Leaflet at the current center and scale.

## Backend environment

Set `MAPBOX_PUBLIC_TOKEN` in `cors_dashboard/backend/.env` or the backend process environment. Use a public `pk.` token with `styles:tiles`, `styles:read`, and `fonts:read`; no secret scopes. Allow the production origin in the token's Allowed URLs.

The local backend `.env` has been updated with the supplied token. `.env` stays ignored by Git, so an existing production installation must receive this environment setting through its normal environment deployment process. Restart the backend after changing it.

`GET /api/public/map-config` returns only `mapbox_public_token` with `Cache-Control: no-store`. Empty or secret `sk.` values are never published. The public token is necessarily visible to the browser in Mapbox requests; other environment values remain server-side.

Mapbox's JS SDK is loaded on demand from `/assets/js/dist/mapbox-gl-sdk.min.js`. The original SDK and CSS are included locally, retaining their license notices. [Mapbox SDK license](https://github.com/mapbox/mapbox-gl-js/blob/v3.9.3/LICENSE.txt).

## Verification

```sh
cd cors_dashboard/backend
python3 -m unittest discover -s tests -p test_map_config.py
python3 -m unittest discover -s tests -p test_satellites.py
```

From the repository root:

```sh
node scripts/mapbox_globe_selftest.js
node scripts/stations_map_lifecycle_selftest.js
node scripts/gnss_rtcm_tracking_selftest.js
node scripts/landing_performance_selftest.js
node scripts/build_frontend_assets.mjs
```

On production, open the station map, enter the globe, zoom to street level, select a real station, enter GNSS orbit tracking, return to Mapbox, then return to 2D. Repeat to check that no stale canvases, popup handlers or tracking rays remain.

The provided token successfully serves Mapbox tiles when the Referer is `https://cors.aitogy.com/`; localhost receives 403 because of URL restrictions. Rendering and camera smoothness with live tiles must therefore be verified on the allowed production origin.

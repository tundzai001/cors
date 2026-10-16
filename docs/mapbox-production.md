# Mapbox in the production station map

The station map starts with one persistent Mapbox GL JS 3.9.3 map. Globe projection provides continuous zoom from Earth to street scale; the toolbar switches between globe and Mercator on that same map. Projection changes preserve center, zoom, station filters, measurements, selected station, and overlay sources. They do not construct Leaflet or replace the Mapbox canvas.

Stations, coverage circles, authorized rover positions/clusters, province boundaries, Vietnam mask, search/nearest-station lines, measurements, NOAA TEC rasters, and GNSS/RTCM/IPP features render as native Mapbox sources and layers. GNSS shares the existing SGP4 propagation and exact RTCM matching. TEC uses the existing raster generator with Mercator sampling and two hemisphere images. Changing the base style restores custom sources after `style.load`.

The separate GNSS orbit view retains Three.js for actual satellite orbits. Entering orbit mode pauses the surface map without destroying it; returning restores its camera and latest data. Rotation is optional and respects reduced motion.

Missing/invalid tokens, configuration or SDK failures, unavailable WebGL, denied tile requests, repeated tile errors, or a 15-second initial-load timeout activate Leaflet with the original Google hybrid/satellite/streets layers. The Mapbox instance is removed before fallback is mounted, preserving its last center/zoom where available. Fallback lasts until the map is remounted, avoiding automatic retry loops. The legacy station, rover, TEC, GNSS, search and measurement tools remain available.

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

On production, zoom from Earth to street level and back, toggle globe/Mercator repeatedly, switch base styles, select a real station, enable TEC and GNSS, and cancel tracking. Enter and leave GNSS orbit view. Verify camera/data continuity and one active surface renderer. Separately test an empty/invalid token and confirm Google fallback, its filters and tools. Restore the valid token and remount the map to return to Mapbox.

The provided token successfully serves Mapbox tiles when the Referer is `https://cors.aitogy.com/`; localhost receives 403 because of URL restrictions. Rendering and camera smoothness with live tiles must therefore be verified on the allowed production origin.

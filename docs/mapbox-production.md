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

Before deploying the domain change, add `https://base.aitogy.com/*` to the token's allowed URLs in Mapbox. Verify tile requests on the new production origin; the previous origin's successful requests do not validate this new hostname. Rendering and camera smoothness with live tiles must be verified after this change.

## Map controls and current satellite identity

Rovers use the original colored triangle icons. The filter contains one **Vùng phủ 20 km** switch; status checkboxes use a compact two-column layout. Province boundaries have a dark casing and cyan stroke. Clicking a province highlights it in amber and shows its area, administrative/postal codes and station counts without moving the camera. The popup's zoom button and province search perform the explicit camera movement.

Hoàng Sa and Trường Sa labels identify Việt Nam directly on the normal map, independently of the optional Vietnam clipping mask. They mark label locations; they do not introduce invented maritime boundary polygons.

The identity service now cross-checks IGS time-bounded assignments against the [Cabinet Office's dated positioning-satellite table](https://qzss.go.jp/en/technical/satellites/index.html), alongside CelesTrak orbital data. `gnss_status_snapshot.json` contains parsed factual status/PRN records dated 2026-09-15, including retired BeiDou-2 spacecraft. A live table refresh runs with the TLE refresh; the latest parsed snapshot is cached in the ignored `gnss_status_cache.json`. Failed or incomplete responses retain the dated bundled/cached snapshot. The table's SVN joins the stable IGS SVN/NORAD crosswalk, independently of IGS's current PRN assignments. GLONASS table blanks are not interpreted as retirement or slot assignments.

Independent PRN fallback requires a recent dated assignment, no later IGS reassignment and no other current NORAD owner of that PRN. Retired spacecraft remain available as orbital objects but cannot match RTCM. An object without a confirmed current PRN displays a unique **#NORAD** label rather than repeated `C?` labels; its canonical identity remains unassigned. API records include `identity_source`, `identity_status`, and, when cross-checked, `status_source`, `status_as_of_utc`, and `operational_status`.

Owned Mapbox popups correct the SDK 3.9.3 close button's `aria-hidden` attribute and do not take focus on map clicks. Inactive map hosts use `inert`. Base-style changes explicitly disable style diffing before restoring overlays, avoiding the unsupported `setSprite` diff warning. Errors from the Mapbox telemetry endpoint do not trigger Google fallback.

`ERR_BLOCKED_BY_CLIENT` for `events.mapbox.com` reflects browser/extension blocking of telemetry, as distinct from tile/style failures. Terrain/hillshade fingerprinting warnings originate from Mapbox's response to browser privacy protection. The application does not disable the user's privacy settings or alter the vendor SDK to suppress these warnings.

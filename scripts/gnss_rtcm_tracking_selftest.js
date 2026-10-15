const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

let telemetry = { satellites: {} };
const window = {
  globalState: { selectedDeviceSerial: 'PI_station1' },
  getCurrentNmeaData: () => telemetry,
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/gnss-satellite-overlay.js'), 'utf8'), { window, Date, Map, Set, console });
const overlay = window.GnssSatelliteOverlay;
overlay.selectedStation = { serial: 'PI_station1' };

const now = Date.now();
telemetry = { satellites: {
  gps: { id: 'RTCM:G:8', system: 'G', prn: 8, messageType: 1077, lastSeen: now },
  glo: { id: 'RTCM:ru:5', system: 'ru', prn: 5, lastSeen: now },
  gal: { id: 'RTCM:eu:12', system: 'eu', prn: 12, lastSeen: now },
  bds: { id: 'RTCM:cn:21', system: 'cn', prn: 21, lastSeen: now },
  nmea: { id: 'G:8', system: 'us', prn: 'G08', lastSeen: now },
  stale: { id: 'RTCM:G:9', system: 'us', prn: 9, lastSeen: now - 20000 },
} };

assert.equal(overlay.getLiveRtcmSatellites().length, 4, 'only fresh RTCM records count as live receiver tracking');
for (const [constellationKey, code] of [['gps', 'G08'], ['glonass', 'R05'], ['galileo', 'E12'], ['beidou', 'C21']]) {
  assert.ok(overlay.getLiveRtcmSatellite({ constellationKey, code }), `${code} resolves to the exact RTCM PRN`);
}
assert.equal(overlay.getLiveRtcmSatellite({ constellationKey: 'gps', code: 'G09' }), null, 'stale satellite is not tracked');
window.globalState.selectedDeviceSerial = 'PI_other';
assert.equal(overlay.getLiveRtcmSatellites().length, 0, 'telemetry from a different station is never matched');
window.globalState.selectedDeviceSerial = 'PI_station1';
const satelliteMarkers = [];
const rayLayers = [];
const createLayerGroup = layers => ({
  addLayer(layer) { if (!layers.includes(layer)) layers.push(layer); },
  removeLayer(layer) { const index = layers.indexOf(layer); if (index >= 0) layers.splice(index, 1); },
  clearLayers() { layers.length = 0; },
  hasLayer(layer) { return layers.includes(layer); },
});
const createMarker = () => ({
  setLatLng() {},
  bindPopup() { return this; },
  bindTooltip() { return this; },
  getPopup() { return null; },
  getTooltip() { return null; },
});
overlay.mapInstance = {};
overlay.layerGroup = createLayerGroup(satelliteMarkers);
overlay.rayLayerGroup = createLayerGroup(rayLayers);
overlay.selectedStation = { serial: 'PI_station1', lat: 21, lon: 105, alt: 50, status: 'online' };
overlay.rawSatellites = [
  { key: 'gps:G08', code: 'G08', name: 'GPS 08', norad_id: 1, constellationKey: 'gps', satrec: {}, marker: null },
  { key: 'gps:G09', code: 'G09', name: 'GPS 09', norad_id: 2, constellationKey: 'gps', satrec: {}, marker: null },
];
overlay.activeSatellites = new Map(overlay.rawSatellites.map(sat => [sat.key, sat]));
window.L = {
  divIcon: () => ({}),
  marker: createMarker,
  polyline: () => ({ setLatLngs() {} }),
};
window.satellite = {
  gstime: () => 0,
  propagate: () => ({ position: { x: 1, y: 2, z: 3 } }),
  eciToGeodetic: () => ({ latitude: 0, longitude: 0, height: 20200 }),
  degreesLat: value => value * 180 / Math.PI,
  degreesLong: value => value * 180 / Math.PI,
  eciToEcf: () => ({ x: 1, y: 2, z: 3 }),
  ecfToLookAngles: () => ({ elevation: Math.PI / 4, azimuth: Math.PI / 6 }),
};
overlay.propagateTick();
assert.equal(overlay.stats.liveRtcmCount, 4, 'propagation updates live RTCM stats without a stale variable reference');
assert.equal(satelliteMarkers.length, 2, 'all enabled orbital markers stay visible while station tracking is active');
assert.equal(overlay.stats.visible, 1, 'only the satellite whose PRN matches RTCM counts as tracked');
assert.equal(rayLayers.length, 3, 'LOS and IPP layers exist only for the matched RTCM satellite');
telemetry = { satellites: {} };
overlay.propagateTick();
assert.equal(satelliteMarkers.length, 2, 'satellite positions remain visible when fresh RTCM records stop');
assert.equal(overlay.stats.visible, 0, 'expired RTCM records stop counting as tracked');
assert.equal(rayLayers.length, 0, 'expired RTCM records remove their LOS and IPP layers');

const mapSource = fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/stations-map.js'), 'utf8');
assert.match(mapSource, /zoomSnap:\s*0\.5[\s\S]*zoomDelta:\s*1[\s\S]*wheelPxPerZoomLevel:\s*35/,
  'wheel zoom should move faster with practical half-step precision');
assert.doesNotMatch(mapSource, /onlyVisible|matchRtcm|sat-elev-mask/,
  'global orbital positions must not disappear behind receiver-only visibility filters');
assert.match(mapSource, /Math\.max\(2, Math\.min\(20, view\.zoom\)\)/,
  'returning from 3D must preserve world-scale 2D zoom');
assert.match(mapSource, /setMinZoom\(2\)/,
  '2D should remain available through the world view before 3D handoff');

const globeSatSource = fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/globe-3d-satellites.js'), 'utf8');
assert.match(globeSatSource, /if \(liveRtcmSat\) \{\s*trackedSats\.push/,
  '3D station cards must count and draw rays only for exact fresh RTCM matches');
assert.doesNotMatch(globeSatSource, /Vệ tinh dự kiến khả kiến|Tính từ quỹ đạo/,
  '3D tracking cards must not present modeled visibility as receiver tracking');

const mainSource = fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/main.js'), 'utf8');
assert.match(mainSource, /if \(globalState\.currentView !== 'dashboard'\) return;/,
  'background status updates must not render the hidden dashboard');
assert.match(mainSource, /if \(!selectedDevice\) \{\s*detailsPanelRoot\.render\(null\)/,
  'details panel must be cleared when no selected device exists');
assert.doesNotMatch(mainSource, /DOM INSPECTION WARNING/,
  'remove the timing-sensitive DOM warning that reported normal React commits as failures');
console.log('PASS: RTCM system/PRN matching, live propagation stats, view-isolated rendering, and map zoom settings');

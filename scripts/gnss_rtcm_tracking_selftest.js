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
overlay.mapInstance = {};
overlay.layerGroup = { hasLayer: () => false };
window.L = {};
window.satellite = { gstime: () => 0 };
overlay.propagateTick();
assert.equal(overlay.stats.liveRtcmCount, 4, 'propagation updates live RTCM stats without a stale variable reference');

const mapSource = fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/stations-map.js'), 'utf8');
assert.match(mapSource, /zoomSnap:\s*0\.5[\s\S]*zoomDelta:\s*1[\s\S]*wheelPxPerZoomLevel:\s*35/,
  'wheel zoom should move faster with practical half-step precision');

const mainSource = fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/main.js'), 'utf8');
assert.match(mainSource, /if \(globalState\.currentView !== 'dashboard'\) return;/,
  'background status updates must not render the hidden dashboard');
assert.match(mainSource, /if \(!selectedDevice\) \{\s*detailsPanelRoot\.render\(null\)/,
  'details panel must be cleared when no selected device exists');
assert.doesNotMatch(mainSource, /DOM INSPECTION WARNING/,
  'remove the timing-sensitive DOM warning that reported normal React commits as failures');
console.log('PASS: RTCM system/PRN matching, live propagation stats, view-isolated rendering, and map zoom settings');

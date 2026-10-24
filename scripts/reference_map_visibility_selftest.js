const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('cors_dashboard/src/js/stations-map.js', 'utf8');
const start = source.indexOf('const referenceStations = useMemo(');
const end = source.indexOf('const referenceMeta =', start);
const stations = [{code: 'TEST', lat: 20, lon: 105}];
function visible(health, show = true) {
    return vm.runInNewContext(source.slice(start, end) + '\nreferenceStations;', {
        useMemo: fn => fn(), search: '', showVngeonet: show, vngeonetHealth: health,
        window: {VngeonetStations: stations}, referenceNetworkMeta: () => ({color: '#999'})
    });
}
assert.equal(visible(null).length, 0, 'Hide locations until the server explicitly enables the layer');
assert.equal(visible({map_status_enabled: false}).length, 0, 'False hides every CORS Cuc coordinate');
assert.equal(visible({map_status_enabled: true}).length, 1, 'True restores the layer');
assert.equal(visible({map_status_enabled: true}, false).length, 0, 'User filter still works when enabled');
assert(source.includes('referenceStations.forEach'), 'Leaflet consumes the gated list');
assert(source.includes('rovers: visibleRovers, referenceStations'), 'Globe consumes the same gated list');
console.log('PASS: env flag gates CORS Cuc locations for Leaflet and globe');

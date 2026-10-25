const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('cors_dashboard/src/js/stations-map.js', 'utf8');
const helper = source.slice(source.indexOf('    function is4gBase('), source.indexOf('    function getPreferredServer('));
const start = source.indexOf('const stations = useMemo(');
const end = source.indexOf('\n        useEffect(', start);
const devices = ['online', 'streaming', 'offline', 'lost', 'sleep', 'locked', 'starting'].flatMap(status => [
    {serial: `esp_${status}`, status}, {serial: `MONITOR_7${status}`, status},
    {serial: `mountpoint_${status}`, status, service_config: {mountpoint1: '7001'}},
    {serial: `fixed_${status}`, status},
]);
const visible = vm.runInNewContext(helper + source.slice(start, end) + '\nstations;', {
    devices, useMemo: fn => fn(), search: '', showStations: true, statusFilters: {},
    getCoords: () => ({lat: 20, lon: 105}), effectiveStatus: device => device.status,
    getPreferredServer: () => ({}), window: {CorsDashboardShared: {
        isReadonly4gBase: device => device.service_config?.mountpoint1 === '7001',
    }},
});
assert.equal(visible.filter(item => item.is4g).length, 6, 'Only online and streaming 4G bases appear');
assert.equal(visible.filter(item => !item.is4g).length, 7, 'Fixed base status visibility stays unchanged');
assert(visible.every(item => !item.is4g || ['online', 'streaming'].includes(item.status)));
assert(source.includes('is4g ? L.marker('), 'Leaflet uses a distinct 4G marker');
assert(source.includes('<rect '), 'Leaflet 4G marker is square');
console.log('PASS: ESP, monitor and mountpoint 4G bases are online-only; fixed bases retain all states');

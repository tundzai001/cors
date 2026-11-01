const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(process.argv[2] || path.join(__dirname, '../cors_dashboard/src/js/dashboard-live.js'), 'utf8');
const helperStart = source.indexOf('function formatTelemetryNumber(');
const helper = helperStart < 0 ? '' : source.slice(helperStart, source.indexOf('function getLeaflet(', helperStart));
const animation = source.slice(source.indexOf('const animate = () => {'), source.indexOf('const handleResize = () => {', source.indexOf('const animate = () => {')));
assert(animation.includes('requestAnimationFrame(animate)'), 'exercise the actual dashboard animation loop');

function render(data, readonly = false, device = {}) {
    const nodes = Object.fromEntries(['db-lat', 'db-lon', 'db-dop'].map(id => [id, {textContent: ''}]));
    let scheduled = 0;
    vm.runInNewContext(`${helper}\n${animation}\nanimate();`, {
        Date: {now: () => 10000},
        document: {getElementById: id => nodes[id] || null},
        safeNmeaDataRef: {current: data}, deviceRef: {current: device}, isDeviceStaleOffline: () => false, isReadonly4g: readonly,
        lastStatsUpdate: {current: 0}, STATS_INTERVAL: 1000,
        lastCanvasUpdate: {current: 0}, CANVAS_INTERVAL: 200,
        lastBarsUpdate: {current: 0}, BARS_INTERVAL: 500,
        drawSkyplotSatellites() {}, drawSignalBars() {},
        animationFrameRef: {}, requestAnimationFrame: () => ++scheduled,
    });
    assert.equal(scheduled, 1, 'bad telemetry must not stop the next animation frame');
    return Object.fromEntries(Object.entries(nodes).map(([id, node]) => [id, node.textContent]));
}

const expected = {'db-lat': '21.32860800', 'db-lon': '105.37940800', 'db-dop': '0.85 / 1.20'};
assert.deepEqual(render({gga: {latitude: '21.328608', longitude: '105.379408'}, gsa: {hdop: '0.85', pdop: '1.2'}}), expected);
assert.deepEqual(render({gga: {latitude: 21.328608, longitude: 105.379408}, gsa: {hdop: 0.85, pdop: 1.2}}), expected);
assert.deepEqual(render({base_coords: {lat: '21.328608', lon: '105.379408'}, gsa: {hdop: '0.85', pdop: '1.2'}}, true), expected);
for (const invalid of [null, undefined, '', ' ', 'bad', '1x', 'Infinity', Infinity, NaN, {}, true]) {
    assert.deepEqual(render({gga: {latitude: invalid, longitude: invalid}, gsa: {hdop: invalid, pdop: invalid}}, true),
        {'db-lat': '—', 'db-lon': '—', 'db-dop': '— / —'});
}
assert.deepEqual(render({gga: {latitude: 0, longitude: '0'}, gsa: {hdop: '99', pdop: 100}}),
    {'db-lat': '0.00000000', 'db-lon': '0.00000000', 'db-dop': '— / —'});
assert.deepEqual(render({}), {'db-lat': '—', 'db-lon': '—', 'db-dop': '— / —'});
console.log('PASS: numeric/string telemetry, saved Base coordinates, invalid/missing values, zero, DOP sentinel, animation continues');

const q = vm.runInNewContext(`${helper}; getGnssQuality;`);
const pvt = {hacc_m: 4.08, source: 'ubx_nav_pvt', age_seconds: 1, satellites: 35, pdop: 1.23};
assert.equal(q({gnss_quality:pvt, server_received_at:100}, {}, true, 102).hacc_m, 4.08);
assert.equal(q({gnss_quality:{...pvt,source:'nmea_gst'}, timestamp:100}, {}, true, 102).hacc_m, 4.08);
assert.equal(q({gnss_quality:{...pvt, source:'unicore_bestnav_sigma', hacc_m:0.5}}, {}, true, 100).hacc_m, 0.5);
assert.equal(q({hacc:4080}, {}, true, 100), null, 'unknown legacy units are not guessed');
assert.equal(q({gnss_quality:pvt, timestamp:100}, {}, false, 100), null);
assert.equal(q({gnss_quality:pvt, timestamp:100}, {}, true, 146), null, 'client ages retained data too');
assert.equal(q({gnss_quality:{...pvt,fix_valid:false}, timestamp:100}, {}, true, 102).hacc_m, null);
assert.equal(q({}, {gst:{hacc:4.08,hacc_source:'gst_lat_lon_sigma',received_at:100}}, true, 102).hacc_m, 4.08);
assert.equal(q({}, {gst:{hacc:4.08,hacc_source:'gst_lat_lon_sigma',received_at:100}}, true, 116), null);
for (const invalid of [null, undefined, '', '4.08', NaN, Infinity, -1]) {
    assert.equal(q({gnss_quality:{...pvt,hacc_m:invalid}}, {}, true, 100).hacc_m, null);
}
assert(!source.includes('Waiting for RTK convergence'), 'Fixed Base must not show rover convergence advice');
console.log('PASS: PVT/GST metres, source/validity/age guards, legacy ambiguity and Fixed Base guidance');

const device = {server_received_at:10,gnss_quality:{...pvt,age_seconds:0},
    gnss_dop:{source:'ubx_nav_dop',age_seconds:0,hdop:0.85,pdop:1.20},
    base_config:{coords:{lat:'21.084989608',lon:'105.786656186'}}};
assert.deepEqual(render({gsa:{hdop:99.99,pdop:99.99}},false,device),
    {'db-lat':'21.08498961','db-lon':'105.78665619','db-dop':'0.85 / 1.20'});
assert.equal(render({gsa:{pdop:99.99}},false,{...device,gnss_dop:null})['db-dop'], '— / 1.23',
    'invalid GSA must not hide valid PVT PDOP');
assert.equal(render({gsa:{hdop:0.9,pdop:1.4,received_at:-10}},false,{})['db-dop'],'— / —');
const satellitesCode=source.slice(source.indexOf('function getSatelliteSystem('),source.indexOf('const DashboardPanel'));
const satelliteContext={Date:{now:()=>1e12},window:{}};
vm.runInNewContext(satellitesCode+';this.visible=getVisibleSatellites;this.positioned=hasSatellitePosition;',satelliteContext);
const signals=Array.from({length:21},(_,i)=>({system:'G',prn:i+1,snr:40,lastSeen:1e12,azimuth:0,elevation:0,has_geometry:false}));
signals.push({system:'G',prn:'G01',snr:45,lastSeen:1e12,azimuth:90,elevation:45,has_geometry:true});
signals.push({system:'G',prn:22,snr:42,lastSeen:1e12-8000});
const visible=satelliteContext.visible(signals,7000);
assert.equal(visible.length,21,'MSM4/MSM7/NMEA signals deduplicate by constellation and satellite');
assert.equal(visible.filter(satelliteContext.positioned).length,1,'unknown angles must not be plotted north');
assert.equal(visible.reduce((n,s)=>n+Number(s.snr),0)/visible.length,845/21);
assert(source.includes('Vệ tinh chip dùng') && source.includes('Vệ tinh giải mã'));
console.log('PASS: NAV-DOP beats GSA sentinels; PVT fallback; saved Base coordinates; 21 decoded vs 28 receiver uses distinct labels; satellite dedup/expiry/geometry');

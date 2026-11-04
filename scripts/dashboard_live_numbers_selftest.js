const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/dashboard-live.js'), 'utf8');
const helperStart = source.indexOf('function formatTelemetryNumber(');
const helper = helperStart < 0 ? '' : source.slice(helperStart, source.indexOf('function getLeaflet(', helperStart));
const satellitesCode=source.slice(source.indexOf('function getSatelliteSystem('),source.indexOf('function isDeviceStaleOffline('));
const animation = source.slice(source.indexOf('const animate = () => {'), source.indexOf('const handleResize = () => {', source.indexOf('const animate = () => {')));
assert(animation.includes('requestAnimationFrame(animate)'), 'exercise the actual dashboard animation loop');

function render(data, readonly = false, device = {}) {
    const nodes = Object.fromEntries(['db-lat', 'db-lon', 'db-dop'].map(id => [id, {textContent: ''}]));
    let scheduled = 0;
    vm.runInNewContext(`${helper}${satellitesCode}\n${animation}\nanimate();`, {
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
const satelliteContext={Date:{now:()=>1e12},window:{}};
vm.runInNewContext(helper+satellitesCode+';this.visible=getVisibleSatellites;this.counts=getDecodedSatelliteCounts;this.positioned=hasSatellitePosition;',satelliteContext);
const signals=Array.from({length:21},(_,i)=>({system:'G',prn:i+1,snr:40,lastSeen:1e12,azimuth:0,elevation:0,has_geometry:false}));
signals.push({system:'G',prn:'G01',snr:45,lastSeen:1e12,azimuth:90,elevation:45,has_geometry:true});
signals.push({system:'G',prn:22,snr:42,lastSeen:1e12-8000});
const visible=satelliteContext.visible(signals,7000);
assert.equal(visible.length,21,'MSM4/MSM7/NMEA signals deduplicate by constellation and satellite');
assert.equal(visible.filter(satelliteContext.positioned).length,1,'unknown angles must not be plotted north');
assert.equal(visible.reduce((n,s)=>n+Number(s.snr),0)/visible.length,845/21);
console.log('PASS: NAV-DOP beats GSA sentinels; PVT fallback; saved Base coordinates; 21 decoded vs 28 receiver uses distinct labels; satellite dedup/expiry/geometry');

const plotReader=vm.runInNewContext(`${helper}${satellitesCode};getSatellitePlotSource;`, {atob:s=>Buffer.from(s,'base64').toString('binary')});
const mixed={satellites:{a:{telemetry_source:'rtcm',prn:1},b:{telemetry_source:'nmea',prn:2},c:{talker:'RTCM',prn:3},d:{talker:'GP',prn:4}}};
const monitorPlot=plotReader({serial:'MONITOR_70737'}, {satellites:{a:{id:'RTCM:G:8',system:'G',prn:8,snr:42,telemetry_source:'nmea'}}},true,100);
assert.equal(monitorPlot.satellites.length,1,'RTCM identity survives a legacy status snapshot mislabeled NMEA');
const ingestionSource=fs.readFileSync(path.join(__dirname,'../cors_dashboard/src/js/main.js'),'utf8');
const handler=ingestionSource.slice(ingestionSource.indexOf('function handleWebSocketMessage('),ingestionSource.indexOf('\nfunction removeDeviceLocal('));
const ingestion=vm.createContext({Date,console,lastSatelliteGcTs:Date.now(),currentNmeaData:{satellites:{}},
  globalState:{selectedDeviceSerial:'MONITOR_70737',devices:[{serial:'MONITOR_70737'}],currentUser:{role:'admin'}},
  enqueueStatusUpdate:()=>{},requestDetailsPanelUpdate:()=>{}});
vm.runInContext(handler,ingestion);
vm.runInContext(`handleWebSocketMessage({type:'status_update',serial:'MONITOR_70737',data:{status:'online',nmea_data:{satellites:[{id:'RTCM:G:8',prn:8,system:'G',snr:42}]}}})`,ingestion);
assert.equal(ingestion.currentNmeaData.satellites['RTCM:G:8'].telemetry_source,'rtcm');
assert.equal(plotReader({serial:'MONITOR_70737'},ingestion.currentNmeaData,true).satellites.length,1);
const navDevice={gnss_satellites:{source:'ubx_nav_sat',payload_b64:'ignored'}};
assert.equal(plotReader(navDevice,mixed).source,'RTCM');
assert.equal(JSON.stringify(plotReader(navDevice,mixed).satellites.map(s=>s.prn)),JSON.stringify([1,3]));
assert.equal(plotReader(navDevice,mixed,false).satellites.length,0);
assert.equal(plotReader(navDevice,{satellites:{}}).satellites.length,0,'never substitute NAV-SAT when RTCM is empty');
console.log('PASS: RTCM-only plot excludes NMEA/NAV-SAT and has no receiver fallback');

const dopReader=vm.runInNewContext(`${helper};getGnssDop;`);
const timing={...device,gnss_quality:{...device.gnss_quality,fix_type:5},gnss_dop:{source:'ubx_nav_dop',age_seconds:0,hdop:99.99,pdop:99.99}};
assert.equal(dopReader(timing,{},true,10).timeOnly,true);
assert.equal(dopReader(timing,{},true,10).rawHdop,99.99);
assert.equal(render({},false,timing)['db-dop'],'Không áp dụng');
const rover={...timing,gnss_quality:{...timing.gnss_quality,fix_type:3}};
assert.equal(render({},false,rover)['db-dop'],'99.99 / 99.99','UBX values must not be discarded by an NMEA threshold');
assert.equal(dopReader({...rover,gnss_dop:{...rover.gnss_dop,hdop:655.35}}, {},true,10).hdop,655.35);
assert.equal(dopReader({...rover,gnss_dop:null},{},true,10).rawHdop,null);
console.log('PASS: verified Time-only mode is explained; original NAV-DOP values retained; high UBX DOP is not silently filtered');

const sourceSignals = [
    {system:'G',prn:1,snr:40,lastSeen:1e12,telemetry_source:'rtcm'},
    {system:'G',prn:1,snr:45,lastSeen:1e12,telemetry_source:'rtcm'},
    {system:'G',prn:'G01',snr:41,lastSeen:1e12,telemetry_source:'nmea'},
    {system:'G',prn:2,snr:40,lastSeen:1e12,talker:'RTCM'},
    {system:'G',prn:3,snr:40,lastSeen:1e12,id:'GP:3'},
    {system:'G',prn:4,snr:40,lastSeen:1e12-8000,telemetry_source:'nmea'},
    {system:'G',prn:5,snr:0,lastSeen:1e12,telemetry_source:'nmea'},
    {system:'G',prn:6,snr:40,lastSeen:1e12}
];
assert.equal(JSON.stringify(satelliteContext.counts(sourceSignals,7000)),JSON.stringify({rtcm:2,nmea:2}));
assert.equal(JSON.stringify(satelliteContext.counts(sourceSignals.filter(s=>s.telemetry_source==='rtcm'||s.talker==='RTCM'),7000)),JSON.stringify({rtcm:2,nmea:0}));
// Exercise the real WebSocket ingestion before counting, including an RTCM record with no talker/id.
const mainSource=fs.readFileSync(path.join(__dirname,'../cors_dashboard/src/js/main.js'),'utf8');
const socketCode=mainSource.slice(mainSource.indexOf('function handleWebSocketMessage('),mainSource.indexOf('async function apiFetch('));
const socketContext={Date:{now:()=>1e12},lastSatelliteGcTs:1e12,currentNmeaData:{satellites:{}},globalState:{selectedDeviceSerial:'test',devices:[]},requestDetailsPanelUpdate(){}};
vm.runInNewContext(socketCode+';this.receive=handleWebSocketMessage;',socketContext);
socketContext.receive({type:'rtcm_signal_update',serial:'test',data:{satellites:[{system:'G',prn:1,snr:42}]}});
socketContext.receive({type:'nmea_update',serial:'test',data:{type:'GSV',satellites:[{talker:'GP',prn:1,snr:40}]}});
assert.equal(JSON.stringify(satelliteContext.counts(Object.values(socketContext.currentNmeaData.satellites),7000)),JSON.stringify({rtcm:1,nmea:1}));
console.log('PASS: real RTCM/NMEA ingestion keeps sources separate; dedup within each source; same satellite counted in both; stale/zero/unknown excluded; RTCM-only NMEA=0');

// NAV-SAT supplies angles only for matching RTCM constellation + PRN.
const rawAngles=Buffer.alloc(8+3*12);rawAngles[4]=1;rawAngles[5]=3;
for(const [i,gnss,sv,az] of [[0,0,1,90],[1,6,1,180],[2,0,2,270]]){
    const o=8+i*12;rawAngles[o]=gnss;rawAngles[o+1]=sv;rawAngles[o+2]=55;
    rawAngles.writeInt8(45,o+3);rawAngles.writeInt16LE(az,o+4);
}
const angleDevice={timestamp:100,gnss_satellites:{source:'ubx_nav_sat',age_seconds:0,payload_b64:rawAngles.toString('base64')}};
const rtcmSat={telemetry_source:'rtcm',system:'G',prn:1,snr:37,lastSeen:100000,has_geometry:false};
const angleData={satellites:{gps:rtcmSat,glo:{...rtcmSat,system:'R',prn:1,snr:34},missing:{...rtcmSat,prn:3},nmea:{...rtcmSat,prn:2,telemetry_source:'nmea'}}};
const joined=plotReader(angleDevice,angleData,true,102).satellites;
assert.equal(joined.length,3,'NAV-only/NMEA-only satellites must not enter the RTCM set');
assert.equal(joined[0].azimuth,90);assert.equal(joined[1].azimuth,180,'same PRN in another constellation must not collide');
assert.equal(joined[0].snr,37,'keep RTCM C/N0 rather than NAV-SAT C/N0');
assert.equal(joined[0].lastSeen,100000,'NAV-SAT must not refresh RTCM observations');
assert.equal(joined[2].has_geometry,false,'no matching NAV-SAT means no invented angle');
assert.equal(plotReader(angleDevice,angleData,true,146).satellites[0].has_geometry,false,'expired angles not used');
assert.equal(plotReader({...angleDevice,gnss_satellites:{...angleDevice.gnss_satellites,payload_b64:'bad'}},angleData,true,102).satellites[0].has_geometry,false);
assert.equal(rtcmSat.has_geometry,false,'do not mutate decoder telemetry');
console.log('PASS: RTCM membership/CNO/time unchanged; NAV-SAT angles joined by constellation+PRN only; invalid/expired/missing angles ignored');

const inputBefore=JSON.stringify(angleData);
const chipBefore=JSON.stringify(angleDevice);
assert.equal(plotReader(angleDevice,{satellites:{}},true,102).satellites.length,0,'fresh NAV-SAT alone never creates RTCM observations');
assert.equal(plotReader(angleDevice,angleData,false,102).satellites.length,0,'offline never shows decoded observations');
const detached=plotReader(angleDevice,angleData,true,102).satellites;
detached[0].azimuth=12;
assert.equal(JSON.stringify(angleData),inputBefore,'angle join never mutates RTCM store');
assert.equal(JSON.stringify(angleDevice),chipBefore,'angle join never mutates chip snapshot');
const expired=plotReader(angleDevice,{satellites:{old:{...rtcmSat,lastSeen:1e12-8000}}},true,102).satellites;
assert.equal(satelliteContext.counts(expired,7000).rtcm,0,'fresh chip angles never revive expired RTCM');
console.log('PASS: NAV-SAT-only, offline, input isolation and expired RTCM remain separate');

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const window = {};
const context = {window};
vm.runInNewContext(fs.readFileSync('cors_dashboard/src/js/receiver-coordinates.js','utf8'), context);
const source = fs.readFileSync('cors_dashboard/src/js/stations-map.js','utf8');
const read = (name, next) => source.slice(source.indexOf(`    function ${name}(`), source.indexOf(`    function ${next}(`));
vm.runInNewContext(read('toNumber','pointInRing') + read('getCoords','effectiveStatus') + '\nwindow.getCoords=getCoords;',context);
const position = {state:'ok', position_type:'SINGLE', raw:'#BESTNAVA,97,GPS,FINE;SOL_COMPUTED,SINGLE,21.08496923000,105.78674200000,-9.9061,0.0000,WGS84,0*12345678'};
const device = {serial:'esp_TEST',status:'online',base_config:{base_setup_method:'SURVEY_IN',coords:{lat:21.074038,lon:105.777101}},
 system_info:{receiver_base_readback:{state:'ok',age_seconds:20,bestpos:position}}};
let coords=window.getCoords(device);
assert.equal(coords.lat,21.08496923);
assert.equal(coords.lon,105.786742);
assert.match(coords.source,/SINGLE/);
assert.equal(window.CorsReceiverCoordinates.fromReceiver(position),null,'An instantaneous SINGLE fix cannot be copied as the surveyed base');
position.position_type='FIXEDPOS';position.raw=position.raw.replace('SINGLE','FIXEDPOS');
coords=window.getCoords(device);assert.equal(coords.lat,21.08496923);assert.match(coords.source,/đã chốt/);
device.system_info.receiver_base_readback.age_seconds=61;
coords=window.getCoords(device);assert.equal(coords.lat,21.074038);assert.match(coords.source,/đã lưu/);
device.system_info.receiver_base_readback.age_seconds=2;device.status='offline';
assert.equal(window.getCoords(device).lat,21.074038);
device.status='online';position.raw=position.raw.replace('WGS84','UNKNOWN');
assert.equal(window.getCoords(device).lat,21.074038);
const legacy={base_config:{coords:{lat:20,lon:106}},status:'online'};
assert.equal(window.getCoords(legacy).lat,20);
console.log('PASS: station marker and popup prefer fresh receiver coordinates, distinguish SINGLE from completed Survey-In, and label saved fallback coordinates');

const helper = window.CorsReceiverCoordinates;
const fixedPosition = helper.fromReceiver({state:'ok', message:'UBX', position_type:'FIXEDPOS',
    datum:'WGS84', altitude_reference:'ELLIPSOID', lat:'21.33363526300', lon:'106.84699289000', alt:'39.1240'});
const fixedConfig = {base_setup_method:'FIXED_LLA', coords:{lat:'21.3336352637',lon:'106.8469928903',alt:'39.124'},accuracy:0.02};
assert.equal(helper.compareFixed(fixedPosition, fixedConfig, 'Ublox').matches,true,
    'Receiver encoding precision must not produce a false coordinate warning');
for (const key of ['lat','lon','alt']) {
    const changed = {...fixedConfig,accuracy:100,coords:{...fixedConfig.coords,[key]:Number(fixedConfig.coords[key])+0.01}};
    assert.equal(helper.compareFixed(fixedPosition,changed,'Ublox').matches,false,
        'Declared accuracy cannot hide a changed fixed coordinate');
}
assert.equal(helper.compareFixed(fixedPosition,{...fixedConfig,coords:{...fixedConfig.coords,alt:''}},'Ublox'),null);
assert.equal(helper.compareFixed(null,fixedConfig,'Ublox'),null);
assert.equal(helper.compareFixed(fixedPosition,{...fixedConfig,base_setup_method:'SURVEY_IN'},'Ublox'),null);
const umPosition = helper.fromReceiver({state:'ok',position_type:'FIXEDPOS',
    raw:'#BESTNAVA,97,GPS;SOL_COMPUTED,FIXEDPOS,21.33363526370,106.84699289030,30.0000,9.1240,WGS84,0*00000000'});
assert.equal(helper.compareFixed(umPosition,{...fixedConfig,altitude_reference:'ELLIPSOID'},'Unicorecomm').matches,true);
assert.equal(helper.compareFixed(umPosition,{...fixedConfig,altitude_reference:'LEGACY',coords:{...fixedConfig.coords,alt:30}},'Unicorecomm').matches,true);
assert.equal(helper.compareFixed(umPosition,{...fixedConfig,altitude_reference:'LEGACY'},'Unicorecomm').matches,false);
console.log('PASS: fixed coordinate match, receiver quantization, real mismatch, missing data and UM height references');

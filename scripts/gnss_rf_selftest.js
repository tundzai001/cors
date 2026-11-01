'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/dashboard-live.js'),'utf8');
const code = source.slice(source.indexOf('function getRfDiagnostic('), source.indexOf('function getLeaflet('));
const decode = vm.runInNewContext(`${code};getRfDiagnostic;`, {atob:s=>Buffer.from(s,'base64').toString('binary')});
const rf=Buffer.alloc(52);rf[1]=2;
for(let i=0;i<2;i++){const o=4+24*i;rf[o]=i;rf[o+1]=i+1;rf[o+2]=2;rf[o+3]=1;rf.writeUInt16LE(80+i,o+12);rf.writeUInt16LE(4000+i,o+14);rf[o+16]=12+i;}
const span=Buffer.alloc(548);span[1]=2;
for(let i=0;i<2;i++){const o=4+272*i;for(let bin=0;bin<256;bin++)span[o+bin]=bin===170?180:70+Math.round(10*Math.sin(bin/20));span.writeUInt32LE(25600000,o+256);span.writeUInt32LE(100000,o+260);span.writeUInt32LE(1575420000-i*350000000,o+264);span[o+268]=30;}
const device={serial:'TEST',timestamp:100,gnss_rf:{source:'ubx_mon_rf',age_seconds:0,payload_b64:rf.toString('base64')},gnss_spectrum:{source:'ubx_mon_span',age_seconds:0,payload_b64:span.toString('base64')}};
const reading=decode(device,'gnss_rf',true,102);
assert.equal(reading.blocks[0].noise,80);assert.equal(reading.blocks[1].agc,4001);assert.equal(reading.blocks[1].cw,13);assert.equal(reading.blocks[1].jamming,2);assert.equal(reading.age_seconds,2);
const spectrum=decode(device,'gnss_spectrum',true,102);
assert.equal(spectrum.blocks.length,2);assert.equal(spectrum.blocks[0].center,1575420000);assert.equal(spectrum.blocks[0].resolution,100000);assert.equal(spectrum.blocks[0].bins[170],180);assert.equal(spectrum.blocks[0].pga,30);
for(const field of ['gnss_rf','gnss_spectrum']){
    assert.equal(decode(device,field,false,102),null);assert.equal(decode(device,field,true,146),null);
    for(const value of [{...device[field],age_seconds:NaN},{...device[field],age_seconds:-1},{...device[field],source:'other'},{...device[field],payload_b64:'bad'},{...device[field],payload_b64:Buffer.alloc(2000).toString('base64')}])assert.equal(decode({...device,[field]:value},field,true,100),null);
}
const duplicate=Buffer.from(rf);duplicate[28]=0;
assert.equal(decode({...device,gnss_rf:{...device.gnss_rf,payload_b64:duplicate.toString('base64')}},'gnss_rf',true,102),null);
console.log('PASS: real RF decoder offsets, per-band states, spectrum bins/frequencies/PGA, stale/offline/invalid/duplicate frames');
if(process.argv[2])fs.writeFileSync(process.argv[2],JSON.stringify(device));

const decodeUm=vm.runInNewContext(`${code};getUnicoreRfDiagnostic;`);
const umDevice={serial:'UM_TEST',timestamp:100,gnss_rf:{source:'unicore_rf',agc:{source:'unicore_agca',age_seconds:0,values:[44,46,63,-1,-1,41,1,0,-1,-1]},jamming:{source:'unicore_freqjamstatusa',age_seconds:0,ratios:[255,10,0],flags:[2,1,0]}}};
assert.equal(decodeUm(umDevice,true,102).agc.values[5],41);
assert.equal(decodeUm(umDevice,true,102).jamming.flags[0],2);
assert.equal(decodeUm(umDevice,true,146),null);
assert.equal(decodeUm(umDevice,false,102),null);
assert.equal(decodeUm({...umDevice,gnss_rf:{...umDevice.gnss_rf,jamming:{...umDevice.gnss_rf.jamming,age_seconds:46}}},true,102).jamming,null);
assert.equal(decodeUm({...umDevice,gnss_rf:{...umDevice.gnss_rf,agc:{...umDevice.gnss_rf.agc,values:[256]}}},true,102).agc,null);
const overall={...umDevice,gnss_rf:{source:'unicore_rf',jamming:{source:'unicore_jamstatusa',age_seconds:0,ratios:[0],flags:[0]}}};
assert.equal(decodeUm(overall,true,102).jamming.flags.length,1);
assert.equal(decodeUm({...overall,gnss_rf:{source:'unicore_rf',jamming:{...overall.gnss_rf.jamming,flags:[3]}}},true,102),null);
console.log('PASS: UM AGC master/slave mapping, -1 invalid channels, per-band and overall CW, independent expiry and malformed rejection');
if(process.argv[3])fs.writeFileSync(process.argv[3],JSON.stringify(umDevice));

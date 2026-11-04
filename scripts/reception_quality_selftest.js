'use strict';
const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const source=fs.readFileSync('cors_dashboard/src/js/dashboard-live.js','utf8');
const code=source.slice(source.indexOf('function getReceiverSatellites('),source.indexOf('function getSatelliteTelemetrySource('));
const {getReceptionSample:sample,compareReception:compare,compareSpectrum:spectrum}=vm.runInNewContext(`${code};({getReceptionSample,compareReception,compareSpectrum})`,{atob:s=>Buffer.from(s,'base64').toString('binary')});
const nav=Buffer.alloc(8+5*12);nav[4]=1;nav[5]=5;
for(let i=0;i<5;i++){let o=8+i*12;nav[o+1]=i+1;nav[o+2]=40+i;nav.writeUInt32LE(i===4?0:11,o+8);}
const device={timestamp:100,gnss_satellites:{source:'ubx_nav_sat',age_seconds:0,payload_b64:nav.toString('base64')}};
const first=sample(device,true,100);assert.equal(first.median,41.5);assert.equal(first.tracking,4);assert.equal(first.used,4);
assert.equal(sample({...device,detected_chip_type:'Unicorecomm'},true,100),null); // Ignore UBX snapshots on UM stations.
assert.equal(sample({...device,detected_chip_type:'Ublox'},true,100).tracking,4);
assert.equal(sample(device,false,100),null);assert.equal(sample(device,true,146),null);
const next={...first,at:130,satellites:first.satellites.map(s=>({...s,cno:s.cno-4}))};
assert.equal(compare(next,first).delta,-4);assert.equal(compare({...next,at:161},first),null);
assert.equal(compare({...next,satellites:next.satellites.slice(1)},first).delta,null);assert.equal(compare({...next,satellites:next.satellites.slice(1)},first).lost,1);
const block={center:1575,span:128,resolution:0.5,pga:57,bins:Array(256).fill(100)};
assert.equal(spectrum(block,{...block,bins:Array(256).fill(90)}).median,10);assert.equal(spectrum(block,{...block,pga:56}),null);
console.log('PASS: reception median/tracking/used, expiry/offline, matched C/N₀ drop, insufficient overlap, gaps, lost tracking, equal-gain spectrum comparison');

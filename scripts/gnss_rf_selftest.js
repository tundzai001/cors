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
const legacy=decode({...device,gnss_rf:{...device.gnss_rf,payload_b64:duplicate.toString('base64')}},'gnss_rf',true,102);
assert.equal(legacy.blocks.length,2);assert.equal(legacy.blocks[1].id,0);assert.equal(legacy.blocks[1].index,1);
const highAgc=Buffer.from(rf);highAgc.writeUInt16LE(9000,18);const high=decode({...device,gnss_rf:{...device.gnss_rf,payload_b64:highAgc.toString('base64')}},'gnss_rf',true,102);assert.equal(high.blocks[0].agc,9000);assert.equal(high.blocks[0].agc_valid,false);
console.log('PASS: real RF decoder offsets, per-band states, spectrum bins/frequencies/PGA, stale/offline/invalid frames, legacy duplicate IDs and raw AGC preservation');
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

const assess=vm.runInNewContext(`${code};getRfAssessment;`);
const healthy={...reading,blocks:reading.blocks.map(b=>({...b,jamming:1}))};
assert.equal(assess({live:true}).title,'Chưa đủ dữ liệu kết luận','spectrum alone must not pass RF assessment');
assert.equal(assess({live:true,rf:high}).title,'Chip báo có nhiễu');
assert.equal(assess({live:true,rf:healthy}).title,'Chưa ghi nhận nhiễu đáng kể');
assert.equal(assess({live:true,rf:{blocks:[{id:0,jamming:0,antenna:2}]}}).tone,'warning');
assert.equal(assess({live:true,rf:{blocks:[{id:0,jamming:3,antenna:2}]}}).tone,'danger');
assert.equal(assess({live:true,rf:{blocks:[{id:0,jamming:1,antenna:4}]}}).title,'Chip báo lỗi đường anten');
assert.equal(assess({live:false,rf:healthy}).title,'Chưa đủ dữ liệu kết luận');
assert.equal(assess({live:true,um:{agc:{values:[40]}}}).title,'Chưa đủ dữ liệu kết luận');
assert.equal(assess({live:true,um:{jamming:{flags:[0,0,0]}}}).title,'Chưa ghi nhận nhiễu CW');
assert.equal(assess({live:true,um:{jamming:{flags:[2,0,0]}}}).tone,'danger');
assert.equal(assess({live:true,rf:healthy,readError:{error_kind:'invalid_response'}}).title,'Chưa đủ dữ liệu kết luận');
const readError=vm.runInNewContext(`${code};getRfReadError;`);
assert.equal(readError({...device,gnss_rf:{source:'ubx_mon_rf',state:'read_error',error_kind:'timeout',error:'timeout',age_seconds:0}},null,true,102).error_kind,'timeout');
assert.equal(readError(device,null,true,102).error_kind,'invalid_response','data present but UI rejects must be distinguished');
assert.equal(readError(device,null,true,146),null);
console.log('PASS: evidence-based verdicts; missing/unknown/stale/error never clean; antenna fault, UM CW and MON-RF separated');

const blockLabel=vm.runInNewContext(`${code};getRfBlockLabel;`);
assert.equal(blockLabel(legacy.blocks[0],legacy.blocks),'RF 1 (ID 0)');
assert.equal(blockLabel(legacy.blocks[1],legacy.blocks),'RF 2 (ID 0)');
assert.equal(assess({live:true,rf:{...high,blocks:high.blocks.map(b=>({...b,jamming:1}))}}).title,'Chưa đủ dữ liệu kết luận');
assert.equal(assess({live:true,unicore:true}).title,'Chưa đủ dữ liệu kết luận');

const decodeReceiver=vm.runInNewContext(`${code};getReceiverDiagnostic;`, {atob:s=>Buffer.from(s,'base64').toString('binary')});
const ver=Buffer.alloc(160);ver.write('EXT CORE 1.00');ver.write('00190000',30);ver.write('FWVER=HPG 1.13',40);ver.write('PROTVER=27.12',70);ver.write('MOD=ZED-F9P',100);ver.write('GPS;GLO;GAL;BDS',130);
const receiverDevice={timestamp:100,gnss_receiver:{source:'ubx_mon_ver',payload_b64:ver.toString('base64'),age_seconds:0,itfm_enabled:false}};
const receiver=decodeReceiver(receiverDevice,true,102);
assert.equal(receiver.firmware,'HPG 1.13');assert.equal(receiver.protocol,'27.12');assert.equal(receiver.model,'ZED-F9P');assert.equal(receiver.hardware,'00190000');assert.equal(receiver.itfm_enabled,false);
assert.equal(decodeReceiver(receiverDevice,true,146),null);assert.equal(decodeReceiver(receiverDevice,false,102),null);
const unknownRf={blocks:[{id:0,index:0,jamming:0,antenna:1}]};
assert.match(assess({live:true,rf:unknownRf,receiver}).reason,/đang tắt/);
assert.match(assess({live:true,rf:unknownRf,receiver:{...receiver,itfm_enabled:true}}).reason,/đang bật/);
assert.match(assess({live:true,rf:unknownRf}).reason,/chưa đọc được/);
assert.equal(decodeReceiver({...receiverDevice,gnss_receiver:{...receiverDevice.gnss_receiver,payload_b64:'bad',itfm_enabled:0}},true,102).itfm_enabled,null);
assert.match(decodeReceiver({...receiverDevice,gnss_receiver:{...receiverDevice.gnss_receiver,payload_b64:Buffer.alloc(42).toString('base64')}},true,102).error,/không hợp lệ/);
console.log('PASS: MON-VER firmware/hardware/protocol/model, TTL and independent ITFM off/on/unknown verdicts');

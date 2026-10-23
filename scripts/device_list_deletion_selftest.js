const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('cors_dashboard/src/js/main.js', 'utf8');
const messages = [];
const context = vm.createContext({ Map, Set, Date, console, setTimeout: () => 1,
  clearTimeout() {}, scheduleRender() {}, window: { CorsUI: { confirmAction: async () => true } },
  globalState: { devices: [], stateVersion: 0, currentView: 'dashboard' },
  showSuccess: message => messages.push(message), showError: message => { throw new Error(message); },
  apiFetch: async () => ({ ok: false, status: 404 }) });
vm.runInContext(source.slice(source.indexOf('const STATUS_FLUSH_INTERVAL'), source.indexOf('function requestDetailsPanelUpdate')), context);
vm.runInContext(source.slice(source.indexOf('function updateGlobalDevices'), source.indexOf('function handleSelectDevice')), context);
vm.runInContext(source.slice(source.indexOf('function removeDeviceFromList'), source.indexOf('async function handleResetDevice')), context);
function run(code) { return vm.runInContext(code, context); }
(async () => {
  run(`updateGlobalDevices([{serial:'LP_9896E5124B00'}, {serial:'esp_9896E5124B00'}]);
    globalState.selectedDeviceSerial = 'LP_9896E5124B00';
    enqueueStatusUpdate({serial:'LP_9896E5124B00', status:'awaiting_license'});`);
  await run(`handleDeleteDevice('LP_9896E5124B00', '')`);
  run('flushPendingStatusUpdates()');
  assert.deepEqual(Array.from(context.globalState.devices, d => d.serial), ['esp_9896E5124B00']);
  assert.equal(context.globalState.selectedDeviceSerial, null);
  assert(messages[0].includes('Đã cập nhật danh sách'));
  run(`enqueueStatusUpdate({serial:'esp_9896E5124B00', status:'online'});
    updateGlobalDevices([], true); flushPendingStatusUpdates();`);
  assert.equal(context.globalState.devices.length, 0, 'Authoritative snapshot removes stale stations and pending status');
  run(`updateGlobalDevices([{serial:'PI_0bd46c72e7be'}, {serial:'pi_8893150dff03'}, {serial:'LP_001122334455'}]);`);
  assert.deepEqual(Array.from(context.globalState.devices, d => d.serial), ['LP_001122334455']);
  console.log('PASS: 404 removes stale card, queued status cannot resurrect deletion, reload reconciles list, PI blocked');
})().catch(error => { console.error(error); process.exitCode = 1; });

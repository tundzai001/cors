// Regressions: live heartbeats with disabled outputs, backend restart, truncated legacy config.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const window = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/dashboard-shared.js'), 'utf8'), { window, console });
const status = window.CorsDashboardShared.getMapEffectiveStatus;
const live = { serial: 'PI', status: 'online', service_config: { password1: 'new-secret' },
  ntrip_stats: { rtcm_input_bps: 1400, server1_bps: 0, server2_bps: 0 } };
assert.equal(status(live), 'online', 'No caster config must not make a live Pi offline');
assert.equal(status({ ...live, status: 'sleep' }), 'sleep');
assert.equal(status({ ...live, status: 'offline' }), 'offline', 'Real offline remains offline');
assert.equal(status({ ...live, service_config: { server1_enabled: false } }), 'online');
assert.equal(status({ ...live, service_config: { server1_enabled: true, serverhost1: 'caster', port1: 2101,
  mountpoint1: 'BASE' }, ntrip_status: { server1: { connected: false, bps: 0 } } }), 'online',
  'Disconnected output does not mean the agent is sleeping/offline');


const source = fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/main.js'), 'utf8');
const context = vm.createContext({ Map, Set, Date, window, console, setTimeout: () => 1,
  clearTimeout() {}, scheduleRender() {}, globalState: { devices: [], stateVersion: 0, currentView: 'dashboard' } });
vm.runInContext(source.slice(source.indexOf('const STATUS_FLUSH_INTERVAL'), source.indexOf('function requestDetailsPanelUpdate')), context);
vm.runInContext(source.slice(source.indexOf('function updateGlobalDevices'), source.indexOf('function handleSelectDevice')), context);
function send(payload) { context.input = payload; vm.runInContext('enqueueStatusUpdate(input); flushPendingStatusUpdates();', context); }
const saved = { server1_enabled: true, serverhost1: 'caster', port1: 2101, mountpoint1: 'BASE',
  username1: 'source', password1: 'saved-secret' };
send({ serial: 'PI', status: 'offline', status_revision: 800, server_received_at: 1000,
  service_config: saved });
send({ ...live, timestamp: 1010, server_received_at: 1010, status_revision: 1, is_synced: false });
let device = context.globalState.devices[0];
assert.equal(device.status, 'online', 'Fresh heartbeat after revision reset must replace offline');
assert.equal(device.timestamp, 1010);
assert.deepEqual(JSON.parse(JSON.stringify(device.service_config)), saved, 'Out-of-sync legacy status must not erase desired config');
send({ serial: 'PI', status: 'offline', server_received_at: 1005, status_revision: 900 });
assert.equal(context.globalState.devices[0].status, 'online', 'Delayed older offline must not win');
send({ serial: 'PI', status: 'sleep', server_received_at: 1010, status_revision: 2 });
assert.equal(context.globalState.devices[0].status, 'sleep', 'Same timestamp uses revision ordering');
send({ serial: 'PI', status: 'online', server_received_at: 1020, status_revision: 3,
  is_synced: true, service_config: { server1_enabled: true } });
assert.equal(context.globalState.devices[0].service_config.password1, 'saved-secret', 'Redacted telemetry retains loaded credentials');
console.log('PASS: live Pi without caster, real offline, revision reset, delayed offline, saved config preservation');

const React = {
  memo: value => value,
  useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}],
  useEffect() {}, useRef: value => ({ current: value }), useMemo: fn => fn(),
  createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
};
const renderContext = vm.createContext({ window, React, console: { log() {}, warn() {} }, Date, setInterval, clearInterval });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../cors_dashboard/frontend/js/dist/dashboard-detail.min.js'), 'utf8'), renderContext);
const tree = window.DetailsPanel({ device: { ...live, timestamp: Date.now() / 1000 },
  currentUser: { role: 'admin' }, nmeaData: {} });
function allNodes(node) {
  if (Array.isArray(node)) return node.flatMap(allNodes);
  if (!node || typeof node !== 'object') return [];
  return [node, ...allNodes(node.children)];
}
const badges = allNodes(tree).filter(node => node.type === 'span' && node.props.className?.includes('badge'));
assert(badges.some(node => node.children.includes('ONLINE')), 'Actual station header must show ONLINE');
assert(!badges.some(node => node.children.includes('OFFLINE')), 'Actual station header must not show OFFLINE');
console.log('PASS: rendered station header is ONLINE for the reported damaged-config scenario');

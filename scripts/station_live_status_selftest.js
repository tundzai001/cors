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

const sleepingRelay = { ...live, detected_chip_type: 'RELAY',
  service_config: {server1_enabled:true, serverhost1:'aitogy.com.vn', port1:2101, mountpoint1:'BASE', server1_stream_on_demand:true, server1_stream_active:false},
  ntrip_status: {server1:{connected:true,bps:0}} };
assert.equal(status(sleepingRelay), 'sleep', 'Authenticated caster in standby must keep the station blue');
assert.equal(window.CorsDashboardShared.getServerTelemetry(sleepingRelay,1).sleep, true);
assert.equal(status({...sleepingRelay,ntrip_stream_state:{server1:{on_demand:true,active:true,sleep:false}}}), 'online', 'Runtime wake overrides saved sleep configuration');
assert.equal(status({...sleepingRelay,status:'offline'}), 'offline');
assert.equal(status({...sleepingRelay,ntrip_stats:{server1_bps:500}}), 'online', 'Actual outgoing RTCM is active');


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
const rfReading={source:'ubx_mon_rf',age_seconds:0,payload_b64:'sample'};
send({serial:'PI',status:'online',status_revision:4,server_received_at:1030,gnss_rf:rfReading,gnss_spectrum:rfReading,gnss_receiver:rfReading});
send({serial:'PI',status:'online',status_revision:0,server_received_at:1025,gnss_rf:null,gnss_spectrum:null,gnss_receiver:null});
assert.equal(context.globalState.devices[0].gnss_rf.payload_b64,'sample','Older DB snapshot must not erase newer RF telemetry');
send({serial:'PI',status:'online',status_revision:5,server_received_at:1040,gnss_rf:null,gnss_spectrum:null,gnss_receiver:null});
assert.equal(context.globalState.devices[0].gnss_rf,null,'Fresh null must clear expired RF');
assert.equal(context.globalState.devices[0].gnss_spectrum,null);
assert.equal(context.globalState.devices[0].gnss_receiver,null,'Fresh null clears receiver readback');
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

vm.runInContext(fs.readFileSync(path.join(__dirname, '../cors_dashboard/frontend/js/dist/dashboard-list.min.js'), 'utf8'), renderContext);
const relayTree = window.DeviceListItem({device:{...sleepingRelay,timestamp:Date.now()/1000},currentUser:{role:'admin'}});
const relayNodes = allNodes(relayTree);
assert(relayNodes.some(node => node.props.className === 'status-bar sleep'), 'Sleeping relay card rail must be blue');
assert(relayNodes.some(node => node.props.className === 'server-status-item sleep'), 'Sleeping relay output tile must use the same blue state');
console.log('PASS: actual relay card renders matching sleep classes for rail and S1');

for (const phase of ['auto_setup_rover', 'auto_setup_wait_fix', 'auto_setup_averaging', 'auto_setup_base']) {
  const configuring = {...live, serial:'esp_TEST', status:phase, timestamp:Date.now()/1000,
    service_config:{server1_enabled:true,serverhost1:'caster',port1:2101,mountpoint1:'BASE'},
    ntrip_status:{server1:{connected:false,bps:0}}};
  assert.equal(status(configuring), phase, 'Auto Base remains reachable with source output paused');
  const nodes = allNodes(window.DeviceListItem({device:configuring,currentUser:{role:'admin'}}));
  assert(nodes.some(node=>node.props.className === `status-bar ${phase}`));
  assert(!nodes.some(node=>node.props.className === 'status-bar offline'));
  assert(nodes.some(node=>node.props.className === 'badge bg-info'));
  assert.equal(status({...configuring,timestamp:Date.now()/1000-240}), 'offline', 'Stale Auto Base must still go offline');
}
console.log('PASS: all Auto Base stages keep their card status while paused S1 and real stale offline remain distinct');

let selectedSerial;
let deletedSerial;
const controls = allNodes(window.DeviceListItem({
  device: {...live, timestamp: Date.now()/1000}, currentUser: {role: 'admin'}, isSelected: true,
  onSelect: serial => { selectedSerial = serial; }, onDelete: serial => { deletedSerial = serial; },
}));
const select = controls.find(node => node.type === 'button' && node.props.className.includes('stretched-link'));
const remove = controls.find(node => node.type === 'button' && node.props.title === 'Xóa trạm');
assert.equal(select.props['aria-pressed'], true);
select.props.onClick();
assert.equal(selectedSerial, live.serial);
assert.equal(deletedSerial, undefined);
remove.props.onClick({stopPropagation() {}});
assert.equal(deletedSerial, live.serial);
console.log('PASS: native station selection and deletion are separate controls');

// Offline takes precedence over saved sleep/output telemetry on every surface.
const staleSleep = { ...sleepingRelay, status: 'sleep', timestamp: Date.now() / 1000 - 240 };
assert.equal(status(staleSleep), 'offline');
assert.equal(status({ ...staleSleep, timestamp: staleSleep.timestamp * 1000 }), 'offline');
assert.equal(status({ ...live, status: 'offline', detected_chip_type: 'VIRTUAL' }), 'offline');
const staleHeader = allNodes(window.DetailsPanel({ device: staleSleep, currentUser: { role: 'admin' }, nmeaData: {} }));
assert(staleHeader.some(node => node.children.includes('OFFLINE')));
assert(!staleHeader.some(node => node.props.className === 'badge bg-info' && node.children.includes('CHẾ ĐỘ NGỦ')));

renderContext.Image = class Image {};
vm.runInContext(fs.readFileSync(path.join(__dirname, '../cors_dashboard/frontend/js/dist/dashboard-live.min.js'), 'utf8'), renderContext);
function diagnostics(device) {
  const nodes = allNodes(window.DashboardPanel({ device, nmeaData: {} }));
  const section = nodes.find(node => node.children.some(child => child?.children?.includes('Antenna Quality Diagnostics')));
  assert(section, 'Diagnostics section must render');
  return allNodes(section).flatMap(node => node.children.filter(child => typeof child === 'string')).join(' ');
}
for (const rawStatus of ['online', 'sleep', 'offline']) {
  const assessment = diagnostics({ ...sleepingRelay, status: rawStatus, timestamp: Date.now()/1000,
    antenna_status: { state: 'FAULT', message: 'Agent detected antenna cable fault' } });
  assert(assessment.includes('Agent detected antenna cable fault'), rawStatus + ' must retain agent assessment');
  assert(!assessment.includes('tạm dừng'));
  assert.equal(assessment.includes('dữ liệu đánh giá lần cuối'), rawStatus === 'offline');
}
assert(diagnostics({ ...staleSleep, system_info: { antenna_status: { state: 'OK' } } }).includes('Ăng-ten hoạt động bình thường.'));
assert(diagnostics({ ...live, timestamp: Date.now()/1000 }).includes('Chưa có dữ liệu GNSS/RTCM để đánh giá.'));
assert(!diagnostics({ ...live, timestamp: Date.now()/1000, antenna_status: { state: 'OK' } }).includes('0 Sats'));
console.log('PASS: stale sleep and virtual offline, agent assessment in all states, persisted assessment and missing-data fallback');

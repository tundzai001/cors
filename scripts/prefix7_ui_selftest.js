const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
// These immutable URLs were already cached before prefix-7 support shipped.
const html = fs.readFileSync(path.join(__dirname, '../cors_dashboard/frontend/index.html'), 'utf8');
const cachedVersions = {
  shared: '20261009-device-lock-v2', list: '20261018-base-types-version-v2',
  live: '20261017-base-brand-v1', detail: '20261018-base-types-version-v2',
};
for (const [name, previous] of Object.entries(cachedVersions)) {
  const url = html.match(new RegExp(`dashboard-${name}\\.min\\.js\\?v=([^"&]+)`));
  assert.ok(url, `Dashboard ${name} must have a versioned URL`);
  assert.notEqual(url[1], previous, `Dashboard ${name} must invalidate pre-4G browser cache`);
}
const window = {};
const React = {
  memo: fn => fn, useState: value => [typeof value === 'function' ? value() : value, () => {}],
  useEffect() {}, useRef: value => ({current: value}), useMemo: fn => fn(),
  createElement: (type, props, ...children) => ({type, props: props || {}, children}),
};
const context = vm.createContext({React, window, console, Date, setInterval, clearInterval});
for (const name of ['dashboard-shared', 'dashboard-list', 'dashboard-detail']) {
  vm.runInContext(fs.readFileSync(path.join(__dirname, `../cors_dashboard/frontend/js/dist/${name}.min.js`), 'utf8'), context);
}
const collect = node => Array.isArray(node) ? node.flatMap(collect) :
  node && typeof node === 'object' ? [node, ...collect(node.children)] : [];
const text = node => Array.isArray(node) ? node.map(text).join('') :
  node && typeof node === 'object' ? text(node.children) : node == null || typeof node === 'boolean' ? '' : String(node);
const station = {serial: 'MONITOR_70001', name: '70001', status: 'online', timestamp: Date.now() / 1000,
  is_synced: true, bps: 512, base_config: {}, service_config: {mountpoint1: '70001'},
  system_info: {auth_status: {server_token_present: false, token_synced: false, agent_token_present: false}}};
for (const status of ['online', 'sleep', 'starting', 'offline', 'awaiting_license', 'unprovisioned']) {
for (const role of ['admin', 'coordinator', 'viewer']) {
  for (const serial of ['MONITOR_70001', 'esp_112233445566']) {
    const device = {...station, serial, status, user_id: 7};
    const tree = window.DetailsPanel({device, currentUser: {id: 7, role}, nmeaData: {}});
    const buttons = collect(tree).filter(n => n.type === 'button');
    assert.deepEqual(buttons.map(text), ['Overview'], `${role}: Overview is the only control`);
    assert.match(text(tree), /Base 4G · Chỉ xem/);
    assert.doesNotMatch(text(tree), /CHỜ LICENSE|Kích hoạt|Khởi động lại|Admin Control|SLEEP|NGỦ|CHỜ RTCM|ĐANG KHỞI/);
    const card = window.DeviceListItem({device, currentUser: {id: 7, role}});
    assert.ok(!collect(card).some(n => n.props.title === 'Xóa trạm'));
    assert.doesNotMatch(text(card), /CHỜ LICENSE|SLEEP|NGỦ|CHỜ RTCM|ĐANG KHỞI/);
    assert.match(text(card), status === 'online' ? /ONLINE/ : /OFFLINE/, 'Every mobile card shows an explicit binary status');
  }
}
}
const readonlyHelper = window.CorsDashboardShared.isReadonly4gBase;
delete window.CorsDashboardShared.isReadonly4gBase;
const resumedCard = window.DeviceListItem({device:{...station,service_config:undefined},currentUser:{role:'admin'}});
assert.doesNotMatch(text(resumedCard), /CHỜ LICENSE|SLEEP|NGỦ|CHỜ RTCM/);
assert.ok(!collect(resumedCard).some(n => n.props.title === 'Xóa trạm'));
window.CorsDashboardShared.isReadonly4gBase = readonlyHelper;
assert.equal(window.CorsDashboardShared.isReadonly4gBase({serial:'MONITOR_HANOI',service_config:{mountpoint1:'HANOI'}}), false);
assert.equal(window.CorsDashboardShared.isReadonly4gBase({serial:'esp_112233445566',service_config:{mountpoint1:'ESP_TEST'}}), false);
console.log('PASS: prefix-7 devices have only Overview for all roles, no editing/license/deletion; legacy stations preserved');

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const window = {};
const React = {
  memo: value => value,
  useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}],
  useEffect: () => {},
  useRef: value => ({ current: value }),
  useMemo: fn => fn(),
  createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
};
const context = vm.createContext({ window, React, console: { log() {}, warn() {} }, Date, setInterval, clearInterval });
const dist = path.join(__dirname, '../cors_dashboard/frontend/js/dist');
for (const name of ['dashboard-shared', 'dashboard-detail']) {
  vm.runInContext(fs.readFileSync(path.join(dist, `${name}.min.js`), 'utf8'), context);
}
const device = {
  serial: 'test-device', name: 'Test station', status: 'awaiting_license',
  timestamp: Date.now() / 1000, base_config: {}, service_config: {},
  system_info: { auth_status: {
    token_synced: true, server_token_present: true, agent_token_present: true,
    needs_token_deploy: false, token_deployment_pending: false,
    agent_reported_status: 'awaiting_license',
  } },
};
function collect(tree, result = []) {
  if (Array.isArray(tree)) tree.forEach(node => collect(node, result));
  else if (tree && typeof tree === 'object') {
    result.push(tree);
    collect(tree.children, result);
  }
  return result;
}
function render(value) {
  return window.DetailsPanel({ device: value, currentUser: { role: 'admin' }, nmeaData: {} });
}
function hasLicenseTab(tree) {
  return collect(tree).some(node => node.type === 'button' &&
    node.children.some(child => child === 'License'));
}
assert.equal(window.CorsDashboardShared.isAccessTokenSynced(device), true);
assert.equal(window.CorsDashboardShared.getMapEffectiveStatus(device), 'awaiting_agent');
const syncedTree = render(device);
assert.equal(hasLicenseTab(syncedTree), false, 'Synced token must hide license tab');
assert(JSON.stringify(syncedTree).includes('CHỜ AGENT'), 'Runtime wait must remain visible');
for (const change of [{ token_synced: false }, { token_deployment_pending: true },
                      { needs_token_deploy: true }, { agent_token_present: false }]) {
  const pending = { ...device, system_info: { auth_status: { ...device.system_info.auth_status, ...change } } };
  assert.equal(window.CorsDashboardShared.isAccessTokenSynced(pending), false);
  assert.equal(hasLicenseTab(render(pending)), true, 'Unconfirmed token must retain recovery tab');
}
assert.equal(window.CorsDashboardShared.getMapEffectiveStatus({ ...device, status: 'offline' }), 'offline');
const discovered = { ...device, status: 'awaiting_license', system_info: { auth_status: {
  server_token_present: false, token_synced: false, agent_token_present: true,
  needs_token_deploy: true, token_check_available: true, license_bypass: false,
} } };
assert.equal(hasLicenseTab(render(discovered)), true, 'MQTT discovery with a local token must show server registration');
if (process.argv[2]) {
  const escape = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
  const html = tree => {
    if (Array.isArray(tree)) return tree.map(html).join('');
    if (tree == null || typeof tree === 'boolean') return '';
    if (typeof tree !== 'object') return escape(tree);
    if (typeof tree.type !== 'string') return '';
    const attributes = Object.entries(tree.props).filter(([key, value]) =>
      !key.startsWith('on') && key !== 'style' && key !== 'children' && typeof value !== 'function')
      .map(([key, value]) => ` ${key === 'className' ? 'class' : key}="${escape(value)}"`).join('');
    return `<${tree.type}${attributes}>${html(tree.children)}</${tree.type}>`;
  };
  const cssRoot = path.resolve(dist, '../../css/dist');
  const styles = ['style', 'enterprise-theme', 'dashboard'].map(name =>
    `<link rel="stylesheet" href="file://${cssRoot}/${name}.min.css">`).join('');
  fs.writeFileSync(process.argv[2], `<!doctype html><html><head><meta charset="utf-8"><title>License UI verification</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">${styles}
    </head><body data-bs-theme="dark"><main style="max-width:1200px;margin:40px auto">${html(syncedTree)}</main></body></html>`);
}
console.log('PASS: confirmed token hides license tab; runtime wait, offline and token recovery preserved');

vm.runInContext(fs.readFileSync(path.join(dist, 'dashboard-forms.min.js'), 'utf8'), context);
(async () => {
  let attempted = false;
  const tree = window.ProvisionForm({
    device: { serial: 'PI_0bd46c72e7be', name: 'Ubuntu station' },
    currentUser: { role: 'admin' },
    onProvision: async () => { attempted = true; throw new Error('Already reported through toast'); },
  });
  const form = collect(tree).find(node => node.type === 'form');
  await assert.doesNotReject(form.props.onSubmit({ preventDefault() {} }));
  assert.equal(attempted, true);
  console.log('PASS: provisioning error does not escape as an unhandled promise');
})().catch(error => { console.error(error); process.exitCode = 1; });

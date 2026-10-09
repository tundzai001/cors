const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const values = [], effects = [];
let cursor = 0, changed = false;
const React = {
  memo: fn => fn,
  useState(initial) {
    const index = cursor++;
    if (!(index in values)) values[index] = initial;
    return [values[index], value => { values[index] = typeof value === 'function' ? value(values[index]) : value; changed = true; }];
  },
  useRef(initial) { const index = cursor++; return values[index] ||= { current: initial }; },
  useEffect(fn, deps) {
    const index = cursor++;
    if (!values[index] || deps.some((dep, i) => !Object.is(dep, values[index][i]))) {
      values[index] = deps; effects.push(fn);
    }
  },
  createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
};
const window = {};
const context = vm.createContext({ React, window, console });
const dist = path.join(__dirname, '../cors_dashboard/frontend/js/dist');
for (const name of ['dashboard-shared', 'dashboard-forms']) vm.runInContext(fs.readFileSync(path.join(dist, `${name}.min.js`), 'utf8'), context);
let sent;
let props = { currentUser: { role: 'admin' }, disabled: false,
  device: { serial: 'TEST', status: 'online', timestamp: Date.now() / 1000, is_synced: true,
    service_config: { server1_enabled: true, serverhost1: 'caster', port1: 2101, mountpoint1: 'BASE',
      stream_on_demand: false, stream_active: true, server1_stream_on_demand: false, server1_stream_active: true },
    ntrip_status: { server1: { connected: true, bps: 0, sleep: true } } },
  onConfigureService: async (serial, payload) => { sent = payload; } };
function render() {
  let tree;
  do { changed = false; cursor = 0; tree = window.ServiceConfigForm(props); while (effects.length) effects.shift()(); } while (changed);
  return tree;
}
function nodes(node) {
  if (Array.isArray(node)) return node.flatMap(nodes);
  if (!node || typeof node !== 'object') return [];
  return [node, ...nodes(node.children)];
}
function input(tree, name) { return nodes(tree).find(node => node.type === 'input' && node.props.name === name); }
(async () => {
  props.currentUser = { role: 'coordinator' };
  props.device.service_config = { ...props.device.service_config, server2_enabled: true,
    serverhost2: 'blocked.test', port2: 2102, mountpoint2: 'BLOCKED' };
  props.device.server_config_lock = Object.fromEntries(['serverhost1','port1','server1_enabled','serverhost2','port2','server2_enabled'].map(key => [key, props.device.service_config[key]]));
  props.device.server_config_lock.locked_servers = [2];
  props.device.system_info = {device_lock:{scoped:true,locked_servers:[2]}};
  let tree = render();
  for (const name of ['server1_enabled','server2_enabled','serverhost1','serverhost2','port1','port2']) {
    assert.equal(input(tree, name).props.disabled, true, `${name} locked for nonadmin`);
  }
  input(tree, 'server1_enabled').props.onChange({target:{name:'server1_enabled',type:'checkbox',checked:false}});
  tree = render();
  assert.equal(input(tree, 'server1_enabled').props.checked, true, 'Cannot turn off allowed S1');
  input(tree, 'mountpoint1').props.onChange({target:{name:'mountpoint1',type:'text',value:'RENAMED'}});
  tree = render(); await tree.props.onSubmit({preventDefault(){}});
  assert.equal(sent.mountpoint1, 'RENAMED');
  assert.equal(sent.server2_enabled, undefined);
  assert.equal(sent.server1_enabled, undefined);
  assert.equal(sent.mountpoint2, undefined);
  assert.equal(sent.serverhost2, undefined);
  assert.equal(sent.server1_stream_active, undefined);
  props.currentUser = {role:'admin'};
  tree = render();
  assert.equal(input(tree, 'server2_enabled').props.disabled, false, 'Admin can change policy');
  console.log('PASS: host fields locked, S2 blocked, S1 cannot be disabled, allowed mountpoint editable, locked fields omitted, admin override');
})().catch(error => { console.error(error); process.exitCode = 1; });

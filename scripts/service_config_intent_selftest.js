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
  let tree = render();
  assert.equal(input(tree, 'server1_stream_on_demand').props.checked, false);
  input(tree, 'server1_stream_on_demand').props.onChange({ target: { name: 'server1_stream_on_demand', type: 'checkbox', checked: true } });
  tree = render();
  assert.equal(input(tree, 'server1_stream_on_demand').props.checked, true);
  await tree.props.onSubmit({ preventDefault() {} });
  tree = render();
  assert.equal(sent.server1_stream_on_demand, true);
  assert.equal(sent.server1_stream_active, false);
  assert.equal(input(tree, 'server1_stream_on_demand').props.checked, true, 'Successful save must not reload pre-save Always-On props');
  props = { ...props, device: { ...props.device, service_config: { ...props.device.service_config, ...sent } } };
  tree = render();
  const text = JSON.stringify(tree);
  assert(text.includes('Đã kết nối Caster'), 'Authenticated caster indicator remains visible in sleep');
  assert(text.includes('Ngủ · chờ rover'), 'Sleep is shown separately from caster connection');
  assert.equal(input(tree, 'server1_stream_on_demand').props.checked, true);
  console.log('PASS: sleep selection survives save and source refresh; caster-connected + sleeping badges coexist');
})().catch(error => { console.error(error); process.exitCode = 1; });

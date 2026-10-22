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
  const addButton = tree => nodes(tree).find(node => node.type === 'button' && node.children.includes('Thêm NTRIP caster'));
  assert(!input(tree, 'serverhost3'));
  addButton(tree).props.onClick(); tree = render();
  assert(input(tree, 'serverhost3'));
  addButton(tree).props.onClick(); tree = render();
  assert(input(tree, 'serverhost4'));
  const change = (name, value, type = 'text') => {
    input(tree, name).props.onChange({ target: { name, type, value, checked: value } }); tree = render();
  };
  change('server3_enabled', true, 'checkbox');
  change('serverhost3', 'third.caster.test');
  change('port3', '2101'); change('mountpoint3', 'BASE3');
  change('password3', 'third-secret', 'password');
  change('server3_stream_on_demand', false, 'checkbox');
  await tree.props.onSubmit({ preventDefault() {} });
  assert.equal(sent.port3, 2101);
  assert.equal(sent.password3, 'third-secret');
  assert.equal(sent.server3_stream_active, true);
  assert.equal(sent.server4_enabled, false);
  props = { ...props, device: { ...props.device,
    service_config: { ...props.device.service_config, ...sent },
    ntrip_status: { server3: true }, ntrip_stats: { server3_bps: 512 } } };
  tree = render();
  assert.equal(input(tree, 'serverhost3').props.value, 'third.caster.test');
  assert(input(tree, 'serverhost4'));
  assert(JSON.stringify(tree).includes('Đang gửi RTCM · 512 B/s'));
  change('password3', '', 'password');
  await tree.props.onSubmit({ preventDefault() {} });
  assert(!('password3' in sent), 'Blank extra password must not erase stored credential');
  props = { ...props, currentUser: { role: 'viewer' } }; tree = render();
  assert.equal(input(tree, 'password3').props.value, '', 'Viewer must not hydrate extra secrets');
  if (process.argv[2]) {
    function html(node) {
      if (Array.isArray(node)) return node.map(html).join('');
      if (node == null || typeof node === 'boolean') return '';
      const escape = text => String(text).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
      if (typeof node !== 'object') return escape(node);
      const attrs = Object.entries(node.props).filter(([key, value]) => !key.startsWith('on') && !['key', 'style', 'children'].includes(key) && value != null && value !== false)
        .map(([key, value]) => ` ${key === 'className' ? 'class' : key === 'htmlFor' ? 'for' : key}="${escape(value)}"`).join('');
      return `<${node.type}${attrs}>${html(node.children)}</${node.type}>`;
    }
    const css = ['style', 'enterprise-theme', 'dashboard', 'ops-ui'].map(name => fs.readFileSync(path.join(__dirname, `../cors_dashboard/frontend/css/dist/${name}.min.css`), 'utf8')).join('\n');
    const bootstrap = process.argv[3] ? fs.readFileSync(process.argv[3], 'utf8') : '';
    fs.writeFileSync(process.argv[2], `<!doctype html><html lang="vi" data-bs-theme="dark"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${bootstrap}\n${css}</style><body class="p-3"><main style="max-width:1100px;margin:auto">${html(tree)}</main></body></html>`);
  }
  console.log('PASS: default 2 slots, add caster 3/4, save typed port, independent stream mode, reload, live bitrate, blank/password redaction');
})().catch(error => { console.error(error); process.exitCode = 1; });

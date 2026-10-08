const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Exercise the shipped form with a minimal hook runtime and fake credentials.
const slots = [], effects = [], deployments = [];
let index = 0, changed = false, tree;
const React = {
  memo: component => component,
  createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }),
  useState(initial) {
    const slot = index++;
    if (!(slot in slots)) slots[slot] = initial;
    return [slots[slot], value => {
      const next = typeof value === 'function' ? value(slots[slot]) : value;
      if (next !== slots[slot]) { slots[slot] = next; changed = true; }
    }];
  },
  useRef(initial) {
    const slot = index++;
    return slots[slot] ||= { current: initial };
  },
  useEffect(effect, dependencies) {
    const slot = index++;
    if (!slots[slot] || dependencies.some((value, i) => value !== slots[slot][i])) {
      slots[slot] = dependencies;
      effects.push(effect);
    }
  },
};
const context = vm.createContext({ React, window: {}, console });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../cors_dashboard/frontend/js/dist/dashboard-forms.min.js'), 'utf8'), context);
let device = { serial: 'LP_TEST', service_config: { server1_enabled: true, server2_enabled: true,
  serverhost1: 'caster.test', mountpoint1: 'TV869', password1: 'stored-secret', password2: 'backup-secret' } };
function render() {
  do {
    changed = false; index = 0;
    tree = context.window.ServiceConfigForm({ device, currentUser: { role: 'admin' },
      onConfigureService: async (serial, payload) => deployments.push({ serial, payload }) });
    effects.splice(0).forEach(effect => effect());
  } while (changed);
}
function find(node, predicate) {
  if (!node || typeof node !== 'object') return null;
  if (predicate(node)) return node;
  for (const child of node.children || []) { const result = find(child, predicate); if (result) return result; }
  return null;
}
const input = name => find(tree, node => node.type === 'input' && node.props.name === name);
const submit = async () => { await tree.props.onSubmit({ preventDefault() {} }); render(); };

(async () => {
  render();
  assert.equal(input('password1').props.value, '', 'Do not hydrate stored credentials into replacement fields');
  input('password1').props.onChange({ target: { name: 'password1', type: 'password', value: 'replacement-secret' } });
  render();
  device = { ...device, service_config: { server1_enabled: true, server2_enabled: true, mountpoint1: 'TV869' } };
  render();
  assert.equal(input('password1').props.value, 'replacement-secret', 'Realtime status must retain edits');
  await submit();
  assert.equal(deployments[0].payload.password1, 'replacement-secret');
  assert.equal(input('password1').props.value, '', 'Clear replacement input after save');
  await submit();
  for (const key of ['password1', 'password2', 'rtcmpassword1', 'command_pass']) {
    assert.ok(!(key in deployments[1].payload), 'Blank field must not erase ' + key);
  }
  input('password2').props.onChange({ target: { name: 'password2', type: 'password', value: 'unsaved-secret' } });
  render();
  device = { serial: 'LP_OTHER', service_config: { server1_enabled: true, mountpoint1: 'OTHER' } };
  render();
  assert.equal(input('password2').props.value, '', 'Do not carry secrets between stations');
  if (process.argv[2]) {
    const escape = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
    function html(node) {
      if (node == null || typeof node === 'boolean') return '';
      if (typeof node !== 'object') return escape(node);
      const attributes = Object.entries(node.props).filter(([key, value]) =>
        !key.startsWith('on') && key !== 'children' && key !== 'style' && value != null && value !== false)
        .map(([key, value]) => ` ${key === 'className' ? 'class' : key === 'htmlFor' ? 'for' : key}="${escape(value)}"`).join('');
      const content = node.children.map(html).join('');
      return `<${node.type}${attributes}>${content}</${node.type}>`;
    }
    const css = ['style', 'enterprise-theme', 'dashboard', 'ops-ui'].map(name =>
      fs.readFileSync(path.join(__dirname, `../cors_dashboard/frontend/css/dist/${name}.min.css`), 'utf8')).join('\n');
    fs.writeFileSync(process.argv[2], `<!doctype html><html lang="vi" data-bs-theme="dark"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"><style>${css}</style><body class="p-4"><main style="max-width:1100px;margin:auto">${html(tree)}</main></body></html>`);
  }
  console.log('Service password UI: passed (realtime edits, replacement, repeated save, station switch)');
})();

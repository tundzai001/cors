const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/rovers-global.js'), 'utf8');
const events = {}, calls = [], messages = [];
const button = { disabled: false, innerHTML: 'Đồng bộ caster',
  addEventListener(type, callback) { events[type] = callback; },
  setAttribute(key, value) { this[key] = value; }, removeAttribute(key) { delete this[key]; }, focus() {} };
const container = { innerHTML: '', querySelectorAll() { return []; } };
const fields = { 'mountpoints-container': container, 'sync-mountpoints-btn': button,
  'mountpoints-table-body': { querySelectorAll() { return []; } } };
const snapshot = { total: 1, last_synced_at: 1700000000,
  all: [{ mountpoint: 'SAVED.BASE', identifier: 'Saved station', host: 'caster.test', domain: 'caster.test', datacount: 123 }],
  by_host: { 'caster.test': [{}] }, by_domain: { 'caster.test': [{}] } };
let inventory = snapshot, releaseSync;
const context = vm.createContext({ console, Date, Number, API_HEADERS: {}, casterSourcesCache: {},
  escapeHtml: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
  showToast: (message, type) => messages.push({ message, type }),
  document: { getElementById: id => fields[id], querySelectorAll: () => [] },
  apiFetchRaw: async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.endsWith('/sync')) return new Promise(resolve => { releaseSync = result => resolve({ ok: true, json: async () => result }); });
    return { ok: true, json: async () => inventory };
  },
});
vm.runInContext(source.slice(source.indexOf('    async function loadAndRenderMountpoints()'), source.indexOf('    function showToast(')), context);
(async () => {
  await context.loadAndRenderMountpoints();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'GET');
  assert(container.innerHTML.includes('SAVED.BASE'));
  assert(container.innerHTML.includes('Đồng bộ caster'));
  assert(container.innerHTML.includes('Cập nhật:'));
  const pending = events.click();
  assert.equal(button.disabled, true);
  assert.equal(button['aria-busy'], 'true');
  assert(button.innerHTML.includes('Đang đồng bộ'));
  releaseSync({ synced_hosts: 0, results: [{ host: 'caster.test', error: 'offline' }] });
  await pending;
  assert.equal(calls[1].method, 'POST');
  assert.equal(calls[1].url, '/api/admin/caster-sources/sync');
  assert.equal(button.disabled, false);
  assert(!('aria-busy' in button));
  assert(container.innerHTML.includes('SAVED.BASE'), 'Failed caster sync keeps saved list visible');
  assert(messages.some(item => item.type === 'warning' && item.message.includes('0/1')));
  if (process.argv[2]) {
    const bootstrap = fs.readFileSync(process.argv[3], 'utf8');
    const styles = fs.readFileSync(path.join(__dirname, '../cors_dashboard/frontend/css/dist/rovers-global.min.css'), 'utf8');
    fs.writeFileSync(process.argv[2], `<!doctype html><html lang="vi" data-bs-theme="dark"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${bootstrap}\n${styles}</style><body class="ops-rovers-page"><main class="p-3"><div class="premium-table-card p-3"><h3 class="h5 text-white">Các mountpoint</h3>${container.innerHTML}</div></main></body></html>`);
  }
  inventory = { all: [], by_host: { 'caster.test': [] }, by_domain: {}, last_synced_at: null };
  await context.loadAndRenderMountpoints();
  assert(container.innerHTML.includes('Chưa có mountpoint đã lưu'));
  assert(container.innerHTML.includes('Tạo mountpoint'));
  assert(container.innerHTML.includes('Đồng bộ caster'));
  console.log('PASS: saved list, explicit sync POST, loading/disabled state, partial outage feedback, empty-state controls');
})().catch(error => { console.error(error); process.exitCode = 1; });

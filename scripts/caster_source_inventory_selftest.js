const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/rovers-global.js'), 'utf8');
const events = {}, calls = [], messages = [];
const button = { disabled: false, innerHTML: 'Đồng bộ caster',
  addEventListener(type, callback) { events[type] = callback; },
  setAttribute(key, value) { this[key] = value; }, removeAttribute(key) { delete this[key]; }, focus() {} };
let restoreConfirmed = true, restoreFails = false;
const restoreButton = { innerHTML: 'Khôi phục', disabled: false, dataset: {host:'caster.test',mountpoint:'SAVED.BASE'}, setAttribute(k,v){this[k]=v;}, removeAttribute(k){delete this[k];} };
const container = { innerHTML: '', querySelector() { return null; }, querySelectorAll(selector) { return selector === '.restore-mp' ? [restoreButton] : []; } };
const fields = { 'mountpoints-container': container, 'sync-mountpoints-btn': button,
  'mountpoints-table-body': { querySelectorAll() { return []; } } };
const snapshot = { total: 1, last_synced_at: 1700000000,
  all: [{ mountpoint: 'SAVED.BASE', identifier: 'Saved station', host: 'caster.test', domain: 'caster.test', datacount: 123, on_caster: false }],
  by_host: { 'caster.test': [{}] }, by_domain: { 'caster.test': [{}] } };
let inventory = snapshot, releaseSync, failInventory = false;
const context = vm.createContext({ console, Date, Number, API_HEADERS: {}, casterSourcesCache: {}, mountpointsRefreshInProgress: false,
  window: { CorsUI: { confirmAction: async () => restoreConfirmed } },
  escapeHtml: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
  showToast: (message, type) => messages.push({ message, type }),
  document: { getElementById: id => fields[id], querySelector: () => null, querySelectorAll: () => [] },
  apiFetchRaw: async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body });
    if (url.endsWith('/restore')) return { ok: !restoreFails, json: async () => restoreFails ? {detail:'Caster unavailable; retry'} : {status:'restored'} };
    if (url.endsWith('/sync')) return new Promise(resolve => { releaseSync = result => resolve({ ok: true, json: async () => result }); });
    return { ok: !failInventory, json: async () => inventory };
  },
});
vm.runInContext(source.slice(source.indexOf('    async function loadAndRenderMountpoints('), source.indexOf('    function showToast(')), context);
(async () => {
  await context.loadAndRenderMountpoints();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'GET');
  assert(container.innerHTML.includes('SAVED.BASE'));
  assert(container.innerHTML.includes('Đồng bộ caster'));
  assert(container.innerHTML.includes('Cập nhật:'));
  assert(container.innerHTML.includes('restore-mp'));
  restoreConfirmed = false;
  await restoreButton.onclick();
  assert.equal(calls.length, 1, 'Cancel restore makes no request');
  restoreConfirmed = true;
  await restoreButton.onclick();
  assert.equal(calls.at(-1).url, '/api/admin/caster-sources/restore');
  assert.equal(calls.at(-1).body, JSON.stringify({host:'caster.test',mountpoint:'SAVED.BASE'}));
  assert.equal(restoreButton.disabled, false);
  assert(!('aria-busy' in restoreButton));
  restoreFails = true;
  await restoreButton.onclick();
  assert(messages.some(item => item.type === 'danger' && item.message.includes('retry')));
  assert.equal(restoreButton.innerHTML, 'Khôi phục');
  restoreFails = false;
  calls.splice(1);

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
  // The Rover poll also refreshes the visible mountpoint tab using GET only.
  context.syncInProgress = false;
  context.currentUser = { role: 'admin' };
  context.fetchRovers = context.fetchCasterUsers = async () => {};
  context.renderTable = () => {};
  context.isAnyRoverModalOpen = () => false;
  fields['tab-mountpoints-content'] = { classList: { contains: () => true } };
  vm.runInContext(source.slice(source.indexOf('    async function syncData()'),
    source.indexOf('    async function refreshDataInPlace()')), context);
  inventory = {...snapshot, all: [{...snapshot.all[0], datacount: 456}], last_synced_at: 1700000100};
  let before = calls.length;
  await context.syncData();
  assert.equal(calls.length, before + 1);
  assert.equal(calls.at(-1).method, 'GET', 'Automatic refresh must not force Caster POST polling');
  assert(container.innerHTML.includes('456'), 'Data counters update without clicking sync');
  context.document.hidden = true;
  before = calls.length;
  await context.syncData();
  assert.equal(calls.length, before, 'Hidden browser tabs do not poll');
  context.document.hidden = false;
  fields['tab-mountpoints-content'].classList.contains = () => false;
  await context.syncData();
  assert.equal(calls.length, before, 'Other page tabs do not fetch mountpoints');
  fields['tab-mountpoints-content'].classList.contains = () => true;
  const saved = container.innerHTML;
  failInventory = true;
  await context.syncData();
  assert.equal(container.innerHTML, saved, 'Background failure preserves the visible saved table');
  failInventory = false;
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
  console.log('PASS: saved list, manual sync, automatic GET refresh, hidden/inactive guards, background failure preservation, empty state');
})().catch(error => { console.error(error); process.exitCode = 1; });

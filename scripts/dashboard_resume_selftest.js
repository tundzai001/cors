const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/main.js'), 'utf8');
const code = source.slice(source.indexOf('function connectWebSocket()'),
  source.indexOf("window.addEventListener('cors:session-expired'"));
const sockets = [], requests = [], updates = [], timers = [], intervals = [];
const windowEvents = {}, documentEvents = {};
let token = true, invalidated = false, now = 100000;
class Socket {
  static CONNECTING = 0; static OPEN = 1; static CLOSED = 3;
  constructor() { this.readyState = Socket.CONNECTING; sockets.push(this); }
  close() { this.readyState = Socket.CLOSED; this.onclose?.({ code: 1000 }); }
  open() { this.readyState = Socket.OPEN; this.onopen?.(); }
}
const context = vm.createContext({
  WebSocket: Socket, console: { warn() {}, error() {} },
  Date: { now: () => now }, Map,
  window: {
    location: { protocol: 'https:', host: 'base.aitogy.com' },
    ApiClient: { hasToken: () => token, isSessionInvalidated: () => invalidated,
      createAuthenticatedWebSocket: () => new Socket(), isAuthWebSocketClose: () => false },
    addEventListener: (name, callback) => { windowEvents[name] = callback; },
  },
  document: { hidden: false, addEventListener: (name, callback) => { documentEvents[name] = callback; } },
  setTimeout: (callback, delay) => { timers.push({ callback, delay }); return timers.length; },
  clearTimeout() {}, setInterval: callback => { intervals.push(callback); },
  apiFetch: (url, options) => new Promise(resolve => requests.push({ url, options, resolve })),
  updateConnectionStatus() {}, handleWebSocketMessage() {},
  enqueueStatusUpdate: values => updates.push(values),
});
vm.runInContext('let ws=null, wsReconnectAttempts=0, wsReconnectTimerId=null; const MAX_RECONNECT_ATTEMPTS=10; const lastStatusMetaBySerial=new Map();', context);
vm.runInContext(code, context);
const tick = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  context.connectWebSocket(); context.connectWebSocket();
  assert.equal(sockets.length, 1, 'Do not create duplicate connecting sockets');
  sockets[0].open();
  assert.equal(requests[0].url, '/api/devices');
  assert.equal(requests[0].options.cache, 'no-store');
  vm.runInContext('lastStatusMetaBySerial.set("newer", {revision: 2, receivedAt: 200});', context);
  requests[0].resolve({ ok: true, json: async () => [{ serial: 'newer', status: 'offline' }, { serial: 'fresh', status: 'online' }] });
  await tick();
  assert.deepEqual(Array.from(updates[0], item => item.serial), ['fresh'], 'Snapshot must not overwrite a newer heartbeat');

  documentEvents.visibilitychange();
  assert.equal(sockets[0].readyState, Socket.CLOSED);
  assert.equal(sockets.length, 2, 'Visible page must reconnect immediately');
  sockets[0].onclose({ code: 1006 });
  assert.equal(timers.length, 0, 'Old socket callbacks must not schedule reconnection');
  sockets[1].open();
  for (let i = 0; i < 12; i++) {
    sockets.at(-1).close();
    timers.at(-1).callback();
  }
  assert.equal(sockets.length, 14, 'Retries must continue beyond ten failures');
  assert.equal(timers.at(-1).delay, 30000, 'Backoff remains capped');

  now += 3600000; intervals[0]();
  assert.equal(sockets.length, 15, 'Timer gap must recover sleep even without a visibility event');
  sockets.at(-1).open();
  const request = requests.at(-1);
  request.resolve({ ok: true, json: async () => [{ serial: 'fresh', status: 'online' }] });
  await tick();
  assert.equal(updates.at(-1)[0].status, 'online');
  context.document.hidden = true; windowEvents.online();
  assert.equal(sockets.length, 15);
  context.document.hidden = false; invalidated = true; token = false;
  documentEvents.visibilitychange();
  assert.equal(sockets.length, 15, 'Do not reconnect after logout');
  console.log('PASS: wake reconnect, fresh snapshot, race protection, stale sockets, retry cap and logout');
})().catch(error => { console.error(error); process.exitCode = 1; });

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('cors_dashboard/src/js/api-client.js', 'utf8');
const built = fs.readFileSync('cors_dashboard/frontend/js/dist/api-client.min.js', 'utf8');

async function verify(code) {
    function storage() {
        const values = new Map();
        return { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
    }
    const events = {};
    let interval;
    let requests = [];
    let finishRequest;
    const document = { hidden: false, addEventListener: (name, fn) => { events[name] = fn; } };
    const window = {
        location: { pathname: '/security', origin: 'https://example.test', replace() {} },
        addEventListener: (name, fn) => { events[name] = fn; },
        dispatchEvent() {},
    };
    vm.runInNewContext(code, {
        window, document, localStorage: storage(), sessionStorage: storage(),
        navigator: { userAgent: 'test' }, AbortController, URL, CustomEvent: class {},
        setTimeout: () => 1, clearTimeout() {},
        setInterval: (fn, delay) => { assert.equal(delay, 15000); interval = fn; },
        fetch: (url, options) => { requests.push({ url, options }); return new Promise(resolve => { finishRequest = resolve; }); },
    });
    assert.equal(requests.length, 0, 'Guests never send a heartbeat');
    window.ApiClient.setToken('viewer-token');
    const pending = interval();
    await interval();
    assert.equal(requests.length, 1, 'No overlapping heartbeat requests');
    assert.equal(requests[0].url, '/api/auth/heartbeat');
    assert.equal(requests[0].options.method, 'POST');
    assert.equal(requests[0].options.headers.Authorization, 'Bearer viewer-token');
    finishRequest({ status: 204 });
    await pending;
    document.hidden = true;
    await interval();
    assert.equal(requests.length, 1, 'Hidden pages stop reporting presence');
    document.hidden = false;
    const wake = events.visibilitychange();
    assert.equal(requests.length, 2, 'Returning to a page reports immediately');
    finishRequest({ status: 204 });
    await wake;
    const reconnect = events.online();
    assert.equal(requests.length, 3, 'Network recovery reports immediately');
    finishRequest({ status: 403 });
    await reconnect;
    assert.equal(window.ApiClient.hasToken(), false, 'Blocked devices stop reporting');
    await interval();
    assert.equal(requests.length, 3);
}

(async () => {
    await verify(source);
    await verify(built);
    console.log('PASS: source and built heartbeat, guest/hidden/blocked gating, no overlaps, immediate resume');
})().catch(error => { console.error(error); process.exitCode = 1; });

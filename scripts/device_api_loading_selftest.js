'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '../cors_dashboard/frontend');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const dependencies = ['api-routes', 'api-client', 'device-api'];
function dependencyScripts(page) {
    const scripts = [...page.matchAll(/<script\b[^>]*src="([^"]+)"[^>]*>/g)].map(match => match[1]);
    const positions = dependencies.map(name => scripts.findIndex(src => src.startsWith(`/assets/js/dist/${name}.min.js?`)));
    assert(positions.every(index => index >= 0), 'Dashboard must load all DeviceApi dependencies');
    assert(positions[0] < positions[1] && positions[1] < positions[2], 'Load routes/client before DeviceApi');
    for (const consumer of ['dashboard-forms', 'dashboard-detail']) {
        assert(scripts.findIndex(src => src.includes(`/${consumer}.min.js?`)) > positions[2], `Load DeviceApi before ${consumer}`);
    }
    return positions.map(index => scripts[index]);
}
const scripts = dependencyScripts(html);
assert.throws(() => dependencyScripts(html.replace(/<script[^>]*src="[^"]*\/device-api\.min\.js[^"]*"[^>]*><\/script>/, '')));
const calls = [];
const context = vm.createContext({
    URL, AbortController, setTimeout, clearTimeout, setInterval: () => 0,
    document: { hidden: true, addEventListener() {} }, addEventListener() {},
    location: { origin: 'https://dashboard.test', pathname: '/dashboard' },
    localStorage: { getItem: key => key === 'access_token' ? 'test-token' : null },
    sessionStorage: { getItem: () => null },
    fetch: async (url, options) => {
        calls.push({ url, options });
        return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
});
context.window = context;
for (const src of scripts) {
    const file = path.join(root, src.replace('/assets/', '').split('?')[0]);
    vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
}
(async () => {
    const payloads = [
        ['VERIFY_GNSS_CONFIG', {}],
        ['CHECK_STORAGE_STATUS', { inspect: true, offset: 0 }],
        ['CLEAR_NVS_DATA', { group: 'unused_wifi', confirm: 'unused_wifi' }]
    ];
    for (const [command, payload] of payloads) {
        await context.DeviceApi.sendCommand('esp/test', command, payload);
        const call = calls.at(-1);
        assert.equal(call.url, '/api/devices/esp%2Ftest/command');
        assert.equal(call.options.method, 'POST');
        assert.equal(call.options.headers.Authorization, 'Bearer test-token');
        assert.deepEqual(JSON.parse(call.options.body), { command, payload });
    }
    assert.equal(calls.length, 3);
    console.log('PASS: dashboard script order, actual API bundles, authenticated GNSS/NVS commands and encoded serial');
})().catch(error => { console.error(error); process.exitCode = 1; });

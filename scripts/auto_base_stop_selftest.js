// Run with: node scripts/auto_base_stop_selftest.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('cors_dashboard/src/js/main.js', 'utf8');
const handler = source.slice(source.indexOf('async function handleStopAutoBase('), source.indexOf('async function handleReadBaseStatus('));
(async () => {
    const messages = [], requests = [];
    let ok = true;
    const context = vm.createContext({
        apiFetch: async (url, options) => {
            requests.push({url, body: JSON.parse(options.body)});
            return {ok, json: async () => ({detail:'Trạm không kết nối'})};
        },
        showSuccess: message => messages.push(message), showError: () => {}
    });
    vm.runInContext(handler, context);
    await context.handleStopAutoBase('test');
    assert.deepEqual(requests[0], {url:'/api/devices/test/command', body:{command:'STOP_AUTO_BASE', payload:{}}});
    assert.match(messages[0], /đang chờ thiết bị xác nhận/);
    ok = false;
    await assert.rejects(() => context.handleStopAutoBase('test'), /Trạm không kết nối/);
    assert.equal(messages.length, 1);
    console.log('PASS: stop sends STOP_AUTO_BASE, waits for device confirmation, and rejects HTTP errors');
})().catch(error => { console.error(error); process.exitCode = 1; });

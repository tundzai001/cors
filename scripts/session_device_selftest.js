const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/api-client.js'), 'utf8');
const deviceName = source.slice(source.indexOf('    function getDeviceName()'), source.indexOf('    function detectLocalIP'));
const cases = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/session-devices.json'), 'utf8'));
for (const item of cases) {
    const scope = {isBrave: item.brave || false, navigator:{userAgent:item.ua, maxTouchPoints:item.maxTouchPoints || 0}};
    vm.createContext(scope);
    vm.runInContext(deviceName, scope);
    assert.equal(scope.getDeviceName(), item.expected, item.ua);
}
console.log(`PASS: ${cases.length} mobile/desktop OS and browser labels`);

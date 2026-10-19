const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/rovers-global.js'), 'utf8');
const fields = {};
for (const prefix of ['new', 'edit']) {
    for (const name of ['expiry', 'expiry-count', 'expiry-unit', 'expiry-custom']) {
        fields[`${prefix}-${name}`] = {
            value: '', disabled: false, hidden: false,
            classList: { toggle(name, value) { this[name] = value; } },
            setCustomValidity(message) { this.error = message; },
            reportValidity() { this.reported = true; }
        };
    }
}
const context = vm.createContext({ document: { getElementById: id => fields[id] } });
vm.runInContext(source.slice(source.indexOf('    function syncExpiryFields('), source.indexOf("    for (const prefix of ['new', 'edit'])")), context);
for (const prefix of ['new', 'edit']) {
    const read = () => context.readExpiryDays(prefix);
    const select = fields[`${prefix}-expiry`];
    const count = fields[`${prefix}-expiry-count`];
    const unit = fields[`${prefix}-expiry-unit`];
    for (const days of [1, 7, 30, 180, 365, 3650]) {
        select.value = String(days);
        assert.equal(read(), days);
    }
    select.value = '';
    assert.equal(read(), null, 'Keep existing expiry');
    context.syncExpiryFields(prefix);
    assert.equal(count.disabled, true);
    assert.equal(fields[`${prefix}-expiry-custom`].classList['d-none'], true);
    select.value = 'custom';
    context.syncExpiryFields(prefix);
    assert.equal(count.disabled, false);
    assert.equal(unit.disabled, false);
    assert.equal(fields[`${prefix}-expiry-custom`].classList['d-none'], false);
    for (const [quantity, multiplier, days] of [[1, 1, 1], [10, 1, 10], [2, 7, 14], [3, 30, 90], [2, 365, 730]]) {
        count.value = String(quantity);
        unit.value = String(multiplier);
        assert.equal(read(), days);
    }
    for (const invalid of ['', '0', '-1', '1.5', 'NaN', 'Infinity', '99999999999999999']) {
        count.value = invalid;
        assert.equal(read(), undefined, `Reject invalid count: ${invalid}`);
        assert(count.error);
        assert(count.reported);
    }
    select.value = '3650';
    context.syncExpiryFields(prefix);
    assert.equal(read(), 3650, 'Ignore stale custom input for lifetime');
    assert.equal(count.disabled, true);
}
assert(source.includes('if (newExpiry !== null) payload.expiry_days = newExpiry;'));
assert(source.includes('expiry_days: expiryDays,'));
console.log('PASS: both forms, presets, custom days/weeks/months/years, keep expiry, lifetime and invalid input');

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Exercise the shipped form so unit changes cannot silently scale saved positions.
const slots = [], effects = [], sent = [];
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
    useEffect(effect, dependencies) {
        const slot = index++;
        if (!slots[slot] || dependencies.some((value, i) => value !== slots[slot][i])) {
            slots[slot] = dependencies;
            effects.push(effect);
        }
    },
};
const context = vm.createContext({ React, console, window: { showError(message) { throw Error(message); } } });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../cors_dashboard/frontend/js/dist/dashboard-forms.min.js'), 'utf8'), context);
let device;
function render() {
    do {
        changed = false;
        index = 0;
        tree = context.window.BaseConfigForm({ device, currentUser: { role: 'admin' },
            onConfigureChip: async (serial, payload) => sent.push(payload) });
        effects.splice(0).forEach(effect => effect());
    } while (changed);
}
function find(node, predicate) {
    if (!node || typeof node !== 'object') return null;
    if (predicate(node)) return node;
    for (const child of node.children || []) {
        const match = find(child, predicate);
        if (match) return match;
    }
    return null;
}
const accuracyInput = () => find(tree, node => node.props.id === `base-accuracy-${device.serial}`);
async function submit() {
    await tree.props.onSubmit({ preventDefault() {} });
    render();
    return sent.at(-1).params.accuracy;
}
(async () => {
    for (const [meters, centimeters] of [[0.005, '0.5'], [0.0123, '1.23'], [0.05, '5'], [0.1, '10'], [1, '100']]) {
        device = { serial: `TEST_${meters}`, detected_chip_type: 'Ublox', base_config: {
            base_setup_method: 'FIXED_LLA', accuracy: meters, coords: { lat: '20', lon: '105', alt: '2' } } };
        render();
        assert.equal(accuracyInput().props.value, centimeters, 'Load stored meters as centimeters');
        assert(Math.abs(await submit() - meters) < 1e-12, 'Saving existing accuracy must preserve its value');
    }
    const field = accuracyInput();
    assert(find(tree, node => node.type === 'label' && node.props.htmlFor === field.props.id));
    assert(find(tree, node => node.props.id === field.props['aria-describedby']));
    field.props.onChange({ target: { value: '5' } });
    render();
    assert.equal(await submit(), 0.05, '5 cm must send 0.05 m');
    accuracyInput().props.onChange({ target: { value: '0.5' } });
    render();
    assert.equal(await submit(), 0.005, '0.5 cm must preserve the old 5 mm setting');
    console.log('PASS: base accuracy centimeters, stored-value round trips, decimal input and accessible help');
})().catch(error => { console.error(error); process.exitCode = 1; });

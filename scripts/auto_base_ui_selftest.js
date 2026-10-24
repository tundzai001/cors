const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const slots = [], effects = [];
let index = 0, changed = false, tree;
const React = {
    memo: value => value,
    createElement: (type, props, ...children) => ({type, props: props || {}, children: children.flat(Infinity)}),
    useState(initial) {
        const slot = index++;
        if (!(slot in slots)) slots[slot] = initial;
        return [slots[slot], value => {
            const next = typeof value === 'function' ? value(slots[slot]) : value;
            if (next !== slots[slot]) { slots[slot] = next; changed = true; }
        }];
    },
    useEffect(effect, deps) {
        const slot = index++;
        if (!slots[slot] || deps.some((value, i) => value !== slots[slot][i])) {
            slots[slot] = deps; effects.push(effect);
        }
    }
};
const context = vm.createContext({React, console, window: {}});
vm.runInContext(fs.readFileSync('cors_dashboard/frontend/js/dist/dashboard-forms.min.js', 'utf8'), context);
let device = {serial: 'ESP_TEST', detected_chip_type: 'Ublox', status: 'auto_setup_wait_fix',
    base_config: {base_setup_method: 'AUTO_CORS'}, last_raw_gga: '$GNGGA,live,5*00',
    last_raw_gga_age: 0.5, last_gga_fix_status: 'RTK_FLOAT',
    auto_base_progress: {phase: 'wait_fix', fixed_streak: 0}};
function render() {
    do {
        index = 0; changed = false;
        tree = context.window.BaseConfigForm({device, currentUser: {role: 'admin'}});
        effects.splice(0).forEach(effect => effect());
    } while (changed);
}
function text(node) {
    if (node == null || typeof node === 'boolean') return '';
    if (typeof node !== 'object') return String(node);
    return (node.children || []).map(text).join(' ');
}
function find(node, predicate) {
    if (!node || typeof node !== 'object') return null;
    if (predicate(node)) return node;
    for (const child of node.children || []) { const result = find(child, predicate); if (result) return result; }
    return null;
}
render();
assert(text(tree).includes('$GNGGA,live,5*00'));
assert(text(tree).includes('RTK FLOAT'));
device = {...device, status: 'online', auto_base_progress: {phase: 'completed', sample_count: 54, updated_at: 1}};
render();
assert(text(tree).includes('Kết quả cũ chưa được xác nhận RTK Fixed'));
assert(!text(tree).includes('Auto Base hoàn tất:'));
device = {...device, auto_base_progress: {phase: 'completed', fixed_samples_only: true,
    sample_count: 60, updated_at: 2, averaged_coord: {lat: 21.123456789, lon: 105.123456789, alt: 12}}};
render();
assert(text(tree).includes('Auto Base hoàn tất: 60 mẫu RTK Fixed.'));
assert(find(tree, node => node.type === 'input' && node.props.readOnly && String(node.props.value).startsWith('21.123456789')));
assert(find(tree, node => node.props.role === 'status' && text(node).includes('Auto Base hoàn tất')));
console.log('PASS: live GGA/Float, reject unverified success, update completed coordinates, accessible result');

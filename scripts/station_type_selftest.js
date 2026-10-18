const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const values = [], effects = [];
let cursor = 0, changed = false;
const React = {
    memo: fn => fn,
    useState(initial) {
        const index = cursor++;
        if (!(index in values)) values[index] = typeof initial === 'function' ? initial() : initial;
        return [values[index], next => {
            const value = typeof next === 'function' ? next(values[index]) : next;
            if (!Object.is(value, values[index])) { values[index] = value; changed = true; }
        }];
    },
    useRef(initial) { const index = cursor++; return values[index] ||= {current: initial}; },
    useMemo(fn) { cursor++; return fn(); },
    useCallback(fn) { cursor++; return fn; },
    useEffect(fn, deps) {
        const index = cursor++;
        if (!values[index] || deps.some((value, i) => !Object.is(value, values[index][i]))) {
            values[index] = deps; effects.push(fn);
        }
    },
    createElement: (type, props, ...children) => ({type, props: props || {}, children}),
};
const window = {CorsDashboardShared: {
    isDeviceStaleOffline: device => device.status === 'offline',
    getMapEffectiveStatus: device => device.status,
}};
const context = vm.createContext({React, window, console, setInterval: () => 1, clearInterval() {}, setTimeout, clearTimeout});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../cors_dashboard/frontend/js/dist/dashboard-list.min.js'), 'utf8'), context);
const station = (serial, status = 'online') => ({serial, name: serial, status, is_synced: true});
let props = {devices: [station('1000000037a7201b'), station('LP_00E04C0CFB9E'), station('RELAY_ATG_DT'),
    station('esp_9896E5124B00'), station('ESP_000000000001', 'offline')],
    selectedDeviceSerial: 'esp_9896E5124B00', searchTerm: '', currentUser: {id: 1, role: 'admin'}, onSearch() {}};
function render() {
    let tree, rounds = 0;
    do {
        changed = false; cursor = 0;
        tree = window.DeviceList(props);
        while (effects.length) effects.shift()();
        assert.ok(++rounds < 10, 'No effect/render loop');
    } while (changed);
    return tree;
}
function nodes(node) {
    if (Array.isArray(node)) return node.flatMap(nodes);
    if (!node || typeof node !== 'object') return [];
    return [node, ...nodes(node.children)];
}
const cards = tree => nodes(tree).filter(node => node.type === window.DeviceListItem).map(node => node.props.device.serial);
const typeButton = (tree, key) => nodes(tree).find(node => node.type === 'button' && node.props.key === key
    && String(node.props.className).includes('dashboard-station-type'));
let tree = render();
assert.deepEqual(cards(tree), ['1000000037a7201b', 'LP_00E04C0CFB9E', 'RELAY_ATG_DT']);
assert.equal(typeButton(tree, 'fixed').props['aria-pressed'], true);
typeButton(tree, 'mobile').props.onClick(); tree = render();
assert.deepEqual(cards(tree), ['esp_9896E5124B00', 'ESP_000000000001']);
const toggle = nodes(tree).find(node => node.props['aria-controls'] === 'dashboard-station-filters');
toggle.props.onClick(); tree = render();
nodes(tree).find(node => node.type === 'button' && node.props.key === 'offline').props.onClick();
assert.deepEqual(cards(render()), ['ESP_000000000001']);
typeButton(render(), 'fixed').props.onClick();
assert.equal(cards(render()).length, 3, 'Switching type resets status filter');
props.selectedDeviceSerial = 'ESP_000000000001'; tree = render();
assert.equal(typeButton(tree, 'fixed').props['aria-pressed'], true, 'Selection does not override the fixed default or manual group');
typeButton(tree, 'mobile').props.onClick();

// The list receives only the authenticated API scope; it must never fetch or
// synthesize unassigned stations for coordinators.
props = {...props, currentUser: {id: 7, role: 'coordinator'}, devices: [station('esp_9896E5124B00')]};
assert.deepEqual(cards(render()), ['esp_9896E5124B00']);
typeButton(render(), 'fixed').props.onClick(); tree = render();
assert.deepEqual(cards(tree), []);
assert.ok(!nodes(tree).some(node => String(node.props.className).includes('skeleton')), 'Empty group is not a perpetual loading state');
console.log('PASS: fixed/mobile groups, prefix case, status filter, selected station, scoped coordinator list, empty group');

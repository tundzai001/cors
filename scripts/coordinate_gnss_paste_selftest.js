// Run after building: node scripts/coordinate_gnss_paste_selftest.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const slots = [], effects = [], sent = [];
let index = 0, changed = false, tree, disabled = false;
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
            slots[slot] = dependencies; effects.push(effect);
        }
    },
};
const context = vm.createContext({ React, console, window: { showError(message) { throw Error(message); } } });
for (const asset of ['coordinate-clipboard', 'dashboard-forms']) {
    vm.runInContext(fs.readFileSync(path.join(root, `cors_dashboard/frontend/js/dist/${asset}.min.js`), 'utf8'), context);
}
const clipboard = context.window.CorsCoordinateClipboard;
const device = { serial: 'PASTE_TEST', detected_chip_type: 'Ublox', base_config: {
    base_setup_method: 'FIXED_LLA', accuracy: 0.05, coords: { lat: '20', lon: '105', alt: '2' } } };
function render() {
    do {
        changed = false; index = 0;
        tree = context.window.BaseConfigForm({ device, disabled, currentUser: { role: 'admin' },
            onConfigureChip: async (_, payload) => sent.push(payload) });
        effects.splice(0).forEach(effect => effect());
    } while (changed);
}
function find(node, predicate) {
    if (!node || typeof node !== 'object') return null;
    if (predicate(node)) return node;
    for (const child of node.children || []) { const match = find(child, predicate); if (match) return match; }
    return null;
}
const field = name => find(tree, node => node.props.id === `base-${name}-${device.serial}`);
const position = () => ['latitude', 'longitude', 'altitude'].map(name => field(name).props.value);
function paste(name, text) {
    let prevented = false;
    field(name).props.onPaste({ clipboardData: { getData: () => text }, preventDefault() { prevented = true; } });
    render(); return prevented;
}
(async () => {
    render();
    assert.equal(clipboard.parse('105.8542'), null);
    assert.equal(clipboard.parse('105.8542\t21.0285\t30'), null, 'Do not guess unnamed coordinate order');
    const text = clipboard.encode({ lat: 20.845, lon: 106.688, alt: 30 });
    for (const name of ['latitude', 'longitude', 'altitude']) {
        assert.equal(paste(name, text), true);
        assert.deepEqual(position(), ['20.845000000', '106.688000000', '30.0000']);
        assert.equal(sent.length, 0, 'Paste must not configure the device');
        assert.equal(field('accuracy').props.value, '0.05', 'Paste must preserve measurement accuracy');
    }
    await tree.props.onSubmit({ preventDefault() {} }); render();
    assert.deepEqual(JSON.parse(JSON.stringify(sent[0].params.coords)), { lat: '20.845000000', lon: '106.688000000', alt: '30.0000' });
    assert.equal(sent[0].params.accuracy, '0.05');
    assert.equal(sent[0].params.altitude_reference, 'ELLIPSOID');
    const precise = clipboard.encode({lat: '20,123456789099', lon: '106.987654321099', alt: '-30.123456789099'});
    assert.deepEqual(JSON.parse(precise).coords, {lat: '20.1234567890', lon: '106.9876543210', alt: '-30.1234567890'});
    paste('latitude', precise);
    await tree.props.onSubmit({ preventDefault() {} }); render();
    assert.deepEqual(JSON.parse(JSON.stringify(sent.at(-1).params.coords)), {lat: '20.123456789', lon: '106.987654321', alt: '-30.1234'},
        'Ublox receives exactly nine decimal LLA digits and four height digits without rounding');
    assert.deepEqual(position(), ['20.123456789', '106.987654321', '-30.1234']);
    for (const [name, value] of [['latitude', '20.999999999'], ['longitude', '106,123456789'], ['altitude', '-30.999999']]) {
        field(name).props.onChange({target:{value}}); render();
    }
    for (const name of ['latitude', 'longitude']) {
        const previous = field(name).props.value;
        field(name).props.onChange({target:{value:previous+'9'}}); render();
        assert.equal(field(name).props.value, previous, 'Ublox rejects the tenth decimal digit');
        field(name).props.onChange({target:{value:previous.replace('.', ',')+'9'}}); render();
        assert.equal(field(name).props.value, previous, 'Comma input uses the same nine-digit limit');
        assert(field(name).props['aria-describedby'].includes('base-ublox-precision-'));
    }
    assert(find(tree, node => node.props.id === 'base-ublox-precision-PASTE_TEST'));
    await tree.props.onSubmit({preventDefault(){}}); render();
    assert.deepEqual(JSON.parse(JSON.stringify(sent.at(-1).params.coords)), {lat:'20.999999999',lon:'106.123456789',alt:'-30.9999'});
    field('accuracy').props.onChange({target:{value:'0.0029'}}); render();
    await tree.props.onSubmit({preventDefault(){}}); render();
    assert.equal(sent.at(-1).params.accuracy, '0.0029', 'Accuracy is sent as decimal meters without unit conversion');
    assert.equal(field('accuracy').props.step, '0.0001');
    const before = position();
    for (const patch of [{ crs: 'EPSG:9210' }, { height_type: 'orthometric' }, { version: 2 },
        { coords: { lat: 91, lon: 106, alt: 30 } }, { coords: { lat: 20, lon: 106 } },
        { coords: { lat: 20, lon: 106, alt: null } }, { coords: { lat: '', lon: 106, alt: 30 } }]) {
        assert.equal(paste('latitude', JSON.stringify({ ...JSON.parse(text), ...patch })), true);
        assert.deepEqual(position(), before, 'Invalid packets must not partially overwrite the form');
        assert(find(tree, node => node.props.role === 'status' && node.props.className.includes('text-danger')));
    }
    assert.equal(paste('longitude', '107.25'), false, 'Normal single-field paste stays native');
    const zero = clipboard.encode({ lat: 0, lon: 0, alt: 0 });
    assert.equal(paste('altitude', zero), true);
    assert.deepEqual(position(), ['0.000000000', '0.000000000', '0.0000']);
    disabled = true; render();
    assert.equal(paste('latitude', text), false);
    assert.deepEqual(position(), ['0.000000000', '0.000000000', '0.0000']);
    assert(field('latitude').props.disabled);
    assert.throws(() => clipboard.encode({ lat: 20, lon: 106, alt: NaN }));
    disabled = false;
    device.serial = 'UNICORE_LEGACY'; device.detected_chip_type = 'Unicorecomm'; render();
    assert(!find(tree, node => node.props.id === 'base-ublox-precision-UNICORE_LEGACY'));
    assert(find(tree, node => node.props.id === 'base-unicore-precision-UNICORE_LEGACY'));
    assert(field('latitude').props['aria-describedby'].includes('base-unicore-precision-'));
    field('latitude').props.onChange({target:{value:'20.1234567890'}}); render();
    assert.equal(field('latitude').props.value, '20.1234567890', 'Unicore still accepts ten digits');
    assert.equal(field('altitude-reference').props.value, 'LEGACY');
    await tree.props.onSubmit({ preventDefault() {} }); render();
    assert.equal(sent.at(-1).params.altitude_reference, 'LEGACY');
    const sentBeforePaste = sent.length;
    assert.equal(paste('altitude', text), true);
    assert.equal(sent.length, sentBeforePaste);
    assert.equal(field('altitude-reference').props.value, 'ELLIPSOID');
    await tree.props.onSubmit({ preventDefault() {} }); render();
    assert.equal(sent.at(-1).params.altitude_reference, 'ELLIPSOID');
    assert.equal(sent.at(-1).params.coords.alt, '30.0000000000');
    device.serial = 'UNICORE_NEW'; device.base_config = {}; render();
    assert.equal(field('altitude-reference').props.value, 'ELLIPSOID');
    console.log('PASS: shipped GNSS form fills all fields from any coordinate input, validates frame/height, preserves accuracy, blocks partial writes, and configures only on submit');
})().catch(error => { console.error(error); process.exitCode = 1; });

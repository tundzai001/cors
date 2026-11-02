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
    useRef(initial) {
        const slot = index++;
        if (!(slot in slots)) slots[slot] = {current:initial};
        return slots[slot];
    },
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
let currentUser = {role:'admin'};
function render() {
    do {
        changed = false;
        index = 0;
        tree = context.window.BaseConfigForm({ device, currentUser,
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
    const monitor = () => find(tree,node=>node.props.id === `gnss-interference-${device.serial}`);
    device={serial:'ITFM_OFF',detected_chip_type:'Ublox',base_config:{base_setup_method:'FIXED_LLA',accuracy:.01,coords:{lat:21,lon:105,alt:10},gnss_options:{interference_detection_enabled:false}}};
    render();assert.equal(monitor().props.checked,false);
    assert.equal(monitor().props.role,'switch');
    const before=sent.length;
    monitor().props.onChange({target:{checked:true}});render();
    assert.equal(sent.length,before,'Switch must not apply receiver config');
    await submit();assert.equal(sent.at(-1).params.gnss_options.interference_detection_enabled,true);
    monitor().props.onChange({target:{checked:false}});render();await submit();
    assert.equal(sent.at(-1).params.gnss_options.interference_detection_enabled,false);
    device={...device,serial:'ITFM_RELOAD',base_config:JSON.parse(JSON.stringify(sent.at(-1).params))};
    render();assert.equal(monitor().props.checked,false,'Explicit false survives saved config reload');
    device={...device,serial:'ITFM_OLD',base_config:{...device.base_config,gnss_options:undefined}};
    render();assert.equal(monitor().props.checked,true,'Old config defaults on');
    device={...device,serial:'ITFM_SURVEY',base_config:{base_setup_method:'SURVEY_IN',survey_in_duration:300,survey_in_accuracy:3,gnss_options:{interference_detection_enabled:false}}};
    render();assert.equal(monitor().props.checked,false);await submit();
    assert.equal(sent.at(-1).params.gnss_options.interference_detection_enabled,false);
    currentUser={role:'coordinator'};render();assert.equal(monitor().props.disabled,true);
    device={...device,serial:'ITFM_151',gnss_receiver:{itfm_supported:false}};render();assert.equal(monitor(),null,'HPG 1.50/1.51 must not offer removed ITFM switch');
    device={...device,serial:'ITFM_UM',detected_chip_type:'Unicorecomm'};render();assert.equal(monitor(),null);
    console.log('PASS: actual BASE form ITFM default/on/off/reload/Survey-in, no apply on toggle, admin restriction and hidden for UM');
})().catch(error => { console.error(error); process.exitCode = 1; });

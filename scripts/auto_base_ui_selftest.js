const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const slots = [], effects = [];
let index = 0, changed = false, tree;
const React = {
    memo: value => value,
    createElement: (type, props, ...children) => ({type, props: props || {}, children: children.flat(Infinity)}),
    useRef(initial) {
        const slot = index++;
        if (!(slot in slots)) slots[slot] = {current: initial};
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
let role = 'admin', configured;
function render() {
    do {
        index = 0; changed = false;
        tree = context.window.BaseConfigForm({device, currentUser: {role},
            onConfigureChip: async (serial, payload) => { configured = {serial, payload}; }});
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
assert(!text(tree).includes('Kết quả Auto Base — chờ kiểm tra'));
assert(!text(tree).includes('Auto Base hoàn tất:'));
device = {...device, auto_base_progress: {phase: 'ready_for_review', fixed_samples_only: true, conversion_report: {transform_applied: true, k0: 0.9999, central_meridian_deg: 105, projected_coord: {north: 2300000, east: 580000, h: 25}},
    sample_count: 60, updated_at: 2, averaged_coord: {lat: 21.123456789, lon: 105.123456789, alt: 12}}};
render();
assert(text(tree).includes('Kết quả Auto Base — chờ kiểm tra'));
assert(text(tree).includes('múi 3°'));
assert(!text(tree).includes('Cần lấy lại từ mountpoint M6'));
assert.equal(find(tree, node => node.type === 'button' && text(node).includes('Fixed LLA')).props.disabled, false);
assert(find(tree, node => node.props.id === 'auto-l0-ESP_TEST'));
assert(find(tree, node => node.props.id === 'auto-k0-ESP_TEST'));
assert(text(tree).includes('21.123456789'));
assert(find(tree, node => node.props.role === 'status' && text(node).includes('Chưa áp dụng tọa độ')));
console.log('PASS: live GGA/Float, reject unverified success, update completed coordinates, accessible result');

(async () => {
    const candidate = {lat:'21.084990131093591',lon:'105.78665745895107',alt:'-8.1155038587749004'};
    device = {serial:'ESP_REVIEW',detected_chip_type:'Unicorecomm',status:'online',
        system_info:{auto_base_review_supported:true},
        base_config:{base_setup_method:'SURVEY_IN',survey_in_duration:120},
        auto_base_progress:{phase:'ready_for_review',fixed_samples_only:true,sample_count:56,
            averaged_coord:candidate,conversion_report:{transform_applied:true,
                output_coord:candidate,calculated_coord:candidate,projected_coord:{north:2331863,east:581515,h:17},
                projection_source:'rtcm1025',processor:'esp32',k0:'0.9996',
                rtcm1025:{raw_l0:'9545454545',raw_k0:660000000,l0_deg:'104.999999995',k0:'0.9996',projection_type:1,frame_hex:'d30019',frame_bytes:31}}}};
    render();
    assert(text(tree).includes('Kết quả Auto Base — chờ kiểm tra'), 'Review must remain visible after restoring saved Survey-In');
    const resultNode = find(tree,node=>typeof node.type === 'function' && node.props.report?.processor === 'esp32');
    assert(resultNode, 'Projection result component must render');
    const auditText = text(resultNode.type(resultNode.props));
    assert(auditText.includes('660000000') && auditText.includes('9545454545') && auditText.includes('ESP32'));
    assert(auditText.includes('CORS · RTCM 1025') && auditText.includes('suy theo k₀'));
    const legacyText = text(resultNode.type({report:{...resultNode.props.report,rtcm1025:undefined}}));
    assert(legacyText.includes('Báo cáo cũ chưa lưu trường thô') && !legacyText.includes('660000000'));
    for (const [source,label] of [['manual','Nhập L₀/k₀ thủ công'],['server_catalog','Danh mục tỉnh trên máy chủ']]) {
        const sourceText = text(resultNode.type({report:{...resultNode.props.report,projection_source:source,rtcm1025:undefined}}));
        assert(sourceText.includes(label) && !sourceText.includes('Đối chiếu giải mã RTCM 1025'));
    }
    const approve = find(tree,node=>node.type === 'button' && text(node).includes('Duyệt và áp dụng LLH'));
    assert(approve && !approve.props.disabled);
    await approve.props.onClick({preventDefault(){}});
    assert.equal(configured.serial,'ESP_REVIEW');
    assert.equal(configured.payload.params.base_setup_method,'FIXED_LLA');
    assert.deepEqual(JSON.parse(JSON.stringify(configured.payload.params.coords)),candidate);
    assert.equal(configured.payload.params.altitude_reference,'ELLIPSOID');
    assert.equal(configured.payload.params.coordinate_source,'AUTO_CORS');
    assert.equal(configured.payload.params.conversion_report.output_coord.alt,candidate.alt);
    assert(!configured.payload.auto_cors, 'Approval must apply LLH, not start another sampling session');
    console.log('PASS: Survey-In restoration retains review; approval directly submits full-precision local LLH as Fixed LLA');
    device = {serial: 'LTE_TEST', detected_chip_type: 'Unicorecomm', is_lte: true, status: 'sleep',
        base_config: {base_setup_method: 'FIXED_LLA', coords: {lat: '18.3863077210', lon: '105.5089184600', alt: '49.92'}}};
    render();
    const mode = () => find(tree, node => node.props.id === `gnss-output-mode-${device.serial}`);
    const msm = () => find(tree, node => node.props.id === `gnss-msm-level-${device.serial}`);
    assert.equal(mode().props.disabled, false);
    assert.equal(msm().props.disabled, false);
    assert.equal(mode().props.value, 'rtcm_only');
    assert.equal(msm().props.value, 'both');
    mode().props.onChange({target: {value: 'diagnostics_rtcm'}});
    render();
    assert.equal(msm().props.value, 'both', 'enabling NMEA must preserve the chosen MSM level');
    assert(!find(msm(), node => node.type === 'option' && node.props.value === 'both').props.disabled);
    msm().props.onChange({target: {value: '7'}});
    render();
    find(tree, node => node.props['aria-label'] === 'Bản tin GPS').props.onChange({target: {checked: false}});
    render();
    await find(tree, node => node.type === 'form').props.onSubmit({preventDefault() {}});
    assert.equal(configured.serial, 'LTE_TEST');
    const options = configured.payload.params.gnss_options;
    assert.equal(options.output_mode, 'diagnostics_rtcm');
    assert.equal(options.msm_level, 7);
    assert.equal(options.constellations.gps, false);
    assert.equal(configured.payload.params.coords.alt, '49.92');
    assert(!('ports' in options), 'receiver port remains owned by the LTE source config');
    device = {...device, serial: 'LTE_RELOAD', base_config: {...device.base_config, gnss_options: options}};
    render();
    assert.equal(mode().props.value, 'diagnostics_rtcm');
    assert.equal(msm().props.value, '7');
    assert.equal(find(tree, node => node.props['aria-label'] === 'Bản tin GPS').props.checked, false);
    role = 'coordinator'; render();
    assert.equal(mode().props.disabled, true);
    assert.equal(msm().props.disabled, true);
    console.log('PASS: LTE admin controls enabled, NMEA/MSM/satellite selection submitted and restored; non-admin read only');
})().catch(error => { console.error(error); process.exitCode = 1; });

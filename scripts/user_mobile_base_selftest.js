const assert = require('node:assert/strict');

const isMobileBase = serial => /^esp_/i.test(String(serial || '').trim());

assert.equal(isMobileBase('esp_9896E5124B00'), true);
assert.equal(isMobileBase('ESP_9896E5124B00'), true);
assert.equal(isMobileBase('esp_000000000001'), true);
assert.equal(isMobileBase('1000000037a7201b'), false);
assert.equal(isMobileBase('LP_00E04C0CFB9E'), false);
assert.equal(isMobileBase('RELAY_ATG_DT'), false);
assert.equal(isMobileBase(''), false);
assert.equal(isMobileBase(null), false);

// Separation test
const allDevices = [
    { serial: '1000000037a7201b', name: 'Base Ha Noi' },
    { serial: 'LP_00E04C0CFB9E', name: 'Base Da Nang' },
    { serial: 'esp_9896E5124B00', name: 'ESP Rover Base 1' },
    { serial: 'ESP_112233445566', name: 'ESP Rover Base 2' },
];

const fixedDevices = allDevices.filter(d => !isMobileBase(d.serial));
const mobileDevices = allDevices.filter(d => isMobileBase(d.serial));

assert.deepEqual(fixedDevices.map(d => d.serial), ['1000000037a7201b', 'LP_00E04C0CFB9E']);
assert.deepEqual(mobileDevices.map(d => d.serial), ['esp_9896E5124B00', 'ESP_112233445566']);

// Payload filtering test
function buildPayload({ canViewMobile, stationAccess, checkedStations }) {
    const validStationAccess = stationAccess.filter(item => canViewMobile || !isMobileBase(item.serial));
    const validCheckedStations = checkedStations.filter(serial => canViewMobile || !isMobileBase(serial));
    return {
        can_view_mobile_bases: canViewMobile,
        assigned_devices: validStationAccess.filter(item => item.access === 'edit').map(i => i.serial),
        view_only_bases: validStationAccess.filter(item => item.access === 'view').map(i => i.serial),
        allowed_bases: validCheckedStations,
    };
}

// Case 1: canViewMobile = false -> mobile stations stripped
const payloadOff = buildPayload({
    canViewMobile: false,
    stationAccess: [
        { serial: '1000000037a7201b', access: 'edit' },
        { serial: 'esp_9896E5124B00', access: 'view' },
    ],
    checkedStations: ['1000000037a7201b', 'esp_9896E5124B00'],
});
assert.equal(payloadOff.can_view_mobile_bases, false);
assert.deepEqual(payloadOff.assigned_devices, ['1000000037a7201b']);
assert.deepEqual(payloadOff.view_only_bases, []);
assert.deepEqual(payloadOff.allowed_bases, ['1000000037a7201b']);

// Case 2: canViewMobile = true -> mobile stations retained
const payloadOn = buildPayload({
    canViewMobile: true,
    stationAccess: [
        { serial: '1000000037a7201b', access: 'edit' },
        { serial: 'esp_9896E5124B00', access: 'view' },
    ],
    checkedStations: ['1000000037a7201b', 'esp_9896E5124B00'],
});
assert.equal(payloadOn.can_view_mobile_bases, true);
assert.deepEqual(payloadOn.assigned_devices, ['1000000037a7201b']);
assert.deepEqual(payloadOn.view_only_bases, ['esp_9896E5124B00']);
assert.deepEqual(payloadOn.allowed_bases, ['1000000037a7201b', 'esp_9896E5124B00']);

console.log('PASS: user_mobile_base_selftest (isMobileBase, station separation, payload filtering)');

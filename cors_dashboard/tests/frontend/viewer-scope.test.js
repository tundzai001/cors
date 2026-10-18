const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', '..', 'src', 'js', 'main.js'), 'utf8');

function app() {
    const timers = new Map();
    const frames = new Map();
    let nextId = 1;
    let reloads = 0;
    const renders = [];
    const classList = { add() {}, remove() {}, toggle() {} };
    const element = () => ({ classList });
    const window = {
        location: { pathname: '/', search: '', reload: () => { reloads++; } },
        addEventListener() {},
        dispatchEvent() {},
        AllStationsMap() {},
    };
    const context = vm.createContext({
        window,
        document: {
            readyState: 'loading',
            body: { classList },
            addEventListener() {},
            getElementById: element,
            querySelector: element,
        },
        React: { Component: class {}, createElement: (type, props) => ({ type, props }) },
        console,
        Event: class {},
        setTimeout: (fn) => { const id = nextId++; timers.set(id, fn); return id; },
        clearTimeout: (id) => timers.delete(id),
        setInterval: () => nextId++,
        clearInterval() {},
        requestAnimationFrame: (fn) => { const id = nextId++; frames.set(id, fn); return id; },
        cancelAnimationFrame: (id) => frames.delete(id),
    });
    vm.runInContext(source, context);
    context.renders = renders;
    vm.runInContext('mapRoot = { render: tree => renders.push(tree) }', context);
    return {
        context,
        window,
        renders,
        get reloads() { return reloads; },
        flushTimers() { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach(fn => fn()); },
        flushFrames() { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(fn => fn()); },
        message(value) { context.message = value; vm.runInContext('handleWebSocketMessage(message)', context); },
    };
}

test('opening Map renders without waiting for station telemetry', () => {
    const ui = app();
    ui.window.switchView('map');
    ui.flushFrames();
    assert.equal(ui.renders.length, 1);
    assert.equal(ui.renders[0].type, ui.window.AllStationsMap);
});

test('viewer receives an assigned station despite different owner and serial case', () => {
    const ui = app();
    ui.window.globalState.currentUser = { id: 20, role: 'viewer' };
    ui.window.globalState.devices = [{ serial: 'TV869', status: 'offline' }];
    ui.message({ type: 'status_update', data: { serial: 'tv869', user_id: 99, status: 'online' } });
    ui.flushTimers();
    assert.equal(ui.window.globalState.devices.length, 1);
    assert.equal(ui.window.globalState.devices[0].status, 'online');

    ui.message({ type: 'status_update', data: { serial: 'OTHER', status: 'online' } });
    ui.flushTimers();
    assert.equal(ui.window.globalState.devices.length, 1);
});

test('assignment changes reload only the affected viewer', () => {
    const ui = app();
    ui.window.globalState.currentUser = { id: 20, role: 'viewer' };
    ui.message({ type: 'user_assignment_updated', data: { user_id: 21 } });
    assert.equal(ui.reloads, 0);
    ui.message({ type: 'user_assignment_updated', data: { user_id: 20 } });
    assert.equal(ui.reloads, 1);
});

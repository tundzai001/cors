const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const dist = process.argv.includes('--dist');
const scriptPath = name => path.join(__dirname, '..', 'cors_dashboard', ...(dist ? ['frontend', 'js', 'dist', `${name}.min.js`] : ['src', 'js', `${name}.js`]));
const source = fs.readFileSync(scriptPath('performance-runtime'), 'utf8');

function profile({ reducedMotion = false, cpu = 8, saveData = false } = {}) {
    const classes = new Set();
    const window = {
        matchMedia: () => ({ matches: reducedMotion }),
        addEventListener() {},
        dispatchEvent() {},
    };
    const context = {
        window,
        navigator: { hardwareConcurrency: cpu, connection: { saveData } },
        document: {
            createElement: () => ({ getContext: () => ({ getExtension: () => null }) }),
            documentElement: {
                dataset: {},
                style: { setProperty() {} },
                classList: { toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); } },
            },
        },
        CustomEvent: class {},
        requestAnimationFrame() {},
    };
    vm.runInNewContext(source, context);
    return { ...window.CorsPerformance, classes };
}

const constrained = profile({ reducedMotion: true, cpu: 2 });
assert.equal(constrained.tier, 'lite');
assert.equal(constrained.reducedMotion, true);
assert.equal(constrained.canUse3D, false);
assert.equal(constrained.classes.has('reduced-motion'), true);

const capable = profile({ cpu: 8 });
assert.equal(capable.tier, 'balanced');
assert.equal(capable.canUse3D, true);
assert.equal(capable.threePixelRatio, 1);
assert.ok(capable.frameInterval >= 1000 / 30);

// Exercise the globe loop at 120 Hz without a GPU: idle, interaction, hidden,
// detach, and reattach must each keep the expected render budget/lifecycle.
const frames = new Map();
const events = new Map();
let frameId = 0;
let renders = 0;
let moving = false;
const document = {
    hidden: false,
    addEventListener: (name, fn) => events.set(name, fn),
    removeEventListener: name => events.delete(name),
};
const window = { THREE: {} };
vm.runInNewContext(fs.readFileSync(scriptPath('globe-3d-view'), 'utf8'), {
    window, document,
    requestAnimationFrame: fn => { frames.set(++frameId, fn); return frameId; },
    cancelAnimationFrame: id => frames.delete(id),
});
const globe = window.Globe3DView;
globe.isInitialized = true;
globe.renderer = { domElement: {}, render: () => renders++ };
globe.hoverTooltipEl = { style: {} };
globe.scene = {};
globe.camera = { position: { lengthSq: () => 100 } };
globe.controls = { update: () => moving };
globe._onResize = () => {};
globe.focusVietnam = () => {};
function advance(from, duration) {
    for (let tick = 0; tick < duration * 120; tick++) {
        const pending = [...frames.values()];
        frames.clear();
        pending.forEach(fn => fn(from + tick * 1000 / 120));
    }
}
const attached = [];
globe.attach({ style: {}, appendChild: child => attached.push(child) });
advance(0, 2);
assert.equal(renders, 2, 'idle globe renders at the telemetry cadence');
moving = true;
renders = 0;
advance(2000, 2);
assert.equal(renders, 60, 'camera interaction is capped at 30 FPS on a 120 Hz display');
document.hidden = true;
events.get('visibilitychange')();
assert.equal(frames.size, 0, 'hidden tab stops scheduling frames');
document.hidden = false;
events.get('visibilitychange')();
assert.equal(frames.size, 1, 'visible tab resumes exactly one loop');
globe.detach();

let radius = 1;
globe.camera = {
    position: {
        lengthSq: () => radius * radius,
        setLength: value => { radius = value; },
        set: (x, y, z) => { radius = Math.hypot(x, y, z); },
    },
    lookAt() {},
};
globe._constrainCamera();
assert.equal(radius, 5.06, 'camera cannot enter Earth even when controls target a satellite');
radius = 0;
globe._constrainCamera();
assert.equal(radius, 5.06, 'camera at the center recovers outside the surface');
assert.equal(frames.size, 0);
assert.equal(events.size, 0);
const reattached = [];
globe.attach({ style: {}, appendChild: child => reattached.push(child) });
assert.deepEqual(reattached, attached, 'reattach moves the renderer into the new container');
assert.equal(frames.size, 1);
globe.detach();

vm.runInNewContext(fs.readFileSync(scriptPath('globe-3d-math'), 'utf8'), { window });
const geo = window.Globe3DMath;
for (const [lat, lng, zoom] of [[21.03, 105.85, 8], [-33.9, 151.2, 10], [51.5, -0.1, 6]]) {
    const rad = Math.PI / 180;
    const normal = { x: Math.cos(lat * rad) * Math.cos(lng * rad), y: Math.sin(lat * rad), z: -Math.cos(lat * rad) * Math.sin(lng * rad) };
    const mpp = 156543.03392 * Math.cos(lat * rad) / 2 ** zoom;
    const radius = 5 + mpp * 900 / 2000 / Math.tan(45 * rad / 2) / 6371 * 5;
    const view = geo.surfaceViewFromRay({ x: normal.x * radius, y: normal.y * radius, z: normal.z * radius }, { x: -normal.x, y: -normal.y, z: -normal.z }, 45, 900);
    assert.ok(Math.abs(view.lat - lat) < 1e-8);
    assert.ok(Math.abs(view.lng - lng) < 1e-8);
    assert.ok(Math.abs(view.zoom - zoom) < 1e-8, '3D-to-2D handoff preserves scale and center');
    assert.equal(geo.shouldEnterGlobe(view.zoom), false, 'surface handoff does not switch back to 3D');
}
assert.equal(geo.shouldEnterGlobe(4), true);
assert.equal(geo.shouldEnterGlobe(4.1), false);
assert.equal(geo.surfaceViewFromRay({ x: 0, y: 0, z: 6 }, { x: 0, y: 0, z: 1 }, 45, 900), null, 'looking into space does not enter 2D');
let handoffs = 0;
globe.getSurfaceView = () => ({ lat: 21.03, lng: 105.85, zoom: 8 });
globe.onSurfaceView = () => { handoffs++; };
globe.isActive = true;
globe.lastFrameAt = null;
globe._animate(10000);
globe._animate(10040);
assert.equal(handoffs, 1, 'surface handoff fires once until React switches view');
globe.detach();

console.log('landing performance capability self-test: OK');

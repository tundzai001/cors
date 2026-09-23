const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'cors_dashboard', 'frontend', 'js', 'performance-runtime.js'), 'utf8');

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
assert.equal(capable.tier, 'high');
assert.equal(capable.canUse3D, true);

console.log('landing performance capability self-test: OK');

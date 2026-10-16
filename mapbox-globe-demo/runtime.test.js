import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { spinStep, flightOptions } from './motion.js';

test('camera interruption, rotation, and authorization state do not fight each other', () => {
    const elements = new Map();
    const element = () => ({
        hidden: false, textContent: '', value: '', open: false, style: {}, dataset: {},
        classList: { toggle() {} }, setAttribute() {}, append() {}, addEventListener() {},
        querySelector: () => ({ textContent: '' }),
        showModal() { this.open = true; }, close() { this.open = false; },
    });
    const get = id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
    class FakeMap {
        constructor(options) { this.center = { lng: options.center[0], lat: options.center[1] }; this.zoom = options.zoom; this.handlers = {}; this.moves = []; this.moving = false; this.scrollZoom = { setWheelZoomRate() {} }; }
        on(type, ...args) { (this.handlers[type] ||= []).push(args.at(-1)); }
        emit(type, event = {}) { for (const callback of this.handlers[type] || []) callback(event); }
        addControl() {} setPadding() {} setFog() {} addSource() {} addLayer() {} setFilter() {}
        getSource() { return true; } getCanvas() { return this.canvas ||= element(); }
        getCenter() { return this.center; } getZoom() { return this.zoom; } isMoving() { return this.moving; }
        easeTo(options, data = {}) { this.moves.push(['ease', options]); this.pending = { options, data }; this.moving = true; }
        flyTo(options, data = {}) { this.moves.push(['fly', options]); this.pending = { options, data }; this.moving = true; }
        stop() { if (this.pending) { const event = this.pending.data; this.pending = null; this.moving = false; this.emit('moveend', event); } }
        finish() { const { options, data } = this.pending; this.center = { lng: options.center[0], lat: options.center[1] }; this.zoom = options.zoom; this.pending = null; this.moving = false; this.emit('moveend', data); }
    }
    const mapboxgl = { Map: FakeMap, NavigationControl: class {}, supported: () => true };
    const document = { hidden: false, getElementById: get, querySelectorAll: () => [], createElement: element, addEventListener() {} };
    const window = { mapboxgl, addEventListener() {} };
    const context = vm.createContext({ window, document, mapboxgl, spinStep, flightOptions,
        matchMedia: () => ({ matches: false, addEventListener() {} }), innerWidth: 1400,
        setTimeout, clearTimeout, console });
    let source = readFileSync(new URL('./app.js', import.meta.url), 'utf8');
    source = source.replace(/^import[^\n]+\n/, '');
    source = source.slice(0, source.lastIndexOf("try {\n    const { default: token }"));
    vm.runInContext(source, context);
    vm.runInContext("start('pk.test')", context);
    const { map, fly } = window.earthPreview;
    map.emit('style.load'); map.emit('load');
    assert.equal(map.moves[0][0], 'ease', 'initial overview rotates natively');
    fly({ center: [105, 21], zoom: 11 });
    assert.equal(map.moves.at(-1)[0], 'fly');
    const count = map.moves.length;
    fly({ center: [8.5, 47.3], zoom: 10 });
    assert.equal(map.moves.length, count + 1, 'interrupting a flight does not insert a rotation');
    map.finish();
    assert.equal(map.moves.length, count + 1, 'close-up view stops rotation');
    fly({ center: [105, 17], zoom: 1.35 }); map.finish();
    assert.equal(map.moves.at(-1)[0], 'ease', 'rotation resumes on overview');
    map.emit('error', { error: { status: 403 } });
    assert.equal(get('token-dialog').open, true);
    map.emit('load');
    assert.equal(map.isMoving(), false, 'late load event does not restart an unauthorized map');
});

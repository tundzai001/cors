const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
    constructor() { this.children = []; this.style = {}; this.listeners = {}; }
    append(...children) { children.forEach(child => { child.parent = this; this.children.push(child); }); }
    prepend(child) { child.parent = this; this.children.unshift(child); }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
    setAttribute() {}
    addEventListener(type, callback) { this.listeners[type] = callback; }
}
const maps = [];
class FakeMap {
    constructor(options) { this.options = options; this.center = { lng: options.center[0], lat: options.center[1] }; this.zoom = options.zoom; this.events = {}; this.sources = {}; this.layers = {}; this.moves = []; this.canvas = new Element(); this.scrollZoom = { setWheelZoomRate() {} }; maps.push(this); }
    on(type, ...args) { (this.events[type] ||= []).push(args.at(-1)); }
    emit(type, data = {}) { (this.events[type] || []).forEach(callback => callback(data)); }
    addControl() {} setFog() {}
    addSource(id, data) { this.sources[id] = { data: data.data, setData(value) { this.data = value; } }; }
    addLayer(layer) { this.layers[layer.id] = layer; }
    getSource(id) { return this.sources[id]; } getLayer(id) { return this.layers[id]; }
    setFilter(id, filter) { this.layers[id].filter = filter; }
    getCanvas() { return this.canvas; } getCenter() { return this.center; } getZoom() { return this.zoom; }
    isMoving() { return Boolean(this.pending); }
    easeTo(options, data = {}) { this.pending = { options, data }; this.moves.push(['ease', options]); }
    flyTo(options, data = {}) { this.pending = { options, data }; this.moves.push(['fly', options]); }
    stop() { if (this.pending) { const data = this.pending.data; this.pending = null; this.emit('moveend', data); } }
    finish() { const { options, data } = this.pending; this.center = { lng: options.center[0], lat: options.center[1] }; this.zoom = options.zoom; this.pending = null; this.emit('moveend', data); }
    remove() { this.removed = true; this.pending = null; }
}
const window = {
    mapboxgl: { Map: FakeMap, NavigationControl: class {}, supported: () => true },
    matchMedia: () => ({ matches: false }), addEventListener() {}, removeEventListener() {},
    apiFetch: async () => ({ ok: true, json: async () => ({ mapbox_public_token: 'pk.test' }) }),
};
const document = { hidden: false, createElement: () => new Element(), addEventListener() {}, removeEventListener() {} };
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname, '../cors_dashboard/src/js/mapbox-globe-view.js'), 'utf8'), { window, document, console, setTimeout, clearTimeout });
(async () => {
    const view = window.MapboxGlobeView;
    const container = new Element();
    const stations = [{ device: { serial: 'PI_real', name: 'Actual station' }, coords: { lat: 16, lon: 108 }, status: 'sleep' }];
    await view.attach(container, { stations });
    const map = view.map;
    assert.equal(map.options.accessToken, 'pk.test', 'globe uses the backend configuration');
    assert.equal(map.options.projection, 'globe');
    map.emit('style.load'); map.emit('load');
    assert.equal(map.getSource('cors-stations').data.features[0].properties.serial, 'PI_real', 'real station data reaches Mapbox');
    assert.equal(map.moves.at(-1)[0], 'ease', 'overview uses native rotation');
    view.flyTo(21, 105, 11);
    const moves = map.moves.length;
    view.flyTo(47, 8, 10);
    assert.equal(map.moves.length, moves + 1, 'interrupting a flight does not insert a rotation');
    map.finish();
    assert.equal(map.moves.at(-1)[0], 'fly', 'close-up does not rotate');
    view.updateStations([], null);
    assert.equal(map.getSource('cors-stations').data.features.length, 0, 'station filtering removes old pins');
    view.detach();
    assert.ok(map.removed, 'leaving globe releases the WebGL map');
    assert.equal(container.children.length, 0, 'leaving globe removes its owned DOM');
    let resolveFetch;
    window.apiFetch = () => new Promise(resolve => { resolveFetch = resolve; });
    const pending = view.attach(container, { stations });
    view.detach();
    resolveFetch({ ok: true, json: async () => ({ mapbox_public_token: 'pk.test' }) });
    await pending;
    assert.equal(maps.length, 1, 'late configuration does not mount a globe after leaving the view');
    assert.equal(container.children.length, 0);
    window.apiFetch = async () => ({ ok: true, json: async () => ({ mapbox_public_token: '' }) });
    await view.attach(container, {});
    assert.equal(view.map, null, 'missing token does not create an unauthorized map');
    assert.equal(view.status.children[0].textContent, 'Chưa cấu hình bản đồ Mapbox.');
    view.detach();
    console.log('PASS: backend token, real stations, native camera, filtering, teardown, and async cancellation');
})().catch(error => { console.error(error); process.exitCode = 1; });

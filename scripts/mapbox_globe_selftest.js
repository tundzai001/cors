const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
    constructor() { this.children = []; this.style = {}; this.listeners = {}; }
    append(...children) { children.forEach(child => { child.parent = this; this.children.push(child); }); }
    prepend(child) { child.parent = this; this.children.unshift(child); }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
    setAttribute(key, value) { (this.attributes ||= {})[key] = value; }
    removeAttribute(key) { delete this.attributes?.[key]; }
    getContext() { return { scale() {}, translate() {}, beginPath() {}, moveTo() {}, lineTo() {}, closePath() {}, fill() {}, stroke() {}, getImageData: () => ({ width: 56, height: 56, data: new Uint8Array(56*56*4) }) }; }
    addEventListener(type, callback) { this.listeners[type] = callback; }
}
const maps = [];
let watchdog;
class FakeMap {
    constructor(options) { this.options = options; this.center = { lng: options.center[0], lat: options.center[1] }; this.zoom = options.zoom; this.events = {}; this.sources = {}; this.layers = {}; this.moves = []; this.canvas = new Element(); this.scrollZoom = { setWheelZoomRate() {} }; maps.push(this); }
    on(type, ...args) { (this.events[type] ||= []).push(args.at(-1)); if (args.length > 1) (this.layerEvents ||= {})[`${type}:${args[0]}`] = args.at(-1); }
    emit(type, data = {}) { (this.events[type] || []).forEach(callback => callback(data)); }
    addControl() {} setFog() {}
    hasImage(id) { return Boolean(this.images?.[id]); }
    addImage(id, data) { (this.images ||= {})[id] = data; }
    addSource(id, data) { this.sources[id] = { ...data, data: data.data, setData(value) { this.data = value; } }; }
    addLayer(layer) { this.layers[layer.id] = layer; }
    getSource(id) { return this.sources[id]; } getLayer(id) { return this.layers[id]; }
    setMaxBounds(bounds) { this.bounds = bounds; }
    setProjection(projection) { this.projection = projection; }
    resize() {}
    getStyle() { return { layers: Object.values(this.layers) }; }
    setLayoutProperty(id, key, value) { (this.layers[id].layout ||= {})[key] = value; }
    setPaintProperty(id, key, value) { this.layers[id].paint[key] = value; }
    setStyle(style, options) { this.style = style; this.styleOptions = options; this.sources = {}; this.layers = {}; this.emit('style.load'); }
    queryRenderedFeatures() { return []; }
    setFilter(id, filter) { this.layers[id].filter = filter; }
    getCanvas() { return this.canvas; } getCenter() { return this.center; } getZoom() { return this.zoom; }
    isMoving() { return Boolean(this.pending); }
    easeTo(options, data = {}) { this.pending = { options, data }; this.moves.push(['ease', options]); }
    flyTo(options, data = {}) { this.pending = { options, data }; this.moves.push(['fly', options]); }
    stop() { if (this.pending) { const data = this.pending.data; this.pending = null; this.emit('moveend', data); } }
    finish() { const { options, data } = this.pending; this.center = { lng: options.center[0], lat: options.center[1] }; this.zoom = options.zoom; this.pending = null; this.emit('moveend', data); }
    remove() { this.removed = true; this.pending = null; }
}
class FakePopup {
    constructor(options) { this.options = options; this.close = new Element(); this.close.setAttribute('aria-hidden', 'true'); }
    setLngLat(value) { this.lngLat = value; return this; }
    setDOMContent(value) { this.content = value; return this; }
    addTo() { return this; }
    getElement() { return { querySelector: () => this.close }; }
    remove() {}
}
const window = {
    mapboxgl: { Map: FakeMap, Popup: FakePopup, NavigationControl: class {}, supported: () => true },
    CorsUI: { getStatusMeta: () => ({ color: '#22c55e' }) },
    StationsMapUtils: { getRoverStatusMeta: () => ({ label: 'Online', color: '#22c55e' }) },
    matchMedia: () => ({ matches: false }), addEventListener() {}, removeEventListener() {},
    apiFetch: async () => ({ ok: true, json: async () => ({ mapbox_public_token: 'pk.test' }) }),
};
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname, '../cors_dashboard/frontend/js/dist/vngeonet-stations.min.js'), 'utf8'), { window });
const referenceStations = window.VngeonetStations;
assert.equal(referenceStations.length, 65, 'public source contains 65 unique VNGEONET stations');
assert.equal(new Set(referenceStations.map(station => station.code)).size, 65);
assert.equal(referenceStations.find(station => station.code === 'TQUA').lat, 21.823601197862928, 'use the public VNGEONET coordinates');
assert(referenceStations.every(station => Number.isFinite(station.lat) && Number.isFinite(station.lon)
    && station.lat >= 6 && station.lat <= 24 && station.lon >= 102 && station.lon <= 118));
const document = { hidden: false, createElement: () => new Element(), addEventListener() {}, removeEventListener() {} };
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname, '../cors_dashboard/src/js/mapbox-globe-view.js'), 'utf8'), { window, document, console, setTimeout: (callback, delay) => { if (delay === 15000) watchdog = callback; return setTimeout(callback, delay); }, clearTimeout });
(async () => {
    const view = window.MapboxGlobeView;
    const container = new Element();
    const stations = [{ device: { serial: 'PI_real', name: 'Actual station' }, coords: { lat: 16, lon: 108 }, status: 'sleep' }];
    await view.attach(container, { stations, referenceStationPopup: station => `VNGEONET ${station.code}` });
    const map = view.map;
    assert.equal(map.options.accessToken, 'pk.test', 'globe uses the backend configuration');
    assert.equal(map.options.projection, 'globe');
    map.emit('style.load'); map.emit('load');
    assert.equal(map.getSource('cors-stations').data.features[0].properties.serial, 'PI_real', 'real station data reaches Mapbox');
    assert.equal(map.getLayer('cors-4g-pins').type, 'symbol', '4G bases use square icons');
    assert.equal(map.getLayer('cors-4g-pins').layout['icon-image'], 'cors-base-4g');
    assert(map.hasImage('cors-base-4g'), 'square 4G image is installed');
    view.updateStations([...stations, {device: {serial: 'MONITOR_7001'}, coords: {lat: 21, lon: 105}, status: 'online', is4g: true}]);
    assert.equal(map.getSource('cors-stations').data.features[1].properties.is4g, true);
    assert.equal(map.getSource('cors-stations').data.features[0].properties.is4g, false);
    view.updateStations(stations);
    view.updateLayers({ referenceStations });
    assert.equal(map.getSource('cors-vngeonet').data.features.length, 65);
    const hanoi = map.getSource('cors-vngeonet').data.features.find(feature => feature.properties.code === 'HNOI');
    assert.deepEqual(Array.from(hanoi.geometry.coordinates), [105.78378647460394, 21.045424473878832], 'GeoJSON uses longitude then latitude');
    assert.equal(hanoi.properties.status, undefined, 'reference stations do not invent a live status');
    assert.equal(map.getSource('cors-stations').data.features.length, 1, 'reference points do not inflate managed station counts');
    assert.equal(map.getLayer('cors-vngeonet-pins').paint['circle-color'], '#a78bfa');
    map.layerEvents['click:cors-vngeonet-pins']({ features: [{ properties: { code: 'HNOI' } }] });
    assert.equal(view.popup.content.innerHTML, 'VNGEONET HNOI');
    assert.deepEqual(Array.from(view.popup.lngLat), [105.78378647460394, 21.045424473878832]);
    const queryFeatures = map.queryRenderedFeatures;
    map.queryRenderedFeatures = (point, options) => options.layers.includes('cors-vngeonet-pins') ? [{}] : [];
    assert(view._hasPointFeature({ point: {} }), 'reference markers must suppress province popups');
    view._mapClick({ point: {} });
    assert.equal(view.popup.content.innerHTML, 'VNGEONET HNOI', 'generic map clicks must not replace reference popups');
    map.queryRenderedFeatures = queryFeatures;

    view.updateLayers({ referenceStations: [] });
    assert.equal(map.getSource('cors-vngeonet').data.features.length, 0, 'reference overlay can be hidden');
    view.updateLayers({ referenceStations });

    assert.equal(map.moves.length, 0, 'station map does not start an unsolicited flight');
    assert.equal(map.getLayer('cors-rover-pins').type, 'symbol', 'rovers retain triangular icons');
    assert.equal(map.getSource('cors-rovers').cluster, false, 'rover clustering is disabled');
    assert.equal(map.getLayer('cors-rover-clusters'), undefined, 'cluster layer is removed');
    assert.ok(map.hasImage('cors-rover-fixed'));
    assert.ok(map.hasImage('cors-ipp'));
    assert.equal(map.getLayer('cors-gnss-ipp').type, 'symbol', 'IPP retains a diamond icon');
    assert.equal(map.getSource('cors-islands').data.features.length, 3, 'island labels are visible independently of mask');
    const province = { type: 'Feature', properties: { code: '01', fullName: 'Hà Nội' }, geometry: { type: 'Polygon', coordinates: [[[105, 20], [106, 20], [106, 22], [105, 22], [105, 20]]] } };
    view.updateLayers({ showProvinces: true, provinces: { type: 'FeatureCollection', features: [province] } });
    view.fitProvince('01', { fit: false, lngLat: { lat: 21, lng: 105.5 } });
    assert.equal(map.moves.length, 0, 'province click highlights without unexpectedly moving camera');
    assert.equal(view.popup.lngLat.lat, 21, 'province popup anchors to clicked location');
    assert.equal(view.popup.close.attributes['aria-hidden'], undefined, 'focusable popup close button is not aria-hidden');
    assert.equal(view.popup.close.attributes['aria-label'], 'Đóng thông tin');
    assert.equal(view.popup.options.focusAfterOpen, false, 'map click does not steal keyboard focus');
    assert.equal(map.getLayer('cors-province-selected-line').filter.at(-1), '01');
    const source = map.getSource('cors-stations');
    const center = map.getCenter();
    for (let cycle = 0; cycle < 3; cycle++) {
        view.setProjection('mercator'); view.setProjection('globe');
        assert.equal(view.map, map, '2D/3D keeps one map');
        assert.equal(map.getSource('cors-stations'), source, '2D/3D preserves data sources');
        assert.equal(map.getCenter(), center, '2D/3D preserves camera');
    }
    view.updateLayers({ coverage: true, rovers: [{ latitude: 16, longitude: 108, username: 'rover1' }] });
    assert.equal(map.getSource('cors-coverage').data.features.length, 1);
    assert.equal(map.getSource('cors-rovers').data.features.length, 1);
    view.updateLayers({ coverage: false });
    assert.equal(map.getSource('cors-coverage').data.features.length, 0, '20 km coverage can be switched off');
    view.updateLayers({ coverage: true });
    view.setActive(false); view.setActive(true);
    assert.equal(view.map, map, 'orbit excursion does not destroy the surface map');
    assert.ok(!map.removed);
    view.updateLayers({ tileMode: 'streets' });
    assert.equal(map.styleOptions.diff, false, 'base-style change avoids unsupported sprite diff');
    assert.equal(map.getSource('cors-rovers').data.features.length, 1, 'style reload restores custom data');
    assert.equal(map.getSource('cors-vngeonet').data.features.length, 65, 'style reload restores VNGEONET');
    assert.equal(map.getSource('cors-coverage').data.features.length, 1);
    view.setMeasureEnabled(true);
    const measure = map.getSource('cors-measure');
    view.setProjection('mercator'); view.setProjection('globe');
    assert.equal(map.getSource('cors-measure'), measure, 'measurement survives projection switches');
    map.zoom = 1.5;
    view.setSpinning(true);
    assert.equal(map.moves.at(-1)[0], 'ease', 'explicit rotation uses native camera');
    view.setSpinning(false);
    view.flyTo(21, 105, 11);
    const pendingFlight = map.pending;
    map.canvas.listeners.wheel();
    assert.equal(map.pending, pendingFlight, 'wheel handler does not cancel native zoom or camera animation');
    const moves = map.moves.length;
    view.flyTo(47, 8, 10);
    assert.equal(map.moves.length, moves + 1, 'interrupting a flight does not insert a rotation');
    map.finish();
    assert.equal(map.moves.at(-1)[0], 'fly', 'close-up does not rotate');
    view.updateStations([], null);
    assert.equal(map.getSource('cors-stations').data.features.length, 0, 'station filtering removes old pins');
    view.detach();
    assert.ok(map.removed, 'unmount releases the WebGL map');
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
    const failures = [];
    await view.attach(container, { onFailure: error => failures.push(error) });
    assert.equal(failures.length, 1, 'missing token selects fallback');
    assert.equal(failures[0].view, null);
    view.detach();
    window.apiFetch = async () => ({ ok: true, json: async () => ({ mapbox_public_token: 'pk.test' }) });
    for (const status of [401, 403, 500]) {
        await view.attach(container, { onFailure: error => failures.push(error) });
        const failing = view.map;
        const previous = failures.length;
        failing.emit('error', { error: { status } });
        failing.emit('error', { error: { status } });
        failing.emit('load');
        assert.equal(failures.length, previous + 1, `HTTP ${status} fails over once`);
        assert.equal(view.ready, false, 'late load cannot revive a failed map');
        assert.equal(failures.at(-1).view.zoom, 6, 'fallback receives current camera');
        view.detach();
    }
    await view.attach(container, { onFailure: error => failures.push(error) });
    watchdog();
    assert.equal(view.failed, true, 'initial-load watchdog selects fallback');
    view.detach();
    window.apiFetch = async () => ({ ok: false, status: 503 });
    await view.attach(container, { onFailure: error => failures.push(error) });
    assert.equal(view.map, null, 'configuration failure selects fallback');
    view.detach();
    window.apiFetch = async () => ({ ok: true, json: async () => ({ mapbox_public_token: 'pk.test' }) });
    window.mapboxgl.supported = () => false;
    await view.attach(container, { onFailure: error => failures.push(error) });
    assert.equal(view.map, null, 'unsupported WebGL selects fallback');
    view.detach();
    console.log('PASS: backend token, real stations, native camera, filtering, teardown, and async cancellation');
})().catch(error => { window.MapboxGlobeView.detach(); console.error(error); process.exitCode = 1; });

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

let cursor = 0;
const hooks = [];
let runEffects = false, effectCursor = 0, dirty = false, pendingEffects = [];
const effects = [];
const React = {
  memo: value => value,
  useState(initial) {
    const index = cursor++;
    if (!(index in hooks)) hooks[index] = typeof initial === 'function' ? initial() : initial;
    return [hooks[index], value => { const next = typeof value === 'function' ? value(hooks[index]) : value; dirty ||= !Object.is(next, hooks[index]); hooks[index] = next; }];
  },
  useRef(initial) {
    const index = cursor++;
    return hooks[index] ||= { current: initial };
  },
  useEffect(callback, deps) {
    const index = effectCursor++;
    if (runEffects && (!effects[index] || deps.some((value, i) => !Object.is(value, effects[index].deps[i])))) pendingEffects.push({ index, callback, deps });
  },
  useMemo: fn => fn(),
  createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
};
const projections = [];
const window = { MapboxGlobeView: { setProjection: value => projections.push(value) }, Globe3DView: { getSurfaceView: () => null } };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../cors_dashboard/frontend/js/dist/stations-map.min.js'), 'utf8'), {
  React, window, console, navigator: {}, document: { visibilityState: 'hidden' }, fetch: async () => ({ ok: true, json: async () => [] }), Date, setTimeout: () => 1, clearTimeout, setInterval: () => 1, clearInterval() {},
});
function collect(tree, nodes = []) {
  if (Array.isArray(tree)) tree.forEach(node => collect(node, nodes));
  else if (tree && typeof tree === 'object') { nodes.push(tree); collect(tree.children, nodes); }
  return nodes;
}
function render() {
  cursor = 0; effectCursor = 0;
  return collect(window.AllStationsMap({ devices: [], currentUser: { role: 'admin' } }));
}
for (let cycle = 0; cycle < 3; cycle++) {
  let nodes = render();
  const initialCanvas = nodes.find(node => node.props.className === 'station-map-canvas');
  assert.ok(initialCanvas, 'Leaflet canvas starts with a stable React class prop');
  const ref = initialCanvas.props.ref;
  nodes.find(node => node.props['data-action'] === 'toggle-globe').props.onClick();
  nodes = render();
  assert.ok(nodes.find(node => node.props.className === 'station-map-flat-layer is-hidden'));
  const hiddenCanvas = nodes.find(node => node.props.ref === ref);
  assert.equal(hiddenCanvas.props.className, initialCanvas.props.className,
    'changing mode must not overwrite classes that Leaflet added to its DOM element');
  assert.equal(nodes.filter(node => node.type === 'button' && node.props['aria-label'] === 'Địa cầu 3D').length, 1,
    '2D uses the same single projection button');
  nodes.find(node => node.props['data-action'] === 'toggle-globe').props.onClick();
  nodes = render();
  assert.ok(nodes.find(node => node.props.className === 'station-map-flat-layer is-hidden'), '2D stays on Mapbox; Leaflet is fallback only');
  assert.equal(nodes.find(node => node.props.ref === ref).props.className, initialCanvas.props.className);
}
let nodes = render();
nodes.find(node => node.props['data-action'] === 'toggle-globe').props.onClick();
nodes = render();
assert.ok(nodes.some(node => node.props.title === 'Xem quỹ đạo vệ tinh GNSS'),
  'production globe starts with Mapbox and offers the orbit view separately');
assert.ok(!nodes.some(node => node.props.title === 'Đổi giao diện địa cầu (Blue Marble / City Lights / Cyber Dark)'),
  'Mapbox does not show controls belonging to the orbit renderer');
nodes.find(node => node.props.title === 'Xem quỹ đạo vệ tinh GNSS').props.onClick();
nodes = render();
assert.ok(nodes.some(node => node.props.title === 'Trở về địa cầu Mapbox'));
assert.ok(nodes.some(node => node.props.title === 'Đổi giao diện địa cầu (Blue Marble / City Lights / Cyber Dark)'));
nodes.find(node => node.props['data-action'] === 'toggle-globe').props.onClick();
nodes = render();
nodes.find(node => node.props['aria-controls'] === 'station-map-filters').props.onClick();
nodes = render();
assert.ok(nodes.find(node => node.props.id === 'station-map-filters'));
assert.equal(nodes.filter(node => node.props.id === 'ionosphere-toggle').length, 1,
  'ionosphere controls live only in the filter panel');
assert.ok(!nodes.some(node => node.props['aria-label'] === 'Tầng điện ly'),
  'toolbar does not repeat the ionosphere filter');
assert.ok(!nodes.some(node => node.props.id === 'satellites-toggle'),
  'filter does not repeat the satellite toolbar action');
nodes.find(node => node.props['aria-controls'] === 'station-map-search').props.onClick();
nodes = render();
assert.ok(nodes.find(node => node.props.id === 'station-map-search'));
assert.ok(!nodes.some(node => node.props.id === 'station-map-filters'),
  'search and filters do not overlap');
assert.equal(projections.length, 7, 'projection toggles reuse the native map');
console.log('PASS: single Mapbox for 2D/3D, stable containers, orbit controls, no duplicate filters');

// Exercise effect ownership, including React's cleanup-before-setup ordering.
hooks.length = 0; runEffects = true;
let attachments = 0, detachments = 0, leafletMaps = 0, nativeMounted = false, mapOptions;
const fallbackMaps = [];
window.MapboxGlobeView = {
  attach(host, options) { attachments++; nativeMounted = true; mapOptions = options; },
  detach() { detachments++; nativeMounted = false; },
  setActive() {}, updateStations() {}, updateLayers() {}, setMeasureEnabled() {},
  setProjection(value) { projections.push(value); },
};
window.CorsDashboardShared = { getLeaflet: () => ({
  map() {
    assert.equal(nativeMounted, false, 'Mapbox is removed before constructing fallback');
    leafletMaps++;
    const map = { setView(center, zoom) { this.center = center; this.zoom = zoom; return this; }, on() {}, remove() { this.removed = true; }, removeLayer() {}, setMaxBounds() {}, setMinZoom() {} };
    fallbackMaps.push(map); return map;
  },
  tileLayer(url) { return { addTo() { this.active = true; return this; }, url }; },
  layerGroup: () => ({ addTo() { return this; }, clearLayers() {} }),
}) };
function commit() {
  let nodes;
  for (let pass = 0; pass < 8; pass++) {
    dirty = false; pendingEffects = []; nodes = render();
    nodes.filter(node => node.props.ref).forEach(node => { node.props.ref.current ||= {}; });
    pendingEffects.forEach(item => effects[item.index]?.cleanup?.());
    pendingEffects.forEach(item => { effects[item.index] = { deps: item.deps, cleanup: item.callback() }; });
    if (!dirty) return nodes;
  }
  throw new Error('effect updates did not settle');
}
nodes = commit();
assert.equal(attachments, 1);
assert.equal(leafletMaps, 0, 'successful native mount does not initialize hidden Leaflet');
mapOptions.onReady(); nodes = commit();
for (let cycle = 0; cycle < 3; cycle++) {
  nodes.find(node => node.props['data-action'] === 'toggle-globe').props.onClick(); nodes = commit();
}
assert.equal(attachments, 1, 'projection changes do not remount Mapbox');
assert.equal(detachments, 0);
assert.equal(leafletMaps, 0);
mapOptions.onFailure({ view: { lat: 21, lng: 105, zoom: 10 } }); nodes = commit();
assert.equal(detachments, 1);
assert.equal(leafletMaps, 1, 'failure initializes Leaflet once');
assert.deepEqual(Array.from(fallbackMaps[0].center), [21, 105]);
assert.equal(fallbackMaps[0].zoom, 10, 'fallback preserves camera');
assert.ok(fallbackMaps[0].__tiles.hybrid.url.includes('google.com/vt/lyrs=y'), 'fallback uses original Google hybrid');
assert.ok(nodes.some(node => node.props.className === 'station-map-flat-layer'));
commit(); assert.equal(leafletMaps, 1, 'fallback does not retry native automatically');
effects.forEach(effect => effect?.cleanup?.());
assert.ok(fallbackMaps[0].removed, 'unmount disposes fallback');
console.log('PASS: effect lifecycle uses one native map and mounts Google only after failure');

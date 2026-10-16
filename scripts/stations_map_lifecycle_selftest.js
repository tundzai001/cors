const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

let cursor = 0;
const hooks = [];
const React = {
  memo: value => value,
  useState(initial) {
    const index = cursor++;
    if (!(index in hooks)) hooks[index] = typeof initial === 'function' ? initial() : initial;
    return [hooks[index], value => { hooks[index] = typeof value === 'function' ? value(hooks[index]) : value; }];
  },
  useRef(initial) {
    const index = cursor++;
    return hooks[index] ||= { current: initial };
  },
  useEffect() {},
  useMemo: fn => fn(),
  createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
};
const window = { Globe3DView: { getSurfaceView: () => null } };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../cors_dashboard/frontend/js/dist/stations-map.min.js'), 'utf8'), {
  React, window, console, navigator: {}, Date, setTimeout, clearTimeout,
});
function collect(tree, nodes = []) {
  if (Array.isArray(tree)) tree.forEach(node => collect(node, nodes));
  else if (tree && typeof tree === 'object') { nodes.push(tree); collect(tree.children, nodes); }
  return nodes;
}
function render() {
  cursor = 0;
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
  assert.equal(nodes.filter(node => node.type === 'button' && node.props['aria-label'] === 'Bản đồ 2D').length, 1,
    'there is one way back to 2D, without a duplicate dock action');
  nodes.find(node => node.props['data-action'] === 'toggle-globe').props.onClick();
  nodes = render();
  assert.ok(nodes.find(node => node.props.className === 'station-map-flat-layer'));
  assert.equal(nodes.find(node => node.props.ref === ref).props.className, initialCanvas.props.className);
}
let nodes = render();
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
console.log('PASS: three 2D/3D cycles preserve Leaflet container ownership and restore the visible wrapper');

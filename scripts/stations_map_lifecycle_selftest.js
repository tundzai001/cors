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
  nodes.find(node => node.props['aria-label'] === '3D Globe').props.onClick();
  nodes = render();
  assert.ok(nodes.find(node => node.props.className === 'station-map-flat-layer is-hidden'));
  const hiddenCanvas = nodes.find(node => node.props.ref === ref);
  assert.equal(hiddenCanvas.props.className, initialCanvas.props.className,
    'changing mode must not overwrite classes that Leaflet added to its DOM element');
  nodes.find(node => node.type === 'button' && node.children.includes(' Bản đồ 2D')).props.onClick();
  nodes = render();
  assert.ok(nodes.find(node => node.props.className === 'station-map-flat-layer'));
  assert.equal(nodes.find(node => node.props.ref === ref).props.className, initialCanvas.props.className);
}
console.log('PASS: three 2D/3D cycles preserve Leaflet container ownership and restore the visible wrapper');

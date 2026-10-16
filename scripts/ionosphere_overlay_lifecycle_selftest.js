const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function main() {
  const window = {};
  let raster;
  const document = { createElement: () => ({ getContext: () => ({ createImageData: (w, h) => ({ data: new Uint8ClampedArray(w*h*4) }), putImageData: value => { raster = value.data; } }), toDataURL: () => 'data:image/png;base64,test' }) };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/ionosphere-overlay.js'), 'utf8'), {
    window, document,
    setInterval: () => 1,
    clearInterval() {},
  });

  const manager = window.IonosphereOverlay;
  manager.cachedData = { tec_grid: [[0, 0], [90, 90]] };
  manager.generateRasterDataUrl();
  const linearRaster = raster.slice();
  manager.generateRasterDataUrl('tec', { mercator: true });
  const pixel = (data, x, y) => Array.from(data.slice((y*288+x)*4, (y*288+x)*4+4));
  pixel(raster, 0, 36).forEach((value, channel) => assert.ok(Math.abs(value - pixel(linearRaster, 0, 19)[channel]) <= 4, 'Mercator latitude sampling matches the original geographic raster'));
  manager.cachedData = { tec_grid: [[0, 90], [0, 90]] };
  manager.generateRasterDataUrl('tec', { mercator: true, west: -180, east: 0 });
  const west = raster.slice();
  manager.generateRasterDataUrl('tec', { mercator: true, west: 0, east: 180 });
  assert.deepEqual(pixel(west, 287, 72), pixel(raster, 0, 72), 'hemisphere rasters join at the same TEC value');
  assert.notDeepEqual(pixel(west, 0, 72), pixel(raster, 287, 72), 'hemispheres retain distinct longitudes');
  manager.cachedData = null;
  let finishFetch;
  manager.fetchData = () => new Promise(resolve => { finishFetch = () => { manager.cachedData = {}; resolve({}); }; });
  manager.generateRasterDataUrl = () => 'data:image/png;base64,test';

  const addedLayers = [];
  let attachedMap = null;
  let removedLayerCount = 0;
  const overlay = {
    addTo(map) { attachedMap = map; addedLayers.push(map); return this; },
    setUrl() {},
    setOpacity() {},
  };
  const map = {
    hasLayer: layer => layer === overlay && attachedMap === map,
    removeLayer(layer) { if (layer === overlay && attachedMap === map) { attachedMap = null; removedLayerCount++; } },
  };
  window.CorsDashboardShared = { getLeaflet: () => ({ imageOverlay: () => overlay }) };

  const attaching = manager.attachToMap(map);
  manager.detachFromMap();
  finishFetch({});
  await attaching;

  assert.equal(addedLayers.length, 0, 'a late NOAA response must not restore a layer after it was turned off');
  assert.equal(manager.imageOverlay, null);

  manager.cachedData = {};
  await manager.attachToMap(map);
  assert.equal(addedLayers.length, 1, 'cached NOAA data attaches while the layer is enabled');
  manager.detachFromMap();
  assert.equal(removedLayerCount, 1, 'turning the layer off removes an already visible raster');
  assert.equal(attachedMap, null);
  console.log('PASS: turning off ionosphere during fetch leaves raster layer detached');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});

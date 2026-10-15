const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function main() {
  const window = {};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/ionosphere-overlay.js'), 'utf8'), {
    window,
    setInterval: () => 1,
    clearInterval() {},
  });

  const manager = window.IonosphereOverlay;
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

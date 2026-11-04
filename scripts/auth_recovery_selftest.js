const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(
  path.join(__dirname, '..', 'cors_dashboard', 'frontend', 'js', 'dist', 'api-client.min.js'),
  'utf8'
);
const frontendRoot = path.join(__dirname, '..', 'cors_dashboard', 'frontend');
const authAssetVersion = '20261104-device-id-v1';

function assertAuthAssetCacheKeys() {
  for (const page of ['index.html', 'users.html', 'security.html', 'operations.html', 'rovers-global.html', 'zalo.html']) {
    const html = fs.readFileSync(path.join(frontendRoot, page), 'utf8');
    assert(html.includes(`/assets/js/dist/api-client.min.js?v=${authAssetVersion}`), `${page} has stale api-client cache key`);
  }

  const indexHtml = fs.readFileSync(path.join(frontendRoot, 'index.html'), 'utf8');
  assert(!indexHtml.includes('text/babel'), 'index.html still contains runtime JSX transforms');
  assert(!indexHtml.includes('@babel/standalone'), 'index.html still loads Babel Standalone');
}

function storage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: key => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key)
  };
}

async function staleTokenOnLandingReloadsAsGuest() {
  const localStorage = storage({ access_token: 'stale' });
  const sessionStorage = storage();
  let reloads = 0;
  const window = {
    location: {
      pathname: '/',
      origin: 'https://example.test',
      reload: () => { reloads += 1; }
    },
    history: { replaceState() {} },
    dispatchEvent() {},
    addEventListener() {},
    ApiRoutes: { auth: { me: '/api/auth/me' } }
  };
  const context = {
    window,
    localStorage,
    sessionStorage,
    CustomEvent: class CustomEvent {},
    AbortController,
    document: { hidden: true, addEventListener() {} },
    setInterval() {},
    URL,
    setTimeout,
    clearTimeout,
    fetch: async () => ({ status: 401, ok: false })
  };
  vm.runInNewContext(source, context);

  await assert.rejects(
    window.ApiClient.apiFetch('/api/auth/me', {}, 0),
    /Unauthorized/
  );
  assert.strictEqual(localStorage.getItem('access_token'), null);
  assert.strictEqual(sessionStorage.getItem('access_token'), null);
  assert.strictEqual(reloads, 1);
}

assertAuthAssetCacheKeys();

staleTokenOnLandingReloadsAsGuest()
  .then(() => console.log('auth recovery self-test passed'))
  .catch(error => {
    console.error(error);
    process.exitCode = 1;
  });

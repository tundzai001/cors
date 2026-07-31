const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(
  path.join(__dirname, '..', 'cors_dashboard', 'frontend', 'js', 'api-client.js'),
  'utf8'
);
const frontendRoot = path.join(__dirname, '..', 'cors_dashboard', 'frontend');
const authAssetVersion = '20260731-auth-recovery-v3';

function assertAuthAssetCacheKeys() {
  for (const page of ['index.html', 'users.html', 'security.html', 'operations.html', 'rovers-global.html', 'zalo.html']) {
    const html = fs.readFileSync(path.join(frontendRoot, page), 'utf8');
    assert(html.includes(`/js/api-client.js?v=${authAssetVersion}`), `${page} has stale api-client cache key`);
    assert(html.includes(`/js/nav-manager.js?v=${authAssetVersion}`), `${page} has stale nav-manager cache key`);
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
  let replacedWith = null;
  const window = {
    location: {
      pathname: '/',
      origin: 'https://example.test',
      href: 'https://example.test/',
      replace: value => { replacedWith = value; }
    },
    history: { replaceState() {} },
    dispatchEvent() {},
    ApiRoutes: { auth: { me: '/api/auth/me' } }
  };
  const context = {
    window,
    localStorage,
    sessionStorage,
    CustomEvent: class CustomEvent {},
    AbortController,
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
  assert.strictEqual(replacedWith, 'https://example.test/');
}

assertAuthAssetCacheKeys();

staleTokenOnLandingReloadsAsGuest()
  .then(() => console.log('auth recovery self-test passed'))
  .catch(error => {
    console.error(error);
    process.exitCode = 1;
  });

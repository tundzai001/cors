// Run: node scripts/coordinate_navigation_selftest.js
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const records = [];
const container = { innerHTML: '' };
const context = {
    window: { location: { pathname: '/coordinates', search: '' } },
    document: { readyState: 'loading', addEventListener() {}, getElementById: id => id === 'main-nav' ? container : null },
    URLSearchParams, console,
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(root, 'cors_dashboard/src/js/nav-manager.js'), 'utf8'), context);
const nav = context.window.NavigationManager;
nav.addGroup = (_, name) => records.push({ group: name });
nav.addNavItem = (_, item) => records.push(item);
nav.detectCurrentPage();
assert.equal(nav.currentPage, 'coordinates');
assert.equal(nav.isPublicRoute('/coordinates'), true);
for (const user of [null, { role: 'viewer' }, { role: 'admin' }, { role: 'coordinator', can_create_users: true }]) {
    records.length = 0;
    nav.currentUser = user;
    nav.renderNavigation();
    assert.equal(records.at(-2).group, 'Công cụ');
    assert.equal(records.at(-1).id, 'coordinates');
    assert.equal(records.at(-1).active, true);
    assert.equal(records.filter(item => item.id === 'coordinates').length, 1);
    if (user) assert(records.findIndex(item => item.id === 'dashboard') < records.findIndex(item => item.id === 'coordinates'));
    if (user?.role === 'admin') for (const id of ['operations', 'security', 'api-keys', 'users', 'rovers']) assert(records.some(item => item.id === id), id);
    else assert(!records.some(item => item.id === 'security'));
}
const html = fs.readFileSync(path.join(root, 'cors_dashboard/frontend/coordinates.html'), 'utf8');
assert(html.includes('id="main-nav"'));
assert(html.includes('nav-manager.min.js'));
assert(!html.includes('<textarea'));
assert(!html.includes('<option value="EPSG:'));
for (const page of ['index', 'users', 'security', 'operations', 'rovers-global', 'zalo', 'coordinates']) {
    const pageHtml = fs.readFileSync(path.join(root, `cors_dashboard/frontend/${page}.html`), 'utf8');
    assert(pageHtml.includes('nav-manager.min.js?v=20261021-coordinate-tools-v3'), `${page} navbar cache version`);
}
console.log('PASS: shared navbar, role links, tools ordering, structured input shell');

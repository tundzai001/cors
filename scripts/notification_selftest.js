// Shared notification contract: all callers use the same position and lifetime.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const elements = new Map();
function element(tagName) {
    return {
        tagName, children: [], listeners: {}, style: {},
        setAttribute() {},
        appendChild(child) {
            if (child.parentElement) child.parentElement.children.splice(child.parentElement.children.indexOf(child), 1);
            this.children.push(child); child.parentElement = this;
            if (child.id) elements.set(child.id, child);
        },
        addEventListener(name, callback) { this.listeners[name] = callback; },
        remove() { this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1); },
    };
}
const body = element('BODY');
const dialog = element('DIALOG');
let openDialog = null;
const shown = [];
const window = {};
const document = {
    body, getElementById: id => elements.get(id),
    createElement: () => element('DIV'), querySelector: () => openDialog,
};
const bootstrap = { Toast: class {
    constructor(node, options) { shown.push({ node, options }); }
    show() {}
}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/ops-ui.js'), 'utf8'), { window, document, bootstrap });
const legacyContainer = element('DIV');
legacyContainer.id = 'toast-container'; legacyContainer.className = 'bottom-0'; body.appendChild(legacyContainer);
for (const type of ['success', 'danger', 'warning', 'info']) window.CorsUI.showToast(type, '<img src=x onerror=alert(1)>');
assert(shown.every(({ options }) => options.delay === 5000));
assert(legacyContainer.className.includes('top-0') && legacyContainer.className.includes('end-0'));
assert(!legacyContainer.className.includes('bottom-0'));
assert(shown.every(({ node }) => node.innerHTML.includes('&lt;img') && !node.innerHTML.includes('<img')));
shown[0].node.listeners['hidden.bs.toast']();
assert.equal(legacyContainer.children.length, 3, 'dismissed notifications are removed');
openDialog = dialog;
window.CorsUI.showToast('success', 'Saved');
assert.equal(legacyContainer.parentElement, dialog, 'native dialog notifications stay in the top layer');
dialog.listeners.close();
assert.equal(legacyContainer.parentElement, body);
console.log('PASS: shared 5-second lifetime, top-right placement, escaped messages, dismissal cleanup, native dialog visibility');

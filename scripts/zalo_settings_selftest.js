const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('cors_dashboard/src/js/zalo-settings.js', 'utf8');
const table = {innerHTML:''};
const state = {query:'', isAdmin:true, items:[
    {id:1, chat_id:'group-1', chat_type:'group', binding_type:'cors_public', is_active:true, station_scope_mode:'none'},
    {id:2, chat_id:'private-1', chat_type:'private', binding_type:'cors_public', is_active:false, station_scope_mode:'none'},
]};
const scope = {state, document:{getElementById:()=>table}, fmtTime:()=>'', escapeHtml:value=>String(value || '')};
vm.createContext(scope);
vm.runInContext(source.slice(source.indexOf('    function bindingLabel'), source.indexOf('    function renderStats')), scope);
vm.runInContext(source.slice(source.indexOf('    function renderBindings'), source.indexOf('    async function loadBindings')), scope);
scope.renderBindings();
assert(table.innerHTML.includes('Dừng gửi'));
assert(table.innerHTML.includes('Cho gửi'));
assert(table.innerHTML.includes('Chỉ tình trạng CORS Cục'));
assert(!table.innerHTML.includes('Tài khoản không khả dụng'), 'Public callers do not need a dashboard account');
state.isAdmin = false;
scope.renderBindings();
assert(!table.innerHTML.includes('data-toggle-sending'), 'Only admins can pause entire chats');
state.isAdmin = true;
let deliveryMode = 'cors_only';
const fields = {};
function field(id) {
    return fields[id] ||= {value:'', textContent:'', classList:{hidden:false, toggle(name, value) { this.hidden = value; }}};
}
field('edit-binding-id').value = 1;
field('edit-binding-type').value = 'cors_public';
scope.document = {getElementById:field, querySelector:()=>({value:deliveryMode})};
vm.runInContext(source.slice(source.indexOf('    function updateScopeEditor'), source.indexOf('    function openEditor')), scope);
scope.updateScopeEditor();
assert.equal(field('cors-delivery-settings').classList.hidden, false);
assert.equal(field('standard-scope-settings').classList.hidden, true);
deliveryMode = 'paused';
scope.updateScopeEditor();
assert.equal(field('cors-delivery-settings').classList.hidden, true);
assert(field('delivery-preview').textContent.includes('không gửi'));
deliveryMode = 'standard';
scope.updateScopeEditor();
assert.equal(field('standard-scope-settings').classList.hidden, false);
console.log('PASS: public chat labels, per-chat pause/resume controls, admin-only controls');

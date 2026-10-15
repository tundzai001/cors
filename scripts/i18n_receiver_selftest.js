const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const window = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../cors_dashboard/frontend/js/dist/i18n.min.js'), 'utf8'), {
  window, document: { readyState: 'loading', addEventListener() {} },
});
const translate = window.AppI18n.translate;
const assessment = 'Tín hiệu thu được từ ăng-ten rất tốt và ổn định (SNR trung bình 45.3 dB-Hz).';
assert.equal(translate(assessment, 'vi'), assessment);
assert.equal(translate('Máy thu và ăng-ten', 'vi'), 'Máy thu và ăng-ten');
assert.equal(translate('Thu hồi', 'vi'), 'Thu hồi');
assert.equal(translate('Thu', 'vi'), 'Thứ 5');
assert.equal(translate('Thứ 5', 'en'), 'Thu');
assert.equal(translate(translate(assessment, 'en'), 'vi'), assessment);
console.log('PASS: Vietnamese reception text, receiver labels, revoke, Thursday and language round-trip');

// Repeated placement must not mutate class: the header observer watches it.
const source = fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/i18n.js'), 'utf8');
const placement = source.slice(source.indexOf('    function placeLanguageButton()'), source.indexOf('    function scheduleLanguageButtonPlacement()'));
const classes = new Set();
let mutations = 0;
const container = {};
const button = {
  parentElement: container,
  classList: {
    contains: name => classes.has(name),
    add(name) { mutations++; classes.add(name); },
    remove(name) { mutations++; classes.delete(name); },
  },
};
const context = { languageButton: button, findLanguageHost: () => ({ container }), document: { body: container } };
vm.createContext(context);
vm.runInContext(placement, context);
for (let i = 0; i < 120; i++) context.placeLanguageButton();
assert.equal(mutations, 0, 'placed header button must settle without observer feedback');
context.findLanguageHost = () => null;
for (let i = 0; i < 120; i++) context.placeLanguageButton();
assert.equal(mutations, 1, 'floating button changes class once');
context.findLanguageHost = () => ({ container });
for (let i = 0; i < 120; i++) context.placeLanguageButton();
assert.equal(mutations, 2, 'returning to header changes class once');
console.log('PASS: language button placement settles without a mutation/render loop');

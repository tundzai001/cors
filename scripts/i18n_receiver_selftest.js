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

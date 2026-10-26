const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const file = process.argv[2] || 'cors_dashboard/src/js/stations-map-search.js';
const calls = [];
let administrative = [], geocode = [];
const window = {};
vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    window, console, URLSearchParams,
    fetch: async url => {
        calls.push(url);
        return {ok: true, json: async () => url.includes('search-administrative-units') ? administrative : geocode};
    },
});
const find = query => window.StationsMapSearch.geocodeVietnam(query);
(async () => {
    const city = {name: 'Ninh Bình', display_name: 'Thành phố Ninh Bình', lat: 20.251, lon: 105.975};
    const road = {name: 'Đường Đồng Văn', display_name: 'Đường Đồng Văn, Phù Vân, Ninh Bình', lat: 20.57, lon: 105.89};
    const ward = {name: 'Ninh Vân', display_name: 'Xã Ninh Vân, Hoa Lư, Ninh Bình', lat: 20.15, lon: 105.98};
    administrative = [{...ward, lat: null, lon: null}, city];
    geocode = [city, road, ward];
    const result = await find('Ninh Vân, Ninh Bình');
    assert.equal(result.label, ward.display_name);
    assert(calls.some(url => url.includes('/geocode?')), 'Specific query must reach geocoding instead of returning a city fallback');
    geocode = [city, road];
    await assert.rejects(find('Ninh Vân, Ninh Bình'), /Chưa tìm thấy đúng địa điểm/);
    administrative = [];
    calls.length = 0;
    assert.equal((await find('Ninh Bình')).source, 'local');
    assert(!calls.some(url => url.includes('/geocode?')), 'Exact city fallback remains available');
    assert.equal((await find('Thành phố Ninh Bình, Việt Nam')).source, 'local');
    console.log('PASS: specific ward/qualifiers retained; broad city and road hits rejected; exact fallback preserved');
})().catch(error => {console.error(error); process.exitCode = 1;});

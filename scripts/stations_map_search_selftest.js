const assert = require('node:assert/strict');
const path = require('node:path');

let responseItems = [];
const requestedUrls = [];

global.window = {
    open() {},
    CorsUI: { showToast() {} }
};
global.fetch = async (url) => {
    requestedUrls.push(String(url));
    return {
        ok: true,
        status: 200,
        json: async () => responseItems
    };
};

require(path.resolve(__dirname, '../cors_dashboard/frontend/js/stations-map-search.js'));

const geocodeVietnam = global.window.StationsMapSearch.geocodeVietnam;

async function expectNotFound(query) {
    await assert.rejects(
        () => geocodeVietnam(query),
        /Khong tim thay toa do/
    );
}

async function run() {
    responseItems = [{
        lat: '20.865139',
        lon: '106.683830',
        display_name: 'Cảng Hải Phòng, Hải Phòng, Việt Nam',
        address: { country_code: 'vn' },
        namedetails: { name: 'Cảng Hải Phòng' }
    }];
    const port = await geocodeVietnam('cảng hải phòng');
    assert.equal(port.source, 'osm');
    assert.equal(port.label, responseItems[0].display_name);
    assert.match(requestedUrls.at(-1), /^https:\/\/nominatim\.openstreetmap\.org\/search\?/);
    assert.match(requestedUrls.at(-1), /countrycodes=vn/);
    assert.match(requestedUrls.at(-1), /bounded=1/);
    assert.ok(!requestedUrls.some(url => /wikipedia|wikidata/i.test(url)));

    responseItems = [{
        lat: '10.8',
        lon: '106.65',
        display_name: 'Việt Nam Cộng hòa',
        address: { country_code: 'vn' },
        namedetails: { name: 'Việt Nam Cộng hòa' }
    }];
    await expectNotFound('cảng tàu biển gì đấy');

    responseItems = [{
        lat: '10.78',
        lon: '106.69',
        display_name: 'Vụ án giết người tại Thành phố Hồ Chí Minh',
        address: { country_code: 'vn' },
        namedetails: { name: 'Vụ án giết người' }
    }];
    await expectNotFound('cảng tàu biển gì đấy');

    responseItems = [{
        lat: '10.78',
        lon: '106.69',
        display_name: 'Cảng Hải Phòng, Việt Nam',
        address: { country_code: 'us' },
        namedetails: { name: 'Cảng Hải Phòng' }
    }];
    await expectNotFound('cảng hải phòng');

    const callsBeforeCoordinates = requestedUrls.length;
    const manual = await geocodeVietnam('10.8231, 106.6297');
    assert.equal(manual.source, 'manual');
    assert.equal(requestedUrls.length, callsBeforeCoordinates);

    console.log('stations-map-search self-test: PASS');
}

run().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});

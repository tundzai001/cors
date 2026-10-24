const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = vm.createContext({window: {}});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../cors_dashboard/src/js/coordinate-angles.js'), 'utf8'), context);
const {parse, format, parsePair} = context.window.CorsCoordinateAngles;
const close = (a, b) => assert(Math.abs(a - b) < 1e-9, `${a} != ${b}`);
const pair = parsePair(`22°20'40.1"N 105°53'25.8"E`);
assert.equal(pair.lat.toFixed(6), '22.344472');
assert.equal(pair.lon.toFixed(6), '105.890500');
const second = parsePair('20°23′22.5″N 105°37′43.4″E');
assert.equal(second.lat.toFixed(6), '20.389583');
assert.equal(second.lon.toFixed(6), '105.628722');
close(parse('222040.1', 'lat', 'packed_dms'), pair.lat);
close(parse('1055325,8', 'lon', 'packed_dms'), pair.lon);
close(parse('22 20 40,1', 'lat', 'dms'), pair.lat);
close(parse('105:53:25.8E', 'lon', 'dms'), pair.lon);
close(parse('S22°20′40.1″', 'lat', 'dms'), -pair.lat);
close(parse('-22°20′40.1″S', 'lat', 'dms'), -pair.lat);
close(parse('22.344472222222', 'lat'), pair.lat);
assert.equal(parse('1e-8', 'lat'), 1e-8);
assert.equal(parse('90°00′0″N', 'lat', 'dms'), 90);
assert.equal(parse('1800000W', 'lon', 'packed_dms'), -180);
const reversed = parsePair('105°53′25.8″W, 22°20′40.1″S');
close(reversed.lat, -pair.lat); close(reversed.lon, -pair.lon);
for (const [value, axis, mode] of [
    ['22°60′1″N', 'lat', 'dms'], ['22°20′60″N', 'lat', 'dms'],
    ['90°00′0.01″N', 'lat', 'dms'], ['180°00′1″E', 'lon', 'dms'],
    ['22°20′40″E', 'lat', 'dms'], ['105°53′25″N', 'lon', 'dms'],
    ['-22°20′40″N', 'lat', 'dms'], ['+22°20′40″S', 'lat', 'dms'],
    ['226040.1', 'lat', 'packed_dms'], ['222060.1', 'lat', 'packed_dms'],
    ['222040.1', 'lat', 'dd'], ['22.3', 'lat', 'dms'], ['', 'lat', 'dd'],
    ['NaN', 'lat', 'dd'], ['Infinity', 'lon', 'dd'], ['105foo', 'lon', 'dd'],
]) assert.throws(() => parse(value, axis, mode), value);
assert.equal(parsePair('22°20′40.1″ 105°53′25.8″'), null, 'Never guess unnamed axis order');
assert.equal(parsePair('22°20′40.1″N 105°53′25.8″E garbage'), null);
assert.throws(() => parsePair('22°20′40.1″N 23°20′40.1″S'));
assert.throws(() => parsePair('22°60′40.1″N 105°53′25.8″E'));
for (const axis of ['lat', 'lon']) for (const value of [0, 1e-8, -1e-8, 9.12, pair.lat, -pair.lat, 89.9999999999, -90]) {
    for (const mode of ['dd', 'dms', 'packed_dms']) close(parse(format(value, axis, mode), axis, mode), value);
}
assert.equal(format(12 + 59 / 60 + 59.9999997 / 3600, 'lat', 'dms'), '13°00′0″N');
console.log('PASS: screenshot DMS examples, packed DMS, directions, invalid ranges, round trips and carry');

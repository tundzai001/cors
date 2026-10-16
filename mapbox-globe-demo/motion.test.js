import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spinStep, flightOptions } from './motion.js';

test('IGS-style spin slows from zoom 3 to 5 and stops close up', () => {
    assert.equal(spinStep(1), 3);
    assert.equal(spinStep(3), 3);
    assert.equal(spinStep(4), 1.5);
    assert.equal(spinStep(5), 0);
    assert.equal(spinStep(12), 0);
});
test('native camera flies to destination and respects reduced motion', () => {
    const normal = flightOptions([105.8342, 21.0278], 11);
    assert.deepEqual(normal.center, [105.8342, 21.0278]);
    assert.equal(normal.zoom, 11);
    assert.equal(normal.pitch, 0);
    assert.ok(normal.speed > 0);
    assert.equal(normal.maxDuration, 5000);
    assert.equal(flightOptions(normal.center, 11, true).duration, 0);
});

const test = require("node:test");
const assert = require("node:assert");
const { makePlotCoords, adaptiveTickInterval, tickValues } = require("../../src/fledermap/web/static/plot_coords.js");

// A raster whose height is NOT a whole multiple of the nominal 4.7 px/kHz: 22.05 kHz * 4.7 =
// 103.6, which the server rounds to 104. The old nominal-constant mapping put the bottom edge at
// -0.08 kHz; the mapping derived from real dimensions must hit 0 exactly.
const coords = makePlotCoords({ widthPx: 441, heightPx: 104, durationS: 0.1, maxFreqKhz: 22.05 });

test("the raster's own edges map exactly onto the axis range", () => {
  assert.strictEqual(coords.yToKhz(0), 22.05);
  assert.strictEqual(coords.yToKhz(104), 0);
  assert.strictEqual(coords.xToS(0), 0);
  assert.strictEqual(coords.xToS(441), 0.1);
});

test("pixel -> reading and reading -> pixel are exact inverses (axis tick == ruler reading)", () => {
  for (const khz of [0, 5, 10, 20, 22.05]) {
    assert.ok(Math.abs(coords.yToKhz(coords.khzToY(khz)) - khz) < 1e-9, `khz ${khz}`);
  }
  for (const s of [0, 0.013, 0.05, 0.1]) {
    assert.ok(Math.abs(coords.xToS(coords.sToX(s)) - s) < 1e-12, `s ${s}`);
  }
});

test("every axis tick lands where the ruler would read exactly that value", () => {
  // The invariant the user actually cares about: the ruler pointed at a tick's own position
  // reports that tick's value.
  for (const khz of tickValues(22.05, 5)) {
    assert.ok(Math.abs(coords.yToKhz(coords.khzToY(khz)) - khz) < 1e-9);
  }
});

test("frequency increases upward: y = 0 is the top of the image", () => {
  assert.ok(coords.yToKhz(10) > coords.yToKhz(20));
  assert.ok(coords.khzToY(100) < coords.khzToY(10));
});

test("pxPerMs matches the raster's real width, not a nominal constant", () => {
  assert.strictEqual(coords.pxPerMs, 441 / 100);
});

test("tickValues includes 0 and the last multiple not exceeding max", () => {
  assert.deepStrictEqual(tickValues(120, 20), [0, 20, 40, 60, 80, 100, 120]);
  assert.deepStrictEqual(tickValues(22.05, 10), [0, 10, 20]);
});

test("tickValues does not drop or duplicate the last tick for a fractional interval", () => {
  assert.strictEqual(tickValues(1, 0.1).length, 11);
});

test("adaptiveTickInterval leaves the base interval alone when labels already have room", () => {
  assert.strictEqual(adaptiveTickInterval(10, 4.7, 40), 10);
});

test("adaptiveTickInterval widens by whole multiples once labels would crowd", () => {
  assert.strictEqual(adaptiveTickInterval(10, 1.5, 40), 30);
});

test("adaptiveTickInterval is continuous around scale 1 (no doubling for a tiny shrink)", () => {
  assert.strictEqual(adaptiveTickInterval(10, 4.7 * 0.97, 40), 10);
});

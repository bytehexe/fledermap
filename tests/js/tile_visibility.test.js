const { test } = require("node:test");
const assert = require("node:assert/strict");
const { orderTilesVisibleFirst } = require("../../src/fledermap/web/static/tile_visibility.js");

test("orderTilesVisibleFirst puts a single fully-visible tile first, others follow in order", () => {
  // Tiles [0,100), [100,200), [200,300); viewport shows only tile 1.
  const order = orderTilesVisibleFirst([100, 100, 100], 100, 200);
  assert.deepEqual(order, [1, 0, 2]);
});

test("orderTilesVisibleFirst keeps original order when nothing is visible", () => {
  const order = orderTilesVisibleFirst([100, 100, 100], 1000, 1100);
  assert.deepEqual(order, [0, 1, 2]);
});

test("orderTilesVisibleFirst includes every tile overlapping the range, in original order", () => {
  // Viewport spans tiles 1 and 2 partially.
  const order = orderTilesVisibleFirst([100, 100, 100, 100], 150, 250);
  assert.deepEqual(order, [1, 2, 0, 3]);
});

test("orderTilesVisibleFirst excludes a tile that only touches the range's edge", () => {
  // Tile 0 ends exactly at rangeStartPx=100 -- touching, not overlapping.
  const order = orderTilesVisibleFirst([100, 100], 100, 200);
  assert.deepEqual(order, [1, 0]);
});

test("orderTilesVisibleFirst returns everything unchanged when the whole recording is visible", () => {
  const order = orderTilesVisibleFirst([100, 100, 100], 0, 300);
  assert.deepEqual(order, [0, 1, 2]);
});

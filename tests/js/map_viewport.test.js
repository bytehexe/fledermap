const { test } = require("node:test");
const assert = require("node:assert/strict");
const { bboxParam, debounce } = require("../../src/fledermap/web/static/map_viewport.js");

test("bboxParam formats west,south,east,north as a comma-separated string", () => {
  assert.equal(
    bboxParam({ west: 9.5, south: 49.5, east: 10.5, north: 50.5 }),
    "9.5,49.5,10.5,50.5",
  );
});

test("bboxParam matches web/params.py's parse_bbox order (min_lon,min_lat,max_lon,max_lat)", () => {
  // west/east are longitude, south/north are latitude -- pinned explicitly
  // since a swapped pair would silently produce a plausible-looking but
  // wrong bbox string that only breaks in the browser.
  const result = bboxParam({ west: 1, south: 2, east: 3, north: 4 });
  const [minLon, minLat, maxLon, maxLat] = result.split(",").map(Number);
  assert.equal(minLon, 1);
  assert.equal(minLat, 2);
  assert.equal(maxLon, 3);
  assert.equal(maxLat, 4);
});

test("debounce delays the call until after the wait", (t, done) => {
  let calls = 0;
  const debounced = debounce(() => {
    calls++;
  }, 10);
  debounced();
  assert.equal(calls, 0);
  setTimeout(() => {
    assert.equal(calls, 1);
    done();
  }, 30);
});

test("debounce coalesces a burst of calls into one", (t, done) => {
  let calls = 0;
  const debounced = debounce(() => {
    calls++;
  }, 10);
  debounced();
  debounced();
  debounced();
  setTimeout(() => {
    assert.equal(calls, 1);
    done();
  }, 30);
});

test("debounce passes through the arguments of the last call", (t, done) => {
  const seen = [];
  const debounced = debounce((value) => seen.push(value), 10);
  debounced("first");
  debounced("second");
  setTimeout(() => {
    assert.deepEqual(seen, ["second"]);
    done();
  }, 30);
});

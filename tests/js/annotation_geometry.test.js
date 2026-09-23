const test = require("node:test");
const assert = require("node:assert/strict");
const {
  normalizeDragBounds,
  annotationShape,
  abbreviateScientificName,
  annotationBoxPx,
  annotationPointPx,
  clampToPlot,
} = require("../../src/fledermap/web/static/annotation_geometry.js");

test("normalizeDragBounds orders start<=end regardless of drag direction", () => {
  const forward = normalizeDragBounds(1.0, 2.0, 20000, 50000);
  const backward = normalizeDragBounds(2.0, 1.0, 50000, 20000);
  assert.deepEqual(forward, backward);
  assert.deepEqual(forward, {
    time_start_s: 1.0,
    time_end_s: 2.0,
    freq_min_hz: 20000,
    freq_max_hz: 50000,
  });
});

test("annotationShape is a point when both dimensions collapse", () => {
  assert.equal(
    annotationShape({ time_start_s: 1, time_end_s: 1, freq_min_hz: 20000, freq_max_hz: 20000 }),
    "point",
  );
});

test("annotationShape is a rect when either dimension has a real range", () => {
  assert.equal(
    annotationShape({ time_start_s: 1, time_end_s: 2, freq_min_hz: 20000, freq_max_hz: 20000 }),
    "rect",
  );
});

test("abbreviateScientificName shortens the genus", () => {
  assert.equal(abbreviateScientificName("Pipistrellus pipistrellus"), "P. pipistrellus");
});

test("abbreviateScientificName leaves a single-word name alone", () => {
  assert.equal(abbreviateScientificName("Myotis"), "Myotis");
});

test("annotationBoxPx converts a rect to native pixel geometry", () => {
  const coords = {
    sToX: (s) => s * 100,
    khzToY: (khz) => 480 - khz * 10,
  };
  const box = annotationBoxPx(
    { time_start_s: 1, time_end_s: 2, freq_min_hz: 20000, freq_max_hz: 30000 },
    coords,
  );
  // left = sToX(1) = 100, width = sToX(2)-sToX(1) = 100
  // top = khzToY(30) = 180 (higher freq -> smaller y), height = khzToY(20)-khzToY(30) = 100
  assert.deepEqual(box, { left: 100, top: 180, width: 100, height: 100 });
});

test("annotationPointPx converts a point to native pixel coordinates", () => {
  const coords = {
    sToX: (s) => s * 100,
    khzToY: (khz) => 480 - khz * 10,
  };
  const point = annotationPointPx(
    { time_start_s: 1.5, time_end_s: 1.5, freq_min_hz: 25000, freq_max_hz: 25000 },
    coords,
  );
  assert.deepEqual(point, { x: 150, y: 230 });
});

test("clampToPlot leaves an in-bounds position unchanged", () => {
  assert.deepEqual(clampToPlot(50, 60, 200, 100), { x: 50, y: 60 });
});

test("clampToPlot clamps negative coordinates to the plot's origin", () => {
  assert.deepEqual(clampToPlot(-10, -5, 200, 100), { x: 0, y: 0 });
});

test("clampToPlot clamps coordinates past the plot's far edge", () => {
  assert.deepEqual(clampToPlot(250, 150, 200, 100), { x: 200, y: 100 });
});

test("clampToPlot clamps each axis independently", () => {
  assert.deepEqual(clampToPlot(-10, 150, 200, 100), { x: 0, y: 100 });
});

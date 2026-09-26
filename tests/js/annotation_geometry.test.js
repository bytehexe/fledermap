const test = require("node:test");
const assert = require("node:assert/strict");
const {
  normalizeDragBounds,
  annotationShape,
  abbreviateScientificName,
  annotationBoxPx,
  annotationPointPx,
  clampToPlot,
  annotationLabelLines,
  isBelowThreshold,
  sortAnnotationsForPaint,
  layerMenuSources,
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

test("annotationBoxPx spans the full plot height when frequency is absent", () => {
  const coords = {
    sToX: (s) => s * 100,
    khzToY: (khz) => 480 - khz * 10,
  };
  const box = annotationBoxPx(
    { time_start_s: 1, time_end_s: 2, freq_min_hz: null, freq_max_hz: null },
    coords,
  );
  // top = 0 (not khzToY(null / 1000), which would wrongly evaluate to khzToY(0) = 480),
  // height = khzToY(0) = 480 (the plot's full native pixel height).
  assert.deepEqual(box, { left: 100, top: 0, width: 100, height: 480 });
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

test("annotationLabelLines shows the abbreviated species and rounded confidence", () => {
  const annotation = { confidence: 0.823, literal: null };
  const taxon = { scientific_name: "Pipistrellus pipistrellus" };
  assert.deepEqual(annotationLabelLines(annotation, taxon), ["P. pipistrellus 82%"]);
});

test("annotationLabelLines omits confidence when the annotation has none (a label)", () => {
  const annotation = { confidence: null, literal: null };
  const taxon = { scientific_name: "Pipistrellus pipistrellus" };
  assert.deepEqual(annotationLabelLines(annotation, taxon), ["P. pipistrellus"]);
});

test("annotationLabelLines includes the literal note as its own line", () => {
  const annotation = { confidence: null, literal: "roosting nearby" };
  assert.deepEqual(annotationLabelLines(annotation, null), ["roosting nearby"]);
});

// `Annotation` carries no `raw_label` column today, and the server's `_serialize_annotation`
// never emits a `raw_label` key -- no production data reaches this fallback yet. It's kept
// so the function still handles the field correctly if a future caller ever supplies one.
test("annotationLabelLines falls back to the raw_label field when the caller supplies one and there is no taxon", () => {
  const annotation = { confidence: 0.5, literal: null, raw_label: "Unknown call" };
  assert.deepEqual(annotationLabelLines(annotation, null), ["Unknown call 50%"]);
});

test("isBelowThreshold is true strictly below the source's configured threshold", () => {
  const thresholds = { batdetect2: 0.8 };
  assert.equal(isBelowThreshold({ source: "batdetect2", confidence: 0.79 }, thresholds), true);
});

test("isBelowThreshold is false exactly at the threshold", () => {
  const thresholds = { batdetect2: 0.8 };
  assert.equal(isBelowThreshold({ source: "batdetect2", confidence: 0.8 }, thresholds), false);
});

test("isBelowThreshold is false when the source has no configured threshold (e.g. label)", () => {
  const thresholds = { batdetect2: 0.8 };
  assert.equal(isBelowThreshold({ source: "label", confidence: 0.1 }, thresholds), false);
});

test("isBelowThreshold is false when the annotation has no confidence", () => {
  const thresholds = { batdetect2: 0.8 };
  assert.equal(isBelowThreshold({ source: "batdetect2", confidence: null }, thresholds), false);
});

test("sortAnnotationsForPaint moves label entries after every other source", () => {
  const annotations = [
    { id: 1, source: "label" },
    { id: 2, source: "batdetect2" },
    { id: 3, source: "batdetect2" },
    { id: 4, source: "label" },
  ];
  const sorted = sortAnnotationsForPaint(annotations);
  assert.deepEqual(sorted.map((a) => a.id), [2, 3, 1, 4]);
});

test("sortAnnotationsForPaint leaves a single-source list unchanged", () => {
  const annotations = [{ id: 1, source: "batdetect2" }, { id: 2, source: "batdetect2" }];
  assert.deepEqual(sortAnnotationsForPaint(annotations).map((a) => a.id), [1, 2]);
});

test("layerMenuSources puts label first, batdetect2 next, others alphabetically after", () => {
  assert.deepEqual(
    layerMenuSources(["battybirdnet", "batdetect2", "label"]),
    ["label", "batdetect2", "battybirdnet"],
  );
});

test("layerMenuSources handles a subset of sources", () => {
  assert.deepEqual(layerMenuSources(["batdetect2"]), ["batdetect2"]);
  assert.deepEqual(layerMenuSources(["label"]), ["label"]);
});

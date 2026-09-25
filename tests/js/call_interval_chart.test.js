const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
  computeCallIntervalHistograms,
  callIntervalHistogramsToChartData,
  callIntervalChartOptions,
  BIN_WIDTH_MS,
  MAX_INTERVAL_MS,
} = require("../../src/fledermap/web/static/call_interval_chart.js");

function bd2Annotation(taxonId, startS, endS) {
  return {
    source: "batdetect2",
    time_start_s: startS,
    time_end_s: endS,
    freq_min_hz: 20000,
    freq_max_hz: 40000,
    taxon_id: taxonId,
    confidence: 0.9,
  };
}

test("computeCallIntervalHistograms bins the midpoint-to-midpoint gap between consecutive calls", () => {
  // Calls at midpoints 1.000s, 1.010s -> a 10ms gap, landing in the [10,20) bin.
  const annotations = [bd2Annotation(1, 1.0, 1.0), bd2Annotation(1, 1.01, 1.01)];
  const result = computeCallIntervalHistograms(annotations);
  assert.equal(result.length, 1);
  assert.equal(result[0].taxonId, 1);
  const binIndex = Math.floor(10 / BIN_WIDTH_MS);
  assert.equal(result[0].binPercentages[binIndex], 100);
  assert.equal(result[0].binPercentages.filter((p) => p > 0).length, 1);
});

test("computeCallIntervalHistograms groups intervals by taxon independently", () => {
  const annotations = [
    bd2Annotation(1, 1.0, 1.0),
    bd2Annotation(2, 1.0, 1.0),
    bd2Annotation(1, 1.01, 1.01),
    bd2Annotation(2, 1.05, 1.05),
  ];
  const result = computeCallIntervalHistograms(annotations);
  assert.deepEqual(result.map((r) => r.taxonId).sort(), [1, 2]);
});

test("computeCallIntervalHistograms excludes gaps over MAX_INTERVAL_MS entirely, as bout breaks", () => {
  // 1.0 -> 1.01 is a 10ms gap (kept); 1.01 -> 3.5 is a >1s gap (dropped, not just clipped).
  const annotations = [bd2Annotation(1, 1.0, 1.0), bd2Annotation(1, 1.01, 1.01), bd2Annotation(1, 3.5, 3.5)];
  const result = computeCallIntervalHistograms(annotations);
  const total = result[0].binPercentages.reduce((a, b) => a + b, 0);
  assert.ok(Math.abs(total - 100) < 1e-9);
});

test("computeCallIntervalHistograms omits a species with fewer than 2 usable calls", () => {
  const annotations = [bd2Annotation(1, 1.0, 1.0)];
  const result = computeCallIntervalHistograms(annotations);
  assert.deepEqual(result, []);
});

test("computeCallIntervalHistograms omits a species whose only gap exceeds MAX_INTERVAL_MS", () => {
  const annotations = [bd2Annotation(1, 1.0, 1.0), bd2Annotation(1, 5.0, 5.0)];
  const result = computeCallIntervalHistograms(annotations);
  assert.deepEqual(result, []);
});

test("computeCallIntervalHistograms ignores non-batdetect2 sources", () => {
  const annotations = [
    { ...bd2Annotation(1, 1.0, 1.0), source: "comment" },
    { ...bd2Annotation(1, 1.01, 1.01), source: "comment" },
  ];
  assert.deepEqual(computeCallIntervalHistograms(annotations), []);
});

test("computeCallIntervalHistograms ignores annotations with no taxon_id", () => {
  const annotations = [bd2Annotation(null, 1.0, 1.0), bd2Annotation(null, 1.01, 1.01)];
  assert.deepEqual(computeCallIntervalHistograms(annotations), []);
});

test("callIntervalHistogramsToChartData names each dataset from taxonById and colors it via colorForTaxon", () => {
  const histograms = [{ taxonId: 1, binPercentages: [100, 0] }];
  const taxonById = new Map([[1, { id: 1, scientific_name: "Pipistrellus pipistrellus" }]]);
  const colorForTaxon = (id) => `color-${id}`;
  const result = callIntervalHistogramsToChartData(histograms, taxonById, colorForTaxon);
  assert.equal(result.datasets.length, 1);
  assert.equal(result.datasets[0].label, "Pipistrellus pipistrellus");
  assert.equal(result.datasets[0].borderColor, "color-1");
});

test("callIntervalHistogramsToChartData falls back to a generic label when the taxon isn't in the index", () => {
  const histograms = [{ taxonId: 42, binPercentages: [100] }];
  const result = callIntervalHistogramsToChartData(histograms, new Map(), () => "c");
  assert.equal(result.datasets[0].label, "Taxon #42");
});

test("callIntervalHistogramsToChartData places each bin's percentage at that bin's start, in ms", () => {
  const histograms = [{ taxonId: 1, binPercentages: [30, 70] }];
  const result = callIntervalHistogramsToChartData(histograms, new Map(), () => "c");
  assert.deepEqual(result.datasets[0].data, [
    { x: 0, y: 30 },
    { x: BIN_WIDTH_MS, y: 70 },
  ]);
});

test("callIntervalChartOptions sets a linear x axis spanning 0 to MAX_INTERVAL_MS", () => {
  const options = callIntervalChartOptions();
  assert.equal(options.scales.x.type, "linear");
  assert.equal(options.scales.x.min, 0);
  assert.equal(options.scales.x.max, MAX_INTERVAL_MS);
});

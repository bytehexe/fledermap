const { test } = require("node:test");
const assert = require("node:assert/strict");

// Load marker_colors and set up globals before requiring the module under test
global.colorForTaxon = require("../../src/fledermap/web/static/marker_colors.js").colorForTaxon;
global.MULTI_SPECIES_COLOR = require("../../src/fledermap/web/static/marker_colors.js").MULTI_SPECIES_COLOR;

const { taxonBreakdownToChartData, seriesToChartData } = require(
  "../../src/fledermap/web/static/statistics_charts.js",
);

test("taxonBreakdownToChartData maps entries to labels/data/colors", () => {
  const breakdown = {
    entries: [
      { taxon: { id: 1, scientific_name: "Eptesicus serotinus" }, count: 5 },
      { taxon: { id: 2, scientific_name: "Pipistrellus pipistrellus" }, count: 3 },
    ],
    other_count: 0,
    unmapped_count: 0,
    multi_species_count: 0,
  };
  const result = taxonBreakdownToChartData(breakdown, "#999999");
  assert.deepEqual(result.labels, [
    "Eptesicus serotinus",
    "Pipistrellus pipistrellus",
  ]);
  assert.deepEqual(result.data, [5, 3]);
  assert.equal(result.colors.length, 2);
});

test("taxonBreakdownToChartData appends Other/Unmapped/Multiple Species slices when present", () => {
  const breakdown = {
    entries: [],
    other_count: 4,
    unmapped_count: 2,
    multi_species_count: 1,
  };
  const result = taxonBreakdownToChartData(breakdown, "#999999");
  assert.deepEqual(result.labels, ["Other", "Unmapped species", "Multiple Species"]);
  assert.deepEqual(result.data, [4, 2, 1]);
  assert.equal(result.colors[0], "#999999");
});

test("taxonBreakdownToChartData omits zero-count extra slices", () => {
  const breakdown = { entries: [], other_count: 0, unmapped_count: 0, multi_species_count: 0 };
  const result = taxonBreakdownToChartData(breakdown, "#999999");
  assert.deepEqual(result.labels, []);
  assert.deepEqual(result.data, []);
});

test("seriesToChartData builds one dataset per taxon plus Other when included", () => {
  const series = {
    labels: ["Jan", "Feb"],
    taxa: [{ id: 1, scientific_name: "Eptesicus serotinus" }],
    other_included: true,
    single_species: false,
    buckets: [{ 1: 3, null: 1 }, {}],
  };
  const result = seriesToChartData(series, "#999999");
  assert.deepEqual(result.labels, ["Jan", "Feb"]);
  assert.equal(result.datasets.length, 2);
  assert.equal(result.datasets[0].label, "Eptesicus serotinus");
  assert.deepEqual(result.datasets[0].data, [3, 0]);
  assert.equal(result.datasets[1].label, "Other");
  assert.deepEqual(result.datasets[1].data, [1, 0]);
});

test("seriesToChartData builds a single unlabeled dataset for a single-species series", () => {
  const series = {
    labels: ["Jan", "Feb"],
    taxa: [],
    other_included: false,
    single_species: true,
    buckets: [{ null: 2 }, { null: 0 }],
  };
  const result = seriesToChartData(series, "#999999");
  assert.equal(result.datasets.length, 1);
  assert.deepEqual(result.datasets[0].data, [2, 0]);
});

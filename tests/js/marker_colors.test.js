const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
  colorForTaxon,
  colorForFeature,
  TAXON_PALETTE,
  MULTI_SPECIES_COLOR,
} = require("../../src/fledermap/web/static/marker_colors.js");

test("colorForTaxon uses the fixed palette for ids within its range", () => {
  assert.equal(colorForTaxon(1), TAXON_PALETTE[1 % TAXON_PALETTE.length]);
  assert.equal(colorForTaxon(10), TAXON_PALETTE[10 % TAXON_PALETTE.length]);
});

test("colorForTaxon hashes ids past the fixed palette to a distinct hue", () => {
  const a = colorForTaxon(11);
  const b = colorForTaxon(12);
  assert.notEqual(a, b);
  assert.match(a, /^hsl\(\d+(\.\d+)?, 70%, 45%\)$/);
});

test("colorForTaxon never collides for two ids 1 and 100 apart within the hashed range", () => {
  // Regression for the original bug this hashing scheme replaced: two taxon
  // ids sharing a residue class mod TAXON_PALETTE.length must not render
  // identically once past the fixed palette.
  assert.notEqual(colorForTaxon(11), colorForTaxon(21));
});

test("colorForFeature returns the reserved multi-species color when multi_species is true", () => {
  assert.equal(
    colorForFeature({ multi_species: true, verdict: "species", taxon_id: 5 }),
    MULTI_SPECIES_COLOR,
  );
});

test("colorForFeature returns gray for noise", () => {
  assert.equal(colorForFeature({ verdict: "noise", taxon_id: null }), "gray");
});

test("colorForFeature returns orange for no_id", () => {
  assert.equal(colorForFeature({ verdict: "no_id", taxon_id: null }), "orange");
});

test("colorForFeature colors by taxon when a taxon_id is present", () => {
  assert.equal(
    colorForFeature({ verdict: "species", taxon_id: 1 }),
    colorForTaxon(1),
  );
});

test("colorForFeature returns the unmapped-species color when taxon_id is null", () => {
  assert.equal(colorForFeature({ verdict: "species", taxon_id: null }), "#333333");
});

test("colorForFeature returns the unmapped-species color when taxon_id is undefined", () => {
  assert.equal(colorForFeature({ verdict: "species" }), "#333333");
});

test("multi_species takes precedence over verdict and taxon_id", () => {
  assert.equal(
    colorForFeature({ multi_species: true, verdict: "noise", taxon_id: 1 }),
    MULTI_SPECIES_COLOR,
  );
});

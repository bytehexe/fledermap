const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
  matchesQuery,
  buildSaveBody,
} = require("../../src/fledermap/web/static/classifier_logic.js");

const pipistrelle = {
  id: 1,
  scientific_name: "Pipistrellus pipistrellus",
  common_name_en: "Common pipistrelle",
  common_name_de: "Zwergfledermaus",
  codes: ["PIPPIP", "PIPI"],
};

test("matchesQuery matches on scientific name, case-insensitively", () => {
  assert.ok(matchesQuery(pipistrelle, "pipistrellus"));
  assert.ok(matchesQuery(pipistrelle, "PIPISTRELLUS"));
});

test("matchesQuery matches on English and German common names", () => {
  assert.ok(matchesQuery(pipistrelle, "common pipistrelle"));
  assert.ok(matchesQuery(pipistrelle, "zwergfledermaus"));
});

test("matchesQuery matches on any code", () => {
  assert.ok(matchesQuery(pipistrelle, "pippip"));
  assert.ok(matchesQuery(pipistrelle, "pipi"));
});

test("matchesQuery does not match an unrelated query", () => {
  assert.equal(matchesQuery(pipistrelle, "myotis"), false);
});

test("matchesQuery does not throw when common names are null (genus/group taxa)", () => {
  const genus = {
    id: 2,
    scientific_name: "Myotis",
    common_name_en: null,
    common_name_de: null,
    codes: ["MYSP"],
  };
  assert.ok(matchesQuery(genus, "myotis"));
  assert.equal(matchesQuery(genus, "nonexistent"), false);
});

test("matchesQuery does not throw when codes is missing entirely", () => {
  const noCodes = {
    id: 3,
    scientific_name: "Plecotus",
    common_name_en: "Long-eared bats",
    common_name_de: null,
  };
  assert.equal(matchesQuery(noCodes, "plecotus"), true);
  assert.equal(matchesQuery(noCodes, "xyz"), false);
});

test("buildSaveBody with an active verdict button sends only that verdict", () => {
  assert.deepEqual(
    buildSaveBody({ activeVerdict: "no_id", taxonIds: [] }),
    { verdict: "no_id" },
  );
});

test("buildSaveBody with taxon ids and no active verdict sends verdict=species", () => {
  assert.deepEqual(
    buildSaveBody({ activeVerdict: null, taxonIds: ["1", "2"] }),
    { verdict: "species", taxonIds: ["1", "2"] },
  );
});

test("buildSaveBody with neither sends an empty clear payload", () => {
  assert.deepEqual(
    buildSaveBody({ activeVerdict: null, taxonIds: [] }),
    {},
  );
});

test("buildSaveBody prefers the active verdict button over any taxon ids", () => {
  // Mirrors the mutual-exclusion the UI already enforces (selecting a
  // verdict button clears the tag box) -- this pins the fallback order if
  // that invariant is ever violated upstream.
  assert.deepEqual(
    buildSaveBody({ activeVerdict: "noise", taxonIds: ["1"] }),
    { verdict: "noise" },
  );
});

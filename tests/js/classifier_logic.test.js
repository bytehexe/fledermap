const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
  matchesQuery,
  deriveSavePayload,
  SENTINEL_ENTRIES,
  likelyChoiceAddsSomething,
  applicableLikelyChoices,
  CLASSIFY_LIKELY_VISIBLE_CAP,
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

test("SENTINEL_ENTRIES has exactly No ID and Noise, matchable by matchesQuery", () => {
  assert.equal(SENTINEL_ENTRIES.length, 2);
  const noId = SENTINEL_ENTRIES.find((e) => e.sentinelVerdict === "no_id");
  const noise = SENTINEL_ENTRIES.find((e) => e.sentinelVerdict === "noise");
  assert.ok(noId);
  assert.ok(noise);
  assert.ok(matchesQuery(noId, "no id"));
  assert.ok(matchesQuery(noise, "noise"));
});

test("deriveSavePayload with only species chips sends verdict=species", () => {
  assert.deepEqual(
    deriveSavePayload([
      { kind: "taxon", id: 1 },
      { kind: "taxon", id: 2 },
    ]),
    { ok: true, verdict: "species", taxonIds: ["1", "2"] },
  );
});

test("deriveSavePayload with a single No ID chip sends verdict=no_id", () => {
  assert.deepEqual(
    deriveSavePayload([{ kind: "sentinel", verdict: "no_id" }]),
    { ok: true, verdict: "no_id", taxonIds: [] },
  );
});

test("deriveSavePayload with a single Noise chip sends verdict=noise", () => {
  assert.deepEqual(
    deriveSavePayload([{ kind: "sentinel", verdict: "noise" }]),
    { ok: true, verdict: "noise", taxonIds: [] },
  );
});

test("deriveSavePayload with no chips sends a clear payload (verdict=null)", () => {
  assert.deepEqual(
    deriveSavePayload([]),
    { ok: true, verdict: null, taxonIds: [] },
  );
});

test("deriveSavePayload rejects mixing a sentinel chip with species chips", () => {
  const result = deriveSavePayload([
    { kind: "taxon", id: 1 },
    { kind: "sentinel", verdict: "no_id" },
  ]);
  assert.equal(result.ok, false);
  assert.ok(result.error);
});

test("deriveSavePayload rejects both No ID and Noise chips together", () => {
  const result = deriveSavePayload([
    { kind: "sentinel", verdict: "no_id" },
    { kind: "sentinel", verdict: "noise" },
  ]);
  assert.equal(result.ok, false);
  assert.ok(result.error);
});

const agreeSuggestion = {
  taxa: [{ id: 1, scientific_name: "Pipistrellus pipistrellus" }],
  is_noise: false,
  label: "Likely: Pipistrellus pipistrellus",
};

const multiSuggestion = {
  taxa: [
    { id: 1, scientific_name: "Pipistrellus pipistrellus" },
    { id: 2, scientific_name: "Myotis mystacinus" },
  ],
  is_noise: false,
  label: "Likely: Myotis mystacinus, Pipistrellus pipistrellus",
};

const noiseSuggestion = { taxa: [], is_noise: true, label: "Likely: Noise" };

test("likelyChoiceAddsSomething is true for a species suggestion with nothing staged", () => {
  assert.ok(likelyChoiceAddsSomething(agreeSuggestion, []));
});

test("likelyChoiceAddsSomething is false once the exact taxon is already staged", () => {
  assert.equal(
    likelyChoiceAddsSomething(agreeSuggestion, [{ kind: "taxon", id: "1" }]),
    false,
  );
});

test("likelyChoiceAddsSomething is true for a multi-species suggestion partially staged", () => {
  assert.ok(likelyChoiceAddsSomething(multiSuggestion, [{ kind: "taxon", id: "1" }]));
});

test("likelyChoiceAddsSomething is false for a multi-species suggestion fully staged", () => {
  assert.equal(
    likelyChoiceAddsSomething(multiSuggestion, [
      { kind: "taxon", id: "1" },
      { kind: "taxon", id: "2" },
    ]),
    false,
  );
});

test("likelyChoiceAddsSomething is true for Noise with nothing staged", () => {
  assert.ok(likelyChoiceAddsSomething(noiseSuggestion, []));
});

test("likelyChoiceAddsSomething is false once Noise is already staged", () => {
  assert.equal(
    likelyChoiceAddsSomething(noiseSuggestion, [{ kind: "sentinel", verdict: "noise" }]),
    false,
  );
});

test("applicableLikelyChoices drops satisfied suggestions and keeps order", () => {
  const result = applicableLikelyChoices(
    [agreeSuggestion, noiseSuggestion],
    [{ kind: "taxon", id: "1" }],
    CLASSIFY_LIKELY_VISIBLE_CAP,
  );
  assert.deepEqual(result, [noiseSuggestion]);
});

test("applicableLikelyChoices caps to visibleCap", () => {
  const many = [agreeSuggestion, noiseSuggestion, multiSuggestion];
  const result = applicableLikelyChoices(many, [], 2);
  assert.equal(result.length, 2);
  assert.deepEqual(result, [agreeSuggestion, noiseSuggestion]);
});

test("applicableLikelyChoices lets a satisfied suggestion's neighbor reappear once staged chips change", () => {
  const pool = [agreeSuggestion, noiseSuggestion];
  const beforeRemoval = applicableLikelyChoices(
    pool,
    [{ kind: "taxon", id: "1" }, { kind: "sentinel", verdict: "noise" }],
    CLASSIFY_LIKELY_VISIBLE_CAP,
  );
  assert.deepEqual(beforeRemoval, []);

  const afterRemovingNoise = applicableLikelyChoices(
    pool,
    [{ kind: "taxon", id: "1" }],
    CLASSIFY_LIKELY_VISIBLE_CAP,
  );
  assert.deepEqual(afterRemovingNoise, [noiseSuggestion]);
});

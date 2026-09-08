// src/fledermap/web/static/classifier_logic.js -- pure classifier-box logic (taxon search
// matching, save-payload construction), split out of classifier_box.js (2026-09-05) for the
// same reason as marker_colors.js/app.js: classifier_box.js executes
// `document.addEventListener(...)` at its top level, which crashes under plain `node --test`.
// This file never touches `document`/`window`. Loaded via its own <script> tag in
// recording_details.html, BEFORE classifier_box.js, which calls these as ordinary globals.

function matchesQuery(taxon, query) {
  const q = query.toLowerCase();
  const fields = [
    taxon.scientific_name,
    taxon.common_name_en,
    taxon.common_name_de,
    ...(taxon.codes || []),
  ];
  return fields.some((f) => f && f.toLowerCase().includes(q));
}

// No ID / Noise used to be two separate toggle buttons, disabling the search
// field while active. They're now just two more entries in the same
// search-and-chip flow species/group taxa already use (shaped like a taxon
// object so matchesQuery needs no special case) -- typing "no id" or
// "noise" surfaces them in the same suggestion dropdown. `sentinelVerdict`
// (rather than an `id`) is what marks a chip as one of these instead of a
// real taxon pick.
const SENTINEL_ENTRIES = [
  {
    sentinelVerdict: "no_id",
    scientific_name: "No ID",
    common_name_en: null,
    common_name_de: null,
    codes: [],
  },
  {
    sentinelVerdict: "noise",
    scientific_name: "Noise",
    common_name_en: null,
    common_name_de: null,
    codes: [],
  },
];

// Chips are staged client-side and only turned into a save() POST when the
// user presses Save (docs/style-guide.md's "a form that changes stored data
// needs an explicit Save" rule) -- this is the pure translation from
// "whatever chips are currently staged" to what
// services/manual_classification.py's set_manual_classification actually
// accepts: verdict=SPECIES needs >=1 taxon_id, NO_ID/NOISE need none, and
// verdict=null means "clear". A sentinel chip and a species chip can't
// coexist (nor can two sentinels) -- rather than preventing that while
// editing, it's only checked here, at save time, so the caller can show a
// clear message instead of silently picking one side.
function deriveSavePayload(chips) {
  const sentinels = chips.filter((c) => c.kind === "sentinel");
  const taxa = chips.filter((c) => c.kind === "taxon");

  if (sentinels.length > 0 && taxa.length > 0) {
    return {
      ok: false,
      error: "Choose either specific species, or No ID/Noise — not both.",
    };
  }
  if (sentinels.length > 1) {
    return { ok: false, error: "Choose only one of No ID or Noise." };
  }
  if (sentinels.length === 1) {
    return { ok: true, verdict: sentinels[0].verdict, taxonIds: [] };
  }
  if (taxa.length > 0) {
    return { ok: true, verdict: "species", taxonIds: taxa.map((c) => String(c.id)) };
  }
  return { ok: true, verdict: null, taxonIds: [] };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { matchesQuery, deriveSavePayload, SENTINEL_ENTRIES };
}

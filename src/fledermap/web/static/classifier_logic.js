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

// The three-way branch save() used to build inline, now a pure function of
// already-DOM-read values so it can be unit-tested directly: an active
// verdict button always wins (mirrors the UI's own mutual exclusion between
// the tag box and the verdict buttons); otherwise a non-empty taxonIds list
// means a species classification; otherwise the payload is empty, which
// save() must translate into omitting `verdict` entirely -- matching
// set_manual_classification's verdict=None ("clear") contract.
function buildSaveBody({ activeVerdict, taxonIds }) {
  if (activeVerdict) {
    return { verdict: activeVerdict };
  }
  if (taxonIds.length > 0) {
    return { verdict: "species", taxonIds };
  }
  return {};
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { matchesQuery, buildSaveBody };
}

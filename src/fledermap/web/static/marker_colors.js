// src/fledermap/web/static/marker_colors.js -- pure map-marker color logic, split out of
// app.js (2026-09-05) so it can be unit-tested with Node's built-in test runner without
// pulling in a DOM: app.js as a whole executes `document.addEventListener(...)` at its top
// level, which crashes under plain `node --test` before any of its functions could be
// imported. This file defines only pure functions and constants -- never touches
// `document`/`window` -- so `require()`-ing it directly in Node is safe. Loaded via its own
// <script> tag in map.html, BEFORE app.js, which still calls colorForFeature/colorForTaxon
// as ordinary globals -- no module system, matching every other script in this project.

// Original fixed palette, kept verbatim -- and kept as literal lookups for
// exactly the taxon_ids it used to cover (1..10 via the same `% length`
// formula it always used) -- so the species most likely to already be
// familiar from the map don't shift color out from under anyone (design spec
// section 9: markers "Coloured by current-best taxon"). Not meant to be
// perceptually perfect, just distinct enough to tell species apart at a
// glance.
const TAXON_PALETTE = [
  "#e6194b", "#3cb44b", "#4363d8", "#f58231",
  "#911eb4", "#46f0f0", "#f032e6", "#bcf60c",
  "#fabebe", "#008080",
];

// Every taxon_id beyond the fixed palette above is colored by a deterministic
// hash instead: golden-angle hue stepping (137.508 degrees apart -- the angle
// that spreads points maximally around a circle with no fixed count needed).
// This is what actually fixes the bug the old `taxon_id % TAXON_PALETTE.length`
// scheme had: once taxa_eu.yaml/taxa_na.yaml grew past 10 species (2026-09-05,
// full EU+NA coverage), unrelated taxa sharing a residue class mod 10 rendered
// as visually identical markers. A hash over the full id range never runs out.
//
// Reserved colors -- never handed out by colorForTaxon, so a generated hue
// can't be mistaken for one of these fixed meanings elsewhere on the map:
//   gray            -- noise verdict
//   orange          -- no_id verdict
//   #333333         -- species verdict with no taxon (unmapped species)
//   blue            -- site-radius circles (the L.circle call below)
//   GPS_TRACK_COLOR -- reserved for a future GPS-track overlay, unused today
//   MULTI_SPECIES_COLOR -- multi_species: true (see below)
const GPS_TRACK_COLOR = "#00bcd4";

// Multi-species recordings (current_best_identification returning more than
// one claim -- a real multi-species file, or several manual group/species
// tags on one recording) get their own fixed, non-hash-derived color so they
// can never collide with a real taxon's generated hue.
const MULTI_SPECIES_COLOR = "#9c27b0";

// Where the golden-angle sequence starts. A hash can't guarantee NO hue ever
// lands near a reserved one -- the sequence sweeps the whole circle given
// enough ids -- but starting well clear of orange (~39 deg) and blue
// (~240 deg) means the first taxa past id 10 (the ones soonest to actually
// show up) don't immediately collide with either.
const HASH_HUE_START = 120;
const GOLDEN_ANGLE = 137.508;

function colorForTaxon(taxonId) {
  if (taxonId <= TAXON_PALETTE.length) {
    return TAXON_PALETTE[taxonId % TAXON_PALETTE.length];
  }
  const hue = (HASH_HUE_START + taxonId * GOLDEN_ANGLE) % 360;
  return `hsl(${hue.toFixed(1)}, 70%, 45%)`;
}

// verdict still overrides taxon color for the cases where taxon doesn't
// apply: noise/no_id have no taxon at all, and a species verdict with a
// null taxon_id (identified as *some* species, but unmapped -- see
// docs/references.md on unmapped labels) gets a distinct neutral color
// rather than crashing on colorForTaxon(undefined).
function colorForFeature(props) {
  if (props.multi_species) return MULTI_SPECIES_COLOR;
  if (props.verdict === "noise") return "gray";
  if (props.verdict === "no_id") return "orange";
  if (props.taxon_id !== null && props.taxon_id !== undefined) {
    return colorForTaxon(props.taxon_id);
  }
  return "#333333";
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    colorForTaxon,
    colorForFeature,
    TAXON_PALETTE,
    MULTI_SPECIES_COLOR,
    GPS_TRACK_COLOR,
    HASH_HUE_START,
    GOLDEN_ANGLE,
  };
}

// src/fledermap/web/static/annotation_geometry.js -- pure annotation geometry/shape logic, no
// DOM access (same split as plot_coords.js/marker_colors.js -- see CLAUDE.md's "JavaScript
// tooling"). Loaded before recording_detail.js.

// A drawn rectangle's drag can start from any corner; DB rows and every downstream consumer
// (rendering, the CHECK constraint) expect start<=end / min<=max regardless of drag direction.
function normalizeDragBounds(startTimeS, endTimeS, startFreqHz, endFreqHz) {
  return {
    time_start_s: Math.min(startTimeS, endTimeS),
    time_end_s: Math.max(startTimeS, endTimeS),
    freq_min_hz: Math.min(startFreqHz, endFreqHz),
    freq_max_hz: Math.max(startFreqHz, endFreqHz),
  };
}

// A point is where BOTH dimensions collapse to a single value (or are absent) -- a real range in
// either dimension makes it a rect. Today's Comment tool always sets all four bounds together
// (spec's Non-goals: the narrower single-dimension shape modes are a later follow-up), but this
// treats a null bound as "collapsed" too so a future partially-bound annotation doesn't crash.
function annotationShape(annotation) {
  const timeIsPoint =
    annotation.time_start_s === null ||
    annotation.time_start_s === annotation.time_end_s;
  const freqIsPoint =
    annotation.freq_min_hz === null ||
    annotation.freq_min_hz === annotation.freq_max_hz;
  return timeIsPoint && freqIsPoint ? "point" : "rect";
}

// "Pipistrellus pipistrellus" -> "P. pipistrellus", per the Obsidian sketch's genus-abbreviation
// convention (also used for the map's own species labels). A one-word name (a genus/group taxon
// like "Myotis") is returned unchanged -- there's no second word to abbreviate against.
function abbreviateScientificName(name) {
  const parts = name.split(" ");
  if (parts.length < 2) return name;
  return `${parts[0][0]}. ${parts.slice(1).join(" ")}`;
}

// `coords` is a plot_coords.js `makePlotCoords()` result -- `sToX`/`khzToY` take seconds/kHz, so
// callers pass Hz-valued annotation fields divided by 1000.
//
// A source with no per-call frequency information (BattyBirdNET's fixed-width analysis windows,
// unlike batdetect2's genuinely per-call boxes) leaves `freq_min_hz`/`freq_max_hz` both `None` --
// rendered as a full-height vertical band (`top: 0`, `height: coords.khzToY(0)`, the plot's full
// pixel height) rather than dividing `null / 1000` into `0`, which would silently collapse the
// box to a zero-height sliver pinned to the bottom edge instead of a visible band.
function annotationBoxPx(annotation, coords) {
  const left = coords.sToX(annotation.time_start_s);
  const width = coords.sToX(annotation.time_end_s) - left;
  const hasFreq = annotation.freq_min_hz !== null && annotation.freq_max_hz !== null;
  const top = hasFreq ? coords.khzToY(annotation.freq_max_hz / 1000) : 0;
  const bottom = hasFreq ? coords.khzToY(annotation.freq_min_hz / 1000) : coords.khzToY(0);
  return { left, top, width, height: bottom - top };
}

function annotationPointPx(annotation, coords) {
  return {
    x: coords.sToX(annotation.time_start_s),
    y: coords.khzToY(annotation.freq_min_hz / 1000),
  };
}

// Confines a native-pixel drag/click position to the plot's own raster before it's converted to
// time/frequency (coords.xToS/yToKhz) -- mouseup/mousemove are tracked on `document`, not `wrap`,
// so a drag that ends past the spectrogram's edge would otherwise convert to a negative
// time_start_s, a negative freq_min_hz, or a frequency above the plot's own max, and that invalid
// geometry would get saved via the API with nothing rejecting it.
function clampToPlot(xPx, yPx, widthPx, heightPx) {
  return {
    x: Math.max(0, Math.min(xPx, widthPx)),
    y: Math.max(0, Math.min(yPx, heightPx)),
  };
}

// The species line, abbreviated the same way as every annotation source, with confidence
// appended for any source that carries one (comment annotations never set confidence, so their
// species line has no percentage). `taxon` is the recording-detail page's own lookup-by-id result
// (null for an unmapped species, where `raw_label` -- if the annotation carries one -- is the only
// name available); `literal` is a separate, free-text line, never merged into the species line.
function annotationLabelLines(annotation, taxon) {
  const lines = [];
  const name = taxon
    ? abbreviateScientificName(taxon.scientific_name)
    : annotation.raw_label || null;
  if (name) {
    const confidence =
      annotation.confidence !== null && annotation.confidence !== undefined
        ? ` ${Math.round(annotation.confidence * 100)}%`
        : "";
    lines.push(`${name}${confidence}`);
  }
  if (annotation.literal) lines.push(annotation.literal);
  return lines;
}

// Strictly below (`<`, not `<=`) the configured threshold for the annotation's own source --
// matching `materialize_claims`'s own "clears the threshold" semantics elsewhere in this
// codebase. A source with no configured threshold (comment) or an annotation with no confidence
// (also comment) is never below-threshold; there is nothing to compare.
function isBelowThreshold(annotation, thresholds) {
  const threshold = thresholds[annotation.source];
  if (threshold === undefined) return false;
  if (annotation.confidence === null || annotation.confidence === undefined) return false;
  return annotation.confidence < threshold;
}

// A stable partition, not a sort by source name: every non-comment annotation keeps its original
// relative order, followed by every comment annotation in its original relative order. Comments
// must paint (and hit-test, since `annotationAtPoint` walks from the end) on top of any other
// source, since only comments are ever user-editable via the popup.
function sortAnnotationsForPaint(annotations) {
  const others = annotations.filter((a) => a.source !== "comment");
  const comments = annotations.filter((a) => a.source === "comment");
  return [...others, ...comments];
}

// Comments lead the Layers menu, batdetect2 next, battybirdnet after that, anything else
// (a future source) alphabetically at the end -- not a plain alphabetical sort, which would put
// "batdetect2" ahead of "comment".
const _LAYER_MENU_PRIORITY = ["comment", "batdetect2", "battybirdnet"];
function layerMenuSources(sources) {
  return [...sources].sort((a, b) => {
    const ia = _LAYER_MENU_PRIORITY.indexOf(a);
    const ib = _LAYER_MENU_PRIORITY.indexOf(b);
    if (ia !== -1 || ib !== -1) {
      return (ia === -1 ? _LAYER_MENU_PRIORITY.length : ia) -
        (ib === -1 ? _LAYER_MENU_PRIORITY.length : ib);
    }
    return a.localeCompare(b);
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
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
  };
}

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
function annotationBoxPx(annotation, coords) {
  const left = coords.sToX(annotation.time_start_s);
  const width = coords.sToX(annotation.time_end_s) - left;
  const top = coords.khzToY(annotation.freq_max_hz / 1000);
  const height = coords.khzToY(annotation.freq_min_hz / 1000) - top;
  return { left, top, width, height };
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

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    normalizeDragBounds,
    annotationShape,
    abbreviateScientificName,
    annotationBoxPx,
    annotationPointPx,
    clampToPlot,
  };
}

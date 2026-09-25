// src/fledermap/web/static/call_interval_chart.js -- pure logic for the recording-detail
// Analysis view's call-distance graph (the placeholder reserved at
// docs/superpowers/specs/2026-09-18-fledermap-analysis-view-design.md, filled in once
// batdetect2 per-call `Annotation` rows existed to compute it from). Unlike
// analysis_chart.js's sound-level spectrum, this needs no server endpoint: the recording's
// annotations are already embedded on the page as `#detail-toolbar`'s `data-annotations`
// (recording_detail.js parses that once at load), so the histogram is computed client-side
// from data already in hand. No top-level DOM access, so this stays `require()`-able in
// Node (CLAUDE.md's JavaScript tooling convention).

// Bin width for the interval histogram. Deliberately a plain tunable constant, not a design
// decision baked into the binning math -- expected to be adjusted once this is checked
// against real recordings.
const BIN_WIDTH_MS = 10;

// Intervals longer than this are excluded from the histogram entirely, not clipped into a
// final bucket: a gap this long is a break between call bouts (the bat went quiet, another
// individual started, etc.), not a data point about the species' pulse-repetition interval.
const MAX_INTERVAL_MS = 1000;

function callMidpointMs(annotation) {
  return ((annotation.time_start_s + annotation.time_end_s) / 2) * 1000;
}

// Per-taxon inter-call-interval histograms, normalized to a percentage of that taxon's own
// kept intervals -- comparable across species regardless of how many calls each has, per the
// design decision (a shared distribution comparison, not a raw count comparison). Only
// batdetect2-sourced annotations with a taxon_id count: BattyBirdNET's long-span detections
// are unreliable as per-call boxes, and manual comment annotations aren't a systematic
// per-call detection set (a comment box would create false "gaps" against them).
//
// `options.minConfidence`, when given, drops any call below it before grouping -- deliberately
// the same cutoff `services/classifier_detections.py`'s `materialize_claims` uses to decide
// whether a detection counts as an identification (`classifierThresholds` in
// recording_detail.js), NOT the separate, usually-lower `classifierDisplayThresholds` that
// governs which boxes are drawn on the spectrogram overlay. A call too weak to count as an
// identification of that species shouldn't shape that species' own interval distribution
// either, even though it might still be visible as a drawn box.
//
// A species needs at least one interval surviving both the confidence filter and the
// MAX_INTERVAL_MS exclusion to appear at all -- fewer than 2 qualifying calls, or every
// remaining gap being a bout break, both mean nothing to plot; the caller renders an
// empty-state message when no species have any data at all, rather than a chart with no
// lines on it.
function computeCallIntervalHistograms(annotations, options = {}) {
  const binWidthMs = options.binWidthMs ?? BIN_WIDTH_MS;
  const maxIntervalMs = options.maxIntervalMs ?? MAX_INTERVAL_MS;
  const binCount = maxIntervalMs / binWidthMs;

  const midpointsByTaxon = new Map();
  for (const a of annotations) {
    if (a.source !== "batdetect2") continue;
    if (a.taxon_id === null || a.taxon_id === undefined) continue;
    if (a.time_start_s === null || a.time_end_s === null) continue;
    if (options.minConfidence !== undefined && a.confidence < options.minConfidence) continue;
    if (!midpointsByTaxon.has(a.taxon_id)) midpointsByTaxon.set(a.taxon_id, []);
    midpointsByTaxon.get(a.taxon_id).push(callMidpointMs(a));
  }

  const results = [];
  for (const [taxonId, midpoints] of midpointsByTaxon) {
    midpoints.sort((a, b) => a - b);
    const counts = new Array(binCount).fill(0);
    let kept = 0;
    for (let i = 1; i < midpoints.length; i++) {
      const gapMs = midpoints[i] - midpoints[i - 1];
      if (gapMs > maxIntervalMs) continue;
      const binIndex = Math.min(Math.floor(gapMs / binWidthMs), binCount - 1);
      counts[binIndex]++;
      kept++;
    }
    if (kept === 0) continue;
    results.push({ taxonId, binPercentages: counts.map((c) => (c / kept) * 100) });
  }
  results.sort((a, b) => a.taxonId - b.taxonId);
  return results;
}

function callIntervalHistogramsToChartData(histograms, taxonById, colorForTaxon, binWidthMs = BIN_WIDTH_MS) {
  const datasets = histograms.map(({ taxonId, binPercentages }) => {
    const taxon = taxonById.get(taxonId);
    return {
      label: taxon ? taxon.scientific_name : `Taxon #${taxonId}`,
      data: binPercentages.map((pct, i) => ({ x: i * binWidthMs, y: pct })),
      borderColor: colorForTaxon(taxonId),
      backgroundColor: colorForTaxon(taxonId),
      pointRadius: 0,
    };
  });
  return { datasets };
}

function callIntervalChartOptions(maxIntervalMs = MAX_INTERVAL_MS) {
  return {
    scales: {
      x: {
        type: "linear",
        min: 0,
        max: maxIntervalMs,
        title: { display: true, text: "Inter-call interval (ms)" },
      },
      y: { title: { display: true, text: "Share of intervals (%)" } },
    },
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    computeCallIntervalHistograms,
    callIntervalHistogramsToChartData,
    callIntervalChartOptions,
    BIN_WIDTH_MS,
    MAX_INTERVAL_MS,
  };
}

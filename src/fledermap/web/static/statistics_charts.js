// src/fledermap/web/static/statistics_charts.js -- pure reshaping logic, no
// DOM access, following the split app.js/marker_colors.js already
// established (CLAUDE.md's JavaScript tooling section): turns the JSON a
// statistics view embeds into the exact shape Chart.js's dataset API wants.
// Loaded via its own <script> tag, before statistics.js (which mounts the
// actual canvases) and after marker_colors.js (colorForTaxon).

// Chart.js draws legend labels directly onto the <canvas>, so a long
// scientific name just gets hard-clipped at the canvas edge with no
// ellipsis (no DOM text node to apply CSS text-overflow to, and Chart.js's
// legend has no built-in truncation option -- confirmed against its docs).
// A fixed character count doesn't work here: the donut's legend column is
// only ~80-90px of actual text width (measured live against a real
// three-column statistics card), so this measures against real rendered
// pixel width instead -- statistics.js's generateLabels callback supplies
// the measuring function (canvas text metrics aren't available outside a
// real <canvas> context, hence the injected `measureWidth` rather than this
// file reaching for `document` itself) and pairs the result with an
// onHover/onLeave handler that sets the canvas's native `title` to the
// untruncated name.
function truncateLegendLabelToWidth(text, maxWidth, measureWidth) {
  if (measureWidth(text) <= maxWidth) return text;
  let fits = 0;
  let doesNotFit = text.length;
  while (fits < doesNotFit) {
    const mid = Math.ceil((fits + doesNotFit) / 2);
    const candidate = `${text.slice(0, mid)}…`;
    if (measureWidth(candidate) <= maxWidth) {
      fits = mid;
    } else {
      doesNotFit = mid - 1;
    }
  }
  return `${text.slice(0, fits)}…`;
}

function taxonBreakdownToChartData(breakdown, otherColor) {
  const labels = [];
  const data = [];
  const colors = [];
  for (const entry of breakdown.entries) {
    labels.push(entry.taxon.scientific_name);
    data.push(entry.count);
    colors.push(colorForTaxon(entry.taxon.id));
  }
  if (breakdown.other_count > 0) {
    labels.push("Other");
    data.push(breakdown.other_count);
    colors.push(otherColor);
  }
  if (breakdown.unmapped_count > 0) {
    labels.push("Unmapped species");
    data.push(breakdown.unmapped_count);
    colors.push("#333333");
  }
  if (breakdown.multi_species_count > 0) {
    labels.push("Multiple Species");
    data.push(breakdown.multi_species_count);
    colors.push(MULTI_SPECIES_COLOR);
  }
  return { labels, data, colors };
}

function seriesToChartData(series, otherColor) {
  const datasets = series.taxa.map((taxon) => ({
    label: taxon.scientific_name,
    data: series.buckets.map((bucket) => bucket[taxon.id] || 0),
    borderColor: colorForTaxon(taxon.id),
  }));
  if (series.other_included) {
    // `bucket[null]` works because a JS object subscript coerces its key to
    // a string ("null"), matching the literal string "null" that
    // views/statistics.py's _series_json deliberately writes for the
    // Other/single-series bucket key -- this is a real Python<->JS contract,
    // not an accident. Do not "fix" this into a JS null/undefined check.
    datasets.push({
      label: "Other",
      data: series.buckets.map((bucket) => bucket[null] || 0),
      borderColor: otherColor,
    });
  }
  if (series.single_species) {
    datasets.push({
      label: "Recordings",
      data: series.buckets.map((bucket) => bucket[null] || 0),
      borderColor: otherColor,
    });
  }
  return { labels: series.labels, datasets };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { taxonBreakdownToChartData, seriesToChartData, truncateLegendLabelToWidth };
}

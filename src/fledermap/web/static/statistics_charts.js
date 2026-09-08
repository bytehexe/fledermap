// src/fledermap/web/static/statistics_charts.js -- pure reshaping logic, no
// DOM access, following the split app.js/marker_colors.js already
// established (CLAUDE.md's JavaScript tooling section): turns the JSON a
// statistics view embeds into the exact shape Chart.js's dataset API wants.
// Loaded via its own <script> tag, before statistics.js (which mounts the
// actual canvases) and after marker_colors.js (colorForTaxon).

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
  module.exports = { taxonBreakdownToChartData, seriesToChartData };
}

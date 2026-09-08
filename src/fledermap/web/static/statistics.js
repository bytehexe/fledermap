// src/fledermap/web/static/statistics.js -- mounts Chart.js canvases from the
// embedded JSON <script type="application/json"> blocks each statistics page
// writes. Pure reshaping lives in statistics_charts.js (node:test-covered);
// this file is the DOM-touching half, per CLAUDE.md's JS-split convention.

const OTHER_COLOR = "#9e9e9e"; // neutral gray, not handed out by colorForTaxon -- see marker_colors.js's reserved-colors comment.

function readEmbeddedJson(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  return JSON.parse(el.textContent);
}

function mountDonut(canvasId, dataId) {
  const canvas = document.getElementById(canvasId);
  const raw = readEmbeddedJson(dataId);
  if (!canvas || !raw) return;
  const chartData = taxonBreakdownToChartData(raw, OTHER_COLOR);
  new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: chartData.labels,
      datasets: [{ data: chartData.data, backgroundColor: chartData.colors }],
    },
    options: { plugins: { legend: { position: "right" } } },
  });
}

function mountLine(canvasId, dataId) {
  const canvas = document.getElementById(canvasId);
  const raw = readEmbeddedJson(dataId);
  if (!canvas || !raw) return;
  const chartData = seriesToChartData(raw, OTHER_COLOR);
  new Chart(canvas, {
    type: "line",
    data: {
      labels: chartData.labels,
      datasets: chartData.datasets.map((ds) => ({ ...ds, fill: false, tension: 0.2 })),
    },
    options: { plugins: { legend: { display: chartData.datasets.length > 1 } } },
  });
}

function mountSiteBar(canvasId, dataId) {
  const canvas = document.getElementById(canvasId);
  const raw = readEmbeddedJson(dataId);
  if (!canvas || !raw) return;
  new Chart(canvas, {
    type: "bar",
    data: {
      labels: raw.entries.map((e) => e.site.name),
      datasets: [{ data: raw.entries.map((e) => e.count), backgroundColor: OTHER_COLOR }],
    },
    options: {
      indexAxis: "y",
      plugins: { legend: { display: false } },
      // A bar in the site-ranking chart click-throughs to that site's own
      // statistics sub-page -- same "chart element -> stats sub-page"
      // behavior as the global page's donut slices (spec's "Name and label
      // linking" section).
      onClick: (_event, elements) => {
        if (!elements.length) return;
        const entry = raw.entries[elements[0].index];
        if (entry && entry.statistics_url) {
          window.location.href = entry.statistics_url;
        }
      },
    },
  });
}

document.addEventListener("DOMContentLoaded", () => {
  mountDonut("species-donut", "species-donut-data");
  mountLine("month-line", "month-line-data");
  mountLine("hour-line", "hour-line-data");
  mountSiteBar("sites-bar", "sites-bar-data");
});

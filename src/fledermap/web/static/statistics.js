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

function navigateOnLegendClick(getDetailUrl) {
  // Chart.js's default legend-item shape differs by chart type: a
  // pie/doughnut legend item carries `.index` (the data-point index), while
  // a line/bar legend item -- one item per dataset -- carries
  // `.datasetIndex` instead and has no `.index` at all. mountDonut's entries
  // line up with `.index`; mountLine's `raw.taxa` line up with
  // `.datasetIndex`. Fall back from one to the other so this one helper
  // serves both mounters correctly.
  return (_event, legendItem) => {
    const idx = legendItem.index ?? legendItem.datasetIndex;
    const url = getDetailUrl(idx);
    if (url) window.location.href = url;
  };
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
    options: {
      plugins: {
        legend: {
          position: "right",
          onClick: navigateOnLegendClick((index) => {
            const entry = raw.entries[index];
            return entry ? `/species/${entry.taxon.id}` : null;
          }),
        },
      },
      // A donut slice click-throughs to that taxon's own statistics
      // sub-page -- same "chart element -> stats sub-page" behavior as the
      // site-ranking bar chart's bars (spec's "Name and label linking"
      // section). Extra slices (Other/Unmapped/Multiple Species) have no
      // statistics_url and are simply not clickable.
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
    options: {
      plugins: {
        legend: {
          display: chartData.datasets.length > 1,
          onClick: navigateOnLegendClick((index) => {
            const taxon = raw.taxa[index];
            return taxon ? `/species/${taxon.id}` : null;
          }),
        },
      },
    },
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
    // The donut's equivalent name-label click (legend text -> detail page) is
    // Chart.js's own `legend.onClick`, but a bar chart's y-axis tick labels have
    // no built-in click hook the way a legend does -- worse, `options.onClick`
    // above is *only ever called for an event over `chartArea`* (confirmed via
    // Chart.js's own docs: "Called if the event is... over chartArea"), so it
    // never even fires for a click on the label column, no matter what hit-test
    // logic lives inside it (verified live: an unconditional log inside onClick
    // printed for every bar click and NONE for a label click). A chart-specific
    // inline plugin's `beforeEvent` hook is Chart.js's own documented way to
    // catch events outside chartArea ("Capturing events outside chartArea using
    // a plugin" in the interactions docs) -- used here instead of onClick to
    // make the axis label clickable too, matching the donut's plain-text-label
    // -> detail-page convention.
    plugins: [
      {
        id: "siteBarAxisLabelClick",
        beforeEvent(chart, args) {
          if (args.event.type !== "click") return;
          const pos = Chart.helpers.getRelativePosition(args.event, chart);
          const yScale = chart.scales.y;
          if (pos.x >= chart.chartArea.left) return; // over the plot area, not the label column
          if (pos.y < yScale.top || pos.y > yScale.bottom) return;
          const entry = raw.entries[Math.round(yScale.getValueForPixel(pos.y))];
          if (entry) window.location.href = `/sites/${entry.site.id}`;
        },
      },
    ],
  });
}

function paintTaxonSwatches() {
  // Rarest-species list swatches (global page only) -- same colorForTaxon()
  // palette every other chart on these pages uses. Safe to call
  // unconditionally: site/species pages have no [data-taxon-swatch]
  // elements, so this is a no-op there.
  document.querySelectorAll("[data-taxon-swatch]").forEach((el) => {
    el.style.backgroundColor = colorForTaxon(Number(el.dataset.taxonSwatch));
  });
}

document.addEventListener("DOMContentLoaded", () => {
  mountDonut("species-donut", "species-donut-data");
  mountLine("month-line", "month-line-data");
  mountLine("hour-line", "hour-line-data");
  mountSiteBar("sites-bar", "sites-bar-data");
  paintTaxonSwatches();
});

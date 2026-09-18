// src/fledermap/web/static/analysis_chart.js -- pure reshaping logic, no DOM access, following
// the split app.js/statistics_charts.js pattern already established (CLAUDE.md's JavaScript
// tooling section): turns the sound-level-spectrum endpoint's JSON into the exact shape
// Chart.js's dataset API wants. Loaded via its own <script> tag, before recording_detail.js
// (which mounts the actual canvas).

function soundLevelSpectrumToChartData(spectrum) {
  // {x: kHz, y: dB} points on a LINEAR x axis (see `soundLevelSpectrumChartOptions`), not a
  // category axis over a `labels` array. A category axis spaces bins by index and labels every
  // Nth bin with its raw kHz value ("7.625", "15.25", ...), which is correct but unreadable for
  // picking a frequency off the chart; a linear axis gets round ticks and places each peak
  // marker by its actual frequency instead of by array index.
  const datasets = [
    {
      label: "Sound level (dB)",
      data: spectrum.freqs_hz.map((hz, i) => ({ x: hz / 1000, y: spectrum.db[i] })),
      borderColor: "currentColor",
      pointRadius: 0,
    },
  ];
  // `peaks_freq_hz` is empty when the loudest searched bin is the search's own lower boundary
  // and nothing else qualifies (see media/sound_level_spectrum.py) -- no markers at all then,
  // rather than a marker on the cutoff.
  if (spectrum.peaks_freq_hz.length > 0) {
    // A second dataset rather than an annotation plugin (this project has no Chart.js annotation
    // plugin dependency, and a marker is exactly a point on the same axes). Each marker sits on
    // the curve itself: the peak frequencies are the endpoint's own bin frequencies, so the bin
    // (and its dB value) is found by exact lookup, not interpolation.
    const peakPoints = spectrum.peaks_freq_hz.map((hz) => ({
      x: hz / 1000,
      y: spectrum.db[spectrum.freqs_hz.indexOf(hz)],
    }));
    // A peak can only be located to within one FFT bin, and the bin width is sr / n: ~0.19 kHz
    // for a whole file, but ~1.3 kHz for a 300-sample Select span. Stating it keeps a short
    // selection's "45.2 kHz" from reading as more precise than it is. The frequencies
    // themselves are labelled on the chart (`peakLabelPlugin`), so the legend only carries this.
    const binWidthKhz =
      spectrum.freqs_hz.length > 1 ? (spectrum.freqs_hz[1] - spectrum.freqs_hz[0]) / 1000 : null;
    datasets.push({
      label: binWidthKhz === null ? "Peaks" : `Peaks (bin width ${binWidthKhz.toFixed(2)} kHz)`,
      data: peakPoints,
      showLine: false,
      pointRadius: 5,
      pointBackgroundColor: "red",
      pointBorderColor: "red",
      peakLabels: true,
    });
  }
  return { datasets };
}

// Axis titles carry the units -- the chart previously had none, so the reader could not tell the
// x values were kHz or the y values were dB relative to the peak. The x axis ends at the
// spectrum's own last bin (Nyquist), not at Chart.js's "nice" rounding, which drew an empty
// 128-140 kHz stretch of axis past the end of the data. `layout.padding.top` reserves room above
// the plot for the peak labels: a 0 dB peak sits on the plot's top edge, and its label (plus one
// staggered row, see `stackPeakLabels`) would otherwise be clipped by the canvas.
function soundLevelSpectrumChartOptions(spectrum) {
  const lastKhz = spectrum.freqs_hz[spectrum.freqs_hz.length - 1] / 1000;
  return {
    layout: { padding: { top: 36 } },
    scales: {
      x: { type: "linear", min: 0, max: lastKhz, title: { display: true, text: "Frequency (kHz)" } },
      y: { title: { display: true, text: "Level (dB re peak)" } },
    },
  };
}

function formatPeakLabel(khz) {
  return `${khz.toFixed(1)} kHz`;
}

// Assigns each label a vertical level (0 = directly above its marker, 1 = one text-height
// higher, ...) so that no two labels overlap horizontally. Labels are never dropped -- the point
// is that every peak stays labelled -- so a crowded pair is staggered instead. `labels` is in
// ascending x order; `gapPx` is the minimum clear space between two labels on the same level.
// Greedy: each label takes the lowest level free of overlap with the labels already placed.
function stackPeakLabels(labels, gapPx) {
  const placed = [];
  return labels.map((label) => {
    let level = 0;
    while (
      placed.some(
        (other) =>
          other.level === level &&
          Math.abs(other.centerPx - label.centerPx) < (other.widthPx + label.widthPx) / 2 + gapPx
      )
    ) {
      level += 1;
    }
    placed.push({ ...label, level });
    return level;
  });
}

// Chart.js inline plugin: always-on frequency labels above every peak marker. Drawn on the
// canvas because Chart.js has no built-in point labels and this project deliberately has no
// datalabels-plugin dependency. Touches the DOM only inside the callback (never at load), so this
// file stays `require()`-able from Node; its drawing is covered by the headless-Chrome check, not
// node:test.
const peakLabelPlugin = {
  id: "peakLabels",
  afterDatasetsDraw(chart) {
    const { ctx, chartArea } = chart;
    const style = getComputedStyle(chart.canvas);
    // A canvas cannot resolve CSS variables itself, so read the resolved token here -- this is
    // what keeps the labels legible in dark mode. Falls back to the inherited text color.
    ctx.fillStyle = style.getPropertyValue("--color-text").trim() || style.color;
    ctx.font = `12px ${style.fontFamily}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    const lineHeightPx = 14;
    const markerRadiusPx = 5;
    chart.data.datasets.forEach((dataset, datasetIndex) => {
      if (!dataset.peakLabels) return;
      const points = chart.getDatasetMeta(datasetIndex).data;
      const labels = points.map((point, i) => {
        const text = formatPeakLabel(dataset.data[i].x);
        const widthPx = ctx.measureText(text).width;
        // Clamped so a peak at the very left/right edge still has its whole label on the canvas.
        const centerPx = Math.min(
          Math.max(point.x, chartArea.left + widthPx / 2),
          chartArea.right - widthPx / 2
        );
        return { text, widthPx, centerPx, markerY: point.y };
      });
      const levels = stackPeakLabels(labels, 4);
      labels.forEach((label, i) => {
        ctx.fillText(
          label.text,
          label.centerPx,
          label.markerY - markerRadiusPx - 2 - levels[i] * lineHeightPx
        );
      });
    });
  },
};

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    soundLevelSpectrumToChartData,
    soundLevelSpectrumChartOptions,
    formatPeakLabel,
    stackPeakLabels,
    peakLabelPlugin,
  };
}

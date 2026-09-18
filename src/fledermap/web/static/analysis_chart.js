// src/fledermap/web/static/analysis_chart.js -- pure reshaping logic, no DOM access, following
// the split app.js/statistics_charts.js pattern already established (CLAUDE.md's JavaScript
// tooling section): turns the sound-level-spectrum endpoint's JSON into the exact shape
// Chart.js's dataset API wants. Loaded via its own <script> tag, before recording_detail.js
// (which mounts the actual canvas).

function soundLevelSpectrumToChartData(spectrum) {
  return {
    labels: spectrum.freqs_hz.map((hz) => hz / 1000),
    datasets: [
      {
        label: "Sound level (dB)",
        data: spectrum.db,
        borderColor: "currentColor",
        pointRadius: 0,
      },
    ],
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { soundLevelSpectrumToChartData };
}

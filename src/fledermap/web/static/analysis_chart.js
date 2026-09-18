// src/fledermap/web/static/analysis_chart.js -- pure reshaping logic, no DOM access, following
// the split app.js/statistics_charts.js pattern already established (CLAUDE.md's JavaScript
// tooling section): turns the sound-level-spectrum endpoint's JSON into the exact shape
// Chart.js's dataset API wants. Loaded via its own <script> tag, before recording_detail.js
// (which mounts the actual canvas).

function soundLevelSpectrumToChartData(spectrum) {
  // {x: kHz, y: dB} points on a LINEAR x axis (see `soundLevelSpectrumChartOptions`), not a
  // category axis over a `labels` array. A category axis spaces bins by index and labels every
  // Nth bin with its raw kHz value ("7.625", "15.25", ...), which is correct but unreadable for
  // picking a frequency off the chart; a linear axis gets round ticks and places the peak marker
  // by its actual frequency instead of by array index. The peak marker is a second dataset rather
  // than an annotation plugin (this project has no Chart.js annotation plugin dependency, and the
  // marker is exactly a point on the same axes). `peak_freq_hz` is 0 dB by construction -- the
  // spectrum is self-relative (see `media/sound_level_spectrum.py`'s module docstring).
  const peakKhz = spectrum.peak_freq_hz / 1000;
  return {
    datasets: [
      {
        label: "Sound level (dB)",
        data: spectrum.freqs_hz.map((hz, i) => ({ x: hz / 1000, y: spectrum.db[i] })),
        borderColor: "currentColor",
        pointRadius: 0,
      },
      {
        label: `Peak frequency (${peakKhz.toFixed(1)} kHz)`,
        data: [{ x: peakKhz, y: 0 }],
        showLine: false,
        pointRadius: 5,
        pointBackgroundColor: "red",
        pointBorderColor: "red",
      },
    ],
  };
}

// Axis titles carry the units -- the chart previously had none, so the reader could not tell the
// x values were kHz or the y values were dB relative to the peak.
function soundLevelSpectrumChartOptions() {
  return {
    scales: {
      x: { type: "linear", min: 0, title: { display: true, text: "Frequency (kHz)" } },
      y: { title: { display: true, text: "Level (dB re peak)" } },
    },
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { soundLevelSpectrumToChartData, soundLevelSpectrumChartOptions };
}

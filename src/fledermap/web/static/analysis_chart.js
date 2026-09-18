// src/fledermap/web/static/analysis_chart.js -- pure reshaping logic, no DOM access, following
// the split app.js/statistics_charts.js pattern already established (CLAUDE.md's JavaScript
// tooling section): turns the sound-level-spectrum endpoint's JSON into the exact shape
// Chart.js's dataset API wants. Loaded via its own <script> tag, before recording_detail.js
// (which mounts the actual canvas).

function soundLevelSpectrumToChartData(spectrum) {
  // The peak marker is a second dataset rather than an annotation plugin (this project has no
  // Chart.js annotation plugin dependency, and the marker is exactly a point on the same axes) --
  // a one-point dataset, `null` everywhere else so Chart.js draws no connecting line through it,
  // aligned by INDEX with `labels`/the main dataset's `data` (both built from the same
  // `freqs_hz` array) rather than by x-value, since the main chart uses a category (not linear)
  // x-axis by default. `peak_freq_hz` is always one of `freqs_hz`'s own values (the endpoint
  // finds it via argmax over that same array), so `indexOf` always finds an exact match. 0 dB is
  // always the peak's own value -- the spectrum is self-relative (see
  // `media/sound_level_spectrum.py`'s module docstring).
  const peakIndex = spectrum.freqs_hz.indexOf(spectrum.peak_freq_hz);
  const peakData = spectrum.freqs_hz.map((_hz, i) => (i === peakIndex ? 0 : null));
  return {
    labels: spectrum.freqs_hz.map((hz) => hz / 1000),
    datasets: [
      {
        label: "Sound level (dB)",
        data: spectrum.db,
        borderColor: "currentColor",
        pointRadius: 0,
      },
      {
        label: "Peak frequency",
        data: peakData,
        showLine: false,
        pointRadius: 5,
        pointBackgroundColor: "red",
        pointBorderColor: "red",
      },
    ],
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { soundLevelSpectrumToChartData };
}

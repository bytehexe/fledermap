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
  // by its actual frequency instead of by array index.
  const datasets = [
    {
      label: "Sound level (dB)",
      data: spectrum.freqs_hz.map((hz, i) => ({ x: hz / 1000, y: spectrum.db[i] })),
      borderColor: "currentColor",
      pointRadius: 0,
    },
  ];
  // `peak_freq_hz` is null when the loudest searched bin is the search's own lower boundary (see
  // media/sound_level_spectrum.py) -- no marker at all then, rather than a marker on the cutoff.
  if (spectrum.peak_freq_hz !== null) {
    // A second dataset rather than an annotation plugin (this project has no Chart.js annotation
    // plugin dependency, and the marker is exactly a point on the same axes). 0 dB by
    // construction -- the spectrum is self-relative (see the module docstring above).
    const peakKhz = spectrum.peak_freq_hz / 1000;
    // The peak can only be located to within one FFT bin, and the bin width is sr / n: ~0.19 kHz
    // for a whole file, but ~1.3 kHz for a 300-sample Select span. Stating it keeps a short
    // selection's "45.2 kHz" from reading as more precise than it is.
    const binWidthKhz =
      spectrum.freqs_hz.length > 1 ? (spectrum.freqs_hz[1] - spectrum.freqs_hz[0]) / 1000 : null;
    datasets.push({
      label:
        binWidthKhz === null
          ? `Peak frequency (${peakKhz.toFixed(1)} kHz)`
          : `Peak frequency (${peakKhz.toFixed(1)} kHz, bin width ${binWidthKhz.toFixed(2)} kHz)`,
      data: [{ x: peakKhz, y: 0 }],
      showLine: false,
      pointRadius: 5,
      pointBackgroundColor: "red",
      pointBorderColor: "red",
    });
  }
  return { datasets };
}

// Axis titles carry the units -- the chart previously had none, so the reader could not tell the
// x values were kHz or the y values were dB relative to the peak. The x axis ends at the
// spectrum's own last bin (Nyquist), not at Chart.js's "nice" rounding, which drew an empty
// 128-140 kHz stretch of axis past the end of the data.
function soundLevelSpectrumChartOptions(spectrum) {
  const lastKhz = spectrum.freqs_hz[spectrum.freqs_hz.length - 1] / 1000;
  return {
    scales: {
      x: { type: "linear", min: 0, max: lastKhz, title: { display: true, text: "Frequency (kHz)" } },
      y: { title: { display: true, text: "Level (dB re peak)" } },
    },
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { soundLevelSpectrumToChartData, soundLevelSpectrumChartOptions };
}

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { soundLevelSpectrumToChartData } = require(
  "../../src/fledermap/web/static/analysis_chart.js"
);

test("soundLevelSpectrumToChartData converts Hz labels to kHz", () => {
  const result = soundLevelSpectrumToChartData({
    freqs_hz: [0, 20000, 40000],
    db: [-80, -20, 0],
    peak_freq_hz: 40000,
  });
  assert.deepEqual(result.labels, [0, 20, 40]);
});

test("soundLevelSpectrumToChartData carries the dB values through unchanged", () => {
  const result = soundLevelSpectrumToChartData({
    freqs_hz: [0, 20000, 40000],
    db: [-80, -20, 0],
    peak_freq_hz: 40000,
  });
  assert.deepEqual(result.datasets[0].data, [-80, -20, 0]);
});

test("soundLevelSpectrumToChartData names the dataset", () => {
  const result = soundLevelSpectrumToChartData({
    freqs_hz: [0],
    db: [0],
    peak_freq_hz: 0,
  });
  assert.equal(result.datasets[0].label, "Sound level (dB)");
});

test("soundLevelSpectrumToChartData marks the peak frequency's own index with a 0 dB point", () => {
  const result = soundLevelSpectrumToChartData({
    freqs_hz: [0, 20000, 40000],
    db: [-80, -20, 0],
    peak_freq_hz: 20000,
  });
  const peakDataset = result.datasets[1];
  assert.deepEqual(peakDataset.data, [null, 0, null]);
});

test("soundLevelSpectrumToChartData leaves the main dataset unaffected by the peak marker", () => {
  const result = soundLevelSpectrumToChartData({
    freqs_hz: [0, 20000, 40000],
    db: [-80, -20, 0],
    peak_freq_hz: 20000,
  });
  assert.deepEqual(result.datasets[0].data, [-80, -20, 0]);
});

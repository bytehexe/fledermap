const { test } = require("node:test");
const assert = require("node:assert/strict");
const { soundLevelSpectrumToChartData, soundLevelSpectrumChartOptions } = require(
  "../../src/fledermap/web/static/analysis_chart.js"
);

const SPECTRUM = { freqs_hz: [0, 20000, 40000], db: [-80, -20, 0], peak_freq_hz: 20000 };

test("soundLevelSpectrumToChartData pairs each bin's kHz with its dB value", () => {
  const result = soundLevelSpectrumToChartData(SPECTRUM);
  assert.deepEqual(result.datasets[0].data, [
    { x: 0, y: -80 },
    { x: 20, y: -20 },
    { x: 40, y: 0 },
  ]);
});

test("soundLevelSpectrumToChartData names the dataset", () => {
  const result = soundLevelSpectrumToChartData({ freqs_hz: [0], db: [0], peak_freq_hz: 0 });
  assert.equal(result.datasets[0].label, "Sound level (dB)");
});

test("soundLevelSpectrumToChartData places the peak marker at the peak's own kHz, at 0 dB", () => {
  const peakDataset = soundLevelSpectrumToChartData(SPECTRUM).datasets[1];
  assert.deepEqual(peakDataset.data, [{ x: 20, y: 0 }]);
});

test("soundLevelSpectrumToChartData states the peak frequency in the legend", () => {
  const peakDataset = soundLevelSpectrumToChartData(SPECTRUM).datasets[1];
  assert.match(peakDataset.label, /^Peak frequency \(20\.0 kHz/);
});

test("the legend states the FFT bin width, since it bounds how precisely the peak is known", () => {
  const peakDataset = soundLevelSpectrumToChartData(SPECTRUM).datasets[1];
  assert.equal(peakDataset.label, "Peak frequency (20.0 kHz, bin width 20.00 kHz)");
});

test("a single-bin spectrum (no bin width to state) still produces a legend", () => {
  const peakDataset = soundLevelSpectrumToChartData({ freqs_hz: [0], db: [0], peak_freq_hz: 0 })
    .datasets[1];
  assert.equal(peakDataset.label, "Peak frequency (0.0 kHz)");
});

test("soundLevelSpectrumToChartData leaves the main dataset unaffected by the peak marker", () => {
  const withPeak = soundLevelSpectrumToChartData(SPECTRUM);
  const otherPeak = soundLevelSpectrumToChartData({ ...SPECTRUM, peak_freq_hz: 40000 });
  assert.deepEqual(withPeak.datasets[0].data, otherPeak.datasets[0].data);
});

test("the chart options use a linear x axis and title both axes with their units", () => {
  const { scales } = soundLevelSpectrumChartOptions(SPECTRUM);
  assert.equal(scales.x.type, "linear");
  assert.match(scales.x.title.text, /kHz/);
  assert.match(scales.y.title.text, /dB/);
});

test("the x axis ends at the spectrum's own last bin, not a rounded-up tick", () => {
  const { scales } = soundLevelSpectrumChartOptions({ freqs_hz: [0, 64000, 128000] });
  assert.equal(scales.x.max, 128);
  assert.equal(scales.x.min, 0);
});

test("a null peak (search-boundary artifact) produces no marker dataset at all", () => {
  const result = soundLevelSpectrumToChartData({ ...SPECTRUM, peak_freq_hz: null });
  assert.equal(result.datasets.length, 1);
  assert.equal(result.datasets[0].label, "Sound level (dB)");
});

const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
  soundLevelSpectrumToChartData,
  soundLevelSpectrumChartOptions,
  formatPeakLabel,
  stackPeakLabels,
} = require("../../src/fledermap/web/static/analysis_chart.js");

const SPECTRUM = {
  freqs_hz: [0, 20000, 40000, 60000],
  db: [-80, -20, 0, -12],
  peak_freq_hz: 40000,
  peaks_freq_hz: [20000, 40000],
};

test("soundLevelSpectrumToChartData pairs each bin's kHz with its dB value", () => {
  const result = soundLevelSpectrumToChartData(SPECTRUM);
  assert.deepEqual(result.datasets[0].data, [
    { x: 0, y: -80 },
    { x: 20, y: -20 },
    { x: 40, y: 0 },
    { x: 60, y: -12 },
  ]);
});

test("soundLevelSpectrumToChartData names the dataset", () => {
  const result = soundLevelSpectrumToChartData({
    freqs_hz: [0], db: [0], peak_freq_hz: null, peaks_freq_hz: [],
  });
  assert.equal(result.datasets[0].label, "Sound level (dB)");
});

test("every peak gets a marker at its own kHz, on the curve at that bin's dB value", () => {
  const peakDataset = soundLevelSpectrumToChartData(SPECTRUM).datasets[1];
  assert.deepEqual(peakDataset.data, [
    { x: 20, y: -20 },
    { x: 40, y: 0 },
  ]);
});

test("the peak dataset is flagged so the label plugin draws a frequency label per point", () => {
  const peakDataset = soundLevelSpectrumToChartData(SPECTRUM).datasets[1];
  assert.equal(peakDataset.peakLabels, true);
});

test("the legend states the FFT bin width, since it bounds how precisely a peak is known", () => {
  const peakDataset = soundLevelSpectrumToChartData(SPECTRUM).datasets[1];
  assert.equal(peakDataset.label, "Peaks (bin width 20.00 kHz)");
});

test("a single-bin spectrum (no bin width to state) still produces a legend", () => {
  const peakDataset = soundLevelSpectrumToChartData({
    freqs_hz: [0], db: [0], peak_freq_hz: 0, peaks_freq_hz: [0],
  }).datasets[1];
  assert.equal(peakDataset.label, "Peaks");
});

test("soundLevelSpectrumToChartData leaves the main dataset unaffected by the peaks", () => {
  const withPeaks = soundLevelSpectrumToChartData(SPECTRUM);
  const otherPeaks = soundLevelSpectrumToChartData({ ...SPECTRUM, peaks_freq_hz: [60000] });
  assert.deepEqual(withPeaks.datasets[0].data, otherPeaks.datasets[0].data);
});

test("no peaks (search-boundary artifact) produces no marker dataset at all", () => {
  const result = soundLevelSpectrumToChartData({
    ...SPECTRUM, peak_freq_hz: null, peaks_freq_hz: [],
  });
  assert.equal(result.datasets.length, 1);
  assert.equal(result.datasets[0].label, "Sound level (dB)");
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

test("the chart leaves headroom above the plot so a 0 dB peak's label is not clipped", () => {
  const { layout } = soundLevelSpectrumChartOptions(SPECTRUM);
  assert.ok(layout.padding.top >= 30);
});

test("formatPeakLabel is the frequency alone, one decimal, in kHz", () => {
  assert.equal(formatPeakLabel(45.23), "45.2 kHz");
  assert.equal(formatPeakLabel(11), "11.0 kHz");
});

test("stackPeakLabels keeps labels that do not overlap on the same level", () => {
  const levels = stackPeakLabels(
    [{ centerPx: 100, widthPx: 50 }, { centerPx: 200, widthPx: 50 }],
    4
  );
  assert.deepEqual(levels, [0, 0]);
});

test("stackPeakLabels raises a label that would overlap the previous one", () => {
  const levels = stackPeakLabels(
    [{ centerPx: 100, widthPx: 50 }, { centerPx: 130, widthPx: 50 }],
    4
  );
  assert.deepEqual(levels, [0, 1]);
});

test("stackPeakLabels drops a raised label back down once it clears the neighbours", () => {
  // Third label overlaps only the raised second one, not the first -> level 0 is free again.
  const levels = stackPeakLabels(
    [
      { centerPx: 100, widthPx: 50 },
      { centerPx: 130, widthPx: 50 },
      { centerPx: 190, widthPx: 50 },
    ],
    4
  );
  assert.deepEqual(levels, [0, 1, 0]);
});

test("stackPeakLabels treats a gap smaller than the padding as an overlap", () => {
  // 50px labels centred 52px apart leave a 2px gap; with a 4px minimum they collide.
  const levels = stackPeakLabels(
    [{ centerPx: 100, widthPx: 50 }, { centerPx: 152, widthPx: 50 }],
    4
  );
  assert.deepEqual(levels, [0, 1]);
});

const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
  soundLevelSpectrumToChartData,
  soundLevelSpectrumChartOptions,
  formatPeakLabel,
  placePeakLabels,
  peakLabelPlugin,
  PEAK_LABEL_HEADROOM_PX,
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

test("the headroom for peak labels is not padding above the legend", () => {
  // layout.padding.top would sit ABOVE the legend, leaving the legend on top of a 0 dB peak's
  // label; the room has to be between the legend and the plot (see peakLabelPlugin.beforeInit).
  const { layout } = soundLevelSpectrumChartOptions(SPECTRUM);
  assert.equal(layout, undefined);
});

test("peakLabelPlugin grows the legend downward so the labels have room between it and the plot", () => {
  const legend = {
    height: 0,
    fit() {
      this.height = 20;
    },
  };
  peakLabelPlugin.beforeInit({ legend });
  legend.fit();
  assert.equal(legend.height, 20 + PEAK_LABEL_HEADROOM_PX);
  assert.ok(PEAK_LABEL_HEADROOM_PX >= 30);
});

test("peakLabelPlugin tolerates a chart without a legend", () => {
  assert.doesNotThrow(() => peakLabelPlugin.beforeInit({ legend: null }));
});

test("the peaks legend swatch is red like the markers, not the default grey", () => {
  // Chart.js draws a legend swatch from the dataset's backgroundColor/borderColor, not from the
  // point styling the markers use.
  const peakDataset = soundLevelSpectrumToChartData(SPECTRUM).datasets[1];
  assert.equal(peakDataset.backgroundColor, peakDataset.pointBackgroundColor);
  assert.equal(peakDataset.borderColor, peakDataset.pointBorderColor);
});

test("formatPeakLabel is the frequency alone, one decimal, in kHz", () => {
  assert.equal(formatPeakLabel(45.23), "45.2 kHz");
  assert.equal(formatPeakLabel(11), "11.0 kHz");
});

const label = (centerPx, baselineY, widthPx = 50) => ({ centerPx, widthPx, baselineY });

test("placePeakLabels leaves labels that do not overlap where their markers want them", () => {
  const ys = placePeakLabels([label(100, 70), label(200, 90)], 4, 14);
  assert.deepEqual(ys, [70, 90]);
});

test("placePeakLabels raises a colliding label to just above the one it collides with", () => {
  const ys = placePeakLabels([label(100, 70), label(130, 70)], 4, 14);
  assert.deepEqual(ys, [70, 56]);
});

test("placePeakLabels stacks against the placed label, not against the label's own marker", () => {
  // The second peak's marker sits lower than the first's; raising it by one line from ITS marker
  // (75 - 14 = 61) would leave the two labels' glyphs overlapping (70 vs 61 is under a line).
  const ys = placePeakLabels([label(100, 70), label(102, 75)], 4, 14);
  assert.deepEqual(ys, [70, 56]);
});

test("placePeakLabels does not raise a label whose marker is already a full line away", () => {
  const ys = placePeakLabels([label(100, 70), label(130, 40)], 4, 14);
  assert.deepEqual(ys, [70, 40]);
});

test("placePeakLabels drops back down once a label clears the raised neighbour", () => {
  // Third label overlaps neither of the first two horizontally -> keeps its own height.
  const ys = placePeakLabels([label(100, 70), label(130, 70), label(190, 70)], 4, 14);
  assert.deepEqual(ys, [70, 56, 70]);
});

test("placePeakLabels treats a gap smaller than the padding as an overlap", () => {
  // 50px labels centred 52px apart leave a 2px gap; with a 4px minimum they collide.
  const ys = placePeakLabels([label(100, 70), label(152, 70)], 4, 14);
  assert.deepEqual(ys, [70, 56]);
});

test("placePeakLabels clears every label it collides with, not just the first", () => {
  // The third overlaps both earlier labels, which sit on two different rows.
  const ys = placePeakLabels([label(100, 70), label(120, 70), label(110, 70)], 4, 14);
  assert.deepEqual(ys, [70, 56, 42]);
});

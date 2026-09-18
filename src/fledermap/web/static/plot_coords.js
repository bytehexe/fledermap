// src/fledermap/web/static/plot_coords.js -- the ONE definition of how a spectrogram raster's
// native pixels map to time and frequency. Pure, no DOM access (same split as marker_colors.js/
// classifier_logic.js, see CLAUDE.md's "JavaScript tooling"), loaded before recording_detail.js.
//
// Every consumer that turns a pixel into a reading (the Ruler's Min/Max kHz and Δt, the crosshair
// readout, click-to-seek) or a reading into a pixel (the axis ticks, the playback cursor, View
// Lock's scroll position) goes through the same object, so the axis and the Ruler cannot disagree
// about the mapping -- they used to each carry their own `maxFreqKhz - y / pxPerKhz` copy fed by a
// server-side nominal constant, which could also drift from the raster's real size by up to half a
// pixel (the server rounds `height_px`/`width_px`). Here the scale is derived from the raster's
// actual dimensions, so both edges of the image land exactly on 0 / maxFreqKhz and 0 / durationS.
//
// "Native" pixels are the raster's own unzoomed pixels: the space `fitDetailHeight`'s CSS `zoom`
// scales visually, and the space `wrap`-local mouse positions are converted to (client px divided
// by the on-screen scale).

function makePlotCoords({ widthPx, heightPx, durationS, maxFreqKhz }) {
  const pxPerS = widthPx / durationS;
  const pxPerKhz = heightPx / maxFreqKhz;
  return {
    pxPerMs: pxPerS / 1000,
    pxPerKhz,
    xToS: (xPx) => xPx / pxPerS,
    sToX: (timeS) => timeS * pxPerS,
    // Row 0 (top) is the highest frequency: the renderer flips the STFT vertically
    // (media/spectrogram.py) so low frequencies sit at the bottom, like every other viewer.
    yToKhz: (yPx) => maxFreqKhz - yPx / pxPerKhz,
    khzToY: (khz) => (maxFreqKhz - khz) * pxPerKhz,
  };
}

// The smallest whole multiple of `baseInterval` whose consecutive labels would be at least
// `minSpacingPx` apart on screen, given `screenPxPerUnit` (native px per unit already multiplied
// by the on-screen scale). Deliberately NOT `baseInterval * Math.ceil(1 / scale)`: that is
// discontinuous right around scale === 1 -- a real but tiny shrink (scale 0.97, from a few px of
// genuine overflow) already rounds `1 / 0.97` up to 2, doubling the label spacing for an
// imperceptible size change. A fixed screen-space threshold only widens once labels would
// actually start crowding.
function adaptiveTickInterval(baseInterval, screenPxPerUnit, minSpacingPx) {
  let interval = baseInterval;
  while (interval * screenPxPerUnit < minSpacingPx) interval += baseInterval;
  return interval;
}

// 0, interval, 2*interval, ... up to and including `max`. Integer multiples rather than
// repeated addition so a fractional interval cannot accumulate float error and drop or double the
// last tick.
function tickValues(max, interval) {
  const values = [];
  for (let i = 0; i * interval <= max; i += 1) values.push(i * interval);
  return values;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { makePlotCoords, adaptiveTickInterval, tickValues };
}

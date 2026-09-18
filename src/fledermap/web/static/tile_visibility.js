// src/fledermap/web/static/tile_visibility.js -- pure tile-ordering logic, split out so it can
// be unit-tested with Node's built-in test runner without a DOM (see marker_colors.js/
// classifier_logic.js for the same pattern; this file never touches `document`/`window`).
// Loaded via its own <script> tag in recording_details.html, BEFORE recording_detail.js, which
// calls orderTilesVisibleFirst as an ordinary global -- no module system, matching every other
// script in this project.

// Backlog: "when activating/deactivating denoise, start with the currently visible tile(s)
// first" -- the detail spectrogram/oscillogram tiles reload in DOM order on every denoise
// toggle, but the server-side render cache (media.py) only pays the real compute cost on
// whichever tile's request the browser sends FIRST for a given recording+denoise state; every
// other tile hits that cache. Reordering which tile's <img src> gets reassigned first is
// therefore enough to make the on-screen tile repaint before the off-screen ones, with no
// server change needed.
function orderTilesVisibleFirst(tileWidthsPx, rangeStartPx, rangeEndPx) {
  const visible = [];
  const rest = [];
  let tileStart = 0;
  tileWidthsPx.forEach((widthPx, index) => {
    const tileEnd = tileStart + widthPx;
    // Half-open overlap test against the half-open visible range -- a tile that merely touches
    // the range's edge (tileEnd === rangeStartPx or tileStart === rangeEndPx) is NOT visible.
    if (tileEnd > rangeStartPx && tileStart < rangeEndPx) {
      visible.push(index);
    } else {
      rest.push(index);
    }
    tileStart = tileEnd;
  });
  return visible.concat(rest);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { orderTilesVisibleFirst };
}

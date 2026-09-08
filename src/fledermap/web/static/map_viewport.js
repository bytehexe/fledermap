// src/fledermap/web/static/map_viewport.js -- pure logic for the map's viewport-scoped
// (bbox) refetching, split out of app.js for the same reason as marker_colors.js/
// classifier_logic.js: app.js executes `document.addEventListener(...)` at its top level,
// which crashes under plain `node --test`. This file never touches `document`/`window`.
// Loaded via its own <script> tag in map.html, BEFORE app.js, which calls these as
// ordinary globals.

// `bounds` is a plain {west, south, east, north} object, not a Leaflet LatLngBounds --
// app.js adapts `map.getBounds()` to this shape before calling in, so this function (and
// its test) needs no Leaflet dependency at all. Matches web/params.py's parse_bbox format:
// "min_lon,min_lat,max_lon,max_lat".
function bboxParam(bounds) {
  return `${bounds.west},${bounds.south},${bounds.east},${bounds.north}`;
}

// A generic trailing-edge debounce: the wrapped function only actually runs `delayMs`
// after the LAST call in a burst, and each call resets that timer -- used so a drag-pan
// (many rapid `moveend`-adjacent events) fires one fetch, not one per intermediate frame.
function debounce(fn, delayMs) {
  let timer = null;
  return (...args) => {
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      fn(...args);
    }, delayMs);
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { bboxParam, debounce };
}

//
// Pure URL query-param manipulation, no DOM access -- extracted as its own file
// so it's require()-able directly in `node:test`, following the established
// marker_colors.js/classifier_logic.js split pattern (CLAUDE.md's JavaScript
// tooling section: a file with top-level DOM access can't be require()-d).

function withQueryParam(url, key, value) {
  const [path, query] = url.split("?");
  const params = new URLSearchParams(query || "");
  params.set(key, value);
  return `${path}?${params.toString()}`;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { withQueryParam };
}

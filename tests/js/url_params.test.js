const test = require("node:test");
const assert = require("node:assert");
const { withQueryParam } = require("../../src/fledermap/web/static/url_params.js");

test("adds a new query param to a URL with no query string", () => {
  assert.strictEqual(
    withQueryParam("/recordings/abc/detail-spectrogram/0.webp", "denoise", "true"),
    "/recordings/abc/detail-spectrogram/0.webp?denoise=true",
  );
});

test("adds a new query param to a URL that already has one", () => {
  assert.strictEqual(
    withQueryParam("/recordings/abc/het-preview.opus?freq_hz=40000", "denoise", "true"),
    "/recordings/abc/het-preview.opus?freq_hz=40000&denoise=true",
  );
});

test("replaces an existing occurrence of the same param instead of duplicating it", () => {
  assert.strictEqual(
    withQueryParam("/x?denoise=true&freq_hz=40000", "denoise", "false"),
    "/x?denoise=false&freq_hz=40000",
  );
});

test("leaves the FREQ_HZ template placeholder in a het preview URL template untouched", () => {
  assert.strictEqual(
    withQueryParam("/recordings/abc/het-preview.opus?freq_hz=FREQ_HZ", "denoise", "true"),
    "/recordings/abc/het-preview.opus?freq_hz=FREQ_HZ&denoise=true",
  );
});

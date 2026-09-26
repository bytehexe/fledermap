// Geolocation fallback for the RTC sidebar widget (design spec
// 2026-09-26-fledermap-rtc-widget-design.md). No pure logic to extract for node:test here --
// this is DOM/browser-API event wiring end to end, covered by live-Chrome verification instead
// (CLAUDE.md's JavaScript tooling section).
document.addEventListener("htmx:afterRequest", function (evt) {
  var widget = document.getElementById("rtc-widget");
  if (!widget || evt.target !== widget) return;
  if (evt.detail.xhr.status !== 204) return;
  if (widget.dataset.geoTried) return;
  widget.dataset.geoTried = "1";
  if (!("geolocation" in navigator)) return;

  navigator.geolocation.getCurrentPosition(
    function (position) {
      var lat = position.coords.latitude;
      var lon = position.coords.longitude;
      var url = "/rtc-widget?lat=" + lat + "&lon=" + lon;
      // Update the element's own hx-get so later polls also carry the coordinates,
      // not just this one-off request. Without this, the next 5-minute poll uses the
      // original bare hx-get, gets another 204, and retries geolocation.
      widget.setAttribute("hx-get", url);
      window.htmx.ajax("GET", url, {
        target: "#rtc-widget",
        swap: "innerHTML",
      });
    },
    function () {
      // Denied, unavailable, or timed out: leave the widget empty. No retry -- the next
      // 5-minute poll will hit this same 204 branch, see geoTried already set, and stop here.
    },
  );
});

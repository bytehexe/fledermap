// Session detail page's mini-map (design spec section 7): plain recording
// markers for one session -- no clustering, no polyline, no site circle,
// spatial context only. Guarded on the container's presence since this
// script is loaded only on session_detail.html.
document.addEventListener("DOMContentLoaded", () => {
  const container = document.getElementById("session-mini-map");
  if (!container) return;

  const sessionId = container.dataset.sessionId;
  const map = L.map(container).setView([51.0, 10.0], 6);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors",
  }).addTo(map);

  // NOTE: the query param the API reads for this filter is `session`, not
  // `session_id` (see web/api/geojson.py's recordings_geojson -- it reads
  // flask.request.args.get("session")). `session_id` is only the Python-side
  // variable name and the DOM dataset key; using it as the query key here
  // would silently return every recording, unfiltered, instead of erroring.
  fetch(`/api/recordings.geojson?session=${sessionId}`)
    .then((response) => response.json())
    .then((data) => {
      const layer = L.geoJSON(data, {
        pointToLayer: (feature, latlng) =>
          L.circleMarker(latlng, { color: "#333333" }),
      }).addTo(map);
      // No markers (a session with no GPS-bearing recordings) is not an
      // error -- design spec section 10, same "degrade in place" convention
      // as Phase 5a's missing-media placeholder. getBounds() on an empty
      // layer is invalid, so this just leaves the map at its default view.
      if (layer.getBounds().isValid()) {
        map.fitBounds(layer.getBounds(), { maxZoom: 15 });
      }
    })
    .catch((err) => console.error("session mini-map fetch failed", err));
});

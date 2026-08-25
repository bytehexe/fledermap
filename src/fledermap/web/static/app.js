// The map is constructed once and never swapped or destroyed (design spec
// section 7, targeting parent spec section 10's tripwire #1 directly).
// Filters update its existing layers in place via fetch() -- there is no
// hx-swap anywhere near #map.

function filterForm() {
  return {
    from: "", to: "", taxon: "", session: "", source: "", verdict: "",
  };
}

document.addEventListener("DOMContentLoaded", () => {
  const map = L.map("map").setView([51.0, 10.0], 6);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors",
  }).addTo(map);

  const recordingsLayer = L.markerClusterGroup().addTo(map);
  const sitesLayer = L.layerGroup().addTo(map);

  function query() {
    const form = document.getElementById("filters");
    const params = new URLSearchParams(new FormData(form));
    for (const [key, value] of [...params.entries()]) {
      if (!value) params.delete(key);
    }
    return params;
  }

  function colorForVerdict(props) {
    if (props.verdict === "noise") return "gray";
    if (props.verdict === "no_id") return "orange";
    return "green";
  }

  async function refresh() {
    const params = query();

    const recordingsResponse = await fetch(`/api/recordings.geojson?${params}`);
    const recordingsData = await recordingsResponse.json();
    recordingsLayer.clearLayers();
    L.geoJSON(recordingsData, {
      pointToLayer: (feature, latlng) =>
        L.circleMarker(latlng, { color: colorForVerdict(feature.properties) })
          .bindPopup(
            `${feature.properties.verdict ?? "unknown"} ` +
            `(${feature.properties.source ?? "no source"})<br>` +
            feature.properties.recorded_at,
          ),
    }).eachLayer((layer) => recordingsLayer.addLayer(layer));

    const sitesResponse = await fetch(`/api/sites.geojson?${params}`);
    const sitesData = await sitesResponse.json();
    sitesLayer.clearLayers();
    L.geoJSON(sitesData, {
      pointToLayer: (feature, latlng) =>
        L.circle(latlng, { radius: feature.properties.radius_m, color: "blue" })
          .bindPopup(
            `${feature.properties.name}<br>` +
            `${feature.properties.recording_count} recordings`,
          ),
    }).eachLayer((layer) => sitesLayer.addLayer(layer));
  }

  document.getElementById("filters").addEventListener("input", refresh);
  refresh();
});

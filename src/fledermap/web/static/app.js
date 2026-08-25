// The map is constructed once and never swapped or destroyed (design spec
// section 7, targeting parent spec section 10's tripwire #1 directly).
// Filters update its existing layers in place via fetch() -- there is no
// hx-swap anywhere near #map.

function filterForm() {
  return {
    from: "", to: "", taxon: "", session: "", source: "", verdict: "",
  };
}

// Small fixed palette, keyed by taxon_id (design spec section 9: markers
// "Coloured by current-best taxon"). Not meant to be perceptually perfect --
// just distinct enough to tell species apart on the map at a glance.
const TAXON_PALETTE = [
  "#e6194b", "#3cb44b", "#4363d8", "#f58231",
  "#911eb4", "#46f0f0", "#f032e6", "#bcf60c",
  "#fabebe", "#008080",
];

// verdict still overrides taxon color for the cases where taxon doesn't
// apply: noise/no_id have no taxon at all, and a species verdict with a
// null taxon_id (identified as *some* species, but unmapped -- see
// docs/references.md on unmapped labels) gets a distinct neutral color
// rather than crashing on `undefined % length`.
function colorForFeature(props) {
  if (props.verdict === "noise") return "gray";
  if (props.verdict === "no_id") return "orange";
  if (props.taxon_id !== null && props.taxon_id !== undefined) {
    return TAXON_PALETTE[props.taxon_id % TAXON_PALETTE.length];
  }
  return "#333333";
}

document.addEventListener("DOMContentLoaded", () => {
  const map = L.map("map").setView([51.0, 10.0], 6);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors",
  }).addTo(map);

  const recordingsLayer = L.markerClusterGroup().addTo(map);
  const sitesLayer = L.layerGroup().addTo(map);
  L.control.layers(null, {
    Recordings: recordingsLayer,
    Sites: sitesLayer,
  }).addTo(map);

  function query() {
    const form = document.getElementById("filters");
    const params = new URLSearchParams(new FormData(form));
    for (const [key, value] of [...params.entries()]) {
      if (!value) params.delete(key);
    }
    return params;
  }

  async function refreshRecordings(params) {
    let response;
    try {
      response = await fetch(`/api/recordings.geojson?${params}`);
    } catch (err) {
      console.error("recordings.geojson fetch failed", err);
      return;
    }
    if (!response.ok) {
      console.error("recordings.geojson returned", response.status);
      return;
    }
    const recordingsData = await response.json();
    recordingsLayer.clearLayers();
    L.geoJSON(recordingsData, {
      pointToLayer: (feature, latlng) =>
        L.circleMarker(latlng, { color: colorForFeature(feature.properties) })
          .bindPopup(
            `${feature.properties.taxon_name ?? "unidentified"}<br>` +
            `${feature.properties.verdict ?? "unknown"} ` +
            `(${feature.properties.source ?? "no source"})<br>` +
            feature.properties.recorded_at,
          ),
    }).eachLayer((layer) => recordingsLayer.addLayer(layer));
  }

  async function refreshSites(params) {
    let response;
    try {
      response = await fetch(`/api/sites.geojson?${params}`);
    } catch (err) {
      console.error("sites.geojson fetch failed", err);
      return;
    }
    if (!response.ok) {
      console.error("sites.geojson returned", response.status);
      return;
    }
    const sitesData = await response.json();
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

  // The two fetches run independently -- one endpoint erroring (e.g. a bad
  // filter value returning 400) must not prevent the other layer from
  // refreshing, and must not throw out of `refresh()` and leave both layers
  // stale.
  function refresh() {
    const params = query();
    void refreshRecordings(params);
    void refreshSites(params);
  }

  document.getElementById("filters").addEventListener("input", refresh);
  refresh();
});

// The map is constructed once and never swapped or destroyed (design spec
// section 7, targeting parent spec section 10's tripwire #1 directly).
// Filters update its existing layers in place via fetch() -- there is no
// hx-swap anywhere near #map.

function filterForm() {
  return {
    from: "", to: "", taxon: "", session: "", source: "", verdict: "", site: "",
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

// Step 0: Register the drawer's Alpine store
document.addEventListener("alpine:init", () => {
  Alpine.store("drawer", { open: false, collapsed: false });
});

// Step 3: Add the site-filter bridge function
window.fledermapFilterBySite = function (siteId) {
  const input = document.querySelector('#filters [name="site"]');
  input.value = siteId;
  input.dispatchEvent(new Event("input", { bubbles: true }));
};

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

  const recordingLayersByHash = new Map();
  let highlightedRecordingLayer = null;

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
    recordingLayersByHash.clear();
    highlightedRecordingLayer = null;
    L.geoJSON(recordingsData, {
      pointToLayer: (feature, latlng) => {
        const marker = L.circleMarker(latlng, { color: colorForFeature(feature.properties) })
          .on("click", () => openRecordingPanel(feature.properties.audio_hash, params));
        recordingLayersByHash.set(feature.properties.audio_hash, marker);
        return marker;
      },
    }).eachLayer((layer) => recordingsLayer.addLayer(layer));
  }

  function openRecordingPanel(audioHash, params) {
    htmx.ajax("GET", `/recordings/${audioHash}/panel?${params}`, {
      target: "#drawer-body",
      swap: "innerHTML",
    });
    Alpine.store("drawer").open = true;
    Alpine.store("drawer").collapsed = false;
  }

  // P5a-6: prev/next must pan AND highlight, not just pan -- otherwise the
  // drawer and the map can visibly disagree about which recording is current.
  function highlightRecording(audioHash) {
    if (highlightedRecordingLayer) {
      highlightedRecordingLayer.setStyle({ weight: 1 });
    }
    const marker = recordingLayersByHash.get(audioHash);
    if (marker) {
      marker.setStyle({ weight: 4 });
      highlightedRecordingLayer = marker;
    }
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
          .on("click", () => openSitePanel(feature.properties.id)),
    }).eachLayer((layer) => sitesLayer.addLayer(layer));
  }

  function openSitePanel(siteId) {
    htmx.ajax("GET", `/sites/${siteId}/panel`, {
      target: "#drawer-body",
      swap: "innerHTML",
    });
    Alpine.store("drawer").open = true;
    Alpine.store("drawer").collapsed = false;
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

  // Step 2: Listen for recording-selected to pan and highlight
  document.body.addEventListener("recording-selected", (event) => {
    const { latitude, longitude, hash } = event.detail;
    if (latitude != null && longitude != null) {
      map.panTo([latitude, longitude]);
    }
    highlightRecording(hash);
  });

  // Step 4: Add drag-resize on the handle
  const drawer = document.getElementById("drawer");
  const handle = document.getElementById("drawer-handle");
  let dragging = false;

  handle.addEventListener("mousedown", () => { dragging = true; });
  document.addEventListener("mouseup", () => { dragging = false; });
  document.addEventListener("mousemove", (event) => {
    if (!dragging) return;
    const newHeight = window.innerHeight - event.clientY;
    drawer.style.height = `${Math.max(120, Math.min(newHeight, window.innerHeight - 80))}px`;
  });

  refresh();
});

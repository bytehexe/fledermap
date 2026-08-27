// The map is constructed once and never swapped or destroyed (design spec
// section 7, targeting parent spec section 10's tripwire #1 directly).
// Filters update its existing layers in place via fetch() -- there is no
// hx-swap anywhere near #map.
//
// The URL's query string mirrors the filter bar plus whichever drawer panel
// is open (`recording=<hash>` or `panel=<site id>` -- distinct from the
// `site` filter param, which narrows the marker set rather than naming an
// open panel), kept in sync via the History API: every user-initiated
// visible change -- a filter edit, opening/closing a drawer panel, prev/next
// inside the drawer -- pushes its own history entry, so back/forward step
// through recent selections one at a time. A `popstate` (history
// navigation) restores state from the URL and re-fits the map to whatever's
// now visible -- arriving via history is "opening" a remembered state, the
// same treatment as a fresh page load, not a live in-place edit. Live
// filter edits deliberately do NOT re-fit the map -- that would make it
// jump around while a person is still adjusting filters.

function filterForm() {
  const params = new URLSearchParams(window.location.search);
  return {
    from: params.get("from") || "",
    to: params.get("to") || "",
    taxon: params.get("taxon") || "",
    session: params.get("session") || "",
    source: params.get("source") || "",
    verdict: params.get("verdict") || "",
    site: params.get("site") || "",
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
  // featureGroup, not plain layerGroup -- only FeatureGroup (and
  // MarkerClusterGroup, which extends it) implements getBounds(), needed
  // below to fit the view to whatever's actually loaded.
  const sitesLayer = L.featureGroup().addTo(map);
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

  // Which drawer panel (if any) is currently open, so URL syncing knows
  // what to encode alongside the filters.
  let openPanel = null; // null | { kind: "recording", id: <hash> } | { kind: "site", id: <site id> }

  function buildUrl() {
    const params = query();
    if (openPanel && openPanel.kind === "recording") {
      params.set("recording", openPanel.id);
    } else if (openPanel && openPanel.kind === "site") {
      params.set("panel", openPanel.id);
    }
    const qs = params.toString();
    return qs ? `?${qs}` : "/";
  }

  function pushUrl() {
    history.pushState(null, "", buildUrl());
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

  function openRecordingPanel(audioHash, params, { sync = true } = {}) {
    htmx.ajax("GET", `/recordings/${audioHash}/panel?${params}`, {
      target: "#drawer-body",
      swap: "innerHTML",
    });
    Alpine.store("drawer").open = true;
    Alpine.store("drawer").collapsed = false;
    openPanel = { kind: "recording", id: audioHash };
    if (sync) pushUrl();
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

  function openSitePanel(siteId, { sync = true } = {}) {
    htmx.ajax("GET", `/sites/${siteId}/panel`, {
      target: "#drawer-body",
      swap: "innerHTML",
    });
    Alpine.store("drawer").open = true;
    Alpine.store("drawer").collapsed = false;
    openPanel = { kind: "site", id: siteId };
    if (sync) pushUrl();
  }

  // Closing the drawer also drops recording=/panel= from the URL, matching
  // the "URL always reflects what's on screen" rule -- exposed on window so
  // the close button's inline @click in map.html can reach it, the same
  // pattern window.fledermapFilterBySite already uses.
  window.fledermapCloseDrawer = function () {
    Alpine.store("drawer").open = false;
    Alpine.store("drawer").collapsed = false;
    document.getElementById("drawer-body").innerHTML = "";
    openPanel = null;
    pushUrl();
  };

  function openPanelFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const recordingHash = params.get("recording");
    const sitePanelId = params.get("panel");
    if (recordingHash) {
      openRecordingPanel(recordingHash, query(), { sync: false });
    } else if (sitePanelId) {
      openSitePanel(sitePanelId, { sync: false });
    }
  }

  // The two fetches run independently -- one endpoint erroring (e.g. a bad
  // filter value returning 400) must not prevent the other layer from
  // refreshing. Promise.allSettled (not Promise.all) is deliberate even
  // though neither function currently throws (each already catches its own
  // errors) -- it preserves that guarantee even if a future edit adds a
  // throwing path.
  async function refreshLayers(params) {
    await Promise.allSettled([refreshRecordings(params), refreshSites(params)]);
  }

  // Fits the view to the union of whatever's currently loaded in both
  // layers -- a no-op (leaves the current view alone) when neither layer
  // has anything, same "degrade in place" convention the session mini-map
  // already uses. maxZoom matches that same convention too, so a single
  // point (or a tight cluster) doesn't zoom in absurdly close.
  function fitToVisible() {
    const bounds = L.latLngBounds();
    bounds.extend(recordingsLayer.getBounds());
    bounds.extend(sitesLayer.getBounds());
    if (bounds.isValid()) {
      map.fitBounds(bounds, { maxZoom: 15 });
    }
  }

  // A live filter edit: refetch, then sync the URL to the new filter
  // values (not waiting on the fetch, so the URL reflects what the user
  // asked for immediately regardless of fetch latency/failure). Does NOT
  // re-fit the map -- see the file header comment.
  function refresh() {
    const params = query();
    void refreshLayers(params);
    pushUrl();
  }

  document.getElementById("filters").addEventListener("input", refresh);

  // Step 2: Listen for recording-selected to pan and highlight
  document.body.addEventListener("recording-selected", (event) => {
    const { latitude, longitude, hash } = event.detail;
    if (latitude != null && longitude != null) {
      map.panTo([latitude, longitude]);
    }
    highlightRecording(hash);
    // prev/next inside the drawer swaps which recording's panel is showing
    // -- the URL and history need to follow, same as a fresh marker click.
    openPanel = { kind: "recording", id: hash };
    pushUrl();
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

  // Back/forward: restore the filter bar from the now-current URL (Alpine's
  // reactive data, not just the DOM -- $data's properties are the actual
  // bound state, so assigning to them updates the x-model'd inputs too),
  // refetch, re-fit (a history jump is an "arrival", like initial load),
  // and reopen whichever panel (if any) the restored URL names.
  window.addEventListener("popstate", () => {
    Object.assign(Alpine.$data(document.body), filterForm());
    const params = query();
    refreshLayers(params).then(() => {
      fitToVisible();
      Alpine.store("drawer").open = false;
      Alpine.store("drawer").collapsed = false;
      document.getElementById("drawer-body").innerHTML = "";
      openPanel = null;
      openPanelFromUrl();
    });
  });

  refreshLayers(query()).then(() => {
    fitToVisible();
    openPanelFromUrl();
  });
});

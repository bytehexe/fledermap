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
    taxon_exclude: params.get("taxon_exclude") === "1",
    session: params.get("session") || "",
    source: params.get("source") || "",
    verdict: params.get("verdict") || "",
    site: params.get("site") || "",
    favourite_only: params.get("favourite_only") === "1",
  };
}

// Original fixed palette, kept verbatim -- and kept as literal lookups for
// exactly the taxon_ids it used to cover (1..10 via the same `% length`
// formula it always used) -- so the species most likely to already be
// familiar from the map don't shift color out from under anyone (design spec
// section 9: markers "Coloured by current-best taxon"). Not meant to be
// perceptually perfect, just distinct enough to tell species apart at a
// glance.
const TAXON_PALETTE = [
  "#e6194b", "#3cb44b", "#4363d8", "#f58231",
  "#911eb4", "#46f0f0", "#f032e6", "#bcf60c",
  "#fabebe", "#008080",
];

// Every taxon_id beyond the fixed palette above is colored by a deterministic
// hash instead: golden-angle hue stepping (137.508 degrees apart -- the angle
// that spreads points maximally around a circle with no fixed count needed).
// This is what actually fixes the bug the old `taxon_id % TAXON_PALETTE.length`
// scheme had: once taxa_eu.yaml/taxa_na.yaml grew past 10 species (2026-09-05,
// full EU+NA coverage), unrelated taxa sharing a residue class mod 10 rendered
// as visually identical markers. A hash over the full id range never runs out.
//
// Reserved colors -- never handed out by colorForTaxon, so a generated hue
// can't be mistaken for one of these fixed meanings elsewhere on the map:
//   gray            -- noise verdict
//   orange          -- no_id verdict
//   #333333         -- species verdict with no taxon (unmapped species)
//   blue            -- site-radius circles (the L.circle call below)
//   GPS_TRACK_COLOR -- reserved for a future GPS-track overlay, unused today
const GPS_TRACK_COLOR = "#00bcd4";

// Where the golden-angle sequence starts. A hash can't guarantee NO hue ever
// lands near a reserved one -- the sequence sweeps the whole circle given
// enough ids -- but starting well clear of orange (~39 deg) and blue
// (~240 deg) means the first taxa past id 10 (the ones soonest to actually
// show up) don't immediately collide with either.
const HASH_HUE_START = 120;
const GOLDEN_ANGLE = 137.508;

function colorForTaxon(taxonId) {
  if (taxonId <= TAXON_PALETTE.length) {
    return TAXON_PALETTE[taxonId % TAXON_PALETTE.length];
  }
  const hue = (HASH_HUE_START + taxonId * GOLDEN_ANGLE) % 360;
  return `hsl(${hue.toFixed(1)}, 70%, 45%)`;
}

// verdict still overrides taxon color for the cases where taxon doesn't
// apply: noise/no_id have no taxon at all, and a species verdict with a
// null taxon_id (identified as *some* species, but unmapped -- see
// docs/references.md on unmapped labels) gets a distinct neutral color
// rather than crashing on colorForTaxon(undefined).
function colorForFeature(props) {
  if (props.verdict === "noise") return "gray";
  if (props.verdict === "no_id") return "orange";
  if (props.taxon_id !== null && props.taxon_id !== undefined) {
    return colorForTaxon(props.taxon_id);
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
    // taxon_exclude's checkbox is disabled via Alpine's `:disabled="!taxon"`,
    // and FormData silently omits disabled fields -- but Alpine's own DOM
    // update is queued on a microtask, while this listener runs
    // synchronously in the same tick as the taxon <select>'s own "input"
    // event. Re-picking a taxon right after "Any" can read the checkbox's
    // *stale* disabled=true from a moment ago, so FormData drops
    // taxon_exclude even though the box is visibly checked (reported bug:
    // re-select a taxon, "not" stays ticked but stops applying). Reading
    // .checked directly sidesteps the disabled attribute's timing entirely.
    const exclude = form.elements.taxon_exclude;
    if (params.get("taxon") && exclude && exclude.checked) {
      params.set("taxon_exclude", "1");
    } else {
      params.delete("taxon_exclude");
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
        // weight: 1 explicitly, matching what highlightRecording() resets a
        // deselected marker to -- CircleMarker's own default (Leaflet's
        // Path default, weight: 3) is close enough to the highlighted
        // weight (4) that every marker looked selected until you'd
        // personally clicked through and away from it at least once.
        const marker = L.circleMarker(latlng, {
          color: colorForFeature(feature.properties),
          weight: 1,
        }).on("click", () => openRecordingPanel(feature.properties.audio_hash, params));
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

  function urlNamesAPanel() {
    const params = new URLSearchParams(window.location.search);
    return params.has("recording") || params.has("panel");
  }

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

  const drawer = document.getElementById("drawer");
  const mapEl = document.getElementById("map");

  // Shrinks #map's own rendered height to the space actually visible above
  // the drawer, rather than leaving the drawer floating on top of a
  // full-height map and trying to compensate individual pan targets
  // afterward -- that couldn't work: Leaflet's own internal panning (e.g.
  // zoomToShowLayer's, or spiderfy's leg-fitting) has no idea about a
  // manual offset and visibly jumps to its own drawer-unaware target
  // first. With the container itself correctly sized, EVERY pan Leaflet
  // does -- ours or its own -- is automatically correct with no
  // per-call compensation anywhere.
  //
  // ResizeObserver on #drawer (not a handful of hand-picked event hooks)
  // reacts to every way its rendered height can change -- open/close
  // (x-show toggles display:none, reporting a zero rect), the collapse
  // toggle, and the drag-resize handle below -- without needing to
  // remember to call this after each one individually. #map's own
  // margin-bottom tracks the drawer's height via a CSS custom property
  // (see app.css); invalidateSize() tells Leaflet to recompute against the
  // new container size, which is the one call actually required here.
  new ResizeObserver(() => {
    const height = drawer.getBoundingClientRect().height;
    mapEl.style.setProperty("--drawer-h", `${height}px`);
    map.invalidateSize();
  }).observe(drawer);

  // Crosshair readout: freq+time next to the cursor while hovering the
  // spectrogram (design backlog "Crosshair on the spectrogram" item).
  // Delegated on #drawer-body -- its content is replaced wholesale by
  // htmx on every panel swap, so binding directly to `.spectrogram` would
  // need re-binding after each swap; delegation needs it once.
  const drawerBody = document.getElementById("drawer-body");
  const readout = document.getElementById("crosshair-readout");
  drawerBody.addEventListener("mousemove", (event) => {
    const img = event.target.closest(".spectrogram");
    if (!img) {
      readout.hidden = true;
      return;
    }
    const durationS = parseFloat(img.dataset.durationS);
    const maxFreqKhz = parseFloat(img.dataset.maxFreqKhz);
    if (Number.isNaN(durationS) || Number.isNaN(maxFreqKhz)) {
      readout.hidden = true;
      return;
    }
    const rect = img.getBoundingClientRect();
    const relX = (event.clientX - rect.left) / rect.width;
    const relY = (event.clientY - rect.top) / rect.height;
    const timeS = relX * durationS;
    const freqKhz = (1 - relY) * maxFreqKhz;
    readout.textContent = `${timeS.toFixed(3)} s\n${freqKhz.toFixed(1)} kHz`;
    readout.style.left = `${event.clientX + 12}px`;
    readout.style.top = `${event.clientY - 12}px`;
    readout.hidden = false;
  });
  drawerBody.addEventListener("mouseleave", () => {
    readout.hidden = true;
  });

  // The recording-details page's "Details" link needs to know where to
  // send its own "back" link -- but the server rendering this panel only
  // ever sees the filter params of the panel's own fetch, never `recording=`
  // / `panel=`, which are added to the URL bar separately by pushUrl()
  // (called from the click handler, or -- for prev/next and the HX-Trigger
  // "recording-selected" path -- from that event's own handler below,
  // always before this listener's swap-completed event fires). So the
  // *only* place the full "what's on screen" URL is known is here, on the
  // client, right after each swap -- read it fresh every time rather than
  // trying to reconstruct it server-side.
  drawerBody.addEventListener("htmx:afterSwap", () => {
    const link = drawerBody.querySelector(".details-link");
    if (!link) return;
    const url = new URL(link.href, window.location.origin);
    url.searchParams.set("return_to", window.location.pathname + window.location.search);
    link.href = `${url.pathname}${url.search}`;
  });

  // Browsers don't reliably pause media on DOM removal -- without this, the
  // outgoing recording's <audio> can keep playing audibly after an htmx swap
  // replaces #drawer-body with the next recording's panel. Pause it just
  // before the swap removes it from the DOM.
  drawerBody.addEventListener("htmx:beforeSwap", () => {
    const audioEl = drawerBody.querySelector(".audio-controls audio");
    if (audioEl && !audioEl.paused) audioEl.pause();
  });

  // HET/TE control bar + click-to-play + playback cursor for the drawer panel
  // (design spec 2026-09-04-fledermap-het-playback-design.md section 4) --
  // delegated / re-initialized on every htmx swap, same reasoning as the
  // crosshair listener above: #drawer-body's content is replaced wholesale.
  let drawerAudioControls = null;
  drawerBody.addEventListener("htmx:afterSwap", () => {
    const controlsEl = drawerBody.querySelector(".audio-controls");
    const audioEl = controlsEl ? controlsEl.querySelector("audio") : null;
    drawerAudioControls = controlsEl && audioEl ? initAudioControls(controlsEl, audioEl) : null;
    if (!drawerAudioControls || !audioEl) return;
    // Bound once here, on the freshly-created `audioEl`, rather than inside a
    // `play` listener -- `audioEl` survives repeated pause/resume cycles on
    // the same panel instance (manual pause, TE/HET mode switch), so binding
    // on every `play` would add a new listener each time with no matching
    // removal. Same pattern as recording_detail.js's own `timeupdate` binding.
    const img = drawerBody.querySelector(".spectrogram");
    const cursor = drawerBody.querySelector(".playback-cursor");
    if (!img || !cursor) return;
    audioEl.addEventListener("timeupdate", () => {
      const durationS = parseFloat(img.dataset.durationS);
      if (Number.isNaN(durationS) || durationS <= 0) return;
      const spectrogramTimeS = audioEl.currentTime / drawerAudioControls.getTimeExpansionFactor();
      const relX = spectrogramTimeS / durationS;
      cursor.style.left = `${relX * 100}%`;
      cursor.hidden = false;
    });
  });

  drawerBody.addEventListener("click", (event) => {
    const img = event.target.closest(".spectrogram");
    if (!img || !drawerAudioControls) return;
    const audioEl = drawerBody.querySelector(".audio-controls audio");
    if (!audioEl) return;
    const durationS = parseFloat(img.dataset.durationS);
    if (Number.isNaN(durationS)) return;
    const rect = img.getBoundingClientRect();
    const relX = (event.clientX - rect.left) / rect.width;
    const spectrogramTimeS = relX * durationS;
    audioEl.currentTime = spectrogramTimeS * drawerAudioControls.getTimeExpansionFactor();
    audioEl.play();
  });

  // Step 2: Listen for recording-selected to pan, reveal (zoom/spiderfy),
  // and highlight. Shared by a fresh marker click, prev/next inside the
  // drawer, and restoring a panel from the URL -- all three dispatch this
  // same event, so all three now reveal the target marker's cluster the
  // same way, not just the initial click.
  //
  // Pan to the exact coordinate FIRST, before calling zoomToShowLayer --
  // not the other way round. zoomToShowLayer only moves the map when it
  // decides the marker isn't already visible; panning to the marker's
  // real lat/lng first means that check already finds it in view for the
  // common case (already unclustered, or clustered but not requiring a
  // zoom change), so zoomToShowLayer does nothing but spiderfy in place --
  // ONE motion, not our own pan and then a second, separately-aimed one
  // from the plugin landing a moment later (previously both "roughly
  // correct" but visibly sequential/competing). When a zoom change genuinely
  // is needed (still clustered at the current zoom), zoomToShowLayer's own
  // zoom-in happens around the point we already centered on, rather than
  // ALSO panning sideways to get there.
  document.body.addEventListener("recording-selected", (event) => {
    const { latitude, longitude, hash } = event.detail;
    const marker = recordingLayersByHash.get(hash);

    if (latitude != null && longitude != null) {
      map.panTo([latitude, longitude]);
    }

    // No marker (e.g. a stale hash after a filter change rebuilt the
    // layer) -- just highlight, same graceful no-op highlightRecording
    // already falls back to.
    if (marker) {
      recordingsLayer.zoomToShowLayer(marker, () => highlightRecording(hash));
    } else {
      highlightRecording(hash);
    }

    // prev/next inside the drawer swaps which recording's panel is showing
    // -- the URL and history need to follow, same as a fresh marker click.
    openPanel = { kind: "recording", id: hash };
    pushUrl();
  });

  // Same HX-Trigger mechanism as recording-selected above, for the site
  // panel -- fits the view to the site's actual extent (centroid + radius)
  // rather than merely panning to its centroid at whatever zoom the map
  // already happened to be at. Without this, opening a site panel (a fresh
  // click, or restoring one from the URL on reload) left the map wherever
  // it was -- on reload from a bare panel=<id> URL that's the initial
  // "whole of Germany" view, since fitToVisible() is deliberately skipped
  // whenever the URL names a panel (see the popstate/initial-load comments
  // below) on the assumption that opening the panel itself would supply a
  // more specific destination, same as it does for a recording.
  document.body.addEventListener("site-selected", (event) => {
    const { latitude, longitude, radius_m } = event.detail;
    if (latitude == null || longitude == null) return;
    const bounds = L.circle([latitude, longitude], { radius: radius_m || 0 }).getBounds();
    map.fitBounds(bounds, { maxZoom: 15 });
  });

  // Step 4: Add drag-resize on the handle
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
  // and reopen whichever panel (if any) the restored URL names. Skip the
  // fit when the URL already names a panel to open -- openPanelFromUrl's
  // own pan-to-that-recording (above) is a second, more specific
  // destination competing with "fit to everything" a moment later;
  // "roughly correct, but two visibly sequential motions" rather than
  // useful. Only fit to everything when there's no panel to zoom to
  // instead.
  window.addEventListener("popstate", () => {
    Object.assign(Alpine.$data(document.body), filterForm());
    const params = query();
    refreshLayers(params).then(() => {
      if (!urlNamesAPanel()) fitToVisible();
      Alpine.store("drawer").open = false;
      Alpine.store("drawer").collapsed = false;
      document.getElementById("drawer-body").innerHTML = "";
      openPanel = null;
      openPanelFromUrl();
    });
  });

  refreshLayers(query()).then(() => {
    if (!urlNamesAPanel()) fitToVisible();
    openPanelFromUrl();
  });
});

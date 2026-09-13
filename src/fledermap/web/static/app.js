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
    needs_review_only: params.get("needs_review_only") === "1",
  };
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

  // A CLONE of `params` with the map's current viewport appended as `bbox`
  // -- never mutates the caller's own params object, and deliberately never
  // used by buildUrl()/pushUrl() below: bbox is a live viewport artifact,
  // not a filter the user consciously set, and pushing a new history entry
  // on every pan/zoom (or sharing a URL that "freezes" the sender's own
  // viewport) would both be wrong. Used only right before a fetch that
  // should be scoped to what's currently on screen (refresh() and the
  // moveend/zoomend listener below) -- NOT the initial load or a
  // popstate-restore, which deliberately fetch everything matching the
  // filters and then fit the view to it (see the file header comment and
  // fitToVisible() below).
  function withViewportBbox(params) {
    const withBbox = new URLSearchParams(params);
    const bounds = map.getBounds();
    withBbox.set(
      "bbox",
      bboxParam({
        west: bounds.getWest(),
        south: bounds.getSouth(),
        east: bounds.getEast(),
        north: bounds.getNorth(),
      }),
    );
    return withBbox;
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
    // No-op when the URL already reflects `openPanel`/the filters -- e.g.
    // openRecordingPanel's own sync-gated push already applied this exact
    // state, so the later async "recording-selected" handler's push (needed
    // for prev/next, which never calls openRecordingPanel) would otherwise
    // add a second, identical history entry. Root-caused live 2026-09:
    // pressing the browser's Back button from a fresh "Show on map"
    // navigation popped one of these no-op duplicates first, landing back
    // on the exact same URL instead of visibly going anywhere (Obsidian
    // backlog, "click show map ... expected to get back to the recordings
    // details page").
    const next = buildUrl();
    if (next === (window.location.search || "/")) return;
    history.pushState(null, "", next);
  }

  const recordingLayersByHash = new Map();
  let highlightedRecordingLayer = null;

  // Whether a recording-selected reveal (panTo + zoomToShowLayer) is
  // currently under way -- see refreshViewport and that handler below.
  // Cleared by zoomToShowLayer's OWN completion callback (the library's
  // real, authoritative "done" signal -- not a guessed duration), plus a
  // generous timeout as a pure safety net in case that callback somehow
  // never fires, so this can't get stuck true forever and silently disable
  // every future viewport refresh.
  //
  // An earlier version of this flag compared `map.getZoom()` against the
  // zoom recorded at reveal-start instead ("unchanged" == "still just this
  // reveal, not a real navigation"), rejected as racing spiderfy's own
  // variable-duration animation. That comparison had a second, worse
  // problem found 2026-09-13 via a live headless-Chrome repro: a reveal
  // starting from the map's default zoom (e.g. the "Show on map" link from
  // the recording-details page) genuinely NEEDS an intermediate real zoom
  // change as ONE step of zoomToShowLayer's own multi-step process (pan,
  // then zoomToBounds, then spiderfy -- see the vendored
  // leaflet.markercluster source's `zoomToShowLayer`). That legitimate
  // mid-reveal zoom change tripped the OLD "zoom changed -> do a real
  // refresh" branch, rebuilding the whole recordings layer
  // (clearLayers()+re-add) while zoomToShowLayer's own recursive moveend
  // handler was still tracking the now-destroyed marker/cluster objects --
  // silently stalling the reveal for good (confirmed via two
  // recordings.geojson fetches firing during a single reveal, the second
  // one ~700ms in). A boolean scoped to "is a reveal in flight at all",
  // not "did zoom move", is immune to that: every moveend/zoomend the
  // reveal itself causes -- pan, intermediate zoom, spiderfy -- is skipped
  // the same way, and only a genuinely independent user pan/zoom (which
  // can't happen mid-reveal on a single-threaded UI anyway, but a fast
  // drag right after one completes could) reaches refreshViewport's real
  // refresh.
  let revealInFlight = false;
  let revealInFlightTimeout = null;

  function endReveal() {
    revealInFlight = false;
    if (revealInFlightTimeout !== null) {
      clearTimeout(revealInFlightTimeout);
      revealInFlightTimeout = null;
    }
  }

  function startReveal() {
    revealInFlight = true;
    if (revealInFlightTimeout !== null) clearTimeout(revealInFlightTimeout);
    revealInFlightTimeout = setTimeout(endReveal, 5000);
  }

  // Whether the MOST RECENT fetch of each layer reported `truncated: true`
  // (services/map_query.py's MAX_FEATURES cap) -- tracked separately since
  // recordings/sites fetch independently (see refreshLayers below), and
  // shown as one shared warning the moment either one truncates.
  let recordingsTruncated = false;
  let sitesTruncated = false;

  function updateTruncationWarning() {
    const warning = document.getElementById("truncation-warning");
    warning.hidden = !(recordingsTruncated || sitesTruncated);
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
    recordingsTruncated = Boolean(recordingsData.truncated);
    updateTruncationWarning();
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
    sitesTruncated = Boolean(sitesData.truncated);
    updateTruncationWarning();
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
    void refreshLayers(withViewportBbox(params));
    pushUrl();
  }

  document.getElementById("filters").addEventListener("input", refresh);

  // Pan/zoom: refetch scoped to the new viewport, debounced so a drag-pan
  // (many intermediate `move` frames -- `moveend` itself only fires once
  // the gesture settles, but a fast series of discrete zoom/pan actions can
  // still fire several moveend/zoomend events close together) coalesces
  // into one fetch. Deliberately does NOT call pushUrl() (panning isn't a
  // "filter change" worth a back-button stop) or fitToVisible() (that
  // would fight the user's own just-made pan/zoom, and could also loop:
  // fitBounds() itself fires moveend).
  const refreshViewport = debounce(() => {
    // A reveal in flight (see recording-selected below and revealInFlight's
    // own comment) owns every moveend/zoomend it causes -- pan,
    // intermediate zoomToBounds, spiderfy alike. Rebuilding the recordings
    // layer from a fresh fetch mid-reveal would destroy the exact
    // marker/cluster objects Leaflet.markercluster's own zoomToShowLayer is
    // still tracking, stalling it. Skip entirely while one is in flight;
    // its own completion (or the safety-net timeout) lets a later, genuine
    // viewport change refresh normally.
    if (revealInFlight) return;
    void refreshLayers(withViewportBbox(query()));
  }, 300);
  map.on("moveend zoomend", refreshViewport);

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
  //
  // Bug (Obsidian, 2026-09): a revealed marker would blink back into its
  // cluster a moment after appearing, and separately (root-caused
  // 2026-09-13, see revealInFlight's own comment) could get stuck
  // permanently unrevealed when the reveal needed a real intermediate zoom
  // change. Both are the same underlying cause: panTo and zoomToShowLayer
  // below fire moveend/zoomend, which the debounced viewport refresh above
  // (300ms) would otherwise pick up and rebuild the ENTIRE recordings layer
  // from a fresh fetch via clearLayers()+re-add mid-reveal. revealInFlight
  // marks the whole reveal (not just a single zoom-unchanged step) so
  // refreshViewport skips every moveend/zoomend this reveal itself causes.
  document.body.addEventListener("recording-selected", (event) => {
    const { latitude, longitude, hash } = event.detail;
    const marker = recordingLayersByHash.get(hash);

    // Only when this reveal will actually move/re-cluster the map -- when
    // neither branch below runs (no coordinates AND no loaded marker),
    // nothing moves, so there's no moveend/zoomend for refreshViewport to
    // even consult this against.
    if ((latitude != null && longitude != null) || marker) {
      startReveal();
    }

    if (latitude != null && longitude != null) {
      map.panTo([latitude, longitude]);
    }

    // No marker (e.g. a stale hash after a filter change rebuilt the
    // layer) -- just highlight, same graceful no-op highlightRecording
    // already falls back to. panTo alone has no completion callback, so
    // wait for its own single moveend instead -- still needs to be skipped
    // by refreshViewport the same as a zoomToShowLayer reveal would be.
    if (marker) {
      recordingsLayer.zoomToShowLayer(marker, () => {
        endReveal();
        highlightRecording(hash);
      });
    } else {
      if (latitude != null && longitude != null) {
        map.once("moveend", endReveal);
      } else {
        endReveal(); // nothing moved -- startReveal() was never called
                      // above either, but keep this call for symmetry.
      }
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

// src/fledermap/web/static/recording_detail.js
//
// Client-side interactions for the recording details page (design spec
// 2026-09-01-fledermap-recording-details-page-design.md, section 4, and
// 2026-09-04-fledermap-recording-detail-tools-design.md for the toolbar):
// a tool-switching toolbar with a Default tool (click-to-play, drag-to-pan)
// and a Ruler tool (drag to measure Δt/Δf, click to clear), a ported
// crosshair readout, a playback-position cursor that snaps into view when
// it scrolls off-screen, and the dense fixed-interval axis gridlines the
// drawer's fixed 3-label axis doesn't have room for.
//
// A dedicated per-page script (mirroring `session_map.js`'s own pattern),
// not folded into `app.js`: this page's DOM ids don't exist on the map
// page, and `app.js`'s own crosshair only ever binds to `#drawer-body`.

document.addEventListener("DOMContentLoaded", () => {
  const spectrogramTiles = Array.from(document.querySelectorAll(".detail-spectrogram-tile"));
  if (spectrogramTiles.length === 0) return; // missing duration/samplerate metadata

  const spectrogramLoading = document.getElementById("spectrogram-loading");
  const oscillogramTiles = Array.from(document.querySelectorAll(".detail-oscillogram-tile"));
  const oscillogramLoading = document.getElementById("oscillogram-loading");
  const oscillogramWrap = document.getElementById("detail-oscillogram-wrap");
  const wrap = document.getElementById("detail-spectrogram-wrap");
  const cursor = document.getElementById("playback-cursor");
  const readout = document.getElementById("crosshair-readout");
  const audioControlsEl = document.getElementById("detail-audio-controls");
  // `#detail-audio-controls` (and the real `<audio>` inside it) are only in the DOM when the
  // preview has actually been rendered (`preview_ready`, recording_detail.py -- Janna,
  // 2026-09-04: the page used to show a fully working-looking toolbar that 404'd on click
  // during the window between a recording's metadata landing and its preview job finishing).
  // A detached, never-appended `<audio>` element is a fully functional stand-in here -- `.play()`/
  // `.pause()`/`.currentTime`/event listeners all work normally on it, just with no `src` and
  // nothing ever visibly listening -- so the click-to-play/cursor code below (which the
  // spectrogram/oscillogram viewer still needs regardless of preview readiness) never has to
  // null-check `audio` itself, only skip wiring real playback controls to it.
  const audio = document.getElementById("detail-audio") || document.createElement("audio");
  const denoiseToggle = document.getElementById("denoise-toggle");
  const previewUrl = audioControlsEl ? audioControlsEl.dataset.previewUrl : null;
  const detailPreviewUrl = audioControlsEl ? audioControlsEl.dataset.detailPreviewUrl : null;
  let denoiseOn = false;
  // `getSeekFloorS`/`getSeekCeilingS` are read lazily (only when a button is
  // actually clicked), so it's safe to reference `viewLocked`/`lockedStartS`
  // (a `let`) and `currentViewEndTimeS` (a hoisted function declaration)
  // here even though neither is declared until further down this same
  // function -- by the time a click can happen, DOMContentLoaded has
  // finished running and both are ready. `getPreviewUrl`/`getHetExtraQuery`
  // are different: `initAudioControls` calls `switchToTe()`/`switchToHet()`
  // synchronously as part of THIS call, which invokes them immediately -- so
  // `denoiseOn`/`previewUrl`/`detailPreviewUrl` must already be declared
  // above this point, not further down like `viewLocked` gets away with.
  const audioControls = audioControlsEl
    ? initAudioControls(audioControlsEl, audio, {
        getSeekFloorS: () => (isViewLockActive() && lockedStartS !== null ? lockedStartS : 0),
        getSeekCeilingS: () => (isViewLockActive() ? currentViewEndTimeS() : null),
        getPreviewUrl: () =>
          denoiseOn ? withQueryParam(detailPreviewUrl, "denoise", "true") : previewUrl,
        getHetExtraQuery: () => (denoiseOn ? "&denoise=true" : ""),
      })
    : { getTimeExpansionFactor: () => 1, refreshSource: () => {}, isRestoringPosition: () => false };
  const scrollEl = document.getElementById("detail-scroll");
  const timeAxis = document.getElementById("detail-axis-time");
  const freqAxis = document.getElementById("detail-axis-freq");
  const mainContent = document.querySelector("main.main-content");
  // Always present whenever oscillogramWrap/wrap are (both live inside the
  // same `{% if params %}` block in recording_details.html) -- the audio
  // view's own last piece of chrome, used by fitDetailHeight below to scope
  // what "fits the viewport" means.
  const audioRow = document.querySelector(".audio-row");
  const rulerReadout = document.getElementById("ruler-readout");
  // Declared here (rather than beside the rest of the ruler code below) so `relayout()` --
  // called once at page load, before that code runs -- can safely reference `rulerBox` without
  // hitting its `let` temporal dead zone.
  let rulerBox = null;
  let rulerBoxLocal = null;
  let currentSelection = null; // {startTimeS, endTimeS} | null -- durable across a view toggle,
  // unlike rulerBox/rulerBoxLocal above, which setActiveTool clears on every tool switch.
  let selectionBox = null;
  // Same hoisting reason as rulerBox above: fitDetailHeight (called once at page load via
  // relayout(), before setActiveView's own declarations run) needs to read this without hitting
  // its `let` temporal dead zone.
  let activeView = "playback";

  // Reveal-on-load (design spec section 3): every tile in a row must load before that row's
  // placeholder clears -- a partially-loaded row (some tiles rendered, others still pending)
  // would otherwise flash broken-looking gaps.
  function revealWhenAllLoaded(tiles, loadingEl) {
    let remaining = tiles.length;
    let hadError = false;

    function settle() {
      remaining -= 1;
      if (remaining === 0) {
        if (hadError) {
          loadingEl.textContent = "Some tiles failed to render.";
          loadingEl.hidden = false;
        } else {
          loadingEl.hidden = true;
        }
        tiles.forEach((t) => {
          if (!t.dataset.failed) t.hidden = false;
        });
      }
    }

    tiles.forEach((img) => {
      // This script tag sits at the end of <body>, after every tile <img> --
      // on a fast (e.g. local) server, some of the earliest tiles can finish
      // loading before this code ever runs. A `load`/`error` listener
      // attached after the fact never fires for those, so `remaining` would
      // never reach 0 and the placeholder would never clear. `img.complete`
      // is true once a load has been attempted, success or failure alike;
      // `naturalWidth === 0` distinguishes a failed attempt from a real one.
      if (img.complete) {
        if (img.naturalWidth === 0) {
          hadError = true;
          img.dataset.failed = "true";
        }
        settle();
        return;
      }
      img.addEventListener("load", settle);
      img.addEventListener("error", () => {
        hadError = true;
        img.dataset.failed = "true";
        settle();
      });
    });
  }

  // Unlike `revealWhenAllLoaded` (initial page load: tiles start hidden behind a
  // "Rendering…" placeholder, revealed together once every tile has a real image),
  // a denoise-toggle reload swaps already-visible tiles -- hiding them first would
  // blank the whole view to that placeholder for the length of the re-render, losing
  // the reader's place (Janna, 2026-09-14: keep the previous image, don't go grey).
  // Assigning a new `src` does NOT itself clear what's on screen -- browsers keep
  // painting the last successfully decoded frame until the new one is ready, so
  // simply never touching `hidden` here is enough to get the "swap in place" effect.
  // Backlog "start with the currently visible tile(s) first": `tiles` is in DOM (left-to-right)
  // order, laid out edge-to-edge, so each tile's native-px start offset is the running sum of
  // the widths before it -- `t.width` reflects each `<img>`'s `width` attribute regardless of
  // its `hidden` state, unlike `getBoundingClientRect()`, which collapses to zero for a
  // `display: none` element. `orderTilesVisibleFirst` (tile_visibility.js) does the actual
  // overlap math so it stays unit-testable without a DOM.
  function visibleFirstOrder(tiles) {
    const rangeStartPx = scrollEl.scrollLeft / currentScale;
    const rangeEndPx = (scrollEl.scrollLeft + scrollEl.clientWidth) / currentScale;
    const widthsPx = tiles.map((t) => t.width);
    return orderTilesVisibleFirst(widthsPx, rangeStartPx, rangeEndPx).map((i) => tiles[i]);
  }

  function reloadTilesInPlace(tiles, loadingEl) {
    let remaining = tiles.length;
    let hadError = false;

    function settle() {
      remaining -= 1;
      if (remaining === 0) {
        if (hadError) {
          loadingEl.textContent = "Some tiles failed to render.";
          loadingEl.hidden = false;
        } else {
          loadingEl.hidden = true;
        }
      }
    }

    visibleFirstOrder(tiles).forEach((t) => {
      delete t.dataset.failed;
      t.addEventListener("load", settle, { once: true });
      t.addEventListener(
        "error",
        () => {
          hadError = true;
          t.dataset.failed = "true";
          settle();
        },
        { once: true },
      );
      // An empty string (not the literal "false") -- `parse_bool` on the server is
      // presence-based, so the literal string "false" is still non-empty and reads as
      // True. `URLSearchParams` renders an empty value as `denoise=` (present but
      // empty), which `parse_bool` correctly reads as False.
      t.src = withQueryParam(t.src, "denoise", denoiseOn ? "true" : "");
    });
  }

  function reloadTilesWithDenoise() {
    reloadTilesInPlace(spectrogramTiles, spectrogramLoading);
    reloadTilesInPlace(oscillogramTiles, oscillogramLoading);
  }

  if (denoiseToggle) {
    denoiseToggle.addEventListener("click", () => {
      denoiseOn = !denoiseOn;
      denoiseToggle.setAttribute("aria-pressed", denoiseOn ? "true" : "false");
      reloadTilesWithDenoise();
      audioControls.refreshSource();
      // No-ops when Analysis view isn't active (fetchAndRenderSpectrum's own early return) --
      // safe to call unconditionally so a stale, pre-toggle chart is never left on screen.
      fetchAndRenderSpectrum();
    });
  }

  const firstTile = spectrogramTiles[0];
  const durationS = parseFloat(firstTile.dataset.durationS);
  const maxFreqKhz = parseFloat(firstTile.dataset.maxFreqKhz);
  // Native (unzoomed) raster size the server reserved on the wrap -- the same numbers the tiles
  // were rendered at. All time/frequency <-> pixel conversions on this page go through `coords`
  // (plot_coords.js), derived from these real dimensions; see that file for why the scale is not
  // a separate nominal constant.
  const nativeWidthPx = parseFloat(wrap.dataset.nativeWidthPx);
  const nativeHeightPx = parseFloat(wrap.dataset.nativeHeightPx);
  const coordsValid = [nativeWidthPx, nativeHeightPx, durationS, maxFreqKhz].every(
    (v) => Number.isFinite(v) && v > 0,
  );
  const coords = makePlotCoords({
    widthPx: nativeWidthPx,
    heightPx: nativeHeightPx,
    durationS,
    maxFreqKhz,
  });

  // Annotations (design spec docs/superpowers/specs/2026-09-23-fledermap-annotations-design.md).
  // Deliberately reads #detail-toolbar directly here rather than via the `toolbar` const --
  // that const isn't declared until the tool-switching section further down, and this block
  // runs earlier (right after `coords`); introducing a second `const toolbar` there would be a
  // duplicate-declaration SyntaxError in the same scope.
  let annotations = JSON.parse(
    document.getElementById("detail-toolbar").dataset.annotations || "[]",
  );
  const classifierThresholds = JSON.parse(
    document.getElementById("detail-toolbar").dataset.classifierThresholds || "{}",
  );
  const taxonSearchIndex = JSON.parse(
    document.getElementById("classifier-box")?.dataset.taxonSearchIndex || "[]",
  );
  const taxonById = new Map(taxonSearchIndex.map((t) => [t.id, t]));
  const recordingAnnotationsUrl = document.getElementById("detail-toolbar").dataset.annotationsUrl;

  // Tracks the currently hover-highlighted annotation by id, not by element reference: elements
  // are torn down and rebuilt wholesale on every renderAnnotations() call (layer toggle, tool
  // switch, CRUD save), which would leave a stale reference pointing at a detached node.
  let hoveredAnnotationId = null;

  function annotationAtPoint(xPx, yPx) {
    // Later (later-rendered, i.e. later in the array) annotations paint on top, so hit-test from
    // the end -- an overlapping pair should highlight whichever one is actually visible on top.
    // sortAnnotationsForPaint puts comments last regardless of DB insertion order, since only
    // comments are user-editable and must win an overlap.
    const ordered = sortAnnotationsForPaint(annotations);
    for (let i = ordered.length - 1; i >= 0; i--) {
      const annotation = ordered[i];
      const forceVisible = annotation.source === "comment" && activeTool === "comment";
      if (!forceVisible && !isLayerVisible(annotation.source)) continue;
      if (annotationShape(annotation) === "point") {
        const { x, y } = annotationPointPx(annotation, coords);
        const dx = xPx - x;
        const dy = yPx - y;
        if (dx * dx + dy * dy <= 36) return annotation;
      } else {
        const box = annotationBoxPx(annotation, coords);
        if (
          xPx >= box.left &&
          xPx <= box.left + box.width &&
          yPx >= box.top &&
          yPx <= box.top + box.height
        ) {
          return annotation;
        }
      }
    }
    return null;
  }

  // Purely a visual highlight (app.css's .annotation-hover), never gated by pointer-events/tool
  // the way click-to-edit is -- hovering must work under every tool, not just Comment, since it
  // never intercepts anything the active tool needs.
  function updateAnnotationHover(annotation) {
    const id = annotation ? annotation.id : null;
    if (id === hoveredAnnotationId) return;
    if (hoveredAnnotationId !== null) {
      annotationLayer
        .querySelector(`[data-annotation-id="${hoveredAnnotationId}"]`)
        ?.classList.remove("annotation-hover");
    }
    if (id !== null) {
      annotationLayer.querySelector(`[data-annotation-id="${id}"]`)?.classList.add("annotation-hover");
    }
    hoveredAnnotationId = id;
  }

  const LAYER_STORAGE_PREFIX = "fledermap-annotation-layer-";

  function isLayerVisible(source) {
    return localStorage.getItem(LAYER_STORAGE_PREFIX + source) === "true";
  }

  function setLayerVisible(source, visible) {
    localStorage.setItem(LAYER_STORAGE_PREFIX + source, visible ? "true" : "false");
    renderAnnotations();
  }

  const layersButton = document.getElementById("layers-button");
  const layersMenu = document.getElementById("layers-menu");

  function rebuildLayersMenu() {
    const sources = layerMenuSources([...new Set(annotations.map((a) => a.source))]);
    layersMenu.innerHTML = "";
    layersButton.disabled = sources.length === 0;
    for (const source of sources) {
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = isLayerVisible(source);
      checkbox.addEventListener("change", () => setLayerVisible(source, checkbox.checked));
      label.appendChild(checkbox);
      label.append(` ${source[0].toUpperCase()}${source.slice(1)}`);
      layersMenu.appendChild(label);
    }
  }

  layersButton.addEventListener("click", (event) => {
    event.stopPropagation();
    layersMenu.hidden = !layersMenu.hidden;
  });
  document.addEventListener("click", (event) => {
    if (!layersMenu.hidden && !layersMenu.contains(event.target) && event.target !== layersButton) {
      layersMenu.hidden = true;
    }
  });

  const annotationLayer = document.createElement("div");
  annotationLayer.className = "annotation-layer";
  wrap.appendChild(annotationLayer);

  function renderAnnotations() {
    annotationLayer.innerHTML = "";
    // Elements are rebuilt from scratch below, so any element the current hover class was on is
    // gone -- reset the tracked id rather than leaving it pointing at a detached node; the next
    // mousemove naturally re-applies the class if the cursor is still over the same annotation.
    hoveredAnnotationId = null;
    for (const annotation of sortAnnotationsForPaint(annotations)) {
      const forceVisible = annotation.source === "comment" && activeTool === "comment";
      if (!forceVisible && !isLayerVisible(annotation.source)) continue;

      const shape = annotationShape(annotation);
      const el = document.createElement("div");
      el.dataset.annotationId = String(annotation.id);
      // Scopes app.css's `.comment-interactive` pointer-events re-enable to comment-source
      // elements only -- a non-comment annotation source (e.g. batdetect2's boxes) must stay
      // click-through under every tool, including Comment, since only comment annotations are
      // ever editable via this popup.
      el.dataset.source = annotation.source;
      // Set once as a custom property, rather than directly on `background`/`border-color`
      // separately per shape, so app.css can derive the box's border, fill, AND hover-fill
      // (a stronger tint of the same color, on request) from one source instead of duplicating
      // the species color into three places that could drift out of sync.
      if (annotation.taxon_id !== null) {
        el.style.setProperty("--annotation-color", colorForTaxon(annotation.taxon_id));
      }
      if (shape === "point") {
        const { x, y } = annotationPointPx(annotation, coords);
        el.className = "annotation-point";
        el.style.left = `${x}px`;
        el.style.top = `${y}px`;
      } else {
        const box = annotationBoxPx(annotation, coords);
        el.className = "annotation-box";
        el.style.left = `${box.left}px`;
        el.style.top = `${box.top}px`;
        el.style.width = `${box.width}px`;
        el.style.height = `${box.height}px`;
      }
      // Must be added after el.className is set above -- className replaces the whole class
      // list, so adding this before either shape branch would get silently wiped out.
      if (isBelowThreshold(annotation, classifierThresholds)) {
        el.classList.add("below-threshold");
      }

      const taxon = annotation.taxon_id !== null ? taxonById.get(annotation.taxon_id) : null;
      const lines = annotationLabelLines(annotation, taxon);
      if (lines.length > 0) {
        const label = document.createElement("span");
        label.className = "annotation-label";
        // textContent, never innerHTML: `literal` is user-authored free text, and `raw_label`
        // (the unmapped-species fallback) is the model's own string, neither sanitized as HTML.
        label.textContent = lines.join("\n");
        label.style.whiteSpace = "pre-line";
        el.appendChild(label);
      }

      annotationLayer.appendChild(el);

      if (annotation.source === "comment") {
        el.addEventListener("mousedown", (event) => {
          if (activeTool !== "comment") return;
          event.stopPropagation();
        });
        el.addEventListener("click", (event) => {
          if (activeTool !== "comment") return;
          event.stopPropagation();
          openCommentPopupForEdit(annotation);
        });
      }
    }
  }

  rebuildLayersMenu();
  // No initial renderAnnotations() call here: it reads `activeTool`, which is a `let` declared
  // later, in the tool-switching section below, and not yet initialized at this point in the
  // script's top-to-bottom execution -- calling it now would throw "Cannot access 'activeTool'
  // before initialization". `setActiveTool("default")`, called once in that section after
  // `activeTool` exists, itself calls renderAnnotations() as part of every tool switch, which
  // covers the first render for free -- no separate bootstrap call needed.

  // Comment tool: popup state and CRUD wiring (design spec section on the Comment tool, section
  // 4.5's required post-implementation UX check). A centered MODAL with a backdrop, not an
  // anchored floating-readout like crosshair/ruler: the anchored popup this originally shipped
  // with (a) never actually anchored anywhere -- `.comment-popup` had no `position: fixed`, so
  // its `style.left`/`style.top` (set from click coordinates) had no effect and it rendered
  // wherever normal document flow put it -- and (b), once that was fixed, still clipped
  // off-screen at a viewport edge with no flip/clamp logic (found during live verification
  // against the real page). The backdrop additionally fixes a real correctness bug the anchored
  // version had: with
  // no backdrop, a click meant for a NEW point on the spectrogram while a draft popup was still
  // open reached `wrap` underneath, silently opening a second draft that inherited the first
  // draft's never-cleared text/species -- the note you were typing for one point could end up
  // attached to a different one with no warning. The backdrop makes that impossible: while the
  // modal is open, every click over the spectrogram hits the backdrop, not `wrap`.
  const commentPopup = document.getElementById("comment-popup");
  const commentPopupBackdrop = document.getElementById("comment-popup-backdrop");
  const commentSpeciesInput = document.getElementById("comment-popup-species-input");
  const commentSpeciesSuggestions = document.getElementById("comment-popup-species-suggestions");
  const commentText = document.getElementById("comment-popup-text");
  const commentDeleteBtn = document.getElementById("comment-popup-delete");
  const commentCancelBtn = document.getElementById("comment-popup-cancel");
  const commentSaveBtn = document.getElementById("comment-popup-save");
  const commentErrorEl = document.getElementById("comment-popup-error");

  let pendingDraft = null; // { time_start_s, time_end_s, freq_min_hz, freq_max_hz } while creating
  let editingAnnotationId = null; // set while editing an existing annotation, else null
  let selectedTaxonId = null;

  // A non-ok response's body is the API's JSON `{"error": "..."}` (recording_annotations.py),
  // but a 404/500 that never reaches the view (a proxy, a stack trace page) may not be JSON at
  // all -- fall back to a generic message rather than letting a second failed `.json()` call
  // replace the real error.
  function describeFetchError(response) {
    return response
      .json()
      .then((body) => (body && body.error) || `Request failed (${response.status})`)
      .catch(() => `Request failed (${response.status})`);
  }

  function updateSaveEnabled() {
    commentSaveBtn.disabled = !selectedTaxonId && commentText.value.trim() === "";
  }
  commentText.addEventListener("input", updateSaveEnabled);

  function closeCommentPopup() {
    commentPopup.hidden = true;
    commentPopupBackdrop.hidden = true;
    document.removeEventListener("keydown", onCommentPopupKeydown);
    pendingDraft = null;
    editingAnnotationId = null;
    selectedTaxonId = null;
    commentSpeciesInput.value = "";
    commentText.value = "";
    commentSpeciesSuggestions.hidden = true;
    commentErrorEl.hidden = true;
    // Reset in case a request was still in flight (e.g. Escape pressed before the response
    // arrived) -- the next open re-derives the real disabled state via updateSaveEnabled().
    commentSaveBtn.disabled = false;
    commentDeleteBtn.disabled = false;
  }

  // Escape is a deliberate keypress, unlike a stray click on the backdrop -- the backdrop
  // itself has no click-to-dismiss handler (see the section comment above for why), but Escape
  // is still offered as the standard modal-dismiss affordance.
  function onCommentPopupKeydown(event) {
    if (event.key === "Escape") closeCommentPopup();
  }

  function openCommentModal() {
    commentPopup.hidden = false;
    commentPopupBackdrop.hidden = false;
    document.addEventListener("keydown", onCommentPopupKeydown);
  }

  commentSpeciesInput.addEventListener("input", () => {
    selectedTaxonId = null;
    updateSaveEnabled();
    const query = commentSpeciesInput.value.trim();
    commentSpeciesSuggestions.innerHTML = "";
    if (query === "") {
      commentSpeciesSuggestions.hidden = true;
      return;
    }
    const matches = taxonSearchIndex.filter((t) => matchesQuery(t, query)).slice(0, 8);
    commentSpeciesSuggestions.hidden = matches.length === 0;
    // `position: fixed` (app.css) needs an explicit screen position -- computed fresh on every
    // keystroke rather than once on focus, since nothing else repositions it and the input's own
    // rect can't move while the modal is open anyway, so this is cheap and always correct.
    if (matches.length > 0) {
      const inputRect = commentSpeciesInput.getBoundingClientRect();
      commentSpeciesSuggestions.style.left = `${inputRect.left}px`;
      commentSpeciesSuggestions.style.top = `${inputRect.bottom}px`;
      // `min-width`, not `width`: the species input is narrower than most scientific names, and
      // a fixed `width` equal to it forced every longer name to wrap mid-word. `min-width` keeps
      // the dropdown at least as wide as the input for a short/empty query, while
      // `white-space: nowrap` (app.css) plus the element's own natural shrink-to-fit sizing lets
      // it grow to whatever the longest visible match actually needs.
      commentSpeciesSuggestions.style.minWidth = `${inputRect.width}px`;
    }
    for (const taxon of matches) {
      const item = document.createElement("div");
      item.textContent = taxon.scientific_name;
      item.addEventListener("click", () => {
        selectedTaxonId = taxon.id;
        commentSpeciesInput.value = taxon.scientific_name;
        commentSpeciesSuggestions.hidden = true;
        updateSaveEnabled();
      });
      commentSpeciesSuggestions.appendChild(item);
    }
  });
  // Mouse-driven dismiss for the suggestions dropdown alone (Escape closes the whole modal, see
  // onCommentPopupKeydown above -- deliberately not duplicated here as a second, narrower Escape
  // handler). Same outside-click pattern as the Layers menu's own listener earlier in this file.
  document.addEventListener("click", (event) => {
    if (
      !commentSpeciesSuggestions.hidden &&
      !commentSpeciesSuggestions.contains(event.target) &&
      event.target !== commentSpeciesInput
    ) {
      commentSpeciesSuggestions.hidden = true;
    }
  });

  commentCancelBtn.addEventListener("click", closeCommentPopup);

  commentDeleteBtn.addEventListener("click", () => {
    if (editingAnnotationId === null) return;
    // Captured now, not read back out of `editingAnnotationId` inside `.then` below -- closing
    // the popup (e.g. Escape) before the response arrives resets that variable to null, which
    // would otherwise make the deletion silently no-op against the in-memory `annotations` array
    // even though the row is already gone server-side.
    const id = editingAnnotationId;
    commentDeleteBtn.disabled = true;
    commentSaveBtn.disabled = true;
    fetch(`${recordingAnnotationsUrl}/${id}`, { method: "DELETE" })
      .then((response) => {
        if (!response.ok) {
          return describeFetchError(response).then((message) => {
            throw new Error(message);
          });
        }
        annotations = annotations.filter((a) => a.id !== id);
        rebuildLayersMenu();
        renderAnnotations();
        closeCommentPopup();
      })
      .catch((error) => {
        commentErrorEl.textContent = `Could not delete: ${error.message}`;
        commentErrorEl.hidden = false;
        commentDeleteBtn.disabled = false;
        updateSaveEnabled();
      });
  });

  commentSaveBtn.addEventListener("click", () => {
    const literal = commentText.value.trim() || null;
    const taxonId = selectedTaxonId;
    // Same reasoning as the Delete handler above: capture before the request, not read back out
    // of the `let` afterwards.
    const id = editingAnnotationId;
    // Disabling Save up front also stops a fast double-click from firing two identical requests
    // (two POSTs would create two duplicate annotations; two PATCHes are merely redundant).
    commentSaveBtn.disabled = true;
    commentDeleteBtn.disabled = true;
    const request =
      id !== null
        ? fetch(`${recordingAnnotationsUrl}/${id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ taxon_id: taxonId, literal }),
          })
        : fetch(recordingAnnotationsUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...pendingDraft, taxon_id: taxonId, literal }),
          });
    request
      .then((response) => {
        if (!response.ok) {
          return describeFetchError(response).then((message) => {
            throw new Error(message);
          });
        }
        return response.json();
      })
      .then((saved) => {
        if (id !== null) {
          annotations = annotations.map((a) => (a.id === saved.id ? saved : a));
        } else {
          annotations = [...annotations, saved];
          rebuildLayersMenu();
        }
        renderAnnotations();
        closeCommentPopup();
      })
      .catch((error) => {
        commentErrorEl.textContent = `Could not save: ${error.message}`;
        commentErrorEl.hidden = false;
        updateSaveEnabled();
        commentDeleteBtn.disabled = false;
      });
  });

  function openCommentPopupForCreate(draft) {
    editingAnnotationId = null;
    pendingDraft = draft;
    // Defense in depth alongside the backdrop (see the section comment above): even though the
    // backdrop now stops a spectrogram click from reaching this while a draft is open, explicitly
    // clearing any leftover species/text here means a stale draft can never surface even if this
    // is reached some other way in the future.
    selectedTaxonId = null;
    commentSpeciesInput.value = "";
    commentText.value = "";
    commentSpeciesSuggestions.hidden = true;
    commentDeleteBtn.hidden = true;
    updateSaveEnabled();
    openCommentModal();
  }

  function openCommentPopupForEdit(annotation) {
    editingAnnotationId = annotation.id;
    pendingDraft = null;
    const taxon = annotation.taxon_id !== null ? taxonById.get(annotation.taxon_id) : null;
    selectedTaxonId = annotation.taxon_id;
    commentSpeciesInput.value = taxon ? taxon.scientific_name : "";
    commentText.value = annotation.literal || "";
    commentDeleteBtn.hidden = false;
    updateSaveEnabled();
    openCommentModal();
  }

  // Shrink-to-fit (backlog "Eliminate vertical scrollbar"): `main.main-content` scrolls
  // vertically (app.css) whenever the locked-scale render is taller than the viewport minus
  // the page's other chrome (nav, header, toolbar, audio row) -- a real recording easily
  // exceeds that on a small screen or window. Rather than let the page scroll vertically (ugly
  // alongside `.detail-scroll`'s own horizontal scrollbar, and defeats seeing the whole call at
  // once), uniformly scale just the two IMAGE wraps (`detail-oscillogram-wrap`,
  // `detail-spectrogram-wrap`) down just enough to remove that vertical overflow. Deliberately
  // scoped to the images, not the whole page: an earlier version zoomed `.detail-body` as a
  // whole, which also shrank the axis tick labels and the ruler's Δt/Δf readout down to
  // unreadable sizes (Janna, 2026-09-04, live screenshot) -- text should stay at its normal
  // size regardless of how small the image gets, same as a real DAW's ruler never shrinks its
  // own numerals just because you zoomed out on the waveform. `.detail-axis-time` and
  // `.detail-axis-freq` are therefore never zoomed; `buildTimeAxis`/`buildFreqAxis` below
  // position their ticks in real screen pixels directly (scaled by the wrap's measured on-screen
  // size) and widen the tick interval as the image shrinks, so labels remain both full-size AND non-overlapping.
  // Deliberately a uniform x/y scale (not a height-only squash): the render itself keeps its
  // exact locked-scale pixel fidelity (design spec "the exact 1:1 point"), only the on-screen
  // DISPLAY size changes, same as zooming out on an image viewer -- an independent x/y squash
  // would distort that. Horizontal overflow is left alone; `.detail-scroll`'s existing
  // horizontal scrollbar is the intended way to navigate a long recording, this only ever fixes
  // the VERTICAL scrollbar.
  //
  // CSS `zoom`, not `transform: scale()` -- `transform` has two real bugs here:
  // it's paint-only, so a transformed ancestor's scrollable-container descendants
  // desync their native scrollWidth/scrollHeight from the visually-shrunk content (allowing
  // overscroll into blank space), and a transformed ancestor is documented to break
  // `position: sticky` on a scrolling descendant in Chromium/Firefox (w3c/csswg-drafts#3186),
  // which is exactly what `.detail-axis-freq` is. `zoom` actually rescales layout (not just
  // paint), so `.detail-scroll`'s own box -- and its scrollable range -- shrinks along with the
  // zoomed wraps with no manual compensation needed, and it isn't a `transform`, so sticky
  // positioning elsewhere on the page keeps working normally.
  let currentScale = 1;

  // A comfortable maximum display height for the spectrogram, independent of
  // both the viewport-fit shrink below and the recording's own locked-scale
  // render resolution (DETAIL_PX_PER_KHZ in services/recording_detail.py).
  // That resolution stays deliberately fixed -- a precise 1:1 STFT-column-
  // to-pixel mapping the Ruler tool's math and a consistent pan scale
  // across recordings both depend on -- it was never meant as "always
  // display at 100% of this." Read from the wrap's own `data-*` attribute
  // (set from services/recording_detail.py's DETAIL_COMFORT_MAX_HEIGHT_PX)
  // rather than hardcoded here a second time -- the template applies that
  // SAME constant as this wrap's initial inline `zoom`, so the very first
  // paint already matches what this function recomputes; two independently
  // hardcoded copies previously disagreed and visibly jumped the instant
  // this script ran (found live 2026-09-13).
  const MAX_SPECTROGRAM_HEIGHT_PX = parseFloat(wrap.dataset.comfortMaxHeightPx);

  // Below this, the spectrogram is unreadable regardless of what's causing
  // the shrink -- hide it and say so (unsupported screen size) rather than
  // rendering something useless. 76px = 2cm at 96dpi (the CSS reference
  // pixel). Client-only, unlike MAX_SPECTROGRAM_HEIGHT_PX above --
  // this decision depends on the real browser viewport, which the server
  // can't know at request time, so there's no server-side counterpart to
  // keep in sync with.
  const MIN_SUPPORTED_SPECTROGRAM_HEIGHT_PX = 76;
  const tooSmallMessage = document.getElementById("detail-too-small-message");

  function fitDetailHeight() {
    // Analysis view forces `.detail-scroll` to `display: none` via CSS (app.css's
    // `.view-analysis .detail-scroll` rule), so every measurement below would read zero height
    // regardless of the window's real size -- not "genuinely too small", just invisible. Left
    // unguarded, that zero would make `tooSmall` true on every resize while Analysis is active,
    // permanently setting `scrollEl.hidden = true` with nothing to undo it on switching back to
    // Playback (that reset was deliberately removed from setActiveView -- see its own comment).
    // Skip entirely while hidden by view; setActiveView calls this again on entering Playback so
    // sizing is always recomputed at least once for whatever the window did while it was away.
    if (activeView !== "playback") return;
    // Same idea as clearing zoom below, for the OTHER piece of state this function can leave
    // behind: if a previous run hid `scrollEl` (too-small case), it and everything inside it
    // -- including the two wraps measured just below -- report a zero `getBoundingClientRect()`
    // height while hidden. Left hidden across this call, that zero height would make every
    // constraint below look satisfied (nothing to shrink) while `tooSmall` stays true forever,
    // since `spectrogramHeight * currentScale` is `0 * anything`, itself always `< MIN_SUPPORTED_
    // SPECTROGRAM_HEIGHT_PX` -- shrinking the window and growing it back stays stuck showing the
    // "too small" message (found live 2026-09-13). Un-hide before measuring so the true natural
    // size is always what gets measured; the too-small check at the end re-decides fresh either
    // way.
    scrollEl.hidden = false;
    // Measure natural (unzoomed) heights first -- clearing any previous zoom before measuring,
    // since already-shrunk wraps would otherwise make `mainContent` look like it has no
    // overflow even though the true unscaled content still would.
    oscillogramWrap.style.zoom = "";
    wrap.style.zoom = "";
    const oscillogramHeight = oscillogramWrap.getBoundingClientRect().height;
    const spectrogramHeight = wrap.getBoundingClientRect().height;
    const shrinkableHeight = oscillogramHeight + spectrogramHeight;

    // Constraint 1: fit the audio view's OWN chrome (top of the page down
    // through .audio-row) within the viewport -- not the whole page (Janna,
    // 2026-09-11: "It's ok to make it smaller if it does not fit on the
    // screen, together with its buttons above and below. We should not make
    // it smaller though, just to accommodate other elements of the page (we
    // can scroll)."). Previously this read `mainContent.scrollHeight -
    // mainContent.clientHeight`, which counted the Identifications/classifier
    // boxes below the audio view too -- shrinking the spectrogram/
    // oscillogram to make THOSE fit on screen as well, when the actual
    // audio view alone would easily have fit at full size. `.getBoundingClientRect()`
    // positions are viewport-relative and would normally be scroll-position-
    // dependent, but subtracting `mainContent`'s own top from `audioRow`'s
    // bottom cancels that out -- both shift by the same amount on scroll --
    // leaving a scroll-position-independent "natural height needed from the
    // top of the page through the end of the audio view" measurement, the
    // same invariant `scrollHeight - clientHeight` gave for the whole page.
    const neededHeight =
      audioRow.getBoundingClientRect().bottom - mainContent.getBoundingClientRect().top;
    const overflow = neededHeight - mainContent.clientHeight;
    // No readability floor on this constraint: an earlier
    // `Math.max(0.2, scale)` clamp meant any window small enough to need more
    // than a 5x shrink stopped shrinking and fell back to `main.main-content`
    // scrolling vertically -- reintroducing exactly the scrollbar this
    // function exists to eliminate for the audio view's OWN chrome (a
    // scrollbar to reach the Identifications/classifier boxes below is fine
    // now -- see the comment above -- this floor is specifically about the
    // audio view itself never needing one). `0.001` is not a readability
    // floor, just the minimum needed to keep `zoom` a valid positive CSS
    // value; only reachable in the degenerate case where `overflow` alone
    // (from the OTHER, unzoomed chrome: nav, header, toolbar, both axes,
    // audio row) already exceeds `shrinkableHeight`.
    const viewportFitScale =
      overflow > 0 && shrinkableHeight > 0
        ? Math.max(0.001, (shrinkableHeight - overflow) / shrinkableHeight)
        : 1;

    // Constraint 2: never display the spectrogram taller than
    // MAX_SPECTROGRAM_HEIGHT_PX, regardless of how much viewport room is
    // available -- independent of constraint 1 above.
    const comfortScale =
      spectrogramHeight > MAX_SPECTROGRAM_HEIGHT_PX
        ? MAX_SPECTROGRAM_HEIGHT_PX / spectrogramHeight
        : 1;

    currentScale = Math.min(1, viewportFitScale, comfortScale);
    if (currentScale < 1) {
      const zoomValue = String(currentScale);
      oscillogramWrap.style.zoom = zoomValue;
      wrap.style.zoom = zoomValue;
    }
    // Read by .annotation-label's `scale(calc(1 / var(--zoom-scale, 1)))` (app.css) to cancel
    // out `wrap`'s own zoom above -- set unconditionally (not just inside the `< 1` branch) so a
    // later call that returns to `currentScale === 1` overwrites a stale shrunk value from an
    // earlier one instead of leaving annotation labels permanently oversized.
    wrap.style.setProperty("--zoom-scale", String(currentScale));

    // Checked after applying the final scale (not per-constraint above) so
    // it catches every path that can produce a too-small result, viewport-
    // fit and comfort-cap alike -- toolbar and audio-row (playback) are
    // never part of this shrink, so they stay visible/usable either way.
    const tooSmall = spectrogramHeight * currentScale < MIN_SUPPORTED_SPECTROGRAM_HEIGHT_PX;
    scrollEl.hidden = tooSmall;
    tooSmallMessage.hidden = !tooSmall;
  }

  // Dense axis (design spec section 3): fixed ms/kHz intervals, positioned from the spectrogram
  // wrap's real on-screen geometry and `coords` (plot_coords.js) -- the same mapping the Ruler and
  // crosshair use, so a tick and a Ruler reading of that tick's own position cannot disagree.
  // 50ms was too sparse to be useful at the current 19px/ms scale (~950px
  // between labels -- one or two per screen at typical zoom); 20ms lands a
  // label roughly every 380px, still with comfortable room for the "0.02s"-
  // style text to not collide with its neighbours. Both base intervals widen
  // by whole multiples as the on-screen scale shrinks (see `fitDetailHeight`), so
  // the on-screen gap between labels stays roughly constant even though the
  // image itself is smaller -- the label text is never scaled, only thinned
  // out. MIN_LABEL_SPACING_PX is chosen below the natural (scale===1)
  // spacing of BOTH axes (~88px/~47px at this page's locked scale), so an
  // unshrunk page never widens -- only real shrink does.
  const TIME_TICK_MS = 20;
  const FREQ_TICK_KHZ = 10;
  const MIN_LABEL_SPACING_PX = 40;

  // The spectrogram wrap's box and on-screen scale, measured from the DOM right now -- or null
  // while it has no size (Analysis view forces `.detail-scroll` to `display: none`, and the
  // too-small message hides it too). Ticks are always placed from THIS, never from a cached
  // `currentScale` or from a sum of assumed row heights: an earlier version placed them from the
  // heights of the rows above the spectrogram at whatever moment `relayout()` last ran, so a
  // window resize while Analysis view was showing (everything measures 0 there) left every freq
  // tick 34-48px off after switching back -- the axis said 20kHz where the Ruler said 29kHz.
  // Measuring the wrap itself means the offset is whatever the layout really is.
  //
  // The scale comes from `rect / native size`, where native size is the server-reserved raster
  // size, not `offsetHeight`: Chromium's `offsetHeight` on a `zoom`ed element has reported both
  // pre- and post-zoom values across versions.
  function measureSpectrogram() {
    const rect = wrap.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return null;
    return { rect, scaleX: rect.width / nativeWidthPx, scaleY: rect.height / nativeHeightPx };
  }

  function buildTimeAxis() {
    const m = measureSpectrogram();
    // Hidden: leave the (invisible) old ticks alone; the ResizeObserver below rebuilds them the
    // moment the graphs have a size again.
    if (!m) return;
    timeAxis.innerHTML = "";
    // Where the spectrogram's left edge sits within the time axis' own box -- 0 today (same
    // flex column) but measured, not assumed, for the same reason as the freq axis' top offset.
    const leftOffsetPx = m.rect.left - timeAxis.getBoundingClientRect().left;
    const screenPxPerMs = coords.pxPerMs * m.scaleX;
    const totalMs = durationS * 1000;
    const tickMs = adaptiveTickInterval(TIME_TICK_MS, screenPxPerMs, MIN_LABEL_SPACING_PX);
    // The LAST tick drawn is `tickMs`-aligned, so it lands a few ms (and therefore a few px)
    // short of the recording's true end. Previously this tick was right-aligned instead of
    // centered like every other one, specifically so its bled-right half wouldn't stick out
    // past the actual rendered image content, into that small gap (found 2026-09-03) -- but a
    // right-aligned label bleeds ONLY left, so it lost the half-gap a centered neighbour would
    // normally have shared with it, and collided with the previous tick's own right-bled half
    // instead (found live 2026-09-13: "14.96s"/"14.98s" ran together with no gap). Simpler
    // fix (Janna, 2026-09-13): drop the special alignment entirely -- every tick is centered,
    // same as the rest, and the last one just isn't drawn at all when there isn't room for it
    // to bleed right without crossing the true content edge.
    const ticks = tickValues(totalMs, tickMs);
    const lastMs = ticks[ticks.length - 1];
    const trailingGapPx = (totalMs - lastMs) * screenPxPerMs;
    const showLastTick = lastMs === 0 || trailingGapPx >= MIN_LABEL_SPACING_PX / 2;
    for (const ms of ticks) {
      if (ms === lastMs && ms !== 0 && !showLastTick) continue;
      const tick = document.createElement("span");
      tick.className = "detail-axis-tick detail-axis-tick-time";
      // `sToX` gives the tick's NATIVE (unzoomed) pixel position -- `* scaleX` converts to the
      // real screen pixels this tick, living OUTSIDE the zoomed wraps, actually needs (its own
      // font-size is never zoomed, so its position can't rely on an ancestor's `zoom`).
      tick.style.left = `${leftOffsetPx + coords.sToX(ms / 1000) * m.scaleX}px`;
      // Every tick is centered on its mark via `.detail-axis-tick-time`'s own
      // `transform: translateX(-50%)` (CSS default), which is fine -- adjacent labels' bled-
      // over halves just share the empty gap between marks. The 0ms tick sits at `left: 0`,
      // so centering it would bleed its left half into negative-x territory -- exactly where
      // the sticky, opaque `.detail-axis-freq` column sits, hiding half the label under it.
      // Left-align this one tick instead: it also reads as more correct for an origin label,
      // which has nothing to its left to straddle.
      if (ms === 0) tick.style.transform = "translateX(0)";
      tick.textContent = `${(ms / 1000).toFixed(2)}s`;
      timeAxis.appendChild(tick);
    }
  }

  function buildFreqAxis() {
    const m = measureSpectrogram();
    if (!m) return;
    freqAxis.innerHTML = "";
    // `.detail-axis-freq` and `.detail-graphs` are flex siblings that both start at the top of
    // `.detail-body`, but the spectrogram starts lower -- below the time axis row and the
    // oscillogram strip. A tick's `top` is relative to `.detail-axis-freq`, so it has to include
    // that distance; measuring the two boxes directly gives it without listing what sits in
    // between (which is exactly what went stale before, see `measureSpectrogram`).
    const topOffsetPx = m.rect.top - freqAxis.getBoundingClientRect().top;
    const tickKhz = adaptiveTickInterval(
      FREQ_TICK_KHZ,
      coords.pxPerKhz * m.scaleY,
      MIN_LABEL_SPACING_PX,
    );
    for (const khz of tickValues(maxFreqKhz, tickKhz)) {
      const tick = document.createElement("span");
      tick.className = "detail-axis-tick detail-axis-tick-freq";
      tick.style.top = `${topOffsetPx + coords.khzToY(khz) * m.scaleY}px`;
      tick.textContent = `${khz}kHz`;
      freqAxis.appendChild(tick);
    }
  }

  const canBuildTimeAxis = timeAxis && coordsValid;
  const canBuildFreqAxis = freqAxis && coordsValid;

  // The guarantee that the axes stay aligned, independent of which code path changed the layout:
  // rebuild whenever anything they are positioned against changes size. That covers a window
  // resize, the fit-to-viewport zoom, switching Playback <-> Analysis (the graphs go from 0 size
  // to real size), the toolbar re-wrapping, and whatever future feature moves things -- none of
  // which needs to remember to call the builders. Building only reads layout and writes
  // absolutely-positioned children of fixed-size boxes, so it cannot retrigger this observer.
  if (typeof ResizeObserver !== "undefined") {
    const axisObserver = new ResizeObserver(() => {
      if (canBuildTimeAxis) buildTimeAxis();
      if (canBuildFreqAxis) buildFreqAxis();
    });
    for (const el of [wrap, oscillogramWrap, timeAxis]) if (el) axisObserver.observe(el);
  }

  // View lock: locks the current field of view -- no scrolling, and playback is limited to
  // that field of view, stopping at its end. An independent toggle, not a third exclusive
  // tool alongside Default/Ruler -- both stay usable while locked, only scrolling and the
  // playback boundary are affected.
  //
  // The locked LEFT edge is stored as an absolute spectrogram TIME (`lockedStartS`), not a raw
  // scrollLeft pixel value -- `currentScale` (the vertical shrink-to-fit zoom `fitDetailHeight`
  // computes) can change on a window resize, and scrollLeft is in that VISUAL, post-zoom pixel
  // space, so a fixed pixel value would silently point at a different TIME after a resize
  // changes the zoom. Storing the time and re-deriving scrollLeft from it (via whatever
  // `currentScale` currently is) after every `relayout()` keeps the locked left edge pinned to
  // the same real moment in the recording regardless of resize.
  //
  // The RIGHT edge deliberately has no stored counterpart -- "stops playing at the end of the
  // view" is checked live, against whatever `scrollLeft + clientWidth` currently shows (see the
  // `timeupdate` handler below), not a snapshot taken when the lock was engaged. A resize can
  // change how much time fits in the viewport at a given zoom; recomputing live means the
  // playback boundary always matches what's actually on screen at that instant, never a stale
  // line from before the resize.
  let viewLocked = false;
  let lockedStartS = null;

  // A Select-tool selection *suppresses* View Lock's clamp entirely while it exists -- it does
  // NOT replace it with a clamp to the selection's own range (the spec's Non-goals section rules
  // out playback-limited-to-selection). Clearing the selection reactivates whatever View Lock's
  // own window was: View Lock's own state (`viewLocked`, `lockedStartS`) is never touched by a
  // selection, only its effect here is gated off. Every read of `viewLocked` that gates seek/
  // scroll/playback-boundary behavior goes through this helper instead of `viewLocked` directly,
  // so the suppression check lives in exactly one place.
  function isViewLockActive() {
    return viewLocked && currentSelection === null;
  }

  function nativeXPxToTimeS(nativeXPx) {
    return coords.xToS(nativeXPx);
  }

  // Re-derives `scrollEl.scrollLeft` from `lockedStartS` and the CURRENT `currentScale` --
  // `scrollLeft` still works programmatically even while every USER-driven way of changing it
  // (wheel, scrollbar drag, keyboard) is being blocked below.
  function applyLockedScrollPosition() {
    if (!isViewLockActive() || lockedStartS === null) return;
    scrollEl.scrollLeft = coords.sToX(lockedStartS) * currentScale;
  }

  function currentViewEndTimeS() {
    const visibleRightNativePx = (scrollEl.scrollLeft + scrollEl.clientWidth) / currentScale;
    return nativeXPxToTimeS(visibleRightNativePx);
  }

  // Cursor position tracking, `requestAnimationFrame`-driven (up to ~60 updates/second) rather
  // than solely the `timeupdate` event -- `timeupdate` only fires a handful of times a second
  // and the browser doesn't guarantee a fixed interval, which both makes ordinary cursor motion
  // visibly step rather than glide, and, while View Lock is engaged, let playback overrun the
  // locked boundary before JS got a chance to pause it, by an amount that varied with that
  // (variable) gap (Janna, 2026-09-04, live use: "in some rare cases plays just a little bit
  // longer"). rAF can't make the boundary pause perfectly sample-accurate (nothing JS-driven
  // against an <audio> element can be -- there's no "pause at this exact sample" API), but
  // polling ~15x more often than `timeupdate` typically does substantially tightens and evens
  // out the overrun, and the same higher cadence is what makes ordinary playback tracking smooth.
  // The `timeupdate` listener below calls the same update function as a fallback for whatever
  // this loop might miss -- e.g. a tab backgrounded, where browsers throttle
  // `requestAnimationFrame` but not `timeupdate`.
  let cursorRafHandle = null;

  function stopCursorRaf() {
    if (cursorRafHandle !== null) {
      cancelAnimationFrame(cursorRafHandle);
      cursorRafHandle = null;
    }
  }

  function stepCursorRaf() {
    cursorRafHandle = null;
    if (audio.paused) return;
    updatePlaybackPosition();
    if (!audio.paused) cursorRafHandle = requestAnimationFrame(stepCursorRaf);
  }

  // Covers every way playback can start: the ▶ button, a spectrogram click, and resuming from
  // a pause.
  function startCursorRafIfNeeded() {
    if (!audio.paused && cursorRafHandle === null) {
      cursorRafHandle = requestAnimationFrame(stepCursorRaf);
    }
  }
  audio.addEventListener("play", startCursorRafIfNeeded);
  audio.addEventListener("pause", stopCursorRaf);
  audio.addEventListener("ended", stopCursorRaf);

  // The axis builders are called here for a deterministic first paint, but `axisObserver` above is
  // what keeps them correct -- don't rely on this function being the only thing that rebuilds them.
  function relayout() {
    fitDetailHeight();
    if (canBuildTimeAxis) buildTimeAxis();
    if (canBuildFreqAxis) buildFreqAxis();
    applyLockedScrollPosition();
    repositionRulerReadout();
  }

  relayout();
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(relayout, 100);
  });

  revealWhenAllLoaded(spectrogramTiles, spectrogramLoading);
  revealWhenAllLoaded(oscillogramTiles, oscillogramLoading);

  // Tool switching (design spec 2026-09-04-fledermap-recording-detail-tools-design.md):
  // `tools[activeTool]` implements onClick/onDrag/onDragEnd for whichever tool is selected.
  // Click-to-play, crosshair, and the playback cursor all still measure against `wrap`'s own
  // bounding rect (the tiled row's container) rather than a single `<img>`'s -- the tiles sit
  // edge-to-edge with no gaps, so the container's rect spans exactly the full locked-scale
  // width, same as the single image did before tiling.
  // Ruler tool: drag draws a live measurement box; a plain click (no drag) clears it. `rulerBox`
  // is the one live DOM node for the current measurement -- created lazily on first drag, reused
  // (repositioned) on subsequent drags, removed entirely on clear.

  const viewLockToggle = document.getElementById("view-lock-toggle");

  const spanButtons = Array.from(document.querySelectorAll(".analysis-span-button"));
  const chartCanvas = document.getElementById("sound-level-spectrum-chart");
  const chartError = document.getElementById("sound-level-spectrum-error");
  let spectrumChart = null;
  let activeSpan = "whole-file";

  function updateSelectionButtonAvailability() {
    const selectionButton = spanButtons.find((btn) => btn.dataset.span === "selection");
    selectionButton.disabled = currentSelection === null;
    if (currentSelection === null && activeSpan === "selection") {
      setActiveSpan("whole-file");
    }
  }

  function setActiveSpan(span) {
    activeSpan = span;
    spanButtons.forEach((btn) => {
      btn.setAttribute("aria-pressed", btn.dataset.span === span ? "true" : "false");
    });
    fetchAndRenderSpectrum();
  }

  spanButtons.forEach((btn) => {
    btn.addEventListener("click", () => setActiveSpan(btn.dataset.span));
  });

  function fetchAndRenderSpectrum() {
    if (activeView !== "analysis") return;
    const params = new URLSearchParams();
    if (denoiseOn) params.set("denoise", "true");
    if (activeSpan === "selection" && currentSelection !== null) {
      params.set("start_s", String(currentSelection.startTimeS));
      params.set("end_s", String(currentSelection.endTimeS));
    }
    const url = `${analysisPanel.dataset.spectrumUrl}?${params}`;
    fetch(url)
      .then((response) => {
        if (!response.ok) throw new Error(`sound-level-spectrum request failed: ${response.status}`);
        return response.json();
      })
      .then((spectrum) => {
        chartCanvas.hidden = false;
        chartError.hidden = true;
        const chartData = soundLevelSpectrumToChartData(spectrum);
        if (spectrumChart) {
          spectrumChart.data = chartData;
          Object.assign(spectrumChart.options.scales.x, soundLevelSpectrumChartOptions(spectrum).scales.x);
          spectrumChart.update();
        } else {
          spectrumChart = new Chart(chartCanvas, {
            type: "line",
            data: chartData,
            options: soundLevelSpectrumChartOptions(spectrum),
            plugins: [peakLabelPlugin],
          });
        }
      })
      // A recording whose source WAV is missing from disk (or `missing_since` is set) still
      // shows the Analysis toggle, but the endpoint 404s -- without this, that left a permanently
      // blank canvas with an unhandled promise rejection and no explanation (found in final
      // whole-branch review).
      .catch((error) => {
        console.error("Failed to fetch/render the sound-level spectrum", error);
        chartCanvas.hidden = true;
        chartError.hidden = false;
      });
  }

  function clearSelectionBox() {
    if (selectionBox) {
      selectionBox.remove();
      selectionBox = null;
    }
    currentSelection = null;
    viewLockToggle.disabled = false;
    scrollEl.classList.toggle("view-locked", isViewLockActive());
    updateSelectionButtonAvailability();
  }

  function clearRulerBox() {
    if (rulerBox) {
      rulerBox.remove();
      rulerBox = null;
    }
    rulerBoxLocal = null;
    rulerReadout.hidden = true;
  }

  // Re-derives the (position: fixed) readout's screen position from the box's last-known LOCAL
  // (unzoomed, wrap-relative) coordinates and a freshly measured `wrap` rect -- needed any time
  // `wrap`'s screen position or `currentScale` can have changed without a new drag event, namely
  // scrolling `.detail-scroll` and a window resize (`relayout()`). `rulerBox` itself needs no such
  // fix-up: it's a DOM child of `wrap`, positioned in `wrap`-relative CSS, so it scrolls/zooms
  // with the content automatically -- only the readout's viewport-fixed positioning goes stale.
  function repositionRulerReadout() {
    if (!rulerBox || !rulerBoxLocal) return;
    const rect = wrap.getBoundingClientRect();
    const { left, top, height } = rulerBoxLocal;
    rulerReadout.style.left = `${rect.left + left * currentScale}px`;
    const boxTopPx = rect.top + top * currentScale;
    const boxBottomPx = rect.top + (top + height) * currentScale;
    const hasRoomAbove = boxTopPx - rect.top >= rulerReadout.offsetHeight + 4;
    rulerReadout.classList.toggle("ruler-readout-below", !hasRoomAbove);
    rulerReadout.style.top = `${hasRoomAbove ? boxTopPx : boxBottomPx}px`;
  }

  function updateRulerBox(dragStart, event) {
    // `getBoundingClientRect()` returns `wrap`'s VISUAL (post-zoom) box once
    // `fitDetailHeight` has zoomed it down -- dividing by `currentScale` converts back to
    // `wrap`'s own local/unzoomed coordinate space, which is what both the real-unit math below
    // (pxPerMs/pxPerKhz are native, unscaled constants) AND positioning `rulerBox` itself need,
    // since `rulerBox` is a DOM child inside that same zoomed element -- its
    // `left`/`top`/`width`/`height` are already visually re-scaled by `wrap`'s own `zoom`, so
    // they must be set in the SAME local space or they'd be scaled twice.
    const rect = wrap.getBoundingClientRect();
    // `dragStart.x/y` are viewport coords captured once at mousedown, but `rect` is
    // re-measured fresh on every call -- if `.detail-scroll` scrolls horizontally mid-drag
    // (mouse wheel, or the playback-cursor auto-follow while audio is playing), `rect.left`
    // moves while `dragStart.x` doesn't, silently shifting the computed start point. Correct
    // for it by the same horizontal distance the scroll container has moved since mousedown
    // (`scrollLeft` is 1:1 with viewport px here -- `.detail-scroll` itself is never zoomed,
    // only its content is, and CSS `zoom` scales `scrollWidth`/`scrollLeft` right along with
    // layout, so a `zoom`-scaled content pixel and a `scrollLeft` pixel already agree).
    const scrollDriftPx = scrollEl.scrollLeft - dragStart.scrollLeft;
    const startX = (dragStart.x - scrollDriftPx - rect.left) / currentScale;
    const startY = (dragStart.y - rect.top) / currentScale;
    const curX = (event.clientX - rect.left) / currentScale;
    const curY = (event.clientY - rect.top) / currentScale;

    const left = Math.min(startX, curX);
    const top = Math.min(startY, curY);
    const width = Math.abs(curX - startX);
    const height = Math.abs(curY - startY);

    const deltaTMs = Math.abs(curX - startX) / coords.pxPerMs;
    // Pulse-repetition-rate reading (bat-call literature convention: inter-pulse interval -> Hz,
    // the same relationship as note-duration -> BPM). A purely vertical drag has deltaTMs === 0
    // -- guard the division rather than showing Infinity.
    const hzText = deltaTMs > 0 ? `${(1000 / deltaTMs).toFixed(1)} Hz` : "—";
    // Absolute frequency range, not bandwidth -- same conversion the crosshair readout uses
    // (`coords.yToKhz`). Reported as min/max because call-ID references (field guides,
    // species tables) give absolute frequency bands, e.g. "35-45 kHz" -- a bare bandwidth number
    // is strictly less useful, since min/max still implies it (max - min) but not the reverse.
    const startFreqKhz = coords.yToKhz(startY);
    const curFreqKhz = coords.yToKhz(curY);
    const minFreqKhz = Math.min(startFreqKhz, curFreqKhz);
    const maxFreqOfDragKhz = Math.max(startFreqKhz, curFreqKhz);

    if (!rulerBox) {
      rulerBox = document.createElement("div");
      rulerBox.className = "ruler-box";
      wrap.appendChild(rulerBox);
    }
    rulerBox.style.left = `${left}px`;
    rulerBox.style.top = `${top}px`;
    rulerBox.style.width = `${width}px`;
    rulerBox.style.height = `${height}px`;
    rulerBoxLocal = { left, top, width, height };

    // The Δt/Δf readout is a SEPARATE, `position: fixed` element (`#ruler-readout`, styled like
    // `#crosshair-readout`) rather than a span nested inside `rulerBox` -- nesting it there
    // would put it inside `wrap`'s zoomed subtree, shrinking the text down to unreadable sizes
    // right along with the image whenever `fitDetailHeight` has scaled the page down (Janna,
    // 2026-09-04, live screenshot). Its screen position (from `rulerBoxLocal`) is computed by
    // `repositionRulerReadout`, shared with the scroll/resize fix-up paths below.
    rulerReadout.textContent = `Δt: ${deltaTMs.toFixed(2)} ms (${hzText})\nMin/Max: ${minFreqKhz.toFixed(1)}–${maxFreqOfDragKhz.toFixed(1)} kHz`;
    rulerReadout.hidden = false;
    // `offsetHeight` (read inside `repositionRulerReadout`, for the above/below room check)
    // needs the element unhidden first -- done above.
    repositionRulerReadout();
  }

  function updateSelectionBox(dragStart, event) {
    const rect = wrap.getBoundingClientRect();
    const scrollDriftPx = scrollEl.scrollLeft - dragStart.scrollLeft;
    const startX = (dragStart.x - scrollDriftPx - rect.left) / currentScale;
    const curX = (event.clientX - rect.left) / currentScale;
    const left = Math.min(startX, curX);
    const width = Math.abs(curX - startX);

    if (!selectionBox) {
      selectionBox = document.createElement("div");
      selectionBox.className = "selection-box";
      wrap.appendChild(selectionBox);
    }
    selectionBox.style.left = `${left}px`;
    selectionBox.style.width = `${width}px`;

    const startTimeS = coords.xToS(Math.min(startX, curX));
    const endTimeS = coords.xToS(Math.max(startX, curX));
    currentSelection = { startTimeS, endTimeS };
    viewLockToggle.disabled = true;
    scrollEl.classList.toggle("view-locked", isViewLockActive());
  }

  let pendingDragBox = null;

  function updateCommentDragBox(dragStart, event) {
    const rect = wrap.getBoundingClientRect();
    const scrollDriftPx = scrollEl.scrollLeft - dragStart.scrollLeft;
    // mousemove/mouseup are tracked on `document`, not `wrap` (see the shared drag-vs-click state
    // machine below), so either end of the drag can land past the spectrogram's edges -- clamp
    // both to the plot's native-pixel bounds before they're converted to time/frequency, or a
    // release outside the image would save an out-of-range annotation (negative time, a
    // frequency past maxFreqKhz) with nothing rejecting it.
    const start = clampToPlot(
      (dragStart.x - scrollDriftPx - rect.left) / currentScale,
      (dragStart.y - rect.top) / currentScale,
      nativeWidthPx,
      nativeHeightPx,
    );
    const cur = clampToPlot(
      (event.clientX - rect.left) / currentScale,
      (event.clientY - rect.top) / currentScale,
      nativeWidthPx,
      nativeHeightPx,
    );
    const startX = start.x;
    const startY = start.y;
    const curX = cur.x;
    const curY = cur.y;

    if (!pendingDragBox) {
      const el = document.createElement("div");
      el.className = "annotation-box";
      wrap.appendChild(el);
      pendingDragBox = { el, bounds: null };
    }
    const left = Math.min(startX, curX);
    const top = Math.min(startY, curY);
    pendingDragBox.el.style.left = `${left}px`;
    pendingDragBox.el.style.top = `${top}px`;
    pendingDragBox.el.style.width = `${Math.abs(curX - startX)}px`;
    pendingDragBox.el.style.height = `${Math.abs(curY - startY)}px`;

    pendingDragBox.bounds = normalizeDragBounds(
      coords.xToS(startX),
      coords.xToS(curX),
      coords.yToKhz(startY) * 1000,
      coords.yToKhz(curY) * 1000,
    );
  }

  const toolbar = document.getElementById("detail-toolbar");
  const toolButtons = Array.from(toolbar.querySelectorAll(".tool-button"));
  let activeTool = "default";

  function setActiveTool(tool) {
    activeTool = tool;
    toolButtons.forEach((btn) => {
      const isActive = btn.dataset.tool === tool;
      btn.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
    wrap.classList.toggle("tool-default", tool === "default");
    wrap.classList.toggle("tool-ruler", tool === "ruler");
    wrap.classList.toggle("tool-select", tool === "select");
    wrap.classList.toggle("tool-comment", tool === "comment");
    wrap.classList.toggle("comment-interactive", tool === "comment");
    clearRulerBox();
    // Avoids clearing the selection you're actively mid-drag-creating the instant you click the
    // Select button itself -- setActiveTool("select") runs once, on the click that activates the
    // tool, before any drag has happened.
    if (tool !== "select") clearSelectionBox();
    renderAnnotations();
  }

  toolButtons.forEach((btn) => {
    btn.addEventListener("click", () => setActiveTool(btn.dataset.tool));
  });
  setActiveTool("default");

  const lockIconUnlocked = viewLockToggle.querySelector(".lock-icon-unlocked");
  const lockIconLocked = viewLockToggle.querySelector(".lock-icon-locked");
  viewLockToggle.addEventListener("click", () => {
    viewLocked = !viewLocked;
    viewLockToggle.setAttribute("aria-pressed", viewLocked ? "true" : "false");
    lockIconUnlocked.hidden = viewLocked;
    lockIconLocked.hidden = !viewLocked;
    scrollEl.classList.toggle("view-locked", viewLocked);
    if (viewLocked) {
      lockedStartS = nativeXPxToTimeS(scrollEl.scrollLeft / currentScale);
    } else {
      lockedStartS = null;
    }
  });

  const viewToggleButton = document.getElementById("view-toggle-button");
  const analysisPanel = document.querySelector(".analysis-panel");
  const viewToggleIconToAnalysis = viewToggleButton.querySelector(".view-toggle-icon-to-analysis");
  const viewToggleIconToPlayback = viewToggleButton.querySelector(".view-toggle-icon-to-playback");
  const viewToggleLabel = viewToggleButton.querySelector(".view-toggle-label");

  function setActiveView(view) {
    activeView = view;
    const targetView = view === "playback" ? "analysis" : "playback";
    viewToggleButton.dataset.targetView = targetView;
    viewToggleButton.title = `Switch to ${targetView === "analysis" ? "Analysis" : "Playback"} view`;
    viewToggleIconToAnalysis.hidden = view !== "playback";
    viewToggleIconToPlayback.hidden = view !== "analysis";
    viewToggleLabel.textContent = targetView === "analysis" ? "Analysis view" : "Playback view";
    mainContent.classList.toggle("view-playback", view === "playback");
    mainContent.classList.toggle("view-analysis", view === "analysis");
    // `scrollEl.hidden`/the too-small message are owned entirely by `fitDetailHeight` (app.css's
    // `.view-analysis .detail-scroll`/`#detail-too-small-message` rules make the view class win
    // at the display level regardless of execution order) -- setting it here too used to race
    // `fitDetailHeight`, which runs on every resize independent of which view is active and would
    // silently re-show Playback's graphs underneath Analysis view.
    analysisPanel.hidden = view !== "analysis";
    if (view === "playback") {
      // fitDetailHeight no-ops entirely while Analysis is active (its measurements would all read
      // zero, forced hidden by CSS) -- recompute now for whatever the window did in the meantime,
      // since no resize may have fired since returning to Playback.
      fitDetailHeight();
    }
    if (view === "analysis") {
      // The playback controls are hidden in Analysis view (app.css's `.view-analysis
      // .audio-row`), so audio still playing would have no visible way to stop it.
      audio.pause();
      // Order matters: `updateSelectionButtonAvailability` only ever needs to enable/disable
      // the Selection button here (a selection made while in Playback never gets a chance to
      // flip it otherwise) -- it must not also be the thing that decides `activeSpan`, or a
      // stale "selection" span from before the selection was cleared would fetch once via its
      // own downgrade-to-whole-file path and again via the explicit call below. Defaulting to
      // Selection when one exists, else Whole file (spec §7), is decided explicitly here instead.
      updateSelectionButtonAvailability();
      setActiveSpan(currentSelection !== null ? "selection" : "whole-file");
    }
  }

  viewToggleButton.addEventListener("click", () => {
    setActiveView(viewToggleButton.dataset.targetView);
  });
  setActiveView("playback");

  // Scroll prevention while locked, entirely JS-driven (Janna, 2026-09-04: keep the scrollbar
  // itself visible rather than switching `overflow-x` to `hidden` -- "disable the scrollbar
  // instead of removing it, noisy UI" -- so `.detail-scroll` stays `overflow-x: auto` at all
  // times; app.css no longer has any lock-specific overflow rule). Two listeners share the job:
  //
  // - `wheel` catches the common case (a trackpad/mouse-wheel horizontal scroll) BEFORE it
  //   happens, since `preventDefault()` on the event stops the scroll from ever occurring.
  // - `scroll` is the reactive fallback for everything `wheel` can't intercept up front --
  //   a scrollbar-track click, a drag of the scrollbar thumb, or keyboard scrolling. Snapping
  //   `scrollLeft` back on every `scroll` event makes a thumb-drag visibly "fight back" to the
  //   locked position instead of silently doing nothing, which is what a disabled (not
  //   removed) scrollbar should look like.
  scrollEl.addEventListener(
    "wheel",
    (event) => {
      if (isViewLockActive()) event.preventDefault();
    },
    { passive: false },
  );
  scrollEl.addEventListener("scroll", () => {
    if (isViewLockActive()) applyLockedScrollPosition();
    repositionRulerReadout();
  });

  const tools = {
    default: {
      onClick(event) {
        const rect = wrap.getBoundingClientRect();
        const xPx = (event.clientX - rect.left) / currentScale;
        const spectrogramTimeS = coords.xToS(xPx);
        let targetTimeS = spectrogramTimeS * audioControls.getTimeExpansionFactor();
        // Clamp below `audio.duration`, not just non-negative: a click at or past the
        // spectrogram's own right edge -- including the cursor's OWN resting position once
        // playback has run to the end, since the cursor sits within a couple of native px of
        // that edge (Janna, 2026-09-04, live use) -- resolves to a `currentTime` at or beyond
        // `duration`. Assigning that lands `play()` on the "ended playback" condition, and
        // per the HTMLMediaElement spec that's a NO-OP (stays paused, currentTime unchanged)
        // rather than an error, so nothing here would otherwise reveal the click did nothing.
        if (!Number.isNaN(audio.duration) && targetTimeS >= audio.duration) {
          targetTimeS = Math.max(0, audio.duration - 0.01);
        }
        audio.currentTime = targetTimeS;
        audio.play();
      },
      onDrag(event, dragStart) {
        // `overflow-x: hidden` (app.css) only blocks NATIVE scroll input -- this sets
        // `scrollLeft` directly via JS, so it needs its own guard while the view is locked.
        if (isViewLockActive()) return;
        scrollEl.scrollLeft = dragStart.scrollLeft - (event.clientX - dragStart.x);
      },
      onDragEnd() {},
    },
    ruler: {
      onClick() {
        clearRulerBox();
      },
      onDrag(event, dragStart) {
        updateRulerBox(dragStart, event);
      },
      onDragEnd() {},
    },
    select: {
      onClick() {
        clearSelectionBox();
      },
      onDrag(event, dragStart) {
        updateSelectionBox(dragStart, event);
      },
      onDragEnd() {},
    },
    comment: {
      onClick(event) {
        const rect = wrap.getBoundingClientRect();
        // Clamped for the same reason as updateCommentDragBox above: `event` here comes through
        // the document-level mouseup listener, not a `wrap`-scoped one.
        const { x: xPx, y: yPx } = clampToPlot(
          (event.clientX - rect.left) / currentScale,
          (event.clientY - rect.top) / currentScale,
          nativeWidthPx,
          nativeHeightPx,
        );
        const timeS = coords.xToS(xPx);
        const freqHz = coords.yToKhz(yPx) * 1000;
        openCommentPopupForCreate({
          time_start_s: timeS,
          time_end_s: timeS,
          freq_min_hz: freqHz,
          freq_max_hz: freqHz,
        });
      },
      onDrag(event, dragStart) {
        updateCommentDragBox(dragStart, event);
      },
      onDragEnd() {
        if (!pendingDragBox) return;
        const bounds = pendingDragBox.bounds;
        pendingDragBox.el.remove();
        pendingDragBox = null;
        openCommentPopupForCreate(bounds);
      },
    },
  };

  // Shared drag-vs-click state machine: mousedown starts tracking, then mousemove/mouseup are
  // bound on `document` (not `wrap`) for the duration of the gesture -- a plain `wrap`-scoped
  // listener would never see mouseup if the drag ends outside `wrap`'s bounds (a fast or wide
  // drag), leaving the gesture stuck "in progress". 4px threshold: below it, treat as a click
  // even if the mouse moved a hair between mousedown and mouseup.
  const DRAG_THRESHOLD_PX = 4;

  wrap.addEventListener("mousedown", (event) => {
    // Primary button only: the `click` listener this replaced only ever fired for the primary
    // (left) button, but `mousedown` fires for every button. Without this guard, a right-click
    // seeks+plays audio (or clears the ruler) underneath the context menu it also opens, and a
    // middle-click does the same while the browser starts its own autoscroll.
    if (event.button !== 0) return;
    // Stop the browser from starting a text selection (or, dragging an existing selection,
    // native drag-and-drop) from this gesture -- either can swallow the `mouseup` below, which
    // is exactly the stuck-gesture path `onMove`'s `buttons === 0` check self-heals below.
    event.preventDefault();

    const dragStart = { x: event.clientX, y: event.clientY, scrollLeft: scrollEl.scrollLeft };
    let dragging = false;

    function onMove(moveEvent) {
      // Self-heal: if no mouse button is currently held, the gesture is already over even
      // though `mouseup` never reached us -- native drag-and-drop (see the `preventDefault`
      // above) can swallow it entirely, which would otherwise leave `dragging` (and these
      // `document`-scoped listeners) stuck, so every subsequent mouse move keeps panning or
      // drawing ruler boxes with no button held. `buttons` reflects the browser's own live
      // button state regardless of whether `mouseup` fired, so this always catches it on the
      // very next move.
      if (moveEvent.buttons === 0) {
        onUp(moveEvent);
        return;
      }
      const movedPx = Math.hypot(moveEvent.clientX - dragStart.x, moveEvent.clientY - dragStart.y);
      if (!dragging && movedPx > DRAG_THRESHOLD_PX) {
        dragging = true;
        wrap.classList.add("dragging");
      }
      if (dragging) tools[activeTool].onDrag(moveEvent, dragStart);
    }

    function onUp(upEvent) {
      if (dragging) {
        tools[activeTool].onDragEnd(upEvent);
      } else {
        tools[activeTool].onClick(upEvent);
      }
      wrap.classList.remove("dragging");
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });

  wrap.addEventListener("mousemove", (event) => {
    const rect = wrap.getBoundingClientRect();
    const xPx = (event.clientX - rect.left) / currentScale;
    const yPx = (event.clientY - rect.top) / currentScale;
    // rect.width/height are also the VISUAL (post-zoom) size -- divide by the same
    // scale to compare against the local-space xPx/yPx above.
    if (xPx < 0 || yPx < 0 || xPx > rect.width / currentScale || yPx > rect.height / currentScale) {
      readout.hidden = true;
      updateAnnotationHover(null);
      return;
    }
    const timeS = coords.xToS(xPx);
    const freqKhz = coords.yToKhz(yPx);
    readout.textContent = `${timeS.toFixed(3)} s\n${freqKhz.toFixed(1)} kHz`;
    readout.style.left = `${event.clientX + 12}px`;
    readout.style.top = `${event.clientY - 12}px`;
    readout.hidden = false;
    updateAnnotationHover(annotationAtPoint(xPx, yPx));
  });
  wrap.addEventListener("mouseleave", () => {
    readout.hidden = true;
    updateAnnotationHover(null);
  });

  // Playback cursor (design spec section 4 + "Future slots"): tracks
  // <audio>'s currentTime, snaps the scroll container into view only when
  // the cursor goes off-screen -- no continuous auto-follow by default. Also enforces the View
  // Lock playback boundary (pausing at the locked view's right edge) -- see the rAF loop above
  // for why this single function is driven from two places.
  function updatePlaybackPosition() {
    // Ignore the spurious zero-position tick a source change (mode switch, denoise toggle)
    // fires before its real position restore has landed -- see the `pendingRestore` comment
    // in audio_controls.js's `setSource`. Reacting to it here would auto-scroll the cursor
    // into view at the wrong (start-of-file) spot before it gets corrected.
    if (audioControls.isRestoringPosition()) return;

    const spectrogramTimeS = audio.currentTime / audioControls.getTimeExpansionFactor();

    // Checked before the cursor is drawn, using whatever the view currently shows (see the
    // view-lock section above for why this is live rather than a stored value) -- stops
    // playback the instant it plays past the locked view's right edge.
    //
    // `displayTimeS` clamps the DRAWN position to that edge on this exact tick, rather than
    // drawing the raw (past-the-edge) `spectrogramTimeS` or skipping the draw outright. Skipping
    // it (via an early `return` here before ever reaching the cursor-drawing lines below) would
    // instead leave the cursor visually frozen at wherever the PREVIOUS tick had drawn it -- up
    // to one `timeupdate` interval's worth of playback short of the true edge, which on a narrow
    // locked view can be a large fraction of what's even on screen. Clamping instead means the
    // very last frame drawn is always the edge itself, matching where playback actually stopped.
    const locked = isViewLockActive();
    const viewEndS = locked ? currentViewEndTimeS() : null;
    const pastEdge = locked && viewEndS !== null && spectrogramTimeS >= viewEndS;
    const displayTimeS = pastEdge ? viewEndS : spectrogramTimeS;

    // `xPx` is in `wrap`'s local/unzoomed coordinate space (pxPerMs is a native, unscaled
    // constant) -- correct as-is for positioning `cursor` (a descendant of `wrap`, so `wrap`'s
    // own `zoom` re-scales it visually automatically).
    const xPx = coords.sToX(displayTimeS);
    cursor.style.left = `${xPx}px`;
    cursor.hidden = false;

    if (pastEdge) {
      audio.pause();
      return;
    }

    // `scrollLeft`/`clientWidth` operate on `.detail-scroll`'s VISUAL (post-zoom)
    // scrollable area, so the comparison/target needs the cursor's visual position too.
    const visualXPx = xPx * currentScale;
    const visibleLeft = scrollEl.scrollLeft;
    const visibleRight = visibleLeft + scrollEl.clientWidth;
    // Locked: never auto-scroll to follow the cursor -- that would defeat "no scrolling any
    // more". The playback-stop check above already prevents the cursor from ever needing to
    // scroll into view in the first place (it can't play past the visible right edge).
    //
    // `!audio.paused`: auto-follow only makes sense while something is actually playing.
    // `setSource` (denoise toggle, TE/HET switch, Lock View) always pauses before swapping
    // source, then restores the previous position once metadata loads -- that restore fires
    // its own `timeupdate` (the correctly-restored one, past `isRestoringPosition`'s guard
    // above), and without this check it would snap the view to wherever the cursor happens to
    // sit even though the user had deliberately scrolled elsewhere and nothing is playing.
    if (
      !audio.paused &&
      !isViewLockActive() &&
      (visualXPx < visibleLeft || visualXPx > visibleRight)
    ) {
      scrollEl.scrollLeft = Math.max(0, visualXPx - scrollEl.clientWidth / 2);
    }
  }
  audio.addEventListener("timeupdate", updatePlaybackPosition);
});

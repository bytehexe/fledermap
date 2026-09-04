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
  const audio = document.getElementById("detail-audio");
  const audioControlsEl = document.getElementById("detail-audio-controls");
  // `getSeekFloorS`/`getSeekCeilingS` are read lazily (only when a button is
  // actually clicked), so it's safe to reference `viewLocked`/`lockedStartS`
  // (a `let`) and `currentViewEndTimeS` (a hoisted function declaration)
  // here even though neither is declared until further down this same
  // function -- by the time a click can happen, DOMContentLoaded has
  // finished running and both are ready.
  const audioControls = initAudioControls(audioControlsEl, audio, {
    getSeekFloorS: () => (viewLocked && lockedStartS !== null ? lockedStartS : 0),
    getSeekCeilingS: () => (viewLocked ? currentViewEndTimeS() : null),
  });
  const scrollEl = document.getElementById("detail-scroll");
  const timeAxis = document.getElementById("detail-axis-time");
  const freqAxis = document.getElementById("detail-axis-freq");
  const mainContent = document.querySelector("main.main-content");
  const rulerReadout = document.getElementById("ruler-readout");

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

  const firstTile = spectrogramTiles[0];
  const durationS = parseFloat(firstTile.dataset.durationS);
  const maxFreqKhz = parseFloat(firstTile.dataset.maxFreqKhz);
  const pxPerMs = parseFloat(firstTile.dataset.pxPerMs);
  const pxPerKhz = parseFloat(firstTile.dataset.pxPerKhz);

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
  // position their ticks in real screen pixels directly (`* currentScale`) and widen the tick
  // interval as the image shrinks, so labels remain both full-size AND non-overlapping.
  // Deliberately a uniform x/y scale (not a height-only squash): the render itself keeps its
  // exact locked-scale pixel fidelity (design spec "the exact 1:1 point"), only the on-screen
  // DISPLAY size changes, same as zooming out on an image viewer -- an independent x/y squash
  // would distort that. Horizontal overflow is left alone; `.detail-scroll`'s existing
  // horizontal scrollbar is the intended way to navigate a long recording, this only ever fixes
  // the VERTICAL scrollbar.
  //
  // CSS `zoom`, not `transform: scale()` -- code review (2026-09-04) caught two real bugs with
  // `transform`: it's paint-only, so a transformed ancestor's scrollable-container descendants
  // desync their native scrollWidth/scrollHeight from the visually-shrunk content (allowing
  // overscroll into blank space), and a transformed ancestor is documented to break
  // `position: sticky` on a scrolling descendant in Chromium/Firefox (w3c/csswg-drafts#3186),
  // which is exactly what `.detail-axis-freq` is. `zoom` actually rescales layout (not just
  // paint), so `.detail-scroll`'s own box -- and its scrollable range -- shrinks along with the
  // zoomed wraps with no manual compensation needed, and it isn't a `transform`, so sticky
  // positioning elsewhere on the page keeps working normally.
  let currentScale = 1;

  function fitDetailHeight() {
    // Measure natural (unzoomed) heights first -- clearing any previous zoom before measuring,
    // since already-shrunk wraps would otherwise make `mainContent` look like it has no
    // overflow even though the true unscaled content still would.
    oscillogramWrap.style.zoom = "";
    wrap.style.zoom = "";
    const shrinkableHeight =
      oscillogramWrap.getBoundingClientRect().height + wrap.getBoundingClientRect().height;
    const overflow = mainContent.scrollHeight - mainContent.clientHeight;
    if (overflow <= 0 || shrinkableHeight <= 0) {
      currentScale = 1;
      return;
    }
    // How far the two wraps alone need to shrink to remove exactly that much overflow --
    // everything else on the page (nav, header, toolbar, both axes, audio row) keeps its own
    // natural height untouched, which is exact here (not an approximation): they're the only
    // things being resized, so their combined height is the only variable in the equation.
    const availableHeight = shrinkableHeight - overflow;
    const scale = availableHeight / shrinkableHeight;
    if (scale >= 1) {
      currentScale = 1;
      return;
    }
    // Never shrink below a point that makes the render unreadable/unusable.
    currentScale = Math.max(0.2, scale);
    const zoomValue = String(currentScale);
    oscillogramWrap.style.zoom = zoomValue;
    wrap.style.zoom = zoomValue;
  }

  // Dense axis (design spec section 3): fixed ms/kHz intervals, built from
  // the exact same data attributes the crosshair/cursor math below uses --
  // the labels can never drift out of sync with the actual rendered scale.
  // 50ms was too sparse to be useful at the current 19px/ms scale (~950px
  // between labels -- one or two per screen at typical zoom); 20ms lands a
  // label roughly every 380px, still with comfortable room for the "0.02s"-
  // style text to not collide with its neighbours. Both base intervals widen
  // by whole multiples as `currentScale` shrinks (see `fitDetailHeight`), so
  // the on-screen gap between labels stays roughly constant even though the
  // image itself is smaller -- the label text is never scaled, only thinned
  // out. MIN_LABEL_SPACING_PX is chosen below the natural (scale===1)
  // spacing of BOTH axes (~88px/~47px at this page's locked scale), so an
  // unshrunk page never widens -- only real shrink does.
  const TIME_TICK_MS = 20;
  const FREQ_TICK_KHZ = 10;
  const MIN_LABEL_SPACING_PX = 40;

  // Grows `baseInterval` by whole multiples of itself until consecutive labels would be at
  // least `MIN_LABEL_SPACING_PX` apart on screen. Deliberately NOT `baseInterval *
  // Math.ceil(1 / currentScale)`: that multiplier is discontinuous right around
  // currentScale===1 -- a real but tiny shrink (currentScale, say, 0.97, from a few px of
  // genuine overflow) already rounds `1/0.97` up to 2, doubling the label density for an
  // imperceptible size change. Growing by a fixed screen-space threshold instead only widens
  // once labels would actually start crowding.
  function adaptiveTickInterval(baseInterval, pxPerUnit) {
    let interval = baseInterval;
    while (interval * pxPerUnit * currentScale < MIN_LABEL_SPACING_PX) interval += baseInterval;
    return interval;
  }

  function buildTimeAxis() {
    timeAxis.innerHTML = "";
    const totalMs = durationS * 1000;
    const tickMs = adaptiveTickInterval(TIME_TICK_MS, pxPerMs);
    // The mirror image of the ms===0 case below: the LAST tick drawn is `tickMs`-aligned, so it
    // lands a few ms (and therefore a few px) short of the recording's true end -- close enough
    // that a centered label's bled-right half can still stick out past the actual rendered
    // image content on the right, widening `.detail-scroll`'s scrollable range with a sliver of
    // empty background past the real content (found 2026-09-03 on a real recording, visible
    // once the sticky freq axis fix above let scrolling reach all the way to the end).
    const lastMs = Math.floor(totalMs / tickMs) * tickMs;
    for (let ms = 0; ms <= totalMs; ms += tickMs) {
      const tick = document.createElement("span");
      tick.className = "detail-axis-tick detail-axis-tick-time";
      // `ms * pxPerMs` is the tick's NATIVE (unzoomed) pixel position -- `* currentScale`
      // converts to the real screen pixels this tick, living OUTSIDE the zoomed wraps, actually
      // needs (its own font-size is never zoomed, so its position can't rely on an ancestor's
      // `zoom` to place it the way `.detail-axis-freq`'s old zoomed ticks used to).
      tick.style.left = `${ms * pxPerMs * currentScale}px`;
      // Every other tick is centered on its mark via `.detail-axis-tick-time`'s
      // `transform: translateX(-50%)`, which is fine -- adjacent labels' bled-over halves
      // just share the empty gap between marks. The 0ms tick sits at `left: 0`, so centering
      // it would bleed its left half into negative-x territory -- exactly where the sticky,
      // opaque `.detail-axis-freq` column sits, hiding half the label under it. Left-align
      // this one tick instead: it also reads as more correct for an origin label, which has
      // nothing to its left to straddle. The LAST tick gets the same treatment mirrored --
      // right-align it (bleed left, into existing content, never past the real right edge).
      if (ms === 0) tick.style.transform = "translateX(0)";
      else if (ms === lastMs) tick.style.transform = "translateX(-100%)";
      tick.textContent = `${(ms / 1000).toFixed(2)}s`;
      timeAxis.appendChild(tick);
    }
  }

  function buildFreqAxis() {
    freqAxis.innerHTML = "";
    const tickKhz = adaptiveTickInterval(FREQ_TICK_KHZ, pxPerKhz);
    // `.detail-axis-freq` and `.detail-graphs` are flex siblings that both
    // start at the top of `.detail-body` -- but the spectrogram image
    // itself starts lower than that, below `.detail-axis-time`'s row AND
    // the oscillogram row that now sits above the spectrogram (compact
    // strip above the main view, matching the drawer panel's convention).
    // A tick's `top` has to include all of that offset or it aligns
    // against the wrong origin. `oscillogramWrap.offsetHeight` is safe to
    // read here even before its tiles load: the template reserves its
    // real final height inline (`style="height: ...px"`, from the same
    // server-known number the tiles themselves use), so it doesn't
    // collapse to 0 while its images are still `hidden` the way it would
    // without that reservation. It's already zoomed by `fitDetailHeight`
    // (a real rendered/layout size, not a native one), so it needs no
    // further scaling here -- only the native `pxPerKhz` term below does.
    const spectrogramTop = timeAxis.offsetHeight + oscillogramWrap.offsetHeight;
    for (let khz = 0; khz <= maxFreqKhz; khz += tickKhz) {
      const tick = document.createElement("span");
      tick.className = "detail-axis-tick detail-axis-tick-freq";
      // Row 0 (top) is the highest frequency -- render_spectrogram flips
      // vertically so low frequencies sit at the bottom, matching every
      // other spectrogram viewer's convention (media/spectrogram.py).
      tick.style.top = `${spectrogramTop + (maxFreqKhz - khz) * pxPerKhz * currentScale}px`;
      tick.textContent = `${khz}kHz`;
      freqAxis.appendChild(tick);
    }
  }

  const canBuildTimeAxis = timeAxis && !Number.isNaN(durationS) && !Number.isNaN(pxPerMs);
  const canBuildFreqAxis = freqAxis && !Number.isNaN(maxFreqKhz) && !Number.isNaN(pxPerKhz);

  // View lock (Janna, 2026-09-04: "a tool that locks the current field of view: no scrolling
  // any more; also limits playback to that field of view -- stops playing at the end of the
  // view"). An independent toggle, not a third exclusive tool alongside Default/Ruler -- both
  // stay usable while locked (design decision 2026-09-04), only scrolling and the playback
  // boundary are affected.
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

  function nativeXPxToTimeS(nativeXPx) {
    return nativeXPx / pxPerMs / 1000;
  }

  // Re-derives `scrollEl.scrollLeft` from `lockedStartS` and the CURRENT `currentScale` --
  // `scrollLeft` still works programmatically even while every USER-driven way of changing it
  // (wheel, scrollbar drag, keyboard) is being blocked below.
  function applyLockedScrollPosition() {
    if (!viewLocked || lockedStartS === null) return;
    scrollEl.scrollLeft = lockedStartS * 1000 * pxPerMs * currentScale;
  }

  function currentViewEndTimeS() {
    const visibleRightNativePx = (scrollEl.scrollLeft + scrollEl.clientWidth) / currentScale;
    return nativeXPxToTimeS(visibleRightNativePx);
  }

  // Tighter boundary watch, `requestAnimationFrame`-driven (up to ~60 checks/second) rather
  // than the `timeupdate` event the cursor-drawing code below also uses for the same check.
  // `timeupdate` only fires a handful of times a second and the browser doesn't guarantee a
  // fixed interval, so between two ticks real playback keeps going before JS gets a chance to
  // pause it -- how far past the boundary it gets depends on that (variable) gap, which is
  // exactly why it sometimes overran further than other times (Janna, 2026-09-04, live use:
  // "in some rare cases plays just a little bit longer"). rAF can't make this perfectly
  // sample-accurate (nothing JS-driven against an <audio> element can be -- there's no
  // "pause at this exact sample" API), but polling ~15x more often than `timeupdate` typically
  // does substantially tightens and evens out the overrun. The `timeupdate` handler's own
  // boundary check stays in place below as a harmless fallback (pausing an already-paused
  // element is a no-op) for whatever this loop might miss -- e.g. a tab backgrounded, where
  // browsers throttle `requestAnimationFrame` but not `timeupdate`.
  let boundaryWatchHandle = null;

  function stopBoundaryWatch() {
    if (boundaryWatchHandle !== null) {
      cancelAnimationFrame(boundaryWatchHandle);
      boundaryWatchHandle = null;
    }
  }

  function stepBoundaryWatch() {
    boundaryWatchHandle = null;
    if (!viewLocked || audio.paused) return;
    const spectrogramTimeS = audio.currentTime / audioControls.getTimeExpansionFactor();
    const viewEndS = currentViewEndTimeS();
    if (spectrogramTimeS >= viewEndS) {
      const xPx = viewEndS * 1000 * pxPerMs;
      cursor.style.left = `${xPx}px`;
      cursor.hidden = false;
      audio.pause();
      return;
    }
    boundaryWatchHandle = requestAnimationFrame(stepBoundaryWatch);
  }

  // Covers every way playback can start while locked: the ▶ button, a spectrogram click, and
  // engaging Lock View itself while already playing (the click handler below calls this too).
  function startBoundaryWatchIfNeeded() {
    if (viewLocked && !audio.paused && boundaryWatchHandle === null) {
      boundaryWatchHandle = requestAnimationFrame(stepBoundaryWatch);
    }
  }
  audio.addEventListener("play", startBoundaryWatchIfNeeded);
  audio.addEventListener("pause", stopBoundaryWatch);
  audio.addEventListener("ended", stopBoundaryWatch);

  function relayout() {
    fitDetailHeight();
    if (canBuildTimeAxis) buildTimeAxis();
    if (canBuildFreqAxis) buildFreqAxis();
    applyLockedScrollPosition();
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
  let rulerBox = null;

  function clearRulerBox() {
    if (rulerBox) {
      rulerBox.remove();
      rulerBox = null;
    }
    rulerReadout.hidden = true;
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
    const startX = (dragStart.x - rect.left) / currentScale;
    const startY = (dragStart.y - rect.top) / currentScale;
    const curX = (event.clientX - rect.left) / currentScale;
    const curY = (event.clientY - rect.top) / currentScale;

    const left = Math.min(startX, curX);
    const top = Math.min(startY, curY);
    const width = Math.abs(curX - startX);
    const height = Math.abs(curY - startY);

    const deltaTMs = Math.abs(curX - startX) / pxPerMs;
    const deltaFKhz = Math.abs(curY - startY) / pxPerKhz;
    // Pulse-repetition-rate reading (bat-call literature convention: inter-pulse interval -> Hz,
    // the same relationship as note-duration -> BPM). A purely vertical drag has deltaTMs === 0
    // -- guard the division rather than showing Infinity.
    const hzText = deltaTMs > 0 ? `${(1000 / deltaTMs).toFixed(1)} Hz` : "—";

    if (!rulerBox) {
      rulerBox = document.createElement("div");
      rulerBox.className = "ruler-box";
      wrap.appendChild(rulerBox);
    }
    rulerBox.style.left = `${left}px`;
    rulerBox.style.top = `${top}px`;
    rulerBox.style.width = `${width}px`;
    rulerBox.style.height = `${height}px`;

    // The Δt/Δf readout is a SEPARATE, `position: fixed` element (`#ruler-readout`, styled like
    // `#crosshair-readout`) rather than a span nested inside `rulerBox` -- nesting it there
    // would put it inside `wrap`'s zoomed subtree, shrinking the text down to unreadable sizes
    // right along with the image whenever `fitDetailHeight` has scaled the page down (Janna,
    // 2026-09-04, live screenshot). `rect.left/top + local-px * currentScale` converts the
    // ruler box's local (unzoomed) top-left corner back to real screen coordinates -- the box
    // outline itself stays fine to visually shrink with the image it's measuring; only the
    // number readout needs to stay full-size.
    rulerReadout.textContent = `Δt: ${deltaTMs.toFixed(2)} ms (${hzText})\nΔf: ${deltaFKhz.toFixed(1)} kHz`;
    rulerReadout.style.left = `${rect.left + left * currentScale}px`;
    rulerReadout.style.top = `${rect.top + top * currentScale}px`;
    rulerReadout.hidden = false;
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
    clearRulerBox();
  }

  toolButtons.forEach((btn) => {
    btn.addEventListener("click", () => setActiveTool(btn.dataset.tool));
  });
  setActiveTool("default");

  const viewLockToggle = document.getElementById("view-lock-toggle");
  viewLockToggle.addEventListener("click", () => {
    viewLocked = !viewLocked;
    viewLockToggle.setAttribute("aria-pressed", viewLocked ? "true" : "false");
    viewLockToggle.textContent = viewLocked ? "🔒 Lock view" : "🔓 Lock view";
    scrollEl.classList.toggle("view-locked", viewLocked);
    if (viewLocked) {
      lockedStartS = nativeXPxToTimeS(scrollEl.scrollLeft / currentScale);
      startBoundaryWatchIfNeeded(); // covers locking while already mid-playback
    } else {
      lockedStartS = null;
      stopBoundaryWatch();
    }
  });

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
      if (viewLocked) event.preventDefault();
    },
    { passive: false },
  );
  scrollEl.addEventListener("scroll", () => {
    if (viewLocked) applyLockedScrollPosition();
  });

  const tools = {
    default: {
      onClick(event) {
        const rect = wrap.getBoundingClientRect();
        const xPx = (event.clientX - rect.left) / currentScale;
        const spectrogramTimeS = xPx / pxPerMs / 1000;
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
        if (viewLocked) return;
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
        tools[activeTool].onDragEnd();
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
      return;
    }
    const timeS = xPx / pxPerMs / 1000;
    const freqKhz = maxFreqKhz - yPx / pxPerKhz;
    readout.textContent = `${timeS.toFixed(3)} s\n${freqKhz.toFixed(1)} kHz`;
    readout.style.left = `${event.clientX + 12}px`;
    readout.style.top = `${event.clientY - 12}px`;
    readout.hidden = false;
  });
  wrap.addEventListener("mouseleave", () => {
    readout.hidden = true;
  });

  // Playback cursor (design spec section 4 + "Future slots"): tracks
  // <audio>'s currentTime, snaps the scroll container into view only when
  // the cursor goes off-screen -- no continuous auto-follow by default.
  audio.addEventListener("timeupdate", () => {
    const spectrogramTimeS = audio.currentTime / audioControls.getTimeExpansionFactor();

    // Checked before the cursor is drawn, using whatever the view currently shows (see the
    // view-lock section above for why this is live rather than a stored value) -- stops
    // playback the instant it plays past the locked view's right edge.
    //
    // `displayTimeS` clamps the DRAWN position to that edge on this exact tick, rather than
    // drawing the raw (past-the-edge) `spectrogramTimeS` or skipping the draw outright. Skipping
    // it (an earlier version of this code did, via an early `return` here before ever reaching
    // the cursor-drawing lines below) left the cursor visually frozen at wherever the PREVIOUS
    // tick had drawn it -- up to one `timeupdate` interval's worth of playback short of the true
    // edge, which on a narrow locked view can be a large fraction of what's even on screen
    // (Janna, 2026-09-04, live use: "playback ... visually seems to stop waaay before the end of
    // the current view"). Clamping instead means the very last frame drawn is always the edge
    // itself, matching where playback actually stopped.
    const locked = viewLocked;
    const viewEndS = locked ? currentViewEndTimeS() : null;
    const pastEdge = locked && viewEndS !== null && spectrogramTimeS >= viewEndS;
    const displayTimeS = pastEdge ? viewEndS : spectrogramTimeS;

    // `xPx` is in `wrap`'s local/unzoomed coordinate space (pxPerMs is a native, unscaled
    // constant) -- correct as-is for positioning `cursor` (a descendant of `wrap`, so `wrap`'s
    // own `zoom` re-scales it visually automatically).
    const xPx = displayTimeS * 1000 * pxPerMs;
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
    if (!viewLocked && (visualXPx < visibleLeft || visualXPx > visibleRight)) {
      scrollEl.scrollLeft = Math.max(0, visualXPx - scrollEl.clientWidth / 2);
    }
  });
});

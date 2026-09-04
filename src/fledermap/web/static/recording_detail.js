// src/fledermap/web/static/recording_detail.js
//
// Client-side interactions for the recording details page (design spec
// 2026-09-01-fledermap-recording-details-page-design.md, section 4):
// click-to-play, a ported crosshair readout, a playback-position cursor
// that snaps into view when it scrolls off-screen, and the dense
// fixed-interval axis gridlines the drawer's fixed 3-label axis doesn't
// have room for. Pan is native browser scrolling -- nothing to build.
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
  const scrollEl = document.getElementById("detail-scroll");
  const timeAxis = document.getElementById("detail-axis-time");
  const freqAxis = document.getElementById("detail-axis-freq");

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
  // The <audio> element plays the x10 time-expanded preview (media/preview.py) -- its
  // currentTime is on THAT clock, not the spectrogram/oscillogram's native-real-time locked
  // scale. 1s of expanded playback is 1 / timeExpansionFactor real seconds: divide by this
  // factor to go from audio.currentTime to spectrogram-time, multiply to go the other way.
  const timeExpansionFactor = parseFloat(firstTile.dataset.timeExpansionFactor);

  // Dense axis (design spec section 3): fixed ms/kHz intervals, built from
  // the exact same data attributes the crosshair/cursor math below uses --
  // the labels can never drift out of sync with the actual rendered scale.
  // 50ms was too sparse to be useful at the current 19px/ms scale (~950px
  // between labels -- one or two per screen at typical zoom); 20ms lands a
  // label roughly every 380px, still with comfortable room for the "0.02s"-
  // style text to not collide with its neighbours.
  const TIME_TICK_MS = 20;
  const FREQ_TICK_KHZ = 10;

  function buildTimeAxis() {
    timeAxis.innerHTML = "";
    const totalMs = durationS * 1000;
    // The mirror image of the ms===0 case below: the LAST tick drawn is `TIME_TICK_MS`-aligned,
    // so it lands a few ms (and therefore a few px) short of the recording's true end -- close
    // enough that a centered label's bled-right half can still stick out past the actual
    // rendered image content on the right, widening `.detail-scroll`'s scrollable range with a
    // sliver of empty background past the real content (found 2026-09-03 on a real recording,
    // visible once the sticky freq axis fix above let scrolling reach all the way to the end).
    const lastMs = Math.floor(totalMs / TIME_TICK_MS) * TIME_TICK_MS;
    for (let ms = 0; ms <= totalMs; ms += TIME_TICK_MS) {
      const tick = document.createElement("span");
      tick.className = "detail-axis-tick detail-axis-tick-time";
      tick.style.left = `${ms * pxPerMs}px`;
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
    // without that reservation.
    const spectrogramTop = timeAxis.offsetHeight + oscillogramWrap.offsetHeight;
    for (let khz = 0; khz <= maxFreqKhz; khz += FREQ_TICK_KHZ) {
      const tick = document.createElement("span");
      tick.className = "detail-axis-tick detail-axis-tick-freq";
      // Row 0 (top) is the highest frequency -- render_spectrogram flips
      // vertically so low frequencies sit at the bottom, matching every
      // other spectrogram viewer's convention (media/spectrogram.py).
      tick.style.top = `${spectrogramTop + (maxFreqKhz - khz) * pxPerKhz}px`;
      tick.textContent = `${khz}kHz`;
      freqAxis.appendChild(tick);
    }
  }

  if (timeAxis && !Number.isNaN(durationS) && !Number.isNaN(pxPerMs)) buildTimeAxis();
  if (freqAxis && !Number.isNaN(maxFreqKhz) && !Number.isNaN(pxPerKhz)) buildFreqAxis();

  revealWhenAllLoaded(spectrogramTiles, spectrogramLoading);
  revealWhenAllLoaded(oscillogramTiles, oscillogramLoading);

  // Tool switching (design spec 2026-09-04-fledermap-recording-detail-tools-design.md):
  // `tools[activeTool]` implements onClick/onDrag/onDragEnd for whichever tool is selected.
  // Click-to-play, crosshair, and the playback cursor all still measure against `wrap`'s own
  // bounding rect (the tiled row's container) rather than a single `<img>`'s -- the tiles sit
  // edge-to-edge with no gaps, so the container's rect spans exactly the full locked-scale
  // width, same as the single image did before tiling.
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
  }

  toolButtons.forEach((btn) => {
    btn.addEventListener("click", () => setActiveTool(btn.dataset.tool));
  });
  setActiveTool("default");

  const tools = {
    default: {
      onClick(event) {
        const rect = wrap.getBoundingClientRect();
        const xPx = event.clientX - rect.left;
        const spectrogramTimeS = xPx / pxPerMs / 1000;
        audio.currentTime = spectrogramTimeS * timeExpansionFactor;
        audio.play();
      },
      onDrag(event, dragStart) {
        scrollEl.scrollLeft = dragStart.scrollLeft - (event.clientX - dragStart.x);
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
    const dragStart = { x: event.clientX, y: event.clientY, scrollLeft: scrollEl.scrollLeft };
    let dragging = false;

    function onMove(moveEvent) {
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
    const xPx = event.clientX - rect.left;
    const yPx = event.clientY - rect.top;
    if (xPx < 0 || yPx < 0 || xPx > rect.width || yPx > rect.height) {
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
    const spectrogramTimeS = audio.currentTime / timeExpansionFactor;
    const xPx = spectrogramTimeS * 1000 * pxPerMs;
    cursor.style.left = `${xPx}px`;
    cursor.hidden = false;

    const visibleLeft = scrollEl.scrollLeft;
    const visibleRight = visibleLeft + scrollEl.clientWidth;
    if (xPx < visibleLeft || xPx > visibleRight) {
      scrollEl.scrollLeft = Math.max(0, xPx - scrollEl.clientWidth / 2);
    }
  });
});

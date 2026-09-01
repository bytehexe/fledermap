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
  const spectrogramImg = document.getElementById("detail-spectrogram");
  if (!spectrogramImg) return; // missing duration/samplerate metadata -- no locked-scale render

  const spectrogramLoading = document.getElementById("spectrogram-loading");
  const oscillogramImg = document.getElementById("detail-oscillogram");
  const oscillogramLoading = document.getElementById("oscillogram-loading");
  const wrap = document.querySelector(".detail-spectrogram-wrap");
  const cursor = document.getElementById("playback-cursor");
  const readout = document.getElementById("crosshair-readout");
  const audio = document.getElementById("detail-audio");
  const scrollEl = document.getElementById("detail-scroll");
  const timeAxis = document.getElementById("detail-axis-time");
  const freqAxis = document.getElementById("detail-axis-freq");

  // Reveal-on-load (design spec section 3): there is no cache-hit fast path
  // here -- every visit renders fresh -- so the "Rendering…" placeholder
  // stays up until each image's own `load` event actually fires.
  spectrogramImg.addEventListener("load", () => {
    spectrogramLoading.hidden = true;
    spectrogramImg.hidden = false;
  });
  oscillogramImg.addEventListener("load", () => {
    oscillogramLoading.hidden = true;
    oscillogramImg.hidden = false;
  });

  const durationS = parseFloat(spectrogramImg.dataset.durationS);
  const maxFreqKhz = parseFloat(spectrogramImg.dataset.maxFreqKhz);
  const pxPerMs = parseFloat(spectrogramImg.dataset.pxPerMs);
  const pxPerKhz = parseFloat(spectrogramImg.dataset.pxPerKhz);

  // Dense axis (design spec section 3): fixed ms/kHz intervals, built from
  // the exact same data attributes the crosshair/cursor math below uses --
  // the labels can never drift out of sync with the actual rendered scale.
  const TIME_TICK_MS = 50;
  const FREQ_TICK_KHZ = 10;

  function buildTimeAxis() {
    timeAxis.innerHTML = "";
    const totalMs = durationS * 1000;
    for (let ms = 0; ms <= totalMs; ms += TIME_TICK_MS) {
      const tick = document.createElement("span");
      tick.className = "detail-axis-tick detail-axis-tick-time";
      tick.style.left = `${ms * pxPerMs}px`;
      tick.textContent = `${(ms / 1000).toFixed(2)}s`;
      timeAxis.appendChild(tick);
    }
  }

  function buildFreqAxis() {
    freqAxis.innerHTML = "";
    // `.detail-axis-freq` and `.detail-graphs` are flex siblings that both
    // start at the top of `.detail-body` -- but the spectrogram image
    // itself starts lower than that, below `.detail-axis-time`'s row. A
    // tick's `top` has to include that same offset or it aligns against
    // the wrong origin (verify this against a real screenshot in Step 3 --
    // it's exactly the kind of thing that looks right in the code and
    // wrong on screen).
    const spectrogramTop = timeAxis.offsetHeight;
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

  // Click-to-play (design spec section 4).
  spectrogramImg.addEventListener("click", (event) => {
    const rect = spectrogramImg.getBoundingClientRect();
    const xPx = event.clientX - rect.left;
    audio.currentTime = xPx / pxPerMs / 1000;
    audio.play();
  });

  // Crosshair (design spec section 4) -- simpler than the drawer's own
  // version: no `object-fit: fill` stretch to undo, direct
  // pixel / px-per-unit division.
  wrap.addEventListener("mousemove", (event) => {
    const rect = spectrogramImg.getBoundingClientRect();
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
    const xPx = audio.currentTime * 1000 * pxPerMs;
    cursor.style.left = `${xPx}px`;
    cursor.hidden = false;

    const visibleLeft = scrollEl.scrollLeft;
    const visibleRight = visibleLeft + scrollEl.clientWidth;
    if (xPx < visibleLeft || xPx > visibleRight) {
      scrollEl.scrollLeft = Math.max(0, xPx - scrollEl.clientWidth / 2);
    }
  });
});

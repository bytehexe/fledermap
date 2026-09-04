// src/fledermap/web/static/audio_controls.js
//
// Shared wiring for one `.audio-controls` bar (design spec
// 2026-09-04-fledermap-het-playback-design.md section 3): TE/HET mode
// toggle, a frequency input (HET mode only), rewind-to-start, and
// play/pause -- no progress bar, since click-to-play + a playback cursor
// (each page's own code, calling the `getTimeExpansionFactor()` accessor
// this returns) make it redundant. One function, called once per
// `.audio-controls` instance -- the recording-detail page has exactly one
// on page load; the drawer panel gets a fresh one every htmx swap, so its
// caller (app.js) re-invokes this after every swap rather than once at
// page load.
//
// Remembers the last-chosen mode (TE vs HET) across sessions, the same
// `localStorage` mechanism `_theme_init.html` uses for dark/light mode
// (Janna, 2026-09-04). Deliberately mode only, not a tuned frequency: a raw
// absolute frequency wouldn't transfer between recordings (each has its own
// peak), so HET always starts from THIS recording's own auto-computed peak,
// same as picking HET manually would -- persistence only skips the extra
// click to get there.
const MODE_STORAGE_KEY = "fledermap-audio-mode";
// Auto-tune lands a couple kHz ABOVE the detected peak, not exactly on it
// (Janna, 2026-09-04) -- a real call is usually an FM sweep through the
// peak rather than a steady tone at it, and tuning slightly above keeps
// more of the sweep in the heterodyne's audible difference-frequency band
// instead of mixing straight to a silent near-zero beat at the peak itself.
const AUTO_TUNE_OFFSET_HZ = 2000;

function initAudioControls(container, audioEl, options = {}) {
  // Optional hook so a page with its own View Lock concept (the recording
  // detail page's `recording_detail.js`) can make rewind seek to the
  // locked view's start instead of the file's absolute start (Janna,
  // 2026-09-04: "in this mode the rewind/back-to-start button sets the
  // cursor to the beginning of the view, not the file"). Returns a
  // real-time (not TE-expanded) offset in seconds; the drawer panel has no
  // such concept, so it never passes this and rewind stays file-absolute.
  const getSeekFloorS = options.getSeekFloorS || (() => 0);
  const previewUrl = container.dataset.previewUrl;
  const hetPreviewUrlTemplate = container.dataset.hetPreviewUrlTemplate;
  const peakFrequencyUrl = container.dataset.peakFrequencyUrl;
  const timeExpansionFactor = parseFloat(container.dataset.timeExpansionFactor);

  const teButton = container.querySelector('[data-mode="expanded"]');
  const hetButton = container.querySelector('[data-mode="het"]');
  const freqControl = container.querySelector(".het-freq-control");
  const freqInput = container.querySelector(".het-freq-input");
  const freqReset = container.querySelector(".het-freq-reset");
  const rewindButton = container.querySelector(".playback-rewind");
  const toggleButton = container.querySelector(".playback-toggle");

  let mode = "expanded";
  // Fetched lazily on the first HET switch, then kept for this instance's
  // lifetime (design spec section 2) -- never re-fetched on repeated toggles.
  let peakFrequencyPromise = null;

  function fetchPeakFrequency() {
    if (peakFrequencyPromise) return peakFrequencyPromise;
    peakFrequencyPromise = fetch(peakFrequencyUrl)
      .then((response) => {
        if (!response.ok) throw new Error(`peak-frequency request failed: ${response.status}`);
        return response.json();
      })
      .then((body) => body.peak_frequency_hz)
      .catch((err) => {
        peakFrequencyPromise = null; // allow a retry on the next click, instead of permanently poisoning this instance
        throw err;
      });
    return peakFrequencyPromise;
  }

  // Single source of truth for the toggle button's icon/label, driven by
  // `audioEl.paused` directly rather than trusting only the 'play'/'pause'
  // events to fire -- a mode switch's `setSource()` below calls
  // `audioEl.pause()` immediately followed by reassigning `.src`, and
  // (Janna, 2026-09-04, live use) the browser does not reliably deliver
  // the 'pause' event in that exact sequence, leaving the icon stuck on
  // "playing" after a pause that genuinely happened. Called both from the
  // real audio events below AND explicitly wherever this module itself
  // changes play state, so the icon can never drift from reality.
  function syncToggleIcon() {
    if (audioEl.paused) {
      toggleButton.textContent = "▶"; // play icon
      toggleButton.setAttribute("aria-label", "Play");
    } else {
      toggleButton.textContent = "⏸"; // pause icon
      toggleButton.setAttribute("aria-label", "Pause");
    }
  }

  function effectiveFactor() {
    return mode === "expanded" ? timeExpansionFactor : 1;
  }

  // Real-time (not TE-expanded) position, on whichever clock `mode`
  // currently uses -- call this BEFORE changing `mode`, since mode governs
  // which factor undoes the expansion on the CURRENT `audioEl.currentTime`.
  function currentRealTimeS() {
    return audioEl.currentTime / effectiveFactor();
  }

  // `restoreRealTimeS`, when given, re-applies that real-time position (in
  // the NEW mode's clock, since `mode` has already been updated by the
  // caller) once the new source has metadata to seek against -- switching
  // source/frequency previously always reset to 0, silently dragging the
  // page's cursor and scrolled-into-view position back to the start on
  // every mode or frequency change (Janna, 2026-09-04: "must keep the
  // cursor and ... the current view"). Undefined on initial page load,
  // where `audioEl.currentTime` is already 0 and there's nothing to restore.
  function setSource(url, restoreRealTimeS) {
    audioEl.pause();
    audioEl.src = url;
    syncToggleIcon();
    if (restoreRealTimeS !== undefined) {
      audioEl.addEventListener(
        "loadedmetadata",
        () => {
          audioEl.currentTime = restoreRealTimeS * effectiveFactor();
        },
        { once: true },
      );
    }
  }

  function hetUrlForFreq(freqKhz) {
    return hetPreviewUrlTemplate.replace("FREQ_HZ", String(freqKhz * 1000));
  }

  function switchToTe() {
    const restoreRealTimeS = currentRealTimeS();
    mode = "expanded";
    localStorage.setItem(MODE_STORAGE_KEY, mode);
    teButton.setAttribute("aria-pressed", "true");
    hetButton.setAttribute("aria-pressed", "false");
    freqControl.hidden = true;
    setSource(previewUrl, restoreRealTimeS);
  }

  function switchToHet() {
    const restoreRealTimeS = currentRealTimeS();
    mode = "het";
    localStorage.setItem(MODE_STORAGE_KEY, mode);
    teButton.setAttribute("aria-pressed", "false");
    hetButton.setAttribute("aria-pressed", "true");
    freqControl.hidden = false;
    fetchPeakFrequency().then((freqHz) => {
      if (mode !== "het") return;
      freqInput.value = Math.round((freqHz + AUTO_TUNE_OFFSET_HZ) / 1000);
      setSource(hetUrlForFreq(freqInput.value), restoreRealTimeS);
    });
  }

  teButton.addEventListener("click", switchToTe);
  hetButton.addEventListener("click", switchToHet);

  // Deliberately longer than app.js's/recording_detail.js's own 100ms
  // resize-debounce -- typing a multi-digit frequency fires several input
  // events in quick succession, and each one would otherwise trigger a real
  // server render (design spec section 3).
  const FREQ_DEBOUNCE_MS = 300;
  let freqDebounceTimer = null;
  freqInput.addEventListener("input", () => {
    clearTimeout(freqDebounceTimer);
    freqDebounceTimer = setTimeout(() => {
      if (mode !== "het") return;
      setSource(hetUrlForFreq(freqInput.value), currentRealTimeS());
    }, FREQ_DEBOUNCE_MS);
  });

  freqReset.addEventListener("click", () => {
    const restoreRealTimeS = currentRealTimeS();
    fetchPeakFrequency().then((freqHz) => {
      if (mode !== "het") return;
      freqInput.value = Math.round((freqHz + AUTO_TUNE_OFFSET_HZ) / 1000);
      setSource(hetUrlForFreq(freqInput.value), restoreRealTimeS);
    });
  });

  rewindButton.addEventListener("click", () => {
    // Doesn't change play/pause state -- restarts from the floor mid-playback if
    // already playing, stays paused there otherwise. Clicking exactly the
    // spectrogram's leftmost pixel to seek to the very start is a fiddly,
    // thin target (design spec section 3), worse on the drawer's smaller,
    // compressed scale than the detail page's.
    //
    // The floor is normally 0 (file start); a locked view (see
    // `getSeekFloorS` above) moves it to the view's own start instead. It's
    // real-time seconds, so it needs the same TE-expansion conversion the
    // spectrogram-click seek in recording_detail.js already applies.
    audioEl.currentTime = getSeekFloorS() * effectiveFactor();
  });

  toggleButton.addEventListener("click", () => {
    if (audioEl.paused) {
      // The HTMLMediaElement spec resets `currentTime` to 0 as part of
      // `play()` itself whenever `ended` is true (Janna, 2026-09-04: "once
      // the cursor reaches the end of the file, click to play ... always
      // plays from the start") -- observed live: playing a recording to
      // its natural end, then clicking ▶ again, silently jumped to 0
      // instead of resuming (there's nothing to resume TO once truly
      // ended, but jumping to absolute 0 ignores a locked view's floor).
      // Seeking off the exact end position first -- to the same floor
      // `getSeekFloorS` already defines for rewind -- clears `ended`
      // before `play()` ever sees it, so this situation never reaches
      // that reset.
      if (audioEl.ended) audioEl.currentTime = getSeekFloorS() * effectiveFactor();
      audioEl.play();
    } else {
      audioEl.pause();
    }
  });
  ["play", "pause", "ended"].forEach((eventName) => {
    audioEl.addEventListener(eventName, syncToggleIcon);
  });

  if (localStorage.getItem(MODE_STORAGE_KEY) === "het") {
    switchToHet();
  } else {
    switchToTe();
  }

  return {
    getTimeExpansionFactor: effectiveFactor,
  };
}

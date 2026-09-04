# Fledermap HET Playback — Design

**Status:** draft — sections approved individually in chat during brainstorming; awaiting the
user's review of this written spec (see brainstorming skill's user-review gate) before writing
an implementation plan.
**Date:** 2026-09-04

## Problem

Obsidian backlog, v1-tagged: *"Auto HET Playback, manual HET playback (UI: 1:10/HET/Auto-HET
selector; spinner for the freq; play)"* and *"Recording detail page: HET playback controls row
near the audio player (per backlog's plantuml sketch)"*. Both pages currently only offer the
x10 time-expanded preview (`media/preview.py`) via a native `<audio controls>` element — the
classic heterodyne technique (mix the ultrasonic signal down to audible range around a tunable
frequency, rather than slowing the whole recording down) doesn't exist yet.

The backlog's own note asks *"what is the correct name for that"* for the 1:10 mode: it's
**Time Expansion (TE)**, the standard bioacoustics/bat-detector term for this exact technique —
one of the three classic bat-detector output types alongside Heterodyne (HET) and Frequency
Division (FD). Used as the mode label below.

The backlog's own notes ask *"HET Playback would be useful in both the overview and the details
page"* — decided in brainstorming to do both together now, since unifying their audio controls
is itself part of the design (see Goals).

## Goals

- A new heterodyne-preview renderer (`media/heterodyne.py`, mirroring `preview.py`'s pure,
  ffmpeg-shells-out shape) and a route serving it at an arbitrary tuned frequency, rendered
  fresh per request.
- A "peak frequency" endpoint giving a sensible starting tune value, computed independently of
  the spectrogram's own display parameters.
- **Both pages** (recording-detail page, drawer panel) get the same custom audio control bar,
  replacing the native `<audio controls>` element: a TE/HET mode toggle, a frequency input
  (HET mode only), and rewind-to-start + play/pause buttons — no progress bar, since
  click-to-play + a playback cursor make it redundant.
- **The drawer panel gains click-to-play and a playback cursor** for the first time (today
  it's detail-page-only) — needed for the control-bar unification above to make sense there too.

## Non-goals

- **No caching of rendered HET audio**, for either the auto-computed or a manually-tuned
  frequency. Spiked against a real 30s field recording: ~0.29s DSP + ~0.16s Opus encode, ~0.45s
  end-to-end — order-of-magnitude cheaper than the spectrogram-tile cost `render-cost-cache`
  just optimized, so caching isn't earning its complexity here. Revisit if real usage shows
  otherwise (Janna, 2026-09-04) — the `render_cache.py` pattern from that work is the template
  to reach for if it does.
- **No live/real-time tunable-while-listening playback** (a genuine analog heterodyne detector's
  dial-sweep experience). Server-rendered files swapped on change instead, matching every other
  derived-media artifact in this codebase — not real-time-tunable, but far simpler, and
  consistent with the existing architecture rather than introducing browser-side real-time DSP
  (Web Audio API) with no precedent here.
- **No batdetect2-aware tuning** (peak-of-detected-calls, per-species tuning) — batdetect2
  doesn't exist as a classifier yet; those stay their own future backlog items exactly as
  already noted there.
- **No preserved playback position across a mode switch.** Switching between TE and HET stops
  playback — they're different underlying audio files. Revisit only if this proves annoying in
  practice.
- **No Ruler-style tool system for the drawer.** Just click-to-play + cursor, mirroring the
  detail page's *Default* tool — not the full tool-switching toolbar (Ruler doesn't make sense
  on the drawer's compressed, non-zoomable overview scale).

## Design

### 1. `media/heterodyne.py` — pure renderer

Two functions, no DB/queue awareness, matching `preview.py`'s module shape:

- **`compute_peak_frequency_hz(wav_path: Path) -> float`** — Welch power spectral density
  (`scipy.signal.welch`) over the whole file, returns the frequency of maximum power WITHIN a
  bounded search window, not the raw argmax over the full 0 Hz-Nyquist range. Janna, 2026-09-04:
  a real recording's low end (below ~10kHz) often carries handling/wind noise that would
  otherwise dominate the peak and mask the actual call -- rather than a real bandpass filter
  (a bigger, separate design question, and the same one the already-planned `fledermap.noise`
  classifier/denoising backlog items exist for), this reuses the SEARCH bound this codebase
  already documents for the same reason (`CLAUDE.md`'s "Noise" backlog notes: *"data below
  10 kHz is usually not helpful but sometimes loud"*) -- bins below ~10kHz and above
  `SpectrogramParams.max_freq_hz`'s 128kHz ceiling are excluded from the argmax, not filtered
  out of the signal itself. The computed value is directly visible to the user (pre-filled into
  the frequency spinner in HET/auto mode), so a wrong pick is easy to catch by ear against real
  recordings once this ships -- not a silent, unverifiable heuristic.
  Deliberately independent of `SpectrogramParams`/`render_full_spectrogram_image`'s own STFT —
  changing the spectrogram's display tuning (window/overlap, chosen for visualization) must
  never silently change what HET calls "the peak frequency" (chosen for audibility).
- **`render_heterodyne_preview(wav_path: Path, out_path: Path, *, tune_freq_hz: float) -> None`**
  — the actual DSP: `samples * cos(2*pi*tune_freq_hz*t)` (mix with a local oscillator), a
  low-pass filter (`scipy.signal.butter` + `sosfiltfilt`, cutoff ~20kHz — keeps the audible
  difference-frequency component, rejects the near-`2*tune_freq_hz` sum component the mix also
  produces), `scipy.signal.resample_poly` down to a standard 48kHz output rate, then the same
  temp-WAV-relabel + `ffmpeg -c:a libopus` + atomic-replace pipeline `preview.py` already uses
  (extract that pipeline into a small shared helper both modules call, rather than a second
  copy — the exact "second copy drifts silently" risk this codebase's own docstrings warn
  about elsewhere).

### 2. Routes (`web/views/media.py`)

- **`GET /recordings/<audio_hash>/het-preview.opus?freq_hz=<float>`** and
  **`GET /recordings/<audio_hash>/peak-frequency`** both resolve the recording via
  `resolve_recording`/`resolve_wav_path` (`services/media.py`, already used elsewhere in this
  module) directly, NOT `_detail_tile_context` — that helper also requires
  `duration_s`/`samplerate_hz` to be set, a real requirement for computing tile boundaries that
  doesn't apply here (HET plays the whole file, nothing is tiled). Requiring it anyway would
  incorrectly block HET playback on metadata it doesn't actually need. Both 404 the same way
  `detail_spectrogram`/`detail_oscillogram` do for an unknown hash / missing source file /
  unreadable WAV; `het-preview.opus` renders fresh via `_serve_temp_render` (already exists,
  not part of the cached-derived-media system); `freq_hz` missing or non-numeric is a 400.
- **`peak-frequency`** returns `compute_peak_frequency_hz`'s
  result (plain JSON, `{"peak_frequency_hz": ...}`). Fetched by JS **lazily, only the first
  time a page/panel switches to HET mode** — not baked into every page/panel render. The
  drawer panel in particular is fetched on every single map click; most of those never open
  HET, so this keeps that hot path exactly as cheap as it is today. The fetched value is kept
  in a JS variable for that page's lifetime (not re-fetched on repeated mode toggles).

### 3. Control bar markup + behavior (both pages)

Replaces the native `<audio controls>` in `recording_details.html` and `_recording_panel.html`
with, e.g.:

```html
<div class="audio-controls">
  <button class="tool-button" data-mode="expanded" aria-pressed="true" title="Time Expansion (×10)">TE</button>
  <button class="tool-button" data-mode="het" aria-pressed="false">HET</button>
  <span class="het-freq-control" hidden>
    <input type="number" class="het-freq-input" step="1" inputmode="decimal"> kHz
    <button type="button" class="het-freq-reset" title="Reset to peak frequency">⟳</button>
  </span>
  <button type="button" class="playback-rewind" aria-label="Rewind to start">⏮</button>
  <button type="button" class="playback-toggle" aria-label="Play">▶</button>
  <audio hidden></audio>
</div>
```

Reuses `.tool-button` styling from the detail-page toolbar for the mode buttons (same visual
language, no new button style needed).

- **Mode switch → TE:** `.het-freq-control` hidden, `<audio>`'s `src` set to the existing
  `preview.opus` route, unchanged from today.
- **Mode switch → HET (first time this page/panel instance):** fetch `/peak-frequency`,
  populate `.het-freq-input`, show `.het-freq-control`, set `<audio>`'s `src` to
  `het-preview.opus?freq_hz=<value>`.
- **Frequency input changes** (debounced ~300ms — deliberately longer than
  `recording_detail.js`'s existing 100ms resize-debounce, since a person typing a multi-digit
  frequency fires several input events in quick succession and each one triggers a real
  server render; a resize's own debounce target is a much lower-frequency event): rebuild the
  `het-preview.opus` URL with the new `freq_hz` and reassign `<audio>.src` — same "swap the
  file" mechanism as a mode switch, just within HET mode.
- **`.het-freq-reset` click:** re-fetch (or reuse the cached) peak frequency, reset the input
  and reload the audio.
- **`.playback-rewind` click:** `<audio>.currentTime = 0` on whichever `<audio>` is currently
  active, and (per section 4 below) snaps the playback cursor back to the start. Doesn't change
  play/pause state — restarts from 0 mid-playback if already playing, stays paused at 0
  otherwise. Added because clicking exactly the spectrogram's leftmost pixel to seek to the
  very start is a fiddly, thin target (Janna, 2026-09-04) — worse on the drawer's smaller,
  compressed scale than the detail page's.
- **`.playback-toggle` click:** `<audio>.play()`/`.pause()`, icon/label reflects state via the
  existing `play`/`pause`/`ended` events.
- **Any mode/frequency change stops playback** (`<audio>.pause()` before swapping `src`) —
  see Non-goals.

### 4. Click-to-play + cursor, unified across both pages

The detail page's existing *Default* tool behavior (`recording_detail.js`) already does
click-to-play against its own locked scale. This spec:

- **Adds the same to the drawer**, in `app.js`, delegated on `#drawer-body` (its content is
  htmx-swapped wholesale, same reasoning as the existing delegated crosshair listener right
  above it) — click position converts to time via the same `relX * durationS` the crosshair
  readout there already computes, and drives whichever `<audio>` is currently active (per the
  control bar's mode state) rather than a hardcoded preview.
- **Time-mapping differs by mode**: TE divides/multiplies by `TIME_EXPANSION_FACTOR` exactly
  as today; HET plays in real time (its rendered audio is NOT time-expanded), so click-time
  maps directly to `audio.currentTime` with no factor.
- A playback cursor overlay (matching `#playback-cursor`'s existing detail-page behavior, one
  new element per page) tracks whichever mode is active, on each page's own scale (drawer:
  compressed/fixed small width; detail: the locked 1:1 scale it already uses).

## Testing

- `media/heterodyne.py`: real unit tests, no Flask/DB (mirrors `test_preview.py`'s shape). Key
  correctness check: mix a synthesized tone at a known frequency `f`, render with
  `tune_freq_hz == f`, FFT the output and assert its strongest component sits near 0 Hz (a
  correctly-tuned heterodyne mix produces a near-DC beat when the tune frequency matches the
  source exactly); mistuned by a known delta `d` should show the strongest component near `d`.
  `compute_peak_frequency_hz` gets its own test against a synthesized single-tone file,
  asserting the returned frequency is close to the known tone frequency.
- Routes: db-marked, matching `test_media_view.py`'s existing patterns for the detail-tile
  routes — 200 with correct content-type for a valid call, `freq_hz` actually reaching the
  renderer, 400 for a missing/non-numeric `freq_hz`, 404 for unknown hash / missing source file
  / unreadable WAV (`UnreadableWavError`, same handling as the other routes in this module).
- JS: no test infrastructure exists for this project yet (separate open backlog item) — stays
  out of scope here, consistent with every other JS change so far.

# Fledermap Denoise/Highpass — Design

**Status:** shipped 2026-09-14
**Date:** 2026-09-14

## Problem

Real field recordings (Echo Meter Touch, per the Obsidian backlog and CLAUDE.md's "Sample data —
not representative"/"real field recordings" notes) carry a persistent low-frequency noise band,
observed roughly 0–10 kHz, that:

- makes the spectrogram harder to read: the noise band's own peaks can dominate the
  loudest-bin-relative dB normalization (`spectrogram.py`'s `render_full_spectrogram_image`),
  making the whole image darker than the actual call content deserves;
- is audible during HET playback ("definitely audible in the HET, probably less so in the TE"
  per the backlog) and can mask a call by ear;
- is visible as a low band on the oscillogram envelope.

This spec covers backlog item #1 of the "Denoise/Highpass" topic: a highpass filter plus a
spectrogram-only background-noise reduction, exposed as one toggle on the recording-details page.
Item #2 (an SNR-based `fledermap.noise` classifier) and item #3 (a batdetect2 feasibility spike)
are separate, deferred backlog items — see "Non-goals" below for why they're kept out of this
pass.

## Goals

- A single "Denoise" toggle on the recording-details page's existing `#detail-toolbar` (see "UI
  wiring — placement decision" below for exactly where), matching the `aria-pressed` toggle-button
  pattern already used there.
- When on: a real, zero-phase highpass filter (removes content below a fixed cutoff) applies to
  the spectrogram, the oscillogram, and both TE and HET audio playback — one filtered signal feeds
  all of them, computed once from the same raw samples.
- When on: additionally, a spectrogram-only background-noise subtraction further cleans up the
  spectrogram image (not applied to audio — see "Why highpass and background-subtraction use
  different techniques" below).
- Independent of the toggle, and always on: the spectrogram's color-normalization peak excludes
  the low band, on both the map drawer and the details page. This is a small, separate fix to
  `render_full_spectrogram_image`'s existing `peak = sxx.max()` line, not gated by the toggle
  (the drawer never gets the toggle at all — see "Non-goals").
- One shared cutoff frequency (5 kHz) used by both the always-on peak-exclusion and the toggle's
  highpass filter.

## Non-goals

- **The SNR-based noise classifier** (backlog item #2: `fledermap.noise`, `Noise`/`NoID` sentinel
  identifications, configurable threshold). Different shape of work entirely (classifier
  precedence, DB writes, threshold-tuning), no shared code path with this toggle today. Tracked
  separately in the Obsidian backlog.
- **The batdetect2 spike** (backlog item #3: "should we denoise/highpass first to improve
  detection quality?"). References this work but doesn't block on it; its own spike.
- **An adaptive/per-recording cutoff.** Discussed and explicitly deferred (Janna, 2026-09-14):
  detecting "where does this recording's noise band actually end" is the same problem as the
  noise-classifier's SNR estimation (item #2 above) — building a bespoke version here would
  duplicate that work with a possibly-inconsistent heuristic, and a wrong adaptive guess (pushing
  the cutoff up onto a real low social call) is harder to reason about than a wrong fixed
  constant. **Follow-up note:** once the SNR/noise-classifier work exists, it will already compute
  the per-recording noise-band information an adaptive cutoff would need — revisit this then,
  reusing that infrastructure rather than inventing a parallel detector now.
- **A user-adjustable cutoff control** (slider/input). Decided fixed-for-now (Janna, 2026-09-14) —
  a per-recording adjustable cutoff can follow later once real use shows whether the fixed value
  is ever wrong for a given recording.
- **True adaptive/reconstructable audio noise reduction** (Wiener filtering, spectral gating,
  MMSE). See "Why highpass and background-subtraction use different techniques" below for why
  this is out of scope, not merely deferred for time.
- **The map drawer's overview.** The toggle is details-page-only (backlog: "probably only required
  for the details view"; CLAUDE.md's drawer/detail parity rule allows an exception with a clear
  reason — a rendering-tuning control adds clutter to the drawer's quick-glance size for little
  benefit there). The always-on peak-exclusion fix (a default behavior change, not a toggle) still
  applies to the drawer, since it has no on/off state to gate.

## Why highpass and background-subtraction use different techniques

Both "denoise" the recording, but they don't share one implementation, deliberately:

**Highpass is a real time-domain filter** (scipy `butter` + `sosfiltfilt`, zero-phase). Cheap,
well-behaved, no reconstruction artifacts — safe to apply to something that gets listened to.

**Background-noise subtraction is spectrogram-image-only.** True audio noise reduction via
spectral subtraction needs a complex STFT (magnitude *and* phase) and reconstruction via inverse
STFT using the original (still-noisy) phase — a different pipeline from
`render_full_spectrogram_image`, which uses `scipy.signal.spectrogram` (power only, phase
discarded) and STFT parameters (3ms window, 50% overlap) tuned for visual resolution, not for
COLA-compliant perfect reconstruction. Even done correctly, magnitude-subtract-then-reconstruct is
the textbook source of "musical noise" — audible warbling artifacts that are far more forgivable
in a rendered image (an odd bright pixel) than in something played back (an audible blip). Real
artifact-resistant audio denoising (Wiener filtering, spectral gating, MMSE) is enough DSP
machinery to be its own "well-tested library, not reinvented" case (CLAUDE.md's dependency
guidance) — out of scope here. Discussed and confirmed with Janna, 2026-09-14.

So: highpass reaches audio + both visuals; background-subtraction reaches the spectrogram image
only.

## Algorithm details

New module `media/denoise.py`, pure (no DB/queue awareness, matching every other `media/` module):

- `DEFAULT_CUTOFF_HZ: float = 5000.0` — the one shared constant every caller below defaults to
  (`SpectrogramParams.cutoff_hz`, `OscillogramParams.cutoff_hz`, and the TE/HET routes, which have
  no params dataclass of their own to hold a field). A single definition so the cutoff can't drift
  between callers the way `media/paths.py`'s own docstring warns against for writer/reader path
  formulas.
- `highpass_filter(samples: np.ndarray, samplerate_hz: float, cutoff_hz: float) -> np.ndarray`:
  a Butterworth highpass (`scipy.signal.butter`, SOS form) applied via `scipy.signal.sosfiltfilt`
  for zero-phase output (no time-shift/click artifact). Order 4, a conventional choice balancing
  rolloff steepness against filter-design edge cases at low cutoff-to-Nyquist ratios.
- `subtract_background_noise(sxx: np.ndarray, percentile: float = 50.0, factor: float = 2.0) ->
  np.ndarray`: per frequency-bin (per row), estimate the noise floor as that bin's `percentile`-th
  percentile power across all time columns, subtract `factor` times that floor from every column
  in that row, clamp at zero. Operates on the STFT's linear power matrix — the same `sxx`
  `render_full_spectrogram_image` already computes — before the existing dB-relative-to-peak
  normalization, so that normalization code doesn't change at all, it just receives cleaner input.
  **Deviation (2026-09-14, found live after shipping):** the plan's original `percentile=10.0,
  factor=1.0` (subtract exactly the 10th-percentile floor) shipped and was confirmed live against
  a real field recording to be barely visually distinguishable from no denoising at all — it only
  zeroes the bottom 10% of pixels per row, leaving the noise floor's own variance (what actually
  reads as visual "noise", and sits well above any single low percentile) fully intact. Classic
  spectral-subtraction "over-subtraction" (subtract more than one floor's worth) fixes this:
  percentile=50 (the row's median) + factor=2.0 was chosen by rendering a real field recording
  (`PIPPIP_20260826_221352.wav`) through several percentile/factor combinations side-by-side —
  it visibly cleaned the background while leaving real calls' shape and brightness intact, where
  weaker settings (e.g. percentile=75, factor=1.2) left visibly more residual noise texture.

## Where each piece plugs in

**`SpectrogramParams`** gains two new fields (both real dataclass fields, so `params_hash`
automatically changes the moment this ships and every existing cached render is correctly
invalidated — CLAUDE.md's "every `SpectrogramParams`/`OscillogramParams` field must be something
`params_hash` actually hashes" rule):

- `cutoff_hz: float = DEFAULT_CUTOFF_HZ` — used for the peak-exclusion fix always, and as the
  highpass cutoff when `denoise` is on. Not exposed via any env var/CLI/UI control (see
  "Non-goals").
- `denoise: bool = False`

`render_full_spectrogram_image` changes:
- Peak becomes `sxx[freqs >= params.cutoff_hz].max()` (previously `sxx.max()`) — unconditional,
  not gated by `denoise`. The full `sxx` (including the low band) is still rendered against this
  peak; low-band content normalizes to a deep negative dB and clips to the palette floor, which is
  the intended, accepted effect ("clipping in that band would be acceptable", Janna 2026-09-14) —
  no separate masking/removal of that band from the displayed image.
- If `denoise`: samples are highpass-filtered before the STFT, and `subtract_background_noise`
  runs on `sxx` before the dB step.

**`OscillogramParams`** gains both `cutoff_hz: float = DEFAULT_CUTOFF_HZ` (needed to actually run
the highpass filter, and for the same `params_hash`-invalidation-on-future-change reasoning as
`SpectrogramParams.cutoff_hz`) and `denoise: bool = False`. If `denoise`, samples are
highpass-filtered before computing the peak-envelope. No analog of the spectrogram's
peak-exclusion fix applies here — nothing requested that the oscillogram's own time-domain
amplitude normalization change when `denoise` is off; flagged as an intentionally-unaddressed
parallel, not a silent gap.

**TE/HET audio:**
- `render_heterodyne_preview` gains `denoise: bool` and `cutoff_hz: float = DEFAULT_CUTOFF_HZ`
  parameters — cheap, since HET is already rendered live per request (`web/views/media.py`'s
  `het_preview` route, no disk cache). The route reads a `denoise` query-string arg, mirroring its
  existing `freq_hz`; `cutoff_hz` stays the module default (not caller-supplied) since it isn't a
  UI-exposed control.
- TE is different: the existing `media.preview` route serves a job-queued, disk-cached file keyed
  only by `audio_hash` + the fixed `PREVIEW_VERSION` string — no per-render params dimension
  exists there, and adding one (a new lock key, cache path, job type) would be real new
  infrastructure for a feature most recordings will never have toggled. Instead: **a new,
  details-page-only, live-rendered endpoint** (`/recordings/<hash>/detail-preview.opus?denoise=`),
  built the same way `detail_spectrogram`/`detail_oscillogram`/`het_preview` already render live
  per request via `_serve_temp_render` — no disk cache, no job queue, no new lock key. When the
  toggle is off, the details page keeps using the existing shared cached `media.preview` URL
  exactly as today (zero behavior change, zero added render cost for the common case); only
  turning the toggle on switches the `<audio>` element's source to the new live endpoint.

## UI wiring — placement decision

`#detail-toolbar` already holds Default, Ruler, and `#view-lock-toggle` — a region with 2+
existing elements, so `docs/style-guide.md`'s "decide element placement once, project-wide" rule
requires a mockup and a decision made here, not left to plan-writing.

**Decision:** the Denoise toggle joins `#detail-toolbar`, immediately after `#view-lock-toggle`,
built the same way (an icon-plus-label `<button>`, `aria-pressed` state, `hidden`-swapped
outline/filled icon spans). Not the audio-row (TE/HET/rewind/play): that row is about *which*
signal is playing and playback state, a different concern from `#detail-toolbar`'s existing
grouping of **binary toggles that change how the current view renders** (Lock view) alongside
tool-selection radios (Default/Ruler). Denoise is exactly that kind of rendering-affecting binary
toggle, not a playback control, even though it also happens to reach audio.

```
#detail-toolbar
+----------+----------+------------------+------------------+
| Default  |  Ruler   |  🔓 Lock view    |  denoise-icon Denoise |
+----------+----------+------------------+------------------+
```

No sweep-conflict found elsewhere: no other page has an equivalent toolbar (the map drawer has no
`#detail-toolbar` analog at all, consistent with the toggle being details-page-only per
"Non-goals").

`recording_detail.js` changes:
- `detail_spectrogram`/`detail_oscillogram` tile URLs gain `&denoise=<bool>`.
- HET's URL template (`data-het-preview-url-template`) gains `&denoise=<bool>`.
- TE's audio source swaps between the existing cached `data-preview-url` (toggle off) and the new
  live `detail-preview.opus` endpoint (toggle on).
- Toggling re-fetches/re-renders the currently visible tiles and swaps the audio source,
  mirroring how `#view-lock-toggle`/tool-switching already re-render on state change.

## Testing

- `media/denoise.py`: pure-function tests for `highpass_filter` (asserts low-frequency energy is
  reduced, e.g. via FFT magnitude comparison below/above cutoff) and `subtract_background_noise`
  (a synthetic `sxx` with a known noise floor + a known "call" bin, asserting the floor drops
  toward zero while the call bin survives).
- `spectrogram.py`/`oscillogram.py`: render-level tests asserting `denoise=True` changes output
  pixel statistics vs `denoise=False` on a fixture WAV with synthetic low-frequency noise, and a
  `params_hash` test confirming `cutoff_hz`/`denoise` changes produce a different hash (mirroring
  existing `SpectrogramParams`/`OscillogramParams` hash-changes coverage) — mutation-tested per
  CLAUDE.md's "mutation-test any... assertion" rule.
- `web/views/media.py`: route tests for the new `denoise` query params on
  `detail_spectrogram`/`detail_oscillogram`/`het_preview`, and for the new
  `detail-preview.opus` endpoint (200 with correct content, 404 handling matching existing
  `_resolve_wav_path_or_404` coverage).
- No new JS unit-test surface expected beyond existing `node:test` pure-logic coverage (this is a
  URL-string swap + a toggle, the same shape as existing tool-switching logic already covered) —
  still requires the mandatory headless-Chrome live-verification pass for the new toggle
  (CLAUDE.md's JavaScript tooling section: "mandatory for any task that adds or changes JS").

## Rollout

No migration (no schema change). Existing cached spectrogram/oscillogram files are naturally
invalidated by the `params_hash` change (new fields) the first time this ships — old files are
simply never matched again, no explicit cleanup needed (same pattern `PREVIEW_VERSION` bumps use
for preview.opus, minus the explicit version string since this goes through real dataclass fields
instead).

**This does NOT mean existing recordings' drawer media re-renders on its own.** Checked against
`services/media.py`: `run_ingest_cycle` (the worker's cron/startup job) only calls `enqueue_media`
for *newly*-ingested recordings; backfilling already-ingested recordings' missing/stale media
(`backfill_media`) only runs via the explicit `fledermap enqueue-media` CLI command — nothing
schedules it automatically. Unlike the details page (which live-renders on every request, so it
just works), the drawer's `spectrogram`/`oscillogram` routes 404 rather than rendering on demand.
So every already-ingested recording's drawer spectrogram/oscillogram will show "not processed yet"
after this ships, until `fledermap enqueue-media` is run once. **Deployment step:** run
`fledermap enqueue-media` after deploying this change.

# Fledermap Audio-Domain Denoise — Design

**Status:** draft
**Date:** 2026-09-14

## Problem

The highpass/background-noise work in `docs/superpowers/specs/2026-09-14-fledermap-denoise-highpass-design.md`
shipped a "Denoise" toggle that cleans the *spectrogram image* (a zero-phase highpass below 5kHz,
plus per-frequency-bin background subtraction on the STFT power matrix). Neither of those touches
what the user actually *hears*: HET and TE playback still carry the full 20–120kHz background noise
that's audible in the field recordings this project works with. The backlog's own research spike
(Obsidian `Fledermap.md`, `#Denoise/Highpass` section, 2026-09-14) already scoped the right
technique — spectral gating via STFT → soft per-bin gain → `scipy.signal.istft` — and explicitly
recommended a fresh session for it, since this needs a real design pass and a listening-based QA
loop, unlike the visual-only work.

This spec covers turning that recommendation into a design: a real, phase-coherent audio denoiser
that reaches every place audio is rendered or played back.

## Goals

- Real audio-domain noise reduction, audible in both TE and HET playback, and feeding the
  spectrogram/oscillogram renders too — one denoised signal used everywhere, the same "compute once,
  feed everything" shape the existing highpass filter already has.
- No new UI: the existing single "Denoise" toggle (`#denoise-toggle`, `recording_details.html`)
  gains more effect, rather than a second control appearing next to it.
- Phase-coherent reconstruction (no "musical noise" artifacts) — the actual hard part the original
  non-goal in the highpass spec was avoiding, now solved via `scipy.signal.istft`'s COLA-correct
  reconstruction (Hann window + 50% overlap).
- The existing image-domain background subtraction (`subtract_background_noise`'s over-subtraction
  + edge-preserving median filter) is re-examined now that its input is cleaner — its parameters,
  and even whether the median-filter step is still needed at all, are judged empirically during
  implementation rather than assumed to carry over unchanged.

## Non-goals

- **A dedicated cache for denoised audio.** `highpass_filter` already recomputes per render call
  with no caching, and it's cheap enough that this was never a problem. Spectral gating is heavier
  (a full STFT/ISTFT pass), but `media/render_cache.py`'s existing per-tile cache already absorbs
  the redundant-within-one-page-view cost for the spectrogram's own STFT step. Build a real cache
  only if implementation-time profiling (against the same ~30s/17-tile field recording the
  render-cost backlog item already profiled) shows a meaningful regression from the known ~7.8s
  baseline — not preemptively.
- **A separate toggle for audio-domain denoising.** Decided: one "Denoise" toggle, doing more.
- **Replacing the existing highpass filter.** Spectral gating's noise floor could in principle
  suppress the low band too, but the highpass filter is simple, already verified, and guarantees
  silence below 5kHz regardless of how the gate's noise-floor estimate behaves on a given
  recording. Kept as a distinct first step; spectral gating runs on its output.
- **An adaptive/per-recording noise-floor detector more sophisticated than a percentile.** Same
  reasoning as the highpass spec's own deferred adaptive-cutoff non-goal: this overlaps the future
  SNR/noise-classifier backlog item, which will already compute per-recording noise information a
  smarter estimate could reuse. Revisit then.
- **Wiener/power-domain gain, minimum-statistics tracking, or any other more elaborate
  speech-enhancement-literature technique.** Both were considered (see backlog's method research)
  and are speech-tuned or add complexity without a demonstrated need for this project's short
  recordings and ultrasonic content.

## Algorithm

New `spectral_gate(samples: np.ndarray, samplerate_hz: float) -> np.ndarray` in
`media/denoise.py`, alongside the existing `highpass_filter`/`subtract_background_noise`:

1. **STFT.** `scipy.signal.stft` with a Hann window and 50% overlap (COLA-correct, so
   `scipy.signal.istft` reconstructs exactly on quiet/unmodified bins). Its own `nperseg`,
   independent of `spectrogram.py`'s `SpectrogramParams.window_ms` — that value is tuned for visual
   time/frequency resolution tradeoffs on the details page, not for gating accuracy or clean
   reconstruction, and the two purposes aren't guaranteed to want the same window size. `nperseg`
   is clamped to the signal length (`min(nperseg, len(samples))`), mirroring
   `render_full_spectrogram_image`'s existing guard for a short or truncated/corrupt recording.
2. **Per-bin noise floor.** Each frequency bin's own low percentile of magnitude across all time
   frames in the recording — the same idiom `subtract_background_noise` already uses, applied here
   to the complex STFT's magnitude instead of the power spectrogram. A whole-recording estimate is
   adequate given how short these recordings are (seconds to low minutes); no sliding-window
   tracking.
3. **Soft gain.** For each bin/frame: `gain = clip(1 - noise_floor / magnitude, gain_floor, 1) ** p`.
   This is a soft-threshold spectral-subtraction gain — it approaches 0 well below the floor and 1
   well above it, and never fully mutes a bin because of the `gain_floor` clamp, which is the
   standard fix for musical noise (an occasional bin snapping fully to zero and back, heard as
   chirping artifacts). Applied to the *complex* STFT (gain scales magnitude, phase untouched), not
   the power spectrogram — phase has to survive for `istft` to reconstruct a usable waveform.
4. **ISTFT.** `scipy.signal.istft` on the gated complex STFT reconstructs the denoised waveform.

`percentile`, `gain_floor`, and the exponent `p` are module-level constants (matching
`_HIGHPASS_ORDER`'s existing precedent — not new `SpectrogramParams`/`OscillogramParams` fields).
Starting values aren't fixed by this spec; they get tuned live by ear/eye against a real field
recording during implementation, the same way `subtract_background_noise`'s percentile/factor were
tuned in the prior pass.

## Wiring

Each of the four existing `denoise`-gated call sites applies `spectral_gate` right after
`highpass_filter`, on its output:

- `media/spectrogram.py` — before the STFT that builds the rendered image.
- `media/oscillogram.py` — before the min/max envelope bucketing.
- `media/heterodyne.py` — before the lowpass/frequency-shift step.
- `media/preview.py` — before Opus re-encoding.

No new field is needed anywhere: `SpectrogramParams.denoise`/`OscillogramParams.denoise` and the
plain `denoise: bool` parameters on `heterodyne`/`preview` already gate the *whole* denoise
pipeline, and `params_hash` already hashes `denoise` — adding a processing step inside the existing
`if params.denoise:` block changes what that hash's cached output *means* without needing a new
field to hash (same as when `subtract_background_noise` was added to the existing toggle).

**Order matters:** highpass first, then spectral gate. The highpass filter removes the <5kHz band
outright, so the gate's noise-floor estimate and gain computation never have to fight that band's
usually-dominant energy.

**The image-domain step's fate is not decided here.** `subtract_background_noise` runs on the STFT
built from this now audio-denoised signal, downstream in `spectrogram.py`. Whether its current
parameters (`median_size=(7,7)`, `preserve_threshold=2.0`) still apply, need retuning, or whether
the selective median-filter step is no longer needed at all once real denoised audio feeds it — the
median filter's own known tradeoff (still-visible smudging on calls) may or may not still be
worth its cost — is judged empirically during implementation, by eye against real spectrograms,
not assumed in advance.

## Testing & QA

**Automated** — `tests/test_denoise.py`, same pure-numpy style as the existing `highpass_filter`
tests (no audio libraries, no fixtures):

- A synthetic tone + white-noise signal shows measurably improved SNR after `spectral_gate`.
- A clean synthetic tone (no added noise) survives close to unchanged — the gate shouldn't damage
  real signal it has no reason to suppress.
- A short signal (fewer samples than one STFT window) and a silent (all-zero) signal don't crash,
  mirroring the existing `nperseg` clamp test coverage for `render_full_spectrogram_image`.

**Manual** — mandatory listening-based QA against real field recordings before shipping, done live
during implementation. Screenshots can't catch musical-noise artifacts; this is the same kind of
verification step the original highpass/background-subtraction tuning already went through, just
for audio instead of (or alongside) images. The image-domain question above (keep/shrink/remove the
median filter) is judged in the same live pass, by eye against real spectrograms.

## Spec self-review

- No placeholders/TBDs remain — the one open point (image-domain step's exact fate) is explicitly
  marked as an implementation-time empirical decision, not a gap in the design.
- Consistent with the shipped highpass spec: reuses its toggle, its call-site list, its `denoise`
  boolean, and its percentile-based tuning idiom rather than introducing a parallel approach.
- Scoped to one implementation plan: one new function, four call-site edits, one existing
  function's parameters (or removal) reconsidered — no unrelated refactor pulled in.

# Fledermap Audio-Domain Denoise Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing "Denoise" toggle actually clean the audio itself (not just the
spectrogram image), so HET and TE playback are audibly quieter, via phase-coherent spectral
gating.

**Architecture:** One new pure function, `spectral_gate()`, in `media/denoise.py` — STFT, soft
per-bin gain from a percentile noise-floor estimate, ISTFT reconstruction. Wired into the same
four `denoise`-gated call sites the existing `highpass_filter` already touches
(`spectrogram.py`, `oscillogram.py`, `heterodyne.py`, `preview.py`), applied right after the
highpass filter and before whatever that call site does next.

**Tech Stack:** numpy, `scipy.signal.stft`/`istft` (already a dependency via `scipy.signal.butter`/
`sosfiltfilt`/`spectrogram` elsewhere in `media/`). No new dependency.

**Spec:** `docs/superpowers/specs/2026-09-14-fledermap-audio-denoise-design.md`

## Global Constraints

- No new dataclass fields on `SpectrogramParams`/`OscillogramParams`, and no new CLI/route
  parameter on `heterodyne.py`/`preview.py` — the existing `denoise: bool` (and, on the two
  dataclasses, the existing `params_hash` that already hashes it) continues to gate this whole
  pipeline, per the spec's "Wiring" section.
- Order is always: `highpass_filter` first, then `spectral_gate`, on the same samples array.
- Tuning constants (`_GATE_*` below) are module-level constants in `media/denoise.py`, matching
  `_HIGHPASS_ORDER`'s existing precedent — never new dataclass fields.
- Test output must stay warning-free (project-wide rule) — `spectral_gate`'s STFT window must be
  clamped to the signal length exactly like `render_full_spectrogram_image`'s existing `nperseg`
  clamp, for the same reason.
- No caching of denoised audio in this plan (spec non-goal) — recompute per render call.

---

## Task 1: `spectral_gate()` core algorithm

**Files:**
- Modify: `src/fledermap/media/denoise.py`
- Test: `tests/test_denoise.py`

**Interfaces:**
- Produces: `spectral_gate(samples: np.ndarray, samplerate_hz: float) -> np.ndarray` — takes and
  returns a 1D float array of the same length as `samples`. Pure, no I/O, matching every other
  function in this module.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_denoise.py` (after the existing `subtract_background_noise` tests, keep the
existing imports and add `spectral_gate` to the import list from `fledermap.media.denoise`):

```python
def _pulsed_tone_with_noise(
    rng: np.random.Generator,
    *,
    samplerate: int = 256_000,
    duration_s: float = 0.05,
    tone_freq_hz: float = 45_000.0,
    tone_amplitude: float = 20_000.0,
    noise_std: float = 3_000.0,
) -> tuple[np.ndarray, int, int]:
    """A short, loud tone pulse in the middle third of an otherwise-silent
    signal, plus white noise everywhere -- the same "mostly quiet, one real
    call" shape `subtract_background_noise`'s own tests already use, not a
    sustained tone (see `spectral_gate`'s docstring for why a continuous tone
    is the wrong shape to test this technique against). Returns
    (samples, pulse_start_idx, pulse_end_idx)."""
    n = int(samplerate * duration_s)
    t = np.arange(n) / samplerate
    pulse_start, pulse_end = int(n * 0.4), int(n * 0.6)
    clean = np.zeros(n)
    clean[pulse_start:pulse_end] = tone_amplitude * np.sin(
        2 * np.pi * tone_freq_hz * t[pulse_start:pulse_end]
    )
    noise = rng.normal(0, noise_std, n)
    return clean + noise, pulse_start, pulse_end


def test_spectral_gate_reduces_noise_outside_the_call() -> None:
    rng = np.random.default_rng(1)
    samples, pulse_start, _pulse_end = _pulsed_tone_with_noise(rng)

    gated = spectral_gate(samples, samplerate_hz=256_000)

    rms_before = np.sqrt(np.mean(samples[:pulse_start] ** 2))
    rms_after = np.sqrt(np.mean(gated[:pulse_start] ** 2))
    # Noise-only region must be meaningfully quieter after gating.
    assert rms_after < rms_before * 0.6


def test_spectral_gate_preserves_the_call_pulse() -> None:
    rng = np.random.default_rng(1)
    samples, pulse_start, pulse_end = _pulsed_tone_with_noise(rng)

    gated = spectral_gate(samples, samplerate_hz=256_000)

    peak_before = np.max(np.abs(samples[pulse_start:pulse_end]))
    peak_after = np.max(np.abs(gated[pulse_start:pulse_end]))
    # The real call's peak amplitude must largely survive -- this is what
    # distinguishes gating from just re-running the highpass filter.
    assert peak_after > peak_before * 0.6


def test_spectral_gate_output_length_matches_input() -> None:
    rng = np.random.default_rng(2)
    samples, _start, _end = _pulsed_tone_with_noise(rng)

    gated = spectral_gate(samples, samplerate_hz=256_000)

    assert len(gated) == len(samples)


def test_spectral_gate_handles_silence_without_crashing() -> None:
    silence = np.zeros(12_800)

    gated = spectral_gate(silence, samplerate_hz=256_000)

    assert len(gated) == len(silence)
    assert np.max(np.abs(gated)) == 0.0


def test_spectral_gate_handles_a_signal_shorter_than_one_window() -> None:
    # 3 samples, far fewer than any reasonable STFT window -- mirrors
    # render_full_spectrogram_image's nperseg-clamp test coverage for the
    # same "very short/truncated recording" shape.
    rng = np.random.default_rng(3)
    tiny = rng.normal(0, 100, 3)

    gated = spectral_gate(tiny, samplerate_hz=256_000)

    assert len(gated) == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `hatch test tests/test_denoise.py -v`
Expected: FAIL — `ImportError: cannot import name 'spectral_gate'`

- [ ] **Step 3: Implement `spectral_gate`**

Add to `src/fledermap/media/denoise.py`, after `subtract_background_noise` (the module already
imports `numpy as np` and `from scipy import ndimage, signal`, both reused here — no new imports
needed):

```python
# Own STFT window for the gate, independent of SpectrogramParams.window_ms -- that value is
# tuned for the spectrogram's visual time/frequency tradeoff, not for gating accuracy or clean
# reconstruction, and the two purposes aren't guaranteed to want the same window size (design
# spec's "Algorithm" section). Hann window + exactly 50% overlap (noverlap = nperseg // 2) is a
# COLA-satisfying combination, which is what makes istft's reconstruction exact on unmodified
# bins -- the actual hard part naive spectral subtraction struggles with.
_GATE_WINDOW_MS = 4.0
# Noise floor = each frequency bin's own 20th-percentile magnitude across every time frame in the
# recording -- low enough that a genuine transient call (present in only a few frames) doesn't
# drag its own bin's floor estimate upward, matching subtract_background_noise's percentile-based
# per-bin idiom, just applied to STFT magnitude instead of the power spectrogram.
_GATE_PERCENTILE = 20.0
# Gain never drops below this, even for a bin that's pure noise floor -- the standard fix for
# "musical noise" (an isolated bin snapping fully to zero and back between frames, heard as
# chirping artifacts) that a hard gate (gain_floor=0) would produce.
_GATE_GAIN_FLOOR = 0.1
# Exponent applied to the clipped linear gain -- steepens the transition between "clearly noise"
# and "clearly signal" beyond what the raw soft-threshold ratio gives alone.
_GATE_EXPONENT = 1.5


def spectral_gate(samples: np.ndarray, samplerate_hz: float) -> np.ndarray:
    """Phase-coherent spectral-gating denoise: STFT, a soft per-bin gain derived from each bin's
    own noise-floor estimate, then `istft` reconstruction -- unlike `subtract_background_noise`
    (which discards phase and is never reconstructed back to audio), this function's whole job is
    producing real, listenable denoised audio.

    Expects a signal shaped like a real bat-call recording: mostly quiet, with the actual call(s)
    as brief, louder transients -- NOT a sustained/continuous tone. A per-bin percentile treats
    whatever a bin does MOST of the time as its own noise floor; a transient call's bin is mostly
    silent so its floor stays low and the call passes through close to unchanged, but a bin that's
    continuously loud (a long constant-frequency call, or a test signal that's just a sustained
    tone) looks exactly like its own noise floor and gets suppressed along with it. This is the
    same fundamental tradeoff `subtract_background_noise` already accepts for the same reason
    (spec's "why highpass and background-subtraction use different techniques"), now inherited by
    the audio-domain version -- worth specifically checking during live listening QA against a
    real constant-frequency-call recording if one is available (e.g. a Rhinolophus species)."""
    nperseg = min(max(int(samplerate_hz * _GATE_WINDOW_MS / 1000), 8), len(samples))
    noverlap = nperseg // 2
    _freqs, _times, stft = signal.stft(
        samples, fs=samplerate_hz, window="hann", nperseg=nperseg, noverlap=noverlap
    )
    magnitude = np.abs(stft)
    noise_floor = np.percentile(magnitude, _GATE_PERCENTILE, axis=1, keepdims=True)
    ratio = np.divide(
        noise_floor,
        magnitude,
        out=np.zeros_like(magnitude),
        where=magnitude > 0,
    )
    gain = np.clip(1.0 - ratio, _GATE_GAIN_FLOOR, 1.0) ** _GATE_EXPONENT
    gated = stft * gain
    _, reconstructed = signal.istft(
        gated, fs=samplerate_hz, window="hann", nperseg=nperseg, noverlap=noverlap
    )
    # istft can return a handful more or fewer samples than the original signal depending on how
    # the window tiles across its length -- trim or zero-pad back to the exact input length so
    # every caller can treat this as a drop-in replacement for its input samples array, the same
    # contract highpass_filter already has.
    result: np.ndarray = reconstructed[: len(samples)]
    if len(result) < len(samples):
        result = np.pad(result, (0, len(samples) - len(result)))
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `hatch test tests/test_denoise.py -v`
Expected: PASS, all tests including the 5 new ones. No warnings.

- [ ] **Step 5: Type-check and lint**

Run: `hatch run types:check` and `hatch fmt`
Expected: no errors; `hatch fmt` may reformat — if so, re-run the tests.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/media/denoise.py tests/test_denoise.py
git commit -m "feat: add spectral_gate audio-domain denoise algorithm

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013mSmeH275QjgBU2FGHt1GZ"
```

---

## Task 2: Wire into the spectrogram and oscillogram renderers

**Files:**
- Modify: `src/fledermap/media/spectrogram.py:23-27` (imports), `:176-178` (denoise block)
- Modify: `src/fledermap/media/oscillogram.py:23` (import), `:87-88` (denoise block)
- Test: `tests/test_spectrogram.py`, `tests/test_oscillogram.py`

**Interfaces:**
- Consumes: `spectral_gate(samples: np.ndarray, samplerate_hz: float) -> np.ndarray` from Task 1.

- [ ] **Step 1: No new spectrogram-level pixel test — here's why**

Unlike the oscillogram (Step 5 below), a pixel-level "denoise reduces brightness in a quiet
region" test does **not** meaningfully exercise `spectral_gate` for the spectrogram specifically:
`subtract_background_noise` (the existing image-domain step, already running downstream of this
wiring) already crushes a stationary synthetic noise region on its own — spiked 2026-09-14, a
quiet-region brightness ratio of ~0.24 with `highpass_filter` + `subtract_background_noise` alone,
*before* `spectral_gate` is even wired in, well past any threshold a real test could use to detect
the gate's own marginal contribution. Writing a passing-either-way test here would be a false
TDD gate. Rely instead on:
- the existing `test_denoise_true_changes_spectrogram_pixels` (still must pass, confirms the
  wiring doesn't crash and still changes output),
- Task 1's `spectral_gate`-specific unit tests (already prove the algorithm itself works, in
  isolation from `subtract_background_noise`),
- Task 4's live listening/visual QA, which is where the real judgment about the two techniques'
  combined effect on the actual rendered image belongs (this file's own "Wiring" section already
  flags the image-domain step's fate as an empirical, not automated-test, decision).

- [ ] **Step 2: Wire `spectral_gate` into `render_full_spectrogram_image`**

In `src/fledermap/media/spectrogram.py`, change the import (line 23-27):

```python
from fledermap.media.denoise import (
    DEFAULT_CUTOFF_HZ,
    highpass_filter,
    spectral_gate,
    subtract_background_noise,
)
```

And the denoise block (currently lines 176-178):

```python
    samples, samplerate = read_pcm(wav_path)
    if params.denoise:
        samples = highpass_filter(samples, samplerate, params.cutoff_hz)
        samples = spectral_gate(samples, samplerate)
```

- [ ] **Step 3: Run the spectrogram tests**

Run: `hatch test tests/test_spectrogram.py -v`
Expected: PASS, all pre-existing tests (`test_denoise_true_changes_spectrogram_pixels`,
`test_params_hash_changes_when_denoise_changes`, etc.) — no new spectrogram test was added, per
Step 1.

- [ ] **Step 4: Write the failing test for the oscillogram**

Add to `tests/test_oscillogram.py`, which already imports `build_wav`/`fmt_payload` from
`tests.fixtures` (see its existing imports) — use those rather than hand-rolling chunk bytes,
matching this file's own established pattern:

```python
def test_denoise_reduces_envelope_outside_the_call(tmp_path: Path) -> None:
    """Distinguishes spectral gating from the highpass filter alone: the
    peak-envelope amplitude in a quiet region, well above the 5kHz highpass
    cutoff, must shrink with denoise=True vs denoise=False."""
    wav_path = tmp_path / "call.wav"
    samplerate = 256_000
    duration_s = 0.05
    n = int(samplerate * duration_s)
    t = np.arange(n) / samplerate
    pulse_start, pulse_end = int(n * 0.4), int(n * 0.6)
    rng = np.random.default_rng(1)
    clean = np.zeros(n)
    clean[pulse_start:pulse_end] = 20000 * np.sin(
        2 * np.pi * 45_000 * t[pulse_start:pulse_end]
    )
    noise = rng.normal(0, 3000, n)
    samples = (clean + noise).astype(np.int16)

    wav_path.write_bytes(
        build_wav(
            [
                (b"fmt ", fmt_payload(samplerate)),
                (b"data", samples.tobytes()),
            ]
        )
    )

    plain_out = tmp_path / "plain.webp"
    denoised_out = tmp_path / "denoised.webp"
    render_oscillogram(wav_path, plain_out, params=OscillogramParams(denoise=False))
    render_oscillogram(wav_path, denoised_out, params=OscillogramParams(denoise=True))

    with Image.open(plain_out) as plain_img, Image.open(denoised_out) as denoised_img:
        # Count non-background pixels over a whole quiet region (columns
        # before the pulse, which starts at 40% width), not a single column --
        # a single column's envelope height is noisy enough to go either way
        # even when the region as a whole is clearly quieter (spiked
        # 2026-09-14: a per-column version of this assertion was flaky the
        # same way a single-pixel spectrogram check was).
        plain_arr = np.array(plain_img)
        denoised_arr = np.array(denoised_img)
        background = np.array(OscillogramParams().background_color)
        col_end = int(0.3 * plain_arr.shape[1])

        def _nonbackground_pixel_count(arr: np.ndarray) -> int:
            region = arr[:, :col_end]
            return int((~np.all(region == background, axis=-1)).sum())

        plain_count = _nonbackground_pixel_count(plain_arr)
        denoised_count = _nonbackground_pixel_count(denoised_arr)
        assert denoised_count < plain_count * 0.85
```

Add `struct` and `numpy as np` imports at the top of `tests/test_oscillogram.py` if not already
present (check the file first — `test_denoise_true_changes_oscillogram_pixels` already exists
there, so a WAV-writing helper or raw `numpy`/`struct` usage may already be in scope).

- [ ] **Step 5: Wire `spectral_gate` into `render_oscillogram`**

In `src/fledermap/media/oscillogram.py`, change the import (line 23):

```python
from fledermap.media.denoise import DEFAULT_CUTOFF_HZ, highpass_filter, spectral_gate
```

And the denoise block (currently lines 87-88):

```python
    if params.denoise:
        samples = highpass_filter(samples, samplerate, params.cutoff_hz)
        samples = spectral_gate(samples, samplerate)
```

- [ ] **Step 6: Run the oscillogram tests**

Run: `hatch test tests/test_oscillogram.py -v`
Expected: PASS, including the new test and `test_denoise_true_changes_oscillogram_pixels`.

- [ ] **Step 7: Run the full fast test suite and type-check**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: PASS, no warnings, no type errors.

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/media/spectrogram.py src/fledermap/media/oscillogram.py \
        tests/test_spectrogram.py tests/test_oscillogram.py
git commit -m "feat: apply spectral gating to spectrogram and oscillogram renders

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013mSmeH275QjgBU2FGHt1GZ"
```

---

## Task 3: Wire into heterodyne and TE preview playback

**Files:**
- Modify: `src/fledermap/media/heterodyne.py:14` (import), `:99-101` (denoise block)
- Modify: `src/fledermap/media/preview.py:23` (import), `:50-53` (denoise block)
- Test: `tests/test_heterodyne.py`, `tests/test_preview.py`

**Interfaces:**
- Consumes: `spectral_gate(samples: np.ndarray, samplerate_hz: float) -> np.ndarray` from Task 1.

- [ ] **Step 1: Wire `spectral_gate` into `render_heterodyne_preview`**

`render_heterodyne_preview`'s existing tests (`test_denoise_true_changes_heterodyne_output`)
already only assert the output *differs* between `denoise=True`/`False`, so they exercise this
change without modification — no new test needed here beyond confirming they still pass in Step 3.

In `src/fledermap/media/heterodyne.py`, change the import (line 14):

```python
from fledermap.media.denoise import DEFAULT_CUTOFF_HZ, highpass_filter, spectral_gate
```

And the denoise block (currently lines 99-101):

```python
    samples, samplerate = read_pcm(wav_path)
    if denoise:
        samples = highpass_filter(samples, samplerate, cutoff_hz)
        samples = spectral_gate(samples, samplerate)
```

- [ ] **Step 2: Wire `spectral_gate` into `make_preview`**

Similarly, `test_denoise_true_changes_preview_output` and
`test_denoise_false_is_byte_identical_to_no_kwarg` already cover this without modification (the
latter stays passing because `spectral_gate`, like `highpass_filter`, only runs inside the
`if denoise:` branch).

In `src/fledermap/media/preview.py`, change the import (line 23):

```python
from fledermap.media.denoise import DEFAULT_CUTOFF_HZ, highpass_filter, spectral_gate
```

And the denoise block (currently lines 50-53):

```python
    if denoise:
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float64)
        filtered = highpass_filter(samples, params.framerate, cutoff_hz)
        gated = spectral_gate(filtered, params.framerate)
        frames = np.clip(gated, -32768, 32767).astype(np.int16).tobytes()
```

- [ ] **Step 3: Run both test files**

Run: `hatch test tests/test_heterodyne.py tests/test_preview.py -v`
Expected: PASS, no changes needed to either test file.

- [ ] **Step 4: Run the full fast test suite and type-check**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: PASS, no warnings, no type errors.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/media/heterodyne.py src/fledermap/media/preview.py
git commit -m "feat: apply spectral gating to HET and TE playback audio

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013mSmeH275QjgBU2FGHt1GZ"
```

---

## Task 4: Re-examine the image-domain cleanup, then live QA

This task is not a normal delegated task — the last step needs a human listening to real audio
and looking at real spectrograms, the same way the original highpass/background-subtraction
tuning was done live (2026-09-14, same day). **Do this task in the controlling session, not via a
dispatched subagent** (mirrors the project's own rule for `db`-marked tests needing the
controlling session, for the same "a subagent can't wait on an interactive human step" reason).

**Files:**
- Possibly modify: `src/fledermap/media/denoise.py` (`subtract_background_noise`'s
  `median_size`/`preserve_threshold` defaults, or its call site in `spectrogram.py:205-206`, if
  the median-filter step is judged no longer worth its cost)
- Test: whichever of `tests/test_denoise.py`/`tests/test_spectrogram.py` covers what changes

**Interfaces:**
- Consumes: everything from Tasks 1-3, fully wired and merged.

- [ ] **Step 1: Render a real comparison set**

Using a real field recording (`~/Bat Sessions/...`, per `project-fledermap-systemd-install-on-this-machine` / the project's own real-recording notes — NOT the bundled iPhone-simulator sample data, which CLAUDE.md's "Sample data" section says isn't representative), render the spectrogram with `denoise=False`, with only the pre-existing highpass+`subtract_background_noise` behavior (temporarily skip the new `spectral_gate` call to produce this baseline if needed), and with the full new pipeline (`highpass_filter` → `spectral_gate` → `subtract_background_noise`). Also render TE and HET Opus previews with `denoise=True` for the same recording to listen to.

- [ ] **Step 2: Judge whether `subtract_background_noise`'s median-filter step is still earning its keep**

Compare the "full new pipeline" spectrogram against a variant with the selective median filter
skipped (`subtract_background_noise(sxx, median_size=(1, 1))`, which disables it per its own
docstring) and against variants with different `median_size`/`preserve_threshold` values. Decide,
by eye:
- Keep the median filter as-is,
- retune its constants (smaller kernel / different threshold), or
- remove the step entirely (call only the over-subtraction, or replace the call with a no-op)
  and simplify `subtract_background_noise` accordingly.

Make the corresponding code change in `src/fledermap/media/denoise.py` (and, if the function's
signature or behavior changes, update `tests/test_denoise.py`'s existing
`subtract_background_noise` tests to match — do not delete a test without replacing what it
covered).

- [ ] **Step 3: Listen**

Listen to the TE and HET previews from Step 1. Confirm:
- Background noise is audibly reduced compared to `denoise=False`.
- No obvious "musical noise" (chirping/warbling artifacts) — if present, the fix is `_GATE_GAIN_FLOOR` in `media/denoise.py` (raise it, e.g. toward 0.15-0.2, to gate less aggressively) rather than a structural change.
- If a constant-frequency-call recording is available, specifically check whether the call itself survives the gate (see `spectral_gate`'s docstring for why this is the known risk case). If it's audibly damaged, that's a real finding to report back, not something to silently work around — this plan's Task 1 constants are a starting point, not a promise.

- [ ] **Step 4: Adjust constants if needed**

If Step 3 surfaces a problem, adjust `_GATE_PERCENTILE`/`_GATE_GAIN_FLOOR`/`_GATE_EXPONENT` in
`src/fledermap/media/denoise.py` and re-render/re-listen. Re-run `hatch test tests/test_denoise.py -v`
after any change — the thresholds in Task 1's tests (0.6 multipliers) have margin against the
spike-verified values (0.49 and 0.80) but a large constant change could still need a test
threshold adjustment; if so, update the threshold and note in the test why.

- [ ] **Step 5: Run the full test suite**

Run: `hatch test -m "not db"` (fast subset) and, separately, `hatch test` (full suite including
`db`-marked tests, `dangerouslyDisableSandbox: true`) at least once before considering this done.
Run: `hatch run types:check`.
Expected: all PASS, no warnings.

- [ ] **Step 6: Commit**

```bash
git add -u
git commit -m "tune: adjust denoise parameters after live listening QA

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013mSmeH275QjgBU2FGHt1GZ"
```

(Skip this commit if Step 4 needed no changes — note that in the plan's tracking instead.)

- [ ] **Step 7: Update the design spec's status**

Change `docs/superpowers/specs/2026-09-14-fledermap-audio-denoise-design.md`'s header from
`**Status:** draft` to `**Status:** shipped 2026-09-14` (or the actual ship date), and add a short
"Shipped" note (matching the prior highpass spec's own convention) summarizing what actually got
tuned during Step 2-4 above. Commit this alongside or separately.

```bash
git add docs/superpowers/specs/2026-09-14-fledermap-audio-denoise-design.md
git commit -m "docs: mark audio-domain denoise spec as shipped

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013mSmeH275QjgBU2FGHt1GZ"
```

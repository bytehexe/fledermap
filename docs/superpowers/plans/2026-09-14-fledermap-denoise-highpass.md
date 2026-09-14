# Fledermap Denoise/Highpass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Denoise" toggle to the recording-details page that highpass-filters audio (TE+HET)
and both visuals (spectrogram+oscillogram), plus spectrogram-only background-noise subtraction;
independently, always fix the spectrogram's color-normalization peak to exclude the low band, on
both the drawer and the details page.

**Architecture:** A new pure `media/denoise.py` module (highpass filter + background-noise
subtraction) plugged into the existing `media/` renderers via two new `SpectrogramParams`/
`OscillogramParams` dataclass fields (`cutoff_hz`, `denoise`) and two new optional keyword
parameters on `render_heterodyne_preview`/`make_preview`. `web/views/media.py` threads a `denoise`
query param through the details page's existing live-render routes and gains one new
details-only live-rendered TE endpoint. JS wiring adds one new toolbar toggle plus a small,
independently-tested pure URL-manipulation helper.

**Tech Stack:** Python/scipy/numpy (existing), Flask, vanilla JS + `node:test`.

**Spec:** `docs/superpowers/specs/2026-09-14-fledermap-denoise-highpass-design.md`

## Global Constraints

- Shared cutoff: 5000.0 Hz (`media/denoise.py`'s `DEFAULT_CUTOFF_HZ`), not user-adjustable, not
  an env var/`Config` field.
- Highpass reaches audio (TE+HET) + spectrogram + oscillogram; background-noise subtraction
  reaches the spectrogram image only (see spec's "Why highpass and background-subtraction use
  different techniques").
- The peak-exclusion fix to `render_full_spectrogram_image` is unconditional (not gated by
  `denoise`) and applies to both the map drawer and the details page.
- The Denoise toggle itself is details-page-only; it joins `#detail-toolbar` immediately after
  `#view-lock-toggle` (spec's placement decision — do not re-derive or re-place this).
- Every `SpectrogramParams`/`OscillogramParams` field must be real (hashed by `params_hash`) —
  `cutoff_hz` and `denoise` are dataclass fields on both, never bare module constants.
- `hatch test -m "not db"` for the fast subset; any `db`-marked test needs
  `dangerouslyDisableSandbox: true` (Docker is blocked by the sandbox) — the controlling session
  runs these itself, never a dispatched subagent (CLAUDE.md's "Environment gotchas").
- `hatch fmt` + `hatch run types:check` (mypy covers `tests/` too) must be clean before any commit.
- Any task touching JS requires the mandatory headless-Chrome live-verification pass (CLAUDE.md's
  JavaScript tooling section) — Task 13 below, not skippable.
- Run `git`/`hatch test -m db` unsandboxed (`dangerouslyDisableSandbox: true`); sandboxed git
  config writes leave a stale `.git/config.lock`.

---

## Task 1: `media/denoise.py` — shared cutoff constant + highpass filter

**Files:**
- Create: `src/fledermap/media/denoise.py`
- Test: `tests/test_denoise.py`

**Interfaces:**
- Produces: `DEFAULT_CUTOFF_HZ: float`, `highpass_filter(samples: np.ndarray, samplerate_hz: float, cutoff_hz: float) -> np.ndarray`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_denoise.py
from __future__ import annotations

import numpy as np

from fledermap.media.denoise import DEFAULT_CUTOFF_HZ, highpass_filter


def test_default_cutoff_is_five_khz() -> None:
    assert DEFAULT_CUTOFF_HZ == 5000.0


def test_highpass_filter_removes_energy_below_cutoff() -> None:
    samplerate = 256_000
    duration_s = 0.05
    t = np.arange(int(samplerate * duration_s)) / samplerate
    # A 2kHz tone (below cutoff) plus a 45kHz tone (above cutoff, in the bat-call range).
    low = np.sin(2 * np.pi * 2_000 * t)
    high = np.sin(2 * np.pi * 45_000 * t)
    samples = (low + high) * 10000

    filtered = highpass_filter(samples, samplerate, cutoff_hz=5000.0)

    fft = np.abs(np.fft.rfft(filtered))
    freqs = np.fft.rfftfreq(len(filtered), 1 / samplerate)
    low_band_energy = fft[(freqs > 1000) & (freqs < 3000)].max()
    high_band_energy = fft[(freqs > 40000) & (freqs < 50000)].max()
    # The low tone must be strongly attenuated relative to the high tone, which must survive.
    assert low_band_energy < high_band_energy * 0.1


def test_highpass_filter_is_zero_phase_no_time_shift() -> None:
    """sosfiltfilt (not sosfilt) must be used -- a causal filter would shift
    a sharp transient in time, which would desync the filtered audio from
    the unfiltered spectrogram/oscillogram's own time axis."""
    samplerate = 256_000
    samples = np.zeros(1000)
    samples[500] = 1.0  # a single impulse well above the cutoff's own ringing

    filtered = highpass_filter(samples, samplerate, cutoff_hz=5000.0)

    # The impulse's peak must still be at (or immediately adjacent to) index 500 --
    # a causal (non-zero-phase) filter would shift it later in time.
    assert abs(int(np.argmax(np.abs(filtered))) - 500) <= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_denoise.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.media.denoise'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/fledermap/media/denoise.py
"""Highpass filtering and spectrogram-only background-noise subtraction for
the "Denoise" toggle (design spec
docs/superpowers/specs/2026-09-14-fledermap-denoise-highpass-design.md). Pure:
no DB, no queue awareness, matching every other `media/` module.

`DEFAULT_CUTOFF_HZ` is the one shared constant every caller (SpectrogramParams,
OscillogramParams, the TE/HET routes) defaults to -- a single definition so the
cutoff can't drift between callers."""

from __future__ import annotations

import numpy as np
from scipy import signal

DEFAULT_CUTOFF_HZ = 5000.0

# A conventional order balancing rolloff steepness against filter-design edge
# cases at a low cutoff-to-Nyquist ratio (this project's recordings run at
# 256kHz+ sample rates, so a 5kHz cutoff is a small fraction of Nyquist).
_HIGHPASS_ORDER = 4


def highpass_filter(
    samples: np.ndarray,
    samplerate_hz: float,
    cutoff_hz: float,
) -> np.ndarray:
    """Zero-phase Butterworth highpass (`sosfiltfilt`, not `sosfilt` -- a
    causal filter would time-shift a sharp transient, desyncing the filtered
    audio from the unfiltered spectrogram/oscillogram's own time axis).
    Mirrors `heterodyne.py`'s existing `butter` + `sosfiltfilt` lowpass
    idiom, the same established pattern in this codebase."""
    sos = signal.butter(
        _HIGHPASS_ORDER,
        cutoff_hz,
        btype="high",
        fs=samplerate_hz,
        output="sos",
    )
    result: np.ndarray = signal.sosfiltfilt(sos, samples)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch test tests/test_denoise.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/media/denoise.py tests/test_denoise.py
git commit -m "feat: add highpass_filter for the denoise toggle"
```

---

## Task 2: `media/denoise.py` — background-noise subtraction

**Files:**
- Modify: `src/fledermap/media/denoise.py`
- Test: `tests/test_denoise.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `subtract_background_noise(sxx: np.ndarray, percentile: float = 10.0) -> np.ndarray`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_denoise.py
from fledermap.media.denoise import subtract_background_noise


def test_subtract_background_noise_removes_a_flat_noise_floor() -> None:
    # 3 frequency bins x 10 time columns. Bin 0: constant noise floor of 2.0.
    # Bin 1: a real "call" -- mostly silent, one loud spike of 100.0.
    # Bin 2: all zero (silence).
    sxx = np.zeros((3, 10))
    sxx[0, :] = 2.0
    sxx[1, :] = 0.1
    sxx[1, 5] = 100.0
    sxx[2, :] = 0.0

    result = subtract_background_noise(sxx, percentile=10.0)

    # The flat noise floor drops to (near) zero everywhere.
    assert result[0].max() < 0.5
    # The call's loud spike survives, clearly distinguishable from its own row's floor.
    assert result[1, 5] > 90.0
    # Never negative (clamped at zero).
    assert (result >= 0).all()


def test_subtract_background_noise_preserves_shape() -> None:
    sxx = np.random.default_rng(0).random((5, 20))
    result = subtract_background_noise(sxx)
    assert result.shape == sxx.shape
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_denoise.py -v`
Expected: FAIL with `ImportError: cannot import name 'subtract_background_noise'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/fledermap/media/denoise.py`:

```python
def subtract_background_noise(sxx: np.ndarray, percentile: float = 10.0) -> np.ndarray:
    """Per frequency-bin (per row) background-noise subtraction: estimate
    each bin's noise floor as its own `percentile`-th percentile power
    across all time columns, subtract it from every column in that row,
    clamp at zero. Operates on the STFT's linear power matrix, before any
    dB conversion -- spectrogram-image-only (see the design spec's "Why
    highpass and background-subtraction use different techniques": this
    is not reconstructed back to audio, so no phase/COLA concern applies)."""
    noise_floor = np.percentile(sxx, percentile, axis=1, keepdims=True)
    return np.maximum(sxx - noise_floor, 0.0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch test tests/test_denoise.py -v`
Expected: PASS (5 tests total)

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/media/denoise.py tests/test_denoise.py
git commit -m "feat: add subtract_background_noise for spectrogram-only denoising"
```

---

## Task 3: `SpectrogramParams` — cutoff_hz/denoise fields, peak-exclusion fix, denoise wiring

**Files:**
- Modify: `src/fledermap/media/spectrogram.py`
- Test: `tests/test_spectrogram.py`

**Interfaces:**
- Consumes: `fledermap.media.denoise.DEFAULT_CUTOFF_HZ`, `highpass_filter`, `subtract_background_noise`
- Produces: `SpectrogramParams.cutoff_hz: float`, `SpectrogramParams.denoise: bool`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_spectrogram.py`:

```python
def test_params_hash_changes_when_cutoff_hz_changes() -> None:
    base = SpectrogramParams()
    changed = SpectrogramParams(cutoff_hz=base.cutoff_hz + 1)
    assert base.params_hash != changed.params_hash


def test_params_hash_changes_when_denoise_changes() -> None:
    base = SpectrogramParams()
    changed = SpectrogramParams(denoise=not base.denoise)
    assert base.params_hash != changed.params_hash


def test_peak_excludes_low_band_even_when_denoise_is_off(tmp_path: Path) -> None:
    """A loud low-frequency noise band must not darken the rest of the
    image by dominating the dB-normalization peak -- excluded from the
    PEAK computation always, regardless of `denoise` (spec: "clipping in
    that band would be acceptable")."""
    wav_path = tmp_path / "noisy.wav"
    samplerate = 256_000
    duration_s = 0.05
    n = int(samplerate * duration_s)
    t = np.arange(n) / samplerate
    # A LOUD 2kHz tone (below the 5kHz cutoff) plus a QUIET 45kHz call.
    loud_low = 32000 * np.sin(2 * np.pi * 2_000 * t)
    quiet_call = 5000 * np.sin(2 * np.pi * 45_000 * t)
    samples = (loud_low + quiet_call).astype(np.int16)
    pcm = samples.tobytes()

    channels, bits = 1, 16
    byte_rate = samplerate * channels * bits // 8
    block_align = channels * bits // 8
    fmt_payload = struct.pack("<HHIIHH", 1, channels, samplerate, byte_rate, block_align, bits)

    def chunk(chunk_id: bytes, payload: bytes) -> bytes:
        out = chunk_id + struct.pack("<I", len(payload)) + payload
        if len(payload) % 2:
            out += b"\x00"
        return out

    body = b"WAVE" + chunk(b"fmt ", fmt_payload) + chunk(b"data", pcm)
    wav_path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)

    params = SpectrogramParams(cutoff_hz=5000.0)
    full_image = render_full_spectrogram_image(wav_path, params)

    # A pixel in the 45kHz call's row must be bright (not crushed to the palette floor) --
    # if the peak were still `sxx.max()` (dominated by the loud low tone), the quiet call
    # would normalize far below the palette's visible range.
    freqs_axis_top_khz = min(params.max_freq_hz, samplerate / 2) / 1000
    call_row_frac = 1 - (45.0 / freqs_axis_top_khz)  # image row 0 = top = highest freq
    call_row = int(call_row_frac * (full_image.image.height - 1))
    pixel = full_image.image.getpixel((full_image.image.width // 2, call_row))
    assert pixel != (0, 0, 0)  # not the palette floor colour


def test_denoise_true_changes_spectrogram_pixels(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    plain_out = tmp_path / "plain.webp"
    denoised_out = tmp_path / "denoised.webp"

    render_spectrogram(wav_path, plain_out, params=SpectrogramParams(denoise=False))
    render_spectrogram(wav_path, denoised_out, params=SpectrogramParams(denoise=True))

    with Image.open(plain_out) as a, Image.open(denoised_out) as b:
        assert list(a.getdata()) != list(b.getdata())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_spectrogram.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'cutoff_hz'` (and the
peak/denoise tests fail similarly once the field exists but isn't wired, or fail alongside).

- [ ] **Step 3: Write minimal implementation**

In `src/fledermap/media/spectrogram.py`, add the import and the two fields:

```python
from fledermap.media.denoise import (
    DEFAULT_CUTOFF_HZ,
    highpass_filter,
    subtract_background_noise,
)
```

Add to `SpectrogramParams` (after `palette: str = "black_blue_rainbow_red"`):

```python
    # Low-frequency noise-band boundary (design spec
    # docs/superpowers/specs/2026-09-14-fledermap-denoise-highpass-design.md): always excluded
    # from the color-normalization peak below, and used as the highpass cutoff when `denoise`
    # is on. A real dataclass field (not a bare module constant) so `params_hash` invalidates
    # existing cached renders if this value is ever revisited.
    cutoff_hz: float = DEFAULT_CUTOFF_HZ
    denoise: bool = False
```

In `render_full_spectrogram_image`, change the samples-read and peak sections:

```python
    samples, samplerate = read_pcm(wav_path)
    if params.denoise:
        samples = highpass_filter(samples, samplerate, params.cutoff_hz)
```

(insert immediately after the existing `samples, samplerate = read_pcm(wav_path)` line, before
the `nperseg`/`noverlap`/`signal.spectrogram` block)

```python
    if params.denoise:
        sxx = subtract_background_noise(sxx)

    # dB relative to this recording's own loudest bin ABOVE the low-frequency noise-band
    # boundary (never the low band itself, which can otherwise dominate and darken the whole
    # image -- design spec's peak-exclusion fix, unconditional, not gated by `denoise`), clipped
    # to a fixed dynamic-range window and normalised to [0, 1] -- NOT `log1p` on raw power. ...
    peak = sxx[freqs >= params.cutoff_hz].max() if (freqs >= params.cutoff_hz).any() else sxx.max()
```

(insert the `if params.denoise: sxx = subtract_background_noise(sxx)` line immediately after the
existing `sxx = sxx[keep, :]` Nyquist-clamp line, and replace the existing `peak = sxx.max()` line
with the new one above — keep the rest of that block, i.e. the `if peak > 0: db = ...` normalization,
unchanged)

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_spectrogram.py -v`
Expected: PASS (all tests, including the 4 new ones)

- [ ] **Step 5: Run mypy**

Run: `hatch run types:check`
Expected: no new errors

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/media/spectrogram.py tests/test_spectrogram.py
git commit -m "feat: wire denoise + always-on peak-exclusion into spectrogram rendering"
```

---

## Task 4: `OscillogramParams` — cutoff_hz/denoise fields, denoise wiring

**Files:**
- Modify: `src/fledermap/media/oscillogram.py`
- Test: `tests/test_oscillogram.py`

**Interfaces:**
- Consumes: `fledermap.media.denoise.DEFAULT_CUTOFF_HZ`, `highpass_filter`
- Produces: `OscillogramParams.cutoff_hz: float`, `OscillogramParams.denoise: bool`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_oscillogram.py`:

```python
def test_params_hash_changes_when_cutoff_hz_changes() -> None:
    base = OscillogramParams()
    changed = OscillogramParams(cutoff_hz=base.cutoff_hz + 1)
    assert base.params_hash != changed.params_hash


def test_params_hash_changes_when_denoise_changes() -> None:
    base = OscillogramParams()
    changed = OscillogramParams(denoise=not base.denoise)
    assert base.params_hash != changed.params_hash


def test_denoise_true_changes_oscillogram_pixels(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path, freq_hz=2_000.0)  # below the 5kHz cutoff -- should be visibly attenuated
    plain_out = tmp_path / "plain.webp"
    denoised_out = tmp_path / "denoised.webp"

    render_oscillogram(wav_path, plain_out, params=OscillogramParams(denoise=False))
    render_oscillogram(wav_path, denoised_out, params=OscillogramParams(denoise=True))

    with Image.open(plain_out) as a, Image.open(denoised_out) as b:
        assert list(a.getdata()) != list(b.getdata())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_oscillogram.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'cutoff_hz'`

- [ ] **Step 3: Write minimal implementation**

In `src/fledermap/media/oscillogram.py`, add the import:

```python
from fledermap.media.denoise import DEFAULT_CUTOFF_HZ, highpass_filter
```

Add to `OscillogramParams` (after `background_color`):

```python
    # Same shared cutoff/denoise fields as SpectrogramParams -- see that module's
    # docstring for why both are real dataclass fields, not bare constants.
    cutoff_hz: float = DEFAULT_CUTOFF_HZ
    denoise: bool = False
```

In `render_oscillogram`, right after `samples, samplerate = read_pcm(wav_path)`:

```python
    if params.denoise:
        samples = highpass_filter(samples, samplerate, params.cutoff_hz)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_oscillogram.py -v`
Expected: PASS (all tests, including the 3 new ones)

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/media/oscillogram.py tests/test_oscillogram.py
git commit -m "feat: wire denoise into oscillogram rendering"
```

---

## Task 5: `render_heterodyne_preview` — denoise/cutoff_hz parameters

**Files:**
- Modify: `src/fledermap/media/heterodyne.py`
- Test: `tests/test_heterodyne.py`

**Interfaces:**
- Consumes: `fledermap.media.denoise.DEFAULT_CUTOFF_HZ`, `highpass_filter`
- Produces: `render_heterodyne_preview(wav_path, out_path, *, tune_freq_hz, denoise=False, cutoff_hz=DEFAULT_CUTOFF_HZ) -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_heterodyne.py` already defines `_sine_wav(path, *, freq_hz, samplerate=256_000, duration_s=0.05)`
(imported via `from tests.fixtures import build_wav, fmt_payload`, already present at the top of
that file). Append:

```python
def test_denoise_true_changes_heterodyne_output(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path, freq_hz=40_000.0)
    plain_out = tmp_path / "plain.opus"
    denoised_out = tmp_path / "denoised.opus"

    render_heterodyne_preview(wav_path, plain_out, tune_freq_hz=40_000, denoise=False)
    render_heterodyne_preview(wav_path, denoised_out, tune_freq_hz=40_000, denoise=True)

    assert plain_out.read_bytes() != denoised_out.read_bytes()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_heterodyne.py -v`
Expected: FAIL — `TypeError: render_heterodyne_preview() got an unexpected keyword argument 'denoise'`

- [ ] **Step 3: Write minimal implementation**

In `src/fledermap/media/heterodyne.py`, add the import:

```python
from fledermap.media.denoise import DEFAULT_CUTOFF_HZ, highpass_filter
```

Change the `render_heterodyne_preview` signature and body:

```python
def render_heterodyne_preview(
    wav_path: Path,
    out_path: Path,
    *,
    tune_freq_hz: float,
    denoise: bool = False,
    cutoff_hz: float = DEFAULT_CUTOFF_HZ,
) -> None:
    """Mix `wav_path`'s audio down to audible range around `tune_freq_hz`
    (classic heterodyne technique) and render it to `out_path` as Opus.
    `denoise`, if set, highpass-filters the raw samples first (before
    mixing) -- filtering after mixing would operate in the shifted
    frequency space, the wrong cutoff entirely."""
    samples, samplerate = read_pcm(wav_path)
    if denoise:
        samples = highpass_filter(samples, samplerate, cutoff_hz)
    if samplerate <= 2 * _LOWPASS_CUTOFF_HZ:
```

(the rest of the function body is unchanged — only the signature and the two new lines right
after `read_pcm` are new)

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch test tests/test_heterodyne.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/media/heterodyne.py tests/test_heterodyne.py
git commit -m "feat: add denoise parameter to render_heterodyne_preview"
```

---

## Task 6: `make_preview` — denoise/cutoff_hz parameters

**Files:**
- Modify: `src/fledermap/media/preview.py`
- Test: `tests/test_preview.py`

**Interfaces:**
- Consumes: `fledermap.media.denoise.DEFAULT_CUTOFF_HZ`, `highpass_filter`
- Produces: `make_preview(wav_path, out_path, *, denoise=False, cutoff_hz=DEFAULT_CUTOFF_HZ) -> None`

**Note:** the existing `wave`-based read path is preserved exactly for `denoise=False` (byte-for-
byte identical to today — TE's existing "nearly free, no DSP" framing must not regress for the
common/default case). `denoise=True` decodes to numpy, filters, and re-encodes as 16-bit PCM.
Assumes mono input, matching this project's established assumption elsewhere (`wav_pcm.read_pcm`
already averages multi-channel down to mono; EMT devices are mono in practice).

- [ ] **Step 1: Write the failing test**

`tests/test_preview.py` already defines `_sine_wav(path, *, freq_hz=45_000.0, samplerate=256_000, duration_s=0.05)`
(no import needed, self-contained — unlike `test_heterodyne.py`'s version it builds the WAV bytes
directly, not via `tests.fixtures`). Append:

```python
def test_denoise_true_changes_preview_output(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path, freq_hz=2_000.0)  # below the 5kHz cutoff -- denoise should audibly change it
    plain_out = tmp_path / "plain.opus"
    denoised_out = tmp_path / "denoised.opus"

    make_preview(wav_path, plain_out, denoise=False)
    make_preview(wav_path, denoised_out, denoise=True)

    assert plain_out.read_bytes() != denoised_out.read_bytes()


def test_denoise_false_is_byte_identical_to_no_kwarg(tmp_path: Path) -> None:
    """Regression guard: the default/off path must be untouched -- the
    existing raw-frame pass-through, not routed through decode/filter/
    re-encode at all."""
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    explicit_out = tmp_path / "explicit.opus"
    default_out = tmp_path / "default.opus"

    make_preview(wav_path, explicit_out, denoise=False)
    make_preview(wav_path, default_out)

    assert explicit_out.read_bytes() == default_out.read_bytes()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_preview.py -v`
Expected: FAIL — `TypeError: make_preview() got an unexpected keyword argument 'denoise'`

- [ ] **Step 3: Write minimal implementation**

In `src/fledermap/media/preview.py`, add imports:

```python
import numpy as np

from fledermap.media.denoise import DEFAULT_CUTOFF_HZ, highpass_filter
```

Change `make_preview`:

```python
def make_preview(
    wav_path: Path,
    out_path: Path,
    *,
    denoise: bool = False,
    cutoff_hz: float = DEFAULT_CUTOFF_HZ,
) -> None:
    """Render `wav_path`'s x10 time-expanded preview to `out_path` as Opus.
    `denoise=False` (the default) is the exact original raw-frame pass-
    through -- no decode, no DSP, "nearly free" as documented above. Only
    `denoise=True` decodes to numpy, highpass-filters, and re-encodes;
    assumes mono input, matching this project's established assumption
    elsewhere (`wav_pcm.read_pcm` already averages multi-channel to mono)."""
    with wave.open(str(wav_path), "rb") as src:
        params = src.getparams()
        frames = src.readframes(src.getnframes())

    if denoise:
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float64)
        filtered = highpass_filter(samples, params.framerate, cutoff_hz)
        frames = np.clip(filtered, -32768, 32767).astype(np.int16).tobytes()

    slow_rate = params.framerate // TIME_EXPANSION_FACTOR
    encode_pcm_as_opus(
        frames=frames,
        nchannels=params.nchannels,
        sampwidth=params.sampwidth,
        framerate=slow_rate,
        out_path=out_path,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch test tests/test_preview.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/media/preview.py tests/test_preview.py
git commit -m "feat: add denoise parameter to make_preview"
```

---

## Task 7: `web/views/media.py` — query params, cache-key fix, new detail-preview route

**Files:**
- Modify: `src/fledermap/web/views/media.py`
- Test: `tests/test_media_view.py`

**Interfaces:**
- Consumes: `fledermap.web.params.parse_bool`, `fledermap.media.preview.make_preview`,
  `fledermap.media.denoise.DEFAULT_CUTOFF_HZ`
- Produces: `denoise` query param on `detail_spectrogram`/`detail_oscillogram`/`het_preview`; new
  route `GET /recordings/<audio_hash>/detail-preview.opus`

- [ ] **Step 0: Fix a pre-existing monkeypatch this task's route change will break**

`test_het_preview_supports_range_requests` (already in `tests/test_media_view.py`) monkeypatches
`render_heterodyne_preview` with `lambda wav_path, out_path, *, tune_freq_hz: out_path.write_bytes(fixed_bytes)`.
Once `het_preview` unconditionally passes `denoise=denoise` (this task's Step 3 below), calling that
lambda raises `TypeError: got an unexpected keyword argument 'denoise'`. Fix the lambda now, in the
same commit as the rest of this task (this is a real regression this task's own change causes, not
pre-existing breakage to leave for later):

```python
        lambda wav_path, out_path, *, tune_freq_hz, denoise=False: out_path.write_bytes(fixed_bytes),
```

(replace the existing lambda at that `monkeypatch.setattr` call with this one — everything else
about that test is unchanged)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_media_view.py`:

```python
def test_detail_spectrogram_denoise_true_differs_from_default(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    _write_wav(archive_root / "a.wav", duration_s=0.05)
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="d1" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.05,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media", archive_roots=(archive_root,))
    client = app.test_client()
    plain = client.get(f"/recordings/{'d1' * 32}/detail-spectrogram/0.webp")
    denoised = client.get(f"/recordings/{'d1' * 32}/detail-spectrogram/0.webp?denoise=true")

    assert plain.status_code == 200
    assert denoised.status_code == 200
    assert plain.data != denoised.data


def test_het_preview_denoise_true_differs_from_default(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    _write_wav(archive_root / "a.wav", duration_s=0.05)
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="d2" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media", archive_roots=(archive_root,))
    client = app.test_client()
    plain = client.get(f"/recordings/{'d2' * 32}/het-preview.opus?freq_hz=40000")
    denoised = client.get(f"/recordings/{'d2' * 32}/het-preview.opus?freq_hz=40000&denoise=true")

    assert plain.status_code == 200
    assert denoised.status_code == 200
    assert plain.data != denoised.data


def test_detail_preview_serves_opus(engine: Engine, tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    _write_wav(archive_root / "a.wav", duration_s=0.05)
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="d3" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media", archive_roots=(archive_root,))
    client = app.test_client()
    plain = client.get(f"/recordings/{'d3' * 32}/detail-preview.opus")
    denoised = client.get(f"/recordings/{'d3' * 32}/detail-preview.opus?denoise=true")

    assert plain.status_code == 200
    assert plain.mimetype == "audio/ogg"
    assert denoised.status_code == 200
    assert plain.data != denoised.data


def test_detail_preview_404s_for_an_unknown_hash(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f1' * 32}/detail-preview.opus")
    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_media_view.py -v`
Expected: FAIL — `test_detail_preview_*` fail with 404 (route doesn't exist);
`test_detail_spectrogram_denoise_true_differs_from_default` and
`test_het_preview_denoise_true_differs_from_default` fail because `plain.data == denoised.data`
(the query param is silently ignored today).

- [ ] **Step 3: Write minimal implementation**

Add imports to `src/fledermap/web/views/media.py`:

```python
from fledermap.media.preview import make_preview
from fledermap.web.params import parse_bool
```

Update `_spectrogram_image_cache_key` to include the two new fields (this is a manually-maintained
list, not automatic — missing this would make a `denoise` toggle silently reuse a cached, un-
denoised full image):

```python
    return (
        str(wav_path),
        wav_path.stat().st_mtime_ns,
        params.window_ms,
        params.overlap,
        params.max_freq_hz,
        params.dynamic_range_db,
        params.palette,
        params.cutoff_hz,
        params.denoise,
    )
```

In `detail_spectrogram`, after computing `wav_path, spectrogram_params, _oscillogram_params, tile = context`:

```python
    denoise = parse_bool(flask.request.args.get("denoise"))
    spectrogram_params = dataclasses.replace(spectrogram_params, denoise=denoise)
    tile_params = dataclasses.replace(spectrogram_params, width_px=tile.width_px)
```

(replace the existing `tile_params = dataclasses.replace(spectrogram_params, width_px=tile.width_px)`
line with these three lines — the cache key below already uses the now-updated `spectrogram_params`)

In `detail_oscillogram`, after computing `wav_path, _spectrogram_params, oscillogram_params, tile = context`:

```python
    denoise = parse_bool(flask.request.args.get("denoise"))
    oscillogram_params = dataclasses.replace(oscillogram_params, denoise=denoise)
    tile_params = dataclasses.replace(oscillogram_params, width_px=tile.width_px)
```

(same replacement pattern as above)

In `het_preview`, after the existing `freq_hz` validation block, before `wav_path = _resolve_wav_path_or_404(audio_hash)`:

```python
    denoise = parse_bool(flask.request.args.get("denoise"))
```

and change the render call:

```python
        return _serve_temp_render(
            lambda out: render_heterodyne_preview(
                wav_path,
                out,
                tune_freq_hz=freq_hz,
                denoise=denoise,
            ),
            suffix=".opus",
            mimetype="audio/ogg",
        )
```

Add the new route after `het_preview`:

```python
@media_bp.get("/recordings/<audio_hash>/detail-preview.opus")
def detail_preview(audio_hash: str) -> ResponseReturnValue:
    """Details-page-only, live-rendered TE preview -- deliberately NOT the
    shared, job-queued, disk-cached `preview` route above (that route has
    no per-render params dimension; see design spec's TE/HET section for
    why a new caching dimension there would be real new infrastructure for
    a feature most recordings will never have toggled). The details page
    keeps using the cached `preview` route when denoise is off; this route
    is only ever hit once a user actually turns the toggle on."""
    denoise = parse_bool(flask.request.args.get("denoise"))
    wav_path = _resolve_wav_path_or_404(audio_hash)
    return _serve_temp_render(
        lambda out: make_preview(wav_path, out, denoise=denoise),
        suffix=".opus",
        mimetype="audio/ogg",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_media_view.py -v`
Expected: PASS (all tests, including the 4 new ones)

- [ ] **Step 5: Run mypy**

Run: `hatch run types:check`
Expected: no new errors

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/views/media.py tests/test_media_view.py
git commit -m "feat: thread denoise query param through detail media routes"
```

---

## Task 8: Vendor the Denoise toggle's icon

**Files:**
- Modify: `src/fledermap/services/vendor_assets.py`

**Interfaces:**
- Produces: `icons/outline/filter.svg`, `icons/filled/filter.svg` available via the `icon()`
  Jinja global (`icon("filter")` / `icon("filter", filled=True)`)

- [ ] **Step 1: Add the two new assets**

In `src/fledermap/services/vendor_assets.py`, add to `ASSETS` (after the `flag-cog` entry added by
the earlier "flag-cog" work, or anywhere in the Tabler Icons section):

```python
    # The Denoise toggle's icon (design spec
    # docs/superpowers/specs/2026-09-14-fledermap-denoise-highpass-design.md).
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/filter.svg",
        sha256="eb4cf4b099901c28fab2e13a70c52e3318cee10e918bd99c39373c2b19085deb",
        relative_path="icons/outline/filter.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/filled/filter.svg",
        sha256="eb1d37c40010e56b214e30f72d0c79dcc109bab4c8495a0d3c1b2e747258f943",
        relative_path="icons/filled/filter.svg",
    ),
```

- [ ] **Step 2: Run the existing vendor-asset test suite**

Run: `hatch test tests/test_vendor_assets.py -v`
Expected: PASS — this suite iterates `ASSETS` generically (fetch + SHA-256 verification), so the
two new entries are covered without a dedicated new test, the same as every icon added before them.

- [ ] **Step 3: Verify the fetch pipeline end-to-end against the real hash**

Run (writes to a throwaway temp dir, confirms the pinned SHA-256 matches the real file at unpkg):

```bash
hatch run python -c "
from pathlib import Path
from fledermap.services.vendor_assets import fetch_all
import tempfile
d = Path(tempfile.mkdtemp())
fetch_all(d)
print((d / 'icons/outline/filter.svg').exists())
print((d / 'icons/filled/filter.svg').exists())
"
```

Expected: `True` / `True`

- [ ] **Step 4: Commit**

```bash
git add src/fledermap/services/vendor_assets.py
git commit -m "feat: vendor the filter icon for the Denoise toggle"
```

---

## Task 9: `static/url_params.js` — pure query-param helper + node test

**Files:**
- Create: `src/fledermap/web/static/url_params.js`
- Test: `tests/js/url_params.test.js`

**Interfaces:**
- Produces: `withQueryParam(url: string, key: string, value: string): string`

**Note:** extracted as its own file with a CommonJS export guard, following the established
`marker_colors.js`/`classifier_logic.js` pattern (CLAUDE.md's JavaScript tooling section) — this
is pure, DOM-independent logic and belongs in `node:test`, not left to the mandatory-but-manual
headless-Chrome pass alone.

- [ ] **Step 1: Write the failing test**

```javascript
// tests/js/url_params.test.js
const test = require("node:test");
const assert = require("node:assert");
const { withQueryParam } = require("../../src/fledermap/web/static/url_params.js");

test("adds a new query param to a URL with no query string", () => {
  assert.strictEqual(
    withQueryParam("/recordings/abc/detail-spectrogram/0.webp", "denoise", "true"),
    "/recordings/abc/detail-spectrogram/0.webp?denoise=true",
  );
});

test("adds a new query param to a URL that already has one", () => {
  assert.strictEqual(
    withQueryParam("/recordings/abc/het-preview.opus?freq_hz=40000", "denoise", "true"),
    "/recordings/abc/het-preview.opus?freq_hz=40000&denoise=true",
  );
});

test("replaces an existing occurrence of the same param instead of duplicating it", () => {
  assert.strictEqual(
    withQueryParam("/x?denoise=true&freq_hz=40000", "denoise", "false"),
    "/x?denoise=false&freq_hz=40000",
  );
});

test("leaves the FREQ_HZ template placeholder in a het preview URL template untouched", () => {
  assert.strictEqual(
    withQueryParam("/recordings/abc/het-preview.opus?freq_hz=FREQ_HZ", "denoise", "true"),
    "/recordings/abc/het-preview.opus?freq_hz=FREQ_HZ&denoise=true",
  );
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/js/url_params.test.js`
Expected: FAIL — `Cannot find module '../../src/fledermap/web/static/url_params.js'`

- [ ] **Step 3: Write minimal implementation**

```javascript
// src/fledermap/web/static/url_params.js
//
// Pure URL query-param manipulation, no DOM access -- extracted as its own file
// so it's require()-able directly in `node:test`, following the established
// marker_colors.js/classifier_logic.js split pattern (CLAUDE.md's JavaScript
// tooling section: a file with top-level DOM access can't be require()-d).

function withQueryParam(url, key, value) {
  const [path, query] = url.split("?");
  const params = new URLSearchParams(query || "");
  params.set(key, value);
  return `${path}?${params.toString()}`;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { withQueryParam };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test tests/js/url_params.test.js`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full JS suite to confirm nothing else broke**

Run: `node --test tests/js/`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/static/url_params.js tests/js/url_params.test.js
git commit -m "feat: add withQueryParam helper for the denoise toggle's URL wiring"
```

---

## Task 10: `audio_controls.js` — dynamic preview-URL hooks + `refreshSource()`

**Files:**
- Modify: `src/fledermap/web/static/audio_controls.js`

**Interfaces:**
- Consumes: nothing new
- Produces: `initAudioControls` accepts optional `options.getPreviewUrl: () => string` and
  `options.getHetExtraQuery: () => string`; the returned object gains `refreshSource: () => void`

**Note:** no dedicated node test for this file — it's DOM-heavy (`container.querySelector`,
`localStorage`, a real `<audio>` element) with zero existing `node:test` coverage today for
exactly that reason (CLAUDE.md: "Only pure, DOM-independent logic is unit-tested this way").
Verified instead by Task 13's mandatory headless-Chrome pass.

- [ ] **Step 1: Add the two optional hooks**

In `src/fledermap/web/static/audio_controls.js`, inside `initAudioControls`, after the existing
`const getSeekCeilingS = options.getSeekCeilingS || (() => null);` line:

```javascript
  // Optional hooks so a page with its own denoise-toggle concept (the recording detail page's
  // recording_detail.js) can make the TE/HET source reflect current toggle state. Default to
  // the original static dataset-driven behavior -- the drawer panel (app.js) never passes
  // these, so it's completely unaffected.
  const getPreviewUrl = options.getPreviewUrl || (() => previewUrl);
  const getHetExtraQuery = options.getHetExtraQuery || (() => "");
```

- [ ] **Step 2: Use the hooks in every place the static values were read**

Change `switchToTe`:

```javascript
  function switchToTe() {
    const restoreRealTimeS = currentRealTimeS();
    mode = "expanded";
    localStorage.setItem(MODE_STORAGE_KEY, mode);
    teButton.setAttribute("aria-pressed", "true");
    hetButton.setAttribute("aria-pressed", "false");
    freqControl.hidden = true;
    setSource(getPreviewUrl(), restoreRealTimeS);
  }
```

Change `hetUrlForFreq`:

```javascript
  function hetUrlForFreq(freqKhz) {
    return hetPreviewUrlTemplate.replace("FREQ_HZ", String(freqKhz * 1000)) + getHetExtraQuery();
  }
```

(every existing caller of `hetUrlForFreq` — `switchToHet`, the `freqInput` "input" listener, and
`freqReset`'s click handler — already goes through this one function, so none of them need their
own separate edit)

- [ ] **Step 3: Add `refreshSource()` and expose it**

Add before the final `return`:

```javascript
  // Re-applies whichever source the CURRENT mode should be playing, recomputed from
  // getPreviewUrl()/getHetExtraQuery() -- for an external toggle (the denoise toggle) to call
  // after it changes state, without duplicating switchToTe/switchToHet's own logic.
  function refreshSource() {
    const restoreRealTimeS = currentRealTimeS();
    if (mode === "expanded") {
      setSource(getPreviewUrl(), restoreRealTimeS);
    } else {
      setSource(hetUrlForFreq(freqInput.value), restoreRealTimeS);
    }
  }
```

Change the final `return`:

```javascript
  return {
    getTimeExpansionFactor: effectiveFactor,
    refreshSource,
  };
```

- [ ] **Step 4: Run the fast Python test subset to confirm nothing broke elsewhere**

Run: `hatch test -m "not db"`
Expected: PASS (this is a JS-only change; this step exists to confirm the fast suite is still
green before moving on, not because it exercises this file)

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/web/static/audio_controls.js
git commit -m "feat: add dynamic preview-URL hooks to audio_controls.js"
```

---

## Task 11: `recording_details.html` — Denoise toggle button + detail-preview URL wiring

**Files:**
- Modify: `src/fledermap/web/templates/recording_details.html`

**Interfaces:**
- Consumes: `icon("filter", filled=...)` (Task 8's vendored icon)
- Produces: `#denoise-toggle` button in `#detail-toolbar`; `data-detail-preview-url` attribute on
  `#detail-audio-controls`

- [ ] **Step 1: Add the toggle button to `#detail-toolbar`**

In `src/fledermap/web/templates/recording_details.html`, inside the `<div class="detail-toolbar" id="detail-toolbar">` block, immediately after the existing `#view-lock-toggle` `</button>` and
before the toolbar's closing `</div>` (placement per the spec's decision: joins `#detail-toolbar`
right after Lock view, not the audio-row):

```html
      <button type="button" id="denoise-toggle" aria-pressed="false" title="Highpass-filter and reduce background noise below 5kHz">
        <span class="filter-icon-outline">{{ icon("filter") }}</span>
        <span class="filter-icon-filled" hidden>{{ icon("filter", filled=True) }}</span>
        <span class="filter-label">Denoise</span>
      </button>
```

- [ ] **Step 2: Add the detail-preview URL as a data attribute**

In the same file, inside the `<div class="audio-controls" id="detail-audio-controls" ...>` opening
tag's existing `data-*` attribute list (alongside `data-preview-url`, `data-het-preview-url-template`,
etc.), add:

```html
        data-detail-preview-url="{{ url_for('media.detail_preview', audio_hash=recording.audio_hash) }}"
```

- [ ] **Step 3: Verify the page still renders (fast Python check)**

Run: `hatch test tests/test_recording_detail_view.py -v`
Expected: PASS — existing tests still pass since nothing about the page's existing assertions
changed; this confirms the new template markup doesn't break Jinja rendering.

- [ ] **Step 4: Commit**

```bash
git add src/fledermap/web/templates/recording_details.html
git commit -m "feat: add Denoise toggle button to the recording-details toolbar"
```

---

## Task 12: `recording_detail.js` — wire the Denoise toggle

**Files:**
- Modify: `src/fledermap/web/templates/recording_details.html` (script tag for Task 9's new file)
- Modify: `src/fledermap/web/static/recording_detail.js`

**Interfaces:**
- Consumes: `withQueryParam` (Task 9), `audioControls.refreshSource` (Task 10)

- [ ] **Step 1: Load `url_params.js` before `recording_detail.js`**

In `recording_details.html`'s `{% block scripts %}`, add a new `<script>` tag for `url_params.js`
BEFORE the existing `recording_detail.js` tag (order matters — `recording_detail.js` calls
`withQueryParam` at load time):

```html
  <script src="{{ url_for('static', filename='url_params.js') }}"></script>
```

- [ ] **Step 2: Add denoise state and the toggle's click handler**

**Ordering matters here:** `initAudioControls` calls `switchToTe()`/`switchToHet()` synchronously
as part of its own call (its last few lines, unchanged by Task 10), which in turn calls
`getPreviewUrl()`/`getHetExtraQuery()` immediately — so every variable those closures reference
(`denoiseOn`, `previewUrl`, `detailPreviewUrl`) must already be declared (via `const`/`let`)
**before** the `initAudioControls(...)` call is evaluated, not after. A `let` referenced in a
closure that runs before the `let` statement itself has executed is a `ReferenceError` (temporal
dead zone), even though the closure is only *defined*, not yet *called*, at that point in the
surrounding code.

In `src/fledermap/web/static/recording_detail.js`, replace the existing block:

```javascript
  const audio = document.getElementById("detail-audio") || document.createElement("audio");
  const audioControls = audioControlsEl
    ? initAudioControls(audioControlsEl, audio, {
        getSeekFloorS: () => (viewLocked && lockedStartS !== null ? lockedStartS : 0),
        getSeekCeilingS: () => (viewLocked ? currentViewEndTimeS() : null),
      })
    : { getTimeExpansionFactor: () => 1 };
```

with:

```javascript
  const audio = document.getElementById("detail-audio") || document.createElement("audio");
  const denoiseToggle = document.getElementById("denoise-toggle");
  const previewUrl = audioControlsEl ? audioControlsEl.dataset.previewUrl : null;
  const detailPreviewUrl = audioControlsEl ? audioControlsEl.dataset.detailPreviewUrl : null;
  let denoiseOn = false;
  const audioControls = audioControlsEl
    ? initAudioControls(audioControlsEl, audio, {
        getSeekFloorS: () => (viewLocked && lockedStartS !== null ? lockedStartS : 0),
        getSeekCeilingS: () => (viewLocked ? currentViewEndTimeS() : null),
        getPreviewUrl: () =>
          denoiseOn ? withQueryParam(detailPreviewUrl, "denoise", "true") : previewUrl,
        getHetExtraQuery: () => (denoiseOn ? "&denoise=true" : ""),
      })
    : { getTimeExpansionFactor: () => 1 };
```

(`denoiseOn` is declared `let`, not `const` — the click handler in Step 3 below reassigns it; the
two getter closures read its CURRENT value each time they're called, which is exactly why a later
reassignment is visible to `audioControls.refreshSource()` without needing to re-create the
closures)

- [ ] **Step 3: Wire tile reloading and the click handler**

Add near `revealWhenAllLoaded` (which this reuses) — a helper that reloads every tile with the
current denoise state, then the click handler itself. Place both after the `revealWhenAllLoaded`
function definition:

```javascript
  function reloadTilesWithDenoise() {
    spectrogramLoading.hidden = false;
    oscillogramLoading.hidden = false;
    spectrogramTiles.forEach((t) => {
      t.hidden = true;
      delete t.dataset.failed;
      t.src = withQueryParam(t.src, "denoise", denoiseOn ? "true" : "false");
    });
    oscillogramTiles.forEach((t) => {
      t.hidden = true;
      delete t.dataset.failed;
      t.src = withQueryParam(t.src, "denoise", denoiseOn ? "true" : "false");
    });
    revealWhenAllLoaded(spectrogramTiles, spectrogramLoading);
    revealWhenAllLoaded(oscillogramTiles, oscillogramLoading);
  }

  if (denoiseToggle) {
    const filterIconOutline = denoiseToggle.querySelector(".filter-icon-outline");
    const filterIconFilled = denoiseToggle.querySelector(".filter-icon-filled");
    denoiseToggle.addEventListener("click", () => {
      denoiseOn = !denoiseOn;
      denoiseToggle.setAttribute("aria-pressed", denoiseOn ? "true" : "false");
      filterIconOutline.hidden = denoiseOn;
      filterIconFilled.hidden = !denoiseOn;
      reloadTilesWithDenoise();
      audioControls.refreshSource();
    });
  }
```

- [ ] **Step 4: Run the fast Python test subset**

Run: `hatch test -m "not db"`
Expected: PASS (JS-only change; confirms nothing Python-side regressed)

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/web/templates/recording_details.html src/fledermap/web/static/recording_detail.js
git commit -m "feat: wire the Denoise toggle's click handler in recording_detail.js"
```

---

## Task 13: Mandatory headless-Chrome live verification

**Files:** none (verification only)

This task is not optional (CLAUDE.md's JavaScript tooling section: skipping this exact kind of
verification twice previously let two Critical bugs ship with a fully green test suite). Use the
technique in `reference-headless-chrome-live-verification-technique` (this session's own memory) —
drive a real headless Chrome against a running `fledermap serve` instance with a real ingested
recording.

- [ ] **Step 1: Start a throwaway demo instance**

Spin up a fresh PostGIS container + `fledermap serve` against a temp media/archive root, ingest at
least one real recording with `duration_s`/`samplerate_hz` set (so the details page's toolbar
actually renders — see `recording_details.html`'s `{% if params %}` gate).

- [ ] **Step 2: Open the recording-details page in headless Chrome and verify**

Check, against real rendered output (not just "no console error"):
- `#denoise-toggle` renders in `#detail-toolbar`, immediately after `#view-lock-toggle`, with the
  outline filter icon showing and `aria-pressed="false"`.
- Clicking it: `aria-pressed` flips to `"true"`, the icon swaps to filled, the spectrogram and
  oscillogram tiles visibly reload (network tab shows new requests with `denoise=true` in the
  query string), and the currently-loaded audio source changes (network tab shows either a new
  `detail-preview.opus?denoise=true` request for TE mode, or a `het-preview.opus?...&denoise=true`
  request for HET mode, depending on which mode was active).
- Switching TE ↔ HET while denoise is on keeps `denoise=true` in the new mode's request too (this
  is exactly what `getPreviewUrl`/`getHetExtraQuery` being read fresh on every `setSource` call,
  Task 10, is for — a stale/cached closure would silently drop it on a mode switch).
- Clicking the toggle again flips everything back to the un-denoised state.
- No console errors at any point in this sequence.

- [ ] **Step 3: Report findings and fix any issues found**

If anything above doesn't hold, fix it and re-verify live before moving on — do not mark this task
done on a passing test suite alone.

---

## Task 14: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Run the fast Python test subset**

Run: `hatch test -m "not db"`
Expected: all pass

- [ ] **Step 2: Run the full Python suite including db-marked tests (unsandboxed)**

Run (requires `dangerouslyDisableSandbox: true`, Docker is blocked by the sandbox otherwise):

```bash
hatch test -m db
```

Expected: all pass

- [ ] **Step 3: Run the full JS suite**

Run: `node --test tests/js/`
Expected: all pass

- [ ] **Step 4: Lint, format, and type-check**

Run:

```bash
hatch fmt
hatch run types:check
```

Expected: `hatch fmt` reports no changes needed (or only whitespace it fixed itself — re-run and
commit if so); `types:check` reports no errors.

- [ ] **Step 5: Update the design spec's status**

Change `**Status:** draft` to `**Status:** shipped 2026-09-14` (or the actual ship date) in
`docs/superpowers/specs/2026-09-14-fledermap-denoise-highpass-design.md`, matching this project's
existing convention of marking specs as shipped once their plan is fully executed and verified.

- [ ] **Step 6: Final commit**

```bash
git add docs/superpowers/specs/2026-09-14-fledermap-denoise-highpass-design.md
git commit -m "docs: mark denoise/highpass spec as shipped"
```

- [ ] **Step 7: Note the required deployment step**

Not something this task executes (no live deployment exists yet) — but record it clearly for
whoever deploys this change, since it's easy to miss: the `SpectrogramParams`/`OscillogramParams`
`params_hash` change means every already-ingested recording's drawer spectrogram/oscillogram will
show "not processed yet" (the drawer's routes 404 rather than rendering on demand, unlike the
details page) until `fledermap enqueue-media` is run once after deploying. If this project has a
CHANGELOG or release-notes file, add a line there; otherwise this plan and the spec's own Rollout
section are the record — mention it explicitly in the PR/commit description when this branch is
finished.

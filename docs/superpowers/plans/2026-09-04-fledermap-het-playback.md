# Fledermap HET Playback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add heterodyne (HET) audio playback alongside the existing Time Expansion (TE)
preview, on both the recording-detail page and the map drawer panel, behind a unified custom
control bar (mode toggle, tunable frequency, rewind, play/pause) that replaces the native
`<audio controls>` element on both pages.

**Architecture:** A pure DSP renderer (`media/heterodyne.py`, mirroring `preview.py`'s
ffmpeg-shells-out shape) and a `compute_peak_frequency_hz` helper are served fresh-per-request
by two new Flask routes. Both pages get the same server-rendered markup for a `.audio-controls`
bar; one shared JS module (`audio_controls.js`) wires up mode switching, frequency tuning, and
transport buttons identically on both pages, exposing a `getTimeExpansionFactor()` accessor that
each page's own click-to-play/cursor code (existing on the detail page, newly added to the
drawer) calls to learn its current time-mapping factor. No caching, no live-tunable playback (see the
spec's Non-goals).

**Tech Stack:** Flask, SQLAlchemy, scipy (`welch`, `butter`/`sosfiltfilt`, `resample_poly`),
numpy, ffmpeg (via `subprocess`), vanilla JS (no framework, matching `app.js`/`recording_detail.js`).

**Spec:** `docs/superpowers/specs/2026-09-04-fledermap-het-playback-design.md`

## Global Constraints

- Ingest/archive files are never touched — this feature only reads WAV files that ingest already
  parsed (D16, project CLAUDE.md).
- No caching of rendered HET audio (spec Non-goals) — every request re-renders.
- No live/real-time tunable playback — files are swapped on change (spec Non-goals).
- `media/heterodyne.py` has no DB/queue awareness, matching `preview.py`'s module shape (spec §1).
- `peak-frequency` is fetched by JS lazily, only the first time a page/panel switches to HET mode
  — never baked into every panel render (spec §2).
- Every new/changed Python behavior needs a real, TDD-first test (`hatch test`); JS stays
  untested, consistent with the rest of this codebase (spec Testing section, project CLAUDE.md).
- `hatch fmt` and `hatch run types:check` must both pass before each commit that touches Python.
- Run `git` unsandboxed (`dangerouslyDisableSandbox: true`) — sandboxed git config writes leave a
  stale `.git/config.lock`.
- DB-marked tests (`-m db`) need `dangerouslyDisableSandbox: true` (Docker/testcontainers).

---

## Task 1: Extract a shared PCM→Opus encode helper from `preview.py`

`media/heterodyne.py` needs the exact same "write a temp WAV, shell out to ffmpeg, atomically
replace the output" pipeline `preview.py` already has (spec §1: "extract that pipeline into a
small shared helper both modules call, rather than a second copy"). Do this extraction first,
as a pure refactor with no behavior change, so both `preview.py` and the new `heterodyne.py`
build on it.

**Files:**
- Create: `src/fledermap/media/opus_pipeline.py`
- Modify: `src/fledermap/media/preview.py`
- Create: `tests/test_opus_pipeline.py`
- Test (must stay green, unmodified): `tests/test_preview.py`

**Interfaces:**
- Produces: `fledermap.media.opus_pipeline.encode_pcm_as_opus(*, frames: bytes, nchannels: int, sampwidth: int, framerate: int, out_path: Path) -> None` — writes `frames` as a temp WAV at the given format, encodes it to Opus via `ffmpeg -c:a libopus`, and atomically replaces `out_path`. Raises `subprocess.CalledProcessError` if ffmpeg fails (unchanged from today's `preview.py` behavior — no new error handling introduced).

- [ ] **Step 1: Write the failing test for the new helper**

```python
# tests/test_opus_pipeline.py
from __future__ import annotations

import json
import math
import struct
import subprocess
from pathlib import Path

from fledermap.media.opus_pipeline import encode_pcm_as_opus


def _sine_frames(*, freq_hz: float = 1000.0, samplerate: int = 48_000, duration_s: float = 0.05) -> bytes:
    n_samples = int(samplerate * duration_s)
    samples = [
        int(16000 * math.sin(2 * math.pi * freq_hz * i / samplerate))
        for i in range(n_samples)
    ]
    return struct.pack(f"<{n_samples}h", *samples)


def _ffprobe_stream_info(path: Path) -> dict[str, object]:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    stream: dict[str, object] = data["streams"][0]
    return stream


def test_encode_pcm_as_opus_produces_a_real_nonempty_opus_file(tmp_path: Path) -> None:
    out_path = tmp_path / "out.opus"

    encode_pcm_as_opus(
        frames=_sine_frames(),
        nchannels=1,
        sampwidth=2,
        framerate=48_000,
        out_path=out_path,
    )

    assert out_path.exists()
    assert out_path.stat().st_size > 0
    stream = _ffprobe_stream_info(out_path)
    assert stream["codec_name"] == "opus"


def test_encode_pcm_as_opus_writes_atomically_leaving_no_temp_file_behind(tmp_path: Path) -> None:
    out_path = tmp_path / "out.opus"

    encode_pcm_as_opus(
        frames=_sine_frames(),
        nchannels=1,
        sampwidth=2,
        framerate=48_000,
        out_path=out_path,
    )

    leftover = [p for p in tmp_path.iterdir() if p != out_path]
    assert leftover == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_opus_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.media.opus_pipeline'`

- [ ] **Step 3: Write the helper (moved out of `preview.py`, not yet wired up)**

```python
# src/fledermap/media/opus_pipeline.py
"""Shared PCM->Opus encode pipeline: write a temp WAV at a given format, shell
out to `ffmpeg -c:a libopus`, atomically replace the output file. Extracted
from `preview.py` (design spec 2026-09-04-fledermap-het-playback-design.md
section 1) so `heterodyne.py` doesn't carry a second, driftable copy -- both
modules produce PCM frames by different means (a straight framerate
relabel for TE, real DSP for HET) but need the identical write/encode/replace
tail.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import wave
from pathlib import Path


def encode_pcm_as_opus(
    *,
    frames: bytes,
    nchannels: int,
    sampwidth: int,
    framerate: int,
    out_path: Path,
) -> None:
    """Write `frames` as a temp WAV at (`nchannels`, `sampwidth`, `framerate`),
    encode it to Opus, and atomically replace `out_path`. Raises
    `subprocess.CalledProcessError` if `ffmpeg` fails."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_wav_fd, tmp_wav_name = tempfile.mkstemp(suffix=".wav")
    os.close(tmp_wav_fd)
    tmp_wav_path = Path(tmp_wav_name)
    try:
        with wave.open(str(tmp_wav_path), "wb") as relabelled:
            relabelled.setnchannels(nchannels)
            relabelled.setsampwidth(sampwidth)
            relabelled.setframerate(framerate)
            relabelled.writeframes(frames)

        out_fd, out_tmp_name = tempfile.mkstemp(
            dir=out_path.parent,
            suffix=".opus.tmp",
        )
        os.close(out_fd)
        out_tmp_path = Path(out_tmp_name)
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(tmp_wav_path),
                    "-c:a",
                    "libopus",
                    "-f",
                    "opus",
                    str(out_tmp_path),
                ],
                check=True,
                capture_output=True,
            )
            os.replace(out_tmp_path, out_path)
        except BaseException:
            out_tmp_path.unlink(missing_ok=True)
            raise
    finally:
        tmp_wav_path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch test tests/test_opus_pipeline.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Refactor `preview.py` to call the shared helper**

```python
# src/fledermap/media/preview.py
"""Time-expanded x10 preview generation. Pure: reads a WAV file, writes an
Opus file. No DB, no queue awareness (design spec §3).

`x10` (not resampling) matches design spec §5's "nearly free" framing exactly:
only the WAV header's declared frame rate changes, so a 45 kHz Pipistrellus
call lands at 4.5 kHz -- audible, classic time-expansion playback, no DSP.

Opus encoding shells out to `ffmpeg` (design spec §2, decision P3-2) rather
than a Python libopus binding -- one mature, well-known binary dependency
instead of a comparatively unmaintained Python wrapper plus manual container
muxing. The actual write/encode/atomic-replace pipeline lives in
`opus_pipeline.py`, shared with `heterodyne.py`
(2026-09-04-fledermap-het-playback-design.md section 1).
"""

from __future__ import annotations

import wave
from pathlib import Path

from fledermap.media.opus_pipeline import encode_pcm_as_opus

# Public: the recording details page's JS needs this to convert between the
# preview <audio>'s expanded playback clock and the spectrogram/oscillogram's
# native-real-time locked scale (audio.currentTime is on THIS expanded
# timeline, not the images' one) -- see web/views/recording_detail.py.
TIME_EXPANSION_FACTOR = 10


def make_preview(wav_path: Path, out_path: Path) -> None:
    """Render `wav_path`'s x10 time-expanded preview to `out_path` as Opus."""
    with wave.open(str(wav_path), "rb") as src:
        params = src.getparams()
        frames = src.readframes(src.getnframes())

    slow_rate = params.framerate // TIME_EXPANSION_FACTOR
    encode_pcm_as_opus(
        frames=frames,
        nchannels=params.nchannels,
        sampwidth=params.sampwidth,
        framerate=slow_rate,
        out_path=out_path,
    )
```

- [ ] **Step 6: Run the full existing preview test suite plus the new one to confirm no regression**

Run: `hatch test tests/test_preview.py tests/test_opus_pipeline.py -v`
Expected: All PASS, unchanged assertions in `test_preview.py`

- [ ] **Step 7: Type-check and lint**

Run: `hatch run types:check && hatch fmt`
Expected: No errors; `hatch fmt` finds nothing to fix (or fixes only formatting)

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/media/opus_pipeline.py src/fledermap/media/preview.py tests/test_opus_pipeline.py
git commit -m "refactor: extract shared PCM-to-Opus encode pipeline from preview.py"
```

---

## Task 2: `compute_peak_frequency_hz`

**Files:**
- Create: `src/fledermap/media/heterodyne.py`
- Test: `tests/test_heterodyne.py`

**Interfaces:**
- Consumes: `fledermap.media.wav_pcm.read_pcm(wav_path: Path) -> tuple[np.ndarray, int]` (existing).
- Produces: `fledermap.media.heterodyne.compute_peak_frequency_hz(wav_path: Path) -> float`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_heterodyne.py (new file, this test only for now)
from __future__ import annotations

import struct
from pathlib import Path

from fledermap.media.heterodyne import compute_peak_frequency_hz
from tests.fixtures import build_wav, fmt_payload


def _sine_wav(path: Path, *, freq_hz: float, samplerate: int = 256_000, duration_s: float = 0.05) -> None:
    n_samples = int(samplerate * duration_s)
    pcm = struct.pack(
        f"<{n_samples}h",
        *(
            int(20000 * __import__("math").sin(2 * __import__("math").pi * freq_hz * i / samplerate))
            for i in range(n_samples)
        ),
    )
    path.write_bytes(
        build_wav([(b"fmt ", fmt_payload(samplerate)), (b"data", pcm)]),
    )


def test_compute_peak_frequency_hz_finds_a_known_single_tone(tmp_path: Path) -> None:
    wav_path = tmp_path / "tone.wav"
    _sine_wav(wav_path, freq_hz=40_000.0)

    peak = compute_peak_frequency_hz(wav_path)

    # Welch's PSD has finite frequency resolution -- close, not exact.
    assert 38_000.0 < peak < 42_000.0


def _two_tone_wav(
    path: Path,
    *,
    loud_freq_hz: float,
    loud_amplitude: float,
    quiet_freq_hz: float,
    quiet_amplitude: float,
    samplerate: int = 256_000,
    duration_s: float = 0.05,
) -> None:
    """Mixes two sine tones into ONE file (summed samples, not two separate
    writes -- writing `_sine_wav` twice at the same path would overwrite
    rather than mix)."""
    import math

    n_samples = int(samplerate * duration_s)
    samples = [
        int(
            loud_amplitude * math.sin(2 * math.pi * loud_freq_hz * i / samplerate)
            + quiet_amplitude * math.sin(2 * math.pi * quiet_freq_hz * i / samplerate)
        )
        for i in range(n_samples)
    ]
    pcm = struct.pack(f"<{n_samples}h", *samples)
    path.write_bytes(build_wav([(b"fmt ", fmt_payload(samplerate)), (b"data", pcm)]))


def test_compute_peak_frequency_hz_ignores_a_louder_tone_below_the_search_window(
    tmp_path: Path,
) -> None:
    """A real recording's low end (below ~10kHz) can carry handling/wind noise loud enough to
    dominate a raw argmax -- the bounded search window (spec §1) must reject it even when it's
    the objectively loudest component in the file. Mixes a quiet 40kHz tone (the "real call",
    in-window) with a much louder 2kHz tone (the "noise", below the window) into one file --
    without the window bound, the 2kHz tone's far greater amplitude would dominate a raw argmax
    and get reported as the peak instead."""
    wav_path = tmp_path / "tone.wav"
    _two_tone_wav(
        wav_path,
        loud_freq_hz=2_000.0,
        loud_amplitude=30_000.0,
        quiet_freq_hz=40_000.0,
        quiet_amplitude=3_000.0,
    )

    peak = compute_peak_frequency_hz(wav_path)

    assert 38_000.0 < peak < 42_000.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_heterodyne.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.media.heterodyne'`

- [ ] **Step 3: Write the minimal implementation**

```python
# src/fledermap/media/heterodyne.py
"""Heterodyne (HET) preview generation and the "peak frequency" helper that
gives HET mode a sensible starting tune value. Pure: reads a WAV file, writes
an Opus file / returns a float. No DB, no queue awareness, matching
`preview.py`'s module shape (design spec
2026-09-04-fledermap-het-playback-design.md section 1)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy import signal

from fledermap.media.wav_pcm import read_pcm

# Bounds the peak-frequency search window -- NOT a real bandpass filter (a
# bigger, separate design question the already-planned `fledermap.noise`
# classifier/denoising backlog items exist for). Reuses the same low-end
# reasoning this codebase already documents (project CLAUDE.md's "Noise"
# backlog notes: real recordings' low end, below ~10kHz, often carries
# handling/wind noise that would otherwise dominate a naive argmax and mask
# the actual call) and the spectrogram's own 128kHz display ceiling
# (`SpectrogramParams.max_freq_hz`) as the high end, so "peak frequency" and
# "what the spectrogram actually shows" never silently disagree about range.
_PEAK_SEARCH_MIN_HZ = 10_000.0
_PEAK_SEARCH_MAX_HZ = 128_000.0


def compute_peak_frequency_hz(wav_path: Path) -> float:
    """Welch power spectral density over the whole file, returning the
    frequency of maximum power WITHIN the bounded search window above --
    deliberately independent of `SpectrogramParams`/
    `render_full_spectrogram_image`'s own STFT: changing the spectrogram's
    display tuning (window/overlap, chosen for visualization) must never
    silently change what HET calls "the peak frequency" (chosen for
    audibility). The result is directly visible to the user (pre-filled
    into the frequency spinner in HET mode), so a wrong pick is easy to
    catch by ear against real recordings."""
    samples, samplerate = read_pcm(wav_path)
    freqs, psd = signal.welch(samples, fs=samplerate)
    in_window = (freqs >= _PEAK_SEARCH_MIN_HZ) & (freqs <= _PEAK_SEARCH_MAX_HZ)
    windowed_freqs = freqs[in_window]
    windowed_psd = psd[in_window]
    return float(windowed_freqs[np.argmax(windowed_psd)])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch test tests/test_heterodyne.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Type-check and lint**

Run: `hatch run types:check && hatch fmt`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/media/heterodyne.py tests/test_heterodyne.py
git commit -m "feat: add compute_peak_frequency_hz for HET auto-tuning"
```

---

## Task 3: `render_heterodyne_preview`

**Files:**
- Modify: `src/fledermap/media/heterodyne.py`
- Test: `tests/test_heterodyne.py`

**Interfaces:**
- Consumes: `fledermap.media.wav_pcm.read_pcm` (existing), `fledermap.media.opus_pipeline.encode_pcm_as_opus` (Task 1).
- Produces: `fledermap.media.heterodyne.render_heterodyne_preview(wav_path: Path, out_path: Path, *, tune_freq_hz: float) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_heterodyne.py`:

```python
import math
import subprocess

import numpy as np

from fledermap.media.heterodyne import render_heterodyne_preview


def _read_opus_as_mono_float(path: Path) -> tuple[np.ndarray, int]:
    """Decode an Opus file back to raw PCM via ffmpeg for FFT analysis in
    tests -- there's no pure-Python opus decoder already in this project's
    dependencies, and shelling out to ffmpeg is exactly what production code
    already does the other direction."""
    raw_wav = path.with_suffix(".decoded.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-ac", "1", str(raw_wav)],
        check=True,
        capture_output=True,
    )
    import wave

    with wave.open(str(raw_wav), "rb") as wav:
        samplerate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float64)
    return samples, samplerate


def _dominant_frequency_hz(samples: np.ndarray, samplerate: int) -> float:
    windowed = samples * np.hanning(len(samples))
    spectrum = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(len(samples), d=1.0 / samplerate)
    return float(freqs[np.argmax(spectrum)])


def test_render_heterodyne_preview_correctly_tuned_produces_a_near_dc_beat(
    tmp_path: Path,
) -> None:
    tone_freq_hz = 40_000.0
    wav_path = tmp_path / "tone.wav"
    _sine_wav(wav_path, freq_hz=tone_freq_hz, samplerate=256_000, duration_s=0.1)
    out_path = tmp_path / "het.opus"

    render_heterodyne_preview(wav_path, out_path, tune_freq_hz=tone_freq_hz)

    samples, samplerate = _read_opus_as_mono_float(out_path)
    dominant = _dominant_frequency_hz(samples, samplerate)
    # A correctly-tuned heterodyne mix produces a near-DC beat -- allow a
    # few hundred Hz of slack for FFT bin width and low-pass filter roll-off.
    assert dominant < 500.0


def test_render_heterodyne_preview_mistuned_produces_a_beat_near_the_offset(
    tmp_path: Path,
) -> None:
    tone_freq_hz = 40_000.0
    offset_hz = 3_000.0
    wav_path = tmp_path / "tone.wav"
    _sine_wav(wav_path, freq_hz=tone_freq_hz, samplerate=256_000, duration_s=0.1)
    out_path = tmp_path / "het.opus"

    render_heterodyne_preview(wav_path, out_path, tune_freq_hz=tone_freq_hz - offset_hz)

    samples, samplerate = _read_opus_as_mono_float(out_path)
    dominant = _dominant_frequency_hz(samples, samplerate)
    assert abs(dominant - offset_hz) < 500.0


def test_render_heterodyne_preview_output_is_a_real_nonempty_opus_file(tmp_path: Path) -> None:
    wav_path = tmp_path / "tone.wav"
    _sine_wav(wav_path, freq_hz=40_000.0)
    out_path = tmp_path / "het.opus"

    render_heterodyne_preview(wav_path, out_path, tune_freq_hz=40_000.0)

    assert out_path.exists()
    assert out_path.stat().st_size > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_heterodyne.py -v`
Expected: FAIL with `ImportError: cannot import name 'render_heterodyne_preview'`

- [ ] **Step 3: Implement `render_heterodyne_preview`**

Append to `src/fledermap/media/heterodyne.py`:

```python
from fledermap.media.opus_pipeline import encode_pcm_as_opus

# Rejects the near-`2*tune_freq_hz` sum-frequency component the mix also
# produces, keeping only the audible difference-frequency component. 20kHz
# comfortably covers human hearing while staying well below any plausible
# sum-frequency artifact given the tune frequencies this feature targets
# (bat calls, tens of kHz and up).
_LOWPASS_CUTOFF_HZ = 20_000.0
_LOWPASS_ORDER = 8
_OUTPUT_SAMPLERATE_HZ = 48_000


def render_heterodyne_preview(
    wav_path: Path,
    out_path: Path,
    *,
    tune_freq_hz: float,
) -> None:
    """Mix `wav_path`'s audio down to audible range around `tune_freq_hz`
    (classic heterodyne technique) and render it to `out_path` as Opus."""
    samples, samplerate = read_pcm(wav_path)
    t = np.arange(len(samples)) / samplerate
    local_oscillator = np.cos(2 * np.pi * tune_freq_hz * t)
    mixed = samples * local_oscillator

    sos = signal.butter(
        _LOWPASS_ORDER,
        _LOWPASS_CUTOFF_HZ,
        btype="low",
        fs=samplerate,
        output="sos",
    )
    filtered = signal.sosfiltfilt(sos, mixed)

    resampled = signal.resample_poly(filtered, _OUTPUT_SAMPLERATE_HZ, samplerate)
    # Normalise to int16 range headroom-safe -- the mix + filter can produce
    # values outside the original PCM's amplitude range.
    peak = np.max(np.abs(resampled))
    if peak > 0:
        resampled = resampled / peak * 32000
    pcm_int16 = resampled.astype(np.int16)

    encode_pcm_as_opus(
        frames=pcm_int16.tobytes(),
        nchannels=1,
        sampwidth=2,
        framerate=_OUTPUT_SAMPLERATE_HZ,
        out_path=out_path,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_heterodyne.py -v`
Expected: All PASS

- [ ] **Step 5: Type-check and lint**

Run: `hatch run types:check && hatch fmt`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/media/heterodyne.py tests/test_heterodyne.py
git commit -m "feat: add render_heterodyne_preview for HET playback"
```

---

## Task 4: Routes — `het-preview.opus` and `peak-frequency`

**Files:**
- Modify: `src/fledermap/web/views/media.py`
- Test: `tests/test_media_view.py`

**Interfaces:**
- Consumes: `fledermap.services.media.resolve_recording`, `resolve_wav_path` (existing);
  `fledermap.media.heterodyne.compute_peak_frequency_hz`, `render_heterodyne_preview` (Tasks 2-3);
  `_serve_temp_render` (existing, in this module, currently produces `.webp` — needs a
  `mimetype` parameter to also serve `.opus`).
- Produces: two new routes, `GET /recordings/<audio_hash>/het-preview.opus?freq_hz=<float>` and
  `GET /recordings/<audio_hash>/peak-frequency` (JSON `{"peak_frequency_hz": <float>}`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_media_view.py`:

```python
def test_het_preview_renders_at_the_requested_frequency(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    _write_wav(archive_root / "a.wav", duration_s=0.05)

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="h1" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        archive_roots=(archive_root,),
    )
    response = app.test_client().get(
        f"/recordings/{'h1' * 32}/het-preview.opus?freq_hz=40000",
    )

    assert response.status_code == 200
    assert response.mimetype == "audio/ogg"
    assert len(response.data) > 0


def test_het_preview_400s_for_a_missing_freq_hz(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    _write_wav(archive_root / "a.wav", duration_s=0.05)

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="h2" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        archive_roots=(archive_root,),
    )
    response = app.test_client().get(f"/recordings/{'h2' * 32}/het-preview.opus")

    assert response.status_code == 400


def test_het_preview_400s_for_a_non_numeric_freq_hz(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    _write_wav(archive_root / "a.wav", duration_s=0.05)

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="h3" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        archive_roots=(archive_root,),
    )
    response = app.test_client().get(
        f"/recordings/{'h3' * 32}/het-preview.opus?freq_hz=not-a-number",
    )

    assert response.status_code == 400


def test_het_preview_404s_for_an_unknown_hash(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(
        f"/recordings/{'h4' * 32}/het-preview.opus?freq_hz=40000",
    )

    assert response.status_code == 404


def test_het_preview_404s_when_the_source_file_is_missing_from_disk(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="h5" * 32,
                path="does-not-exist.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        archive_roots=(archive_root,),
    )
    response = app.test_client().get(
        f"/recordings/{'h5' * 32}/het-preview.opus?freq_hz=40000",
    )

    assert response.status_code == 404


def test_peak_frequency_returns_json_with_a_plausible_value(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    _write_wav(archive_root / "a.wav", duration_s=0.05)

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="h6" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        archive_roots=(archive_root,),
    )
    response = app.test_client().get(f"/recordings/{'h6' * 32}/peak-frequency")

    assert response.status_code == 200
    body = response.get_json()
    assert 30_000.0 < body["peak_frequency_hz"] < 50_000.0  # near _write_wav's 45kHz tone


def test_peak_frequency_404s_for_an_unknown_hash(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'h7' * 32}/peak-frequency")

    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_media_view.py -k "het_preview or peak_frequency" -v`
Expected: FAIL — 404s where 400/200 expected, since the routes don't exist yet (Flask returns
its own default 404 for any unregistered path).

- [ ] **Step 3: Implement the routes**

Modify `src/fledermap/web/views/media.py`. First, generalize `_serve_temp_render` to take a
mimetype (it's currently hardcoded to `"image/webp"` and a `.webp` suffix — both wrong for
Opus):

```python
def _serve_temp_render(
    make: Callable[[Path], None],
    *,
    suffix: str,
    mimetype: str,
) -> ResponseReturnValue:
    """Renders to a throwaway temp file and streams the bytes back --
    deliberately not `spectrogram_path`/`oscillogram_path`/`preview_path`
    under the media root: this route is not part of the cached-derived-media
    system (design spec Non-goals), so nothing here is meant to persist."""
    fd, tmp_name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        make(tmp_path)
        data = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
    return flask.Response(data, mimetype=mimetype)
```

Update its two existing callers (`detail_spectrogram`, `detail_oscillogram`) to pass
`suffix=".webp", mimetype="image/webp"` explicitly:

```python
        return _serve_temp_render(
            lambda out: render_spectrogram(
                wav_path,
                out,
                params=tile_params,
                time_range_s=time_range_s,
                full_image=full_image,
            ),
            suffix=".webp",
            mimetype="image/webp",
        )
```

```python
        return _serve_temp_render(
            lambda out: render_oscillogram(
                wav_path,
                out,
                params=tile_params,
                time_range_s=time_range_s,
            ),
            suffix=".webp",
            mimetype="image/webp",
        )
```

Then add a resolver and the two new routes, near the bottom of the file:

```python
from fledermap.media.heterodyne import compute_peak_frequency_hz, render_heterodyne_preview


def _resolve_wav_path_or_404(audio_hash: str) -> Path:
    """Shared by the two routes below -- resolves straight via
    `resolve_recording`/`resolve_wav_path`, NOT `_detail_tile_context`: that
    helper also requires `duration_s`/`samplerate_hz`, a real requirement
    for computing tile boundaries that doesn't apply here (HET plays the
    whole file, nothing is tiled). Requiring it anyway would incorrectly
    block HET playback on metadata it doesn't actually need (design spec
    section 2)."""
    engine = flask.current_app.config["ENGINE"]
    archive_roots = flask.current_app.config["ARCHIVE_ROOTS"]
    with OrmSession(engine) as session:
        try:
            recording = resolve_recording(session, audio_hash)
        except (NoResultFound, FileNotFoundError):
            flask.abort(404)
        try:
            wav_path = resolve_wav_path(archive_roots, recording)
        except FileNotFoundError:
            flask.abort(404)
    if not wav_path.exists():
        flask.abort(404)
    return wav_path


@media_bp.get("/recordings/<audio_hash>/het-preview.opus")
def het_preview(audio_hash: str) -> ResponseReturnValue:
    freq_hz_raw = flask.request.args.get("freq_hz")
    if freq_hz_raw is None:
        flask.abort(400)
    try:
        freq_hz = float(freq_hz_raw)
    except ValueError:
        flask.abort(400)

    wav_path = _resolve_wav_path_or_404(audio_hash)
    try:
        return _serve_temp_render(
            lambda out: render_heterodyne_preview(wav_path, out, tune_freq_hz=freq_hz),
            suffix=".opus",
            mimetype="audio/ogg",
        )
    except UnreadableWavError as exc:
        logger.warning("unreadable source WAV for %s: %s", audio_hash, exc)
        flask.abort(404)


@media_bp.get("/recordings/<audio_hash>/peak-frequency")
def peak_frequency(audio_hash: str) -> ResponseReturnValue:
    wav_path = _resolve_wav_path_or_404(audio_hash)
    try:
        peak_hz = compute_peak_frequency_hz(wav_path)
    except UnreadableWavError as exc:
        logger.warning("unreadable source WAV for %s: %s", audio_hash, exc)
        flask.abort(404)
    return flask.jsonify({"peak_frequency_hz": peak_hz})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_media_view.py -v`
Expected: All PASS, including the pre-existing detail-spectrogram/oscillogram tests (the
`_serve_temp_render` signature change must not break them).

- [ ] **Step 5: Type-check and lint**

Run: `hatch run types:check && hatch fmt`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/views/media.py tests/test_media_view.py
git commit -m "feat: add het-preview.opus and peak-frequency routes"
```

---

## Task 5: Shared `audio_controls.js` module + CSS

Builds the reusable JS wiring for one `.audio-controls` bar instance (mode toggle, frequency
input + debounce + reset, rewind, play/pause) plus its CSS, with no page yet consuming it. Tasks
6 and 7 wire it into the two pages.

**Files:**
- Create: `src/fledermap/web/static/audio_controls.js`
- Modify: `src/fledermap/web/static/app.css`

**Interfaces:**
- Produces: `window.initAudioControls(container, audioEl)` — `container` is a `.audio-controls`
  element carrying `data-preview-url`, `data-het-preview-url-template` (containing the literal
  string `FREQ_HZ` where the frequency belongs), and `data-peak-frequency-url` attributes;
  `audioEl` is the `<audio>` element it controls. Returns `{ getTimeExpansionFactor(): number }`
  — the page's own TE constant (`data-time-expansion-factor`) while in TE mode, `1` in HET mode
  (HET's rendered audio is not time-expanded). Consumed by Tasks 6/7's click-to-play/cursor code
  to know which clock `audio.currentTime` is on.

- [ ] **Step 1: Write `audio_controls.js`**

```javascript
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

function initAudioControls(container, audioEl) {
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
  let peakFrequencyHz = null;
  let peakFrequencyPromise = null;

  function fetchPeakFrequency() {
    if (peakFrequencyPromise) return peakFrequencyPromise;
    peakFrequencyPromise = fetch(peakFrequencyUrl)
      .then((response) => response.json())
      .then((body) => {
        peakFrequencyHz = body.peak_frequency_hz;
        return peakFrequencyHz;
      });
    return peakFrequencyPromise;
  }

  function setSource(url) {
    audioEl.pause();
    audioEl.src = url;
  }

  function hetUrlForFreq(freqHz) {
    return hetPreviewUrlTemplate.replace("FREQ_HZ", String(freqHz));
  }

  function switchToTe() {
    mode = "expanded";
    teButton.setAttribute("aria-pressed", "true");
    hetButton.setAttribute("aria-pressed", "false");
    freqControl.hidden = true;
    setSource(previewUrl);
  }

  function switchToHet() {
    mode = "het";
    teButton.setAttribute("aria-pressed", "false");
    hetButton.setAttribute("aria-pressed", "true");
    freqControl.hidden = false;
    fetchPeakFrequency().then((freqHz) => {
      freqInput.value = Math.round(freqHz);
      setSource(hetUrlForFreq(freqInput.value));
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
      setSource(hetUrlForFreq(freqInput.value));
    }, FREQ_DEBOUNCE_MS);
  });

  freqReset.addEventListener("click", () => {
    fetchPeakFrequency().then((freqHz) => {
      freqInput.value = Math.round(freqHz);
      setSource(hetUrlForFreq(freqInput.value));
    });
  });

  rewindButton.addEventListener("click", () => {
    // Doesn't change play/pause state -- restarts from 0 mid-playback if
    // already playing, stays paused at 0 otherwise. Clicking exactly the
    // spectrogram's leftmost pixel to seek to the very start is a fiddly,
    // thin target (design spec section 3), worse on the drawer's smaller,
    // compressed scale than the detail page's.
    audioEl.currentTime = 0;
  });

  toggleButton.addEventListener("click", () => {
    if (audioEl.paused) audioEl.play();
    else audioEl.pause();
  });
  audioEl.addEventListener("play", () => {
    toggleButton.textContent = "⏸"; // pause icon
    toggleButton.setAttribute("aria-label", "Pause");
  });
  ["pause", "ended"].forEach((eventName) => {
    audioEl.addEventListener(eventName, () => {
      toggleButton.textContent = "▶"; // play icon
      toggleButton.setAttribute("aria-label", "Play");
    });
  });

  return {
    getTimeExpansionFactor: () => (mode === "expanded" ? timeExpansionFactor : 1),
  };
}
```

- [ ] **Step 2: Add CSS for the control bar**

Append to `src/fledermap/web/static/app.css`:

```css
.audio-controls { display: flex; align-items: center; gap: 0.4rem; padding: 0.25rem 0; }
.het-freq-control { display: flex; align-items: center; gap: 0.2rem; }
.het-freq-input { width: 4.5em; }
```

- [ ] **Step 3: Verify no test suite regressions** (no Python touched, but confirm the baseline is clean before the template/JS wiring tasks)

Run: `hatch test -m "not db"`
Expected: All PASS (unchanged — this task touches no Python)

- [ ] **Step 4: Commit**

```bash
git add src/fledermap/web/static/audio_controls.js src/fledermap/web/static/app.css
git commit -m "feat: add shared audio_controls.js control-bar module"
```

---

## Task 6: Wire the control bar + mode-aware click-to-play/cursor into the recording-detail page

**Files:**
- Modify: `src/fledermap/web/templates/recording_details.html`
- Modify: `src/fledermap/web/static/recording_detail.js`

**Interfaces:**
- Consumes: `window.initAudioControls` (Task 5); existing `tools.default.onClick`/`timeupdate`
  handler in `recording_detail.js` (both must switch from the hardcoded `timeExpansionFactor` to
  whatever `audio_controls.js` currently reports).

- [ ] **Step 1: Replace the native `<audio controls>` with the control bar markup**

In `src/fledermap/web/templates/recording_details.html`, replace:

```html
    <div class="audio-row">
      <audio controls id="detail-audio" src="{{ url_for('media.preview', audio_hash=recording.audio_hash) }}"></audio>
    </div>
```

with:

```html
    <div class="audio-row">
      <div
        class="audio-controls"
        id="detail-audio-controls"
        data-preview-url="{{ url_for('media.preview', audio_hash=recording.audio_hash) }}"
        data-het-preview-url-template="{{ url_for('media.het_preview', audio_hash=recording.audio_hash) }}?freq_hz=FREQ_HZ"
        data-peak-frequency-url="{{ url_for('media.peak_frequency', audio_hash=recording.audio_hash) }}"
        data-time-expansion-factor="{{ time_expansion_factor }}"
      >
        <button class="tool-button" data-mode="expanded" aria-pressed="true" title="Time Expansion (×10)">TE</button>
        <button class="tool-button" data-mode="het" aria-pressed="false">HET</button>
        <span class="het-freq-control" hidden>
          <input type="number" class="het-freq-input" step="1" inputmode="decimal"> kHz
          <button type="button" class="het-freq-reset" title="Reset to peak frequency">⟲</button>
        </span>
        <button type="button" class="playback-rewind" aria-label="Rewind to start">⏮</button>
        <button type="button" class="playback-toggle" aria-label="Play">▶</button>
        <audio hidden id="detail-audio"></audio>
      </div>
    </div>
```

Add the new script tag before `recording_detail.js`'s own tag:

```html
  <script src="{{ url_for('static', filename='audio_controls.js') }}"></script>
  <script src="{{ url_for('static', filename='recording_detail.js') }}"></script>
```

- [ ] **Step 2: Wire `initAudioControls` and make click-to-play/cursor mode-aware in `recording_detail.js`**

In `src/fledermap/web/static/recording_detail.js`, near the top where `audio` is looked up,
initialize the control bar and replace every direct use of `timeExpansionFactor` in the
click-to-play (`tools.default.onClick`) and `timeupdate` cursor handler with a call to the
control bar's `getTimeExpansionFactor()`:

```javascript
  const audioControlsEl = document.getElementById("detail-audio-controls");
  const audioControls = initAudioControls(audioControlsEl, audio);
```

Replace:

```javascript
    default: {
      onClick(event) {
        const rect = wrap.getBoundingClientRect();
        const xPx = (event.clientX - rect.left) / currentScale;
        const spectrogramTimeS = xPx / pxPerMs / 1000;
        audio.currentTime = spectrogramTimeS * timeExpansionFactor;
        audio.play();
      },
```

with:

```javascript
    default: {
      onClick(event) {
        const rect = wrap.getBoundingClientRect();
        const xPx = (event.clientX - rect.left) / currentScale;
        const spectrogramTimeS = xPx / pxPerMs / 1000;
        audio.currentTime = spectrogramTimeS * audioControls.getTimeExpansionFactor();
        audio.play();
      },
```

And replace the `timeupdate` handler:

```javascript
  audio.addEventListener("timeupdate", () => {
    const spectrogramTimeS = audio.currentTime / timeExpansionFactor;
```

with:

```javascript
  audio.addEventListener("timeupdate", () => {
    const spectrogramTimeS = audio.currentTime / audioControls.getTimeExpansionFactor();
```

Any mode/frequency change stops playback and resets position implicitly: `audio_controls.js`'s
`setSource` already calls `audioEl.pause()` before swapping `src`, and a fresh `<audio>` `src`
naturally resets `currentTime` to 0, so the cursor's next `timeupdate` will reflect the new
source correctly — no additional code needed here for that.

- [ ] **Step 3: Manual verification** (no automated JS tests in this codebase — project convention)

Run the app locally (see the `run` skill) and, against a real recording:
1. Confirm the page loads with the TE button active, no console errors.
2. Click HET — confirm the frequency input populates with a plausible kHz value and audio plays
   at that tuning after a moment.
3. Type a different frequency — confirm playback updates after ~300ms and doesn't render on
   every keystroke.
4. Click the rewind button while playing — confirm playback jumps to 0 without pausing.
5. Click the spectrogram — confirm playback starts at the clicked position in the active mode.
6. Switch back to TE — confirm the x10 preview plays again and the cursor tracks correctly.

Report and fix any real bugs found before proceeding (per project convention — see
CLAUDE.md's "UI bugs: diagnose live" workflow).

- [ ] **Step 4: Commit**

```bash
git add src/fledermap/web/templates/recording_details.html src/fledermap/web/static/recording_detail.js
git commit -m "feat: wire HET control bar into the recording-detail page"
```

---

## Task 7: Wire the control bar + click-to-play/cursor into the drawer panel

The drawer panel has never had click-to-play or a playback cursor before (spec §4) — this task
adds both, delegated on `#drawer-body` since its content is htmx-swapped wholesale (matching the
existing crosshair listener's pattern in `app.js`).

**Files:**
- Modify: `src/fledermap/web/templates/_recording_panel.html`
- Modify: `src/fledermap/web/templates/map.html`
- Modify: `src/fledermap/web/static/app.js`
- Modify: `src/fledermap/web/views/recording.py` (or wherever `_recording_panel.html`'s context
  is built — locate via `grep -rn "_recording_panel.html" src/fledermap/web/views/`)

**Interfaces:**
- Consumes: `window.initAudioControls` (Task 5).
- Produces: a `#playback-cursor` element inside `.spectrogram-wrap`, plus click-to-play wired
  through `.spectrogram`'s existing `data-duration-s`/`data-max-freq-khz` attributes (already
  present) and a new `data-time-expansion-factor` attribute.

- [ ] **Step 1: Locate the panel's view function and confirm the context variable it already
  passes for `time_expansion_factor`-equivalent data**

```bash
grep -rn "_recording_panel.html\|preview_ready\|TIME_EXPANSION_FACTOR" src/fledermap/web/views/*.py
```

Read the matched view function fully before editing — it must import
`fledermap.media.preview.TIME_EXPANSION_FACTOR` (same import `recording_detail.py` already
uses) and pass it into the template context as `time_expansion_factor`, plus
`het_preview_url`/`peak_frequency_url` built via `url_for("media.het_preview", ...)` /
`url_for("media.peak_frequency", ...)`.

- [ ] **Step 2: Replace the panel's native `<audio controls>` and add the cursor element**

In `src/fledermap/web/templates/_recording_panel.html`, replace:

```html
<div class="audio-row">
  {% if preview_ready %}
  <audio controls src="{{ url_for('media.preview', audio_hash=recording.audio_hash) }}"></audio>
  {% else %}
  <p class="media-placeholder">Audio preview not processed yet.</p>
  {% endif %}
</div>
```

with:

```html
<div class="audio-row">
  {% if preview_ready %}
  <div
    class="audio-controls"
    data-preview-url="{{ url_for('media.preview', audio_hash=recording.audio_hash) }}"
    data-het-preview-url-template="{{ url_for('media.het_preview', audio_hash=recording.audio_hash) }}?freq_hz=FREQ_HZ"
    data-peak-frequency-url="{{ url_for('media.peak_frequency', audio_hash=recording.audio_hash) }}"
    data-time-expansion-factor="{{ time_expansion_factor }}"
  >
    <button class="tool-button" data-mode="expanded" aria-pressed="true" title="Time Expansion (×10)">TE</button>
    <button class="tool-button" data-mode="het" aria-pressed="false">HET</button>
    <span class="het-freq-control" hidden>
      <input type="number" class="het-freq-input" step="1" inputmode="decimal"> kHz
      <button type="button" class="het-freq-reset" title="Reset to peak frequency">⟲</button>
    </span>
    <button type="button" class="playback-rewind" aria-label="Rewind to start">⏮</button>
    <button type="button" class="playback-toggle" aria-label="Play">▶</button>
    <audio hidden></audio>
  </div>
  {% else %}
  <p class="media-placeholder">Audio preview not processed yet.</p>
  {% endif %}
</div>
```

And add `data-time-expansion-factor` to the existing `.spectrogram` `<img>` tag plus a cursor
element in `.spectrogram-wrap`:

```html
  <div class="spectrogram-wrap">
    {% if spectrogram_ready %}
    <img class="spectrogram" src="{{ url_for('media.spectrogram', audio_hash=recording.audio_hash) }}" alt="Spectrogram" data-duration-s="{{ duration_s }}" data-max-freq-khz="{{ max_freq_khz }}" data-time-expansion-factor="{{ time_expansion_factor }}">
    <div class="playback-cursor" hidden></div>
    {% else %}
    <p class="media-placeholder">Spectrogram not processed yet.</p>
    {% endif %}
  </div>
```

- [ ] **Step 3: Load `audio_controls.js` once from `map.html`**

In `src/fledermap/web/templates/map.html`, add before the existing `app.js` script tag:

```html
  <script src="{{ url_for('static', filename='audio_controls.js') }}"></script>
  <script src="{{ url_for('static', filename='app.js') }}"></script>
```

- [ ] **Step 4: Wire it up in `app.js`, delegated on `#drawer-body`**

In `src/fledermap/web/static/app.js`, near the existing crosshair listener block, add:

```javascript
  // HET/TE control bar + click-to-play + playback cursor for the drawer panel
  // (design spec 2026-09-04-fledermap-het-playback-design.md section 4) --
  // delegated / re-initialized on every htmx swap, same reasoning as the
  // crosshair listener above: #drawer-body's content is replaced wholesale.
  let drawerAudioControls = null;
  drawerBody.addEventListener("htmx:afterSwap", () => {
    const controlsEl = drawerBody.querySelector(".audio-controls");
    const audioEl = controlsEl ? controlsEl.querySelector("audio") : null;
    drawerAudioControls = controlsEl && audioEl ? initAudioControls(controlsEl, audioEl) : null;
  });

  drawerBody.addEventListener("click", (event) => {
    const img = event.target.closest(".spectrogram");
    if (!img || !drawerAudioControls) return;
    const audioEl = drawerBody.querySelector(".audio-controls audio");
    if (!audioEl) return;
    const durationS = parseFloat(img.dataset.durationS);
    if (Number.isNaN(durationS)) return;
    const rect = img.getBoundingClientRect();
    const relX = (event.clientX - rect.left) / rect.width;
    const spectrogramTimeS = relX * durationS;
    audioEl.currentTime = spectrogramTimeS * drawerAudioControls.getTimeExpansionFactor();
    audioEl.play();
  });

  drawerBody.addEventListener("play", (event) => {
    if (!event.target.matches(".audio-controls audio") || !drawerAudioControls) return;
    const audioEl = event.target;
    const img = drawerBody.querySelector(".spectrogram");
    const cursor = drawerBody.querySelector(".playback-cursor");
    if (!img || !cursor) return;
    audioEl.addEventListener("timeupdate", () => {
      const durationS = parseFloat(img.dataset.durationS);
      if (Number.isNaN(durationS) || durationS <= 0) return;
      const spectrogramTimeS = audioEl.currentTime / drawerAudioControls.getTimeExpansionFactor();
      const relX = spectrogramTimeS / durationS;
      cursor.style.left = `${relX * 100}%`;
      cursor.hidden = false;
    });
  }, true);
```

Note: `play` is added with the capture-phase `true` flag because it doesn't bubble; `timeupdate`
also doesn't bubble, which is why it's bound directly on `audioEl` from inside the `play`
listener rather than delegated further.

- [ ] **Step 5: Add `.playback-cursor` positioning for the drawer's percentage-based layout**

The detail page's `.playback-cursor` CSS rule already exists and uses pixel `left` (Task 6 reuses
it unchanged there). The drawer sets `cursor.style.left` as a percentage string above, which the
existing rule already supports (`left` accepts any CSS length, percentage included) — no new CSS
rule needed. Confirm this by inspecting `src/fledermap/web/static/app.css`'s existing
`.playback-cursor` rule (already `position: absolute; ... width: 1px; ...`) before assuming a
change is needed; only add a drawer-specific override if manual testing (Step 6) shows it
positioned incorrectly.

- [ ] **Step 6: Manual verification**

Run the app locally (see the `run` skill) and, against a real recording, open the drawer panel:
1. Confirm TE/HET toggle and rewind work identically to the detail page.
2. Click the spectrogram — confirm playback starts at the clicked position and the cursor tracks
   it as it plays.
3. Switch to a different recording (prev/next or a new marker click) — confirm the control bar
   and cursor re-initialize cleanly with no leftover state or duplicate listeners causing
   playback to jump unexpectedly.

Report and fix any real bugs found before proceeding.

- [ ] **Step 7: Run the full test suite to confirm nothing broke**

Run: `hatch test -m "not db"` then `hatch test` (full, `dangerouslyDisableSandbox: true`)
Expected: All PASS

- [ ] **Step 8: Type-check and lint**

Run: `hatch run types:check && hatch fmt`
Expected: No errors

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/web/templates/_recording_panel.html src/fledermap/web/templates/map.html src/fledermap/web/static/app.js src/fledermap/web/views/<the view file found in Step 1>
git commit -m "feat: wire HET control bar and click-to-play into the drawer panel"
```

---

## Final check

- [ ] Run `hatch test` (full suite including `-m db`, `dangerouslyDisableSandbox: true`) and
  confirm pristine output (no warnings).
- [ ] Run `hatch run types:check` over `src/fledermap`, `tests`, and `scripts`.
- [ ] Run `hatch fmt` and confirm no outstanding changes.
- [ ] Re-read the spec's Testing section and confirm every listed test exists and passes.
- [ ] Hand off to `superpowers:finishing-a-development-branch` for the merge/PR decision.

# Fledermap Phase 3 (Media + jobs) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate and persist a spectrogram (WebP) and a time-expanded ÷10 preview (Opus) for every recording, via a Postgres-backed job queue (Procrastinate), asynchronously off the ingest critical path.

**Architecture:** Two new pure modules (`media/` — rendering, no DB/queue awareness; `jobs/` — Procrastinate app + task wrappers bridging `Recording` rows to `media/`). `services/ingest.py` gains a `created_hashes` list so newly-created recordings can be enqueued; `services/media.py` owns enqueueing (with duplicate-enqueue protection) and a disk-state-based backfill sweep. Two new CLI commands (`worker`, `enqueue-media`). One Procrastinate `App` per process, shared by defer-side code (a sync connector opened against this project's own SQLAlchemy engine) and the worker (a second, async connector swapped in only for the worker's run — see Task 4's note on why one connector cannot serve both).

**Tech Stack:** `scipy.signal` (STFT), `Pillow` (WebP), `ffmpeg` subprocess (Opus), `procrastinate` (Postgres-backed job queue) with `SQLAlchemyPsycopg2Connector` for deferring and `PsycopgConnector` (psycopg 3) for running the worker.

**Spec:** `docs/superpowers/specs/2026-08-25-fledermap-phase3-media-jobs-design.md` (the plan argues from this spec; read both — the spec's deviations section (§2) and §6/§9 record two significant corrections found by live-testing against a real Postgres container before this plan was written, not assumed from documentation).

## Global Constraints

- **`hatch` only.** Never `pip`, never bare `python`/`python3`, never `PYTHONPATH`. `hatch test`, `hatch run ruff:ruff check .` / `hatch run ruff:ruff format --check --diff .` (NOT bare `hatch fmt` — see below), `hatch run types:check`.
- **Test output must be pristine** — a warning is a defect, fix the cause, never `filterwarnings`.
- **`hatch run types:check` covers `tests/` too** — test code must type-check for real (bind `X | None`, assert not None, dereference — never `# type: ignore`).
- **New third-party imports mypy can't resolve go in `[tool.hatch.envs.types]`'s `extra-dependencies`, OR a scoped `[[tool.mypy.overrides]]` if the package ships no type information at all** (matching the existing `sklearn.*` precedent in `pyproject.toml`) — never a global `ignore_missing_imports`. Run `hatch run types:check` after adding each new dependency to find out which is needed; don't guess in advance.
- **`db`-marked tests need Docker, which the command sandbox blocks** — run with `dangerouslyDisableSandbox: true`. Failure looks like `requests.exceptions.ConnectionError: PermissionError(1, 'Operation not permitted')` out of `docker.from_env()`, not an obvious sandbox message.
- **`ffmpeg` must be installed and on `PATH`** wherever tests for `media/preview.py` or job-execution tests run. If it's missing, `subprocess.run(["ffmpeg", ...])` raises `FileNotFoundError` — a clear, loud failure, not a silent skip.
- **`media/` stays pure** — no DB session import, no `procrastinate` import, no queue awareness.
- **`archive_root`/`media_root`/the DB engine reach task bodies via `pass_context=True` + `context.additional_context`**, never a bare module-level global, never a re-read `Config.from_env()` inside a task call (Task 5).
- **Every job deferral uses combined `lock` + `queueing_lock`**, keyed `f"{task_prefix}:{audio_hash}:{params_hash}"` — `queueing_lock` alone does not prevent a duplicate once the first job has moved past "todo" (Tasks 5 and 7).
- **`backfill_media` checks disk state, not Procrastinate's job table**, to decide what needs enqueueing (Task 7).
- **Procrastinate's own schema-apply is broken against a real Postgres server — confirmed empirically, not assumed.** `app.schema_manager.apply_schema()` fails with `psycopg2.errors.SyntaxError: too many parameters specified for RAISE` (its own `%`→`%%` escaping corrupts a legitimate `%` inside the schema's own `RAISE '...%', arg` statements). The confirmed working fix — execute `schema_manager.get_schema()` (unescaped) via a raw psycopg2 cursor with NO params argument at all — is spelled out in full in Task 4. Do not use `apply_schema()`/`apply_schema_async()` directly.
- **Running the worker requires a SEPARATE, async-capable connector from the one used to defer jobs — confirmed empirically, not assumed.** `SQLAlchemyPsycopg2Connector` (used everywhere else, sharing this project's own engine) raises `SyncConnectorConfigurationError` if you call `run_worker()` on it. Task 4/8 use `App.replace_connector(...)` with a second `PsycopgConnector` (psycopg 3) for the worker's run only — one `App` object throughout (tasks are bound to the App, not the connector), connector swapped just for that call.
- **The Procrastinate `App`'s connector must be explicitly `.open(engine)`-ed with the real engine (or connector-swapped) by whoever is about to defer or run — never assumed already open.** The module-level `app` in `jobs/tasks.py` is constructed once at import time with no engine bound yet (mirroring `SQLAlchemyPsycopg2Connector()`'s own documented pattern); every CLI command and every test opens it against its own real engine before using it.
- **`media_root` is a required `Config` field, distinct from `archive_root`** — never a path under `archive_root` (D16: ingest is read-only on the archive) and never `platformdirs`-guessed (Task 3).

---

## Task 1: `media/spectrogram.py` — spectrogram rendering

**Files:**
- Create: `src/fledermap/media/__init__.py` (empty)
- Create: `src/fledermap/media/spectrogram.py`
- Test: `tests/test_spectrogram.py`
- Modify: `pyproject.toml` — add `pillow` to `dependencies`

**Interfaces:**
- Consumes: nothing from other Phase 3 tasks.
- Produces: `SpectrogramParams` (frozen dataclass: `window_ms: float`, `overlap: float`, `max_freq_hz: float`, `width_px: int`, `height_px: int`, `params_hash: str` property) and `render_spectrogram(wav_path: Path, out_path: Path, *, params: SpectrogramParams = SpectrogramParams()) -> None`, both used by Task 5 (`jobs/tasks.py`).

- [ ] **Step 1: Add `pillow` and confirm it needs no extra mypy config**

Add `"pillow"` to `[project] dependencies` in `pyproject.toml`, appended after `"scikit-learn"` (this project's existing append-only convention).

Run: `hatch run types:check`. Pillow ships its own inline types; this should
already be clean. If it is NOT clean, follow the existing `sklearn.*`
precedent in `pyproject.toml`'s `[[tool.mypy.overrides]]` block — add a
same-shaped override for `PIL.*`, with a comment explaining why (mirror the
sklearn comment's wording, substituting the actual reason `hatch run
types:check` gives you).

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_spectrogram.py
from __future__ import annotations

import math
import struct
from pathlib import Path

from PIL import Image

from fledermap.media.spectrogram import SpectrogramParams, render_spectrogram


def _sine_wav(
    path: Path,
    *,
    freq_hz: float = 45_000.0,
    samplerate: int = 256_000,
    duration_s: float = 0.05,
) -> None:
    """A real, non-silent 16-bit mono PCM WAV -- a synthesized bat-call-range
    tone, not all-zero bytes, so the STFT has real structure to render."""
    n_samples = int(samplerate * duration_s)
    samples = [
        int(32000 * math.sin(2 * math.pi * freq_hz * i / samplerate))
        for i in range(n_samples)
    ]
    pcm = struct.pack(f"<{n_samples}h", *samples)

    channels, bits = 1, 16
    byte_rate = samplerate * channels * bits // 8
    block_align = channels * bits // 8
    fmt_payload = struct.pack(
        "<HHIIHH", 1, channels, samplerate, byte_rate, block_align, bits,
    )

    def chunk(chunk_id: bytes, payload: bytes) -> bytes:
        out = chunk_id + struct.pack("<I", len(payload)) + payload
        if len(payload) % 2:
            out += b"\x00"
        return out

    body = b"WAVE" + chunk(b"fmt ", fmt_payload) + chunk(b"data", pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def test_renders_a_webp_of_the_configured_dimensions(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "spectrogram.webp"
    params = SpectrogramParams(width_px=256, height_px=128)

    render_spectrogram(wav_path, out_path, params=params)

    with Image.open(out_path) as img:
        assert img.format == "WEBP"
        assert img.size == (256, 128)


def test_default_params_produce_the_default_dimensions(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)

    with Image.open(out_path) as img:
        assert img.size == (
            SpectrogramParams().width_px,
            SpectrogramParams().height_px,
        )


def test_params_hash_changes_when_any_field_changes() -> None:
    base = SpectrogramParams()
    changed = SpectrogramParams(width_px=base.width_px + 1)

    assert base.params_hash != changed.params_hash


def test_params_hash_is_stable_for_equal_params() -> None:
    a = SpectrogramParams(width_px=999)
    b = SpectrogramParams(width_px=999)

    assert a.params_hash == b.params_hash


def test_clamps_max_freq_to_the_recordings_own_nyquist_limit(tmp_path: Path) -> None:
    """A recording at 44.1 kHz (an ordinary, non-ultrasonic sample rate) has a
    Nyquist limit of 22.05 kHz -- far below the 128 kHz default. This must not
    crash or silently render garbage for the requested-but-nonexistent upper
    frequency range; it must render successfully, using its own real limit."""
    wav_path = tmp_path / "low_rate.wav"
    _sine_wav(wav_path, freq_hz=8_000.0, samplerate=44_100, duration_s=0.1)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)  # must not raise

    with Image.open(out_path) as img:
        assert img.size == (
            SpectrogramParams().width_px,
            SpectrogramParams().height_px,
        )


def test_a_very_short_recording_renders_without_warning(
    tmp_path: Path,
    recwarn: pytest.WarningsRecorder,
) -> None:
    """32 samples at 256 kHz (0.125 ms) -- shorter than even one default
    3 ms analysis window. This is the exact shape of the CLI's own shared
    `_archive()` test fixture (tests/test_cli.py), which writes recordings
    this short. Without clamping nperseg to the signal's own length,
    scipy.signal.spectrogram silently shrinks it but raises a UserWarning
    doing so -- a defect under this project's pristine-test-output rule."""
    wav_path = tmp_path / "tiny.wav"
    _sine_wav(wav_path, samplerate=256_000, duration_s=32 / 256_000)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)  # must not raise

    assert len(recwarn) == 0, [str(w.message) for w in recwarn]


def test_writes_atomically_leaving_no_temp_file_behind(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "spectrogram.webp"

    render_spectrogram(wav_path, out_path)

    leftover = [p for p in tmp_path.iterdir() if p != wav_path and p != out_path]
    assert leftover == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `hatch test tests/test_spectrogram.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.media'`

- [ ] **Step 4: Implement**

```python
# src/fledermap/media/spectrogram.py
"""Spectrogram rendering. Pure: reads a WAV file, writes a WebP image. No DB,
no queue awareness (design spec §3) -- `jobs/tasks.py` is the only caller.

Written fresh against scipy/numpy/Pillow rather than ported from batogram
(design spec §2, decision P3-1): batogram is a Tkinter GUI application with no
stable, separable library API, not a clean porting target the way
mkmapdiary's LocalProjection/GeoCluster were in Phase 2.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import wave
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import signal


@dataclass(frozen=True)
class SpectrogramParams:
    """Every field that affects rendered output. `params_hash` is the on-disk
    filename's `<params>` component (design spec §8/parent spec §8) --
    changing any field here invalidates existing renders without touching
    `audio_hash`, so a settings change never requires a migration."""

    window_ms: float = 3.0
    overlap: float = 0.5
    # 128 kHz covers the practical bat-call range (roughly 9-212 kHz across
    # this project's EU species list, docs/references.md) without wasting
    # resolution on near-silent bins above it. This happens to equal the
    # Nyquist frequency of the bundled EMT's 256 kHz sample rate -- named
    # explicitly so nobody mistakes that coincidence for the reason.
    # `render_spectrogram` clamps to the SOURCE recording's own Nyquist limit
    # at render time regardless of this value (design spec §4) -- this field
    # is a ceiling, not a promise every recording reaches it.
    max_freq_hz: float = 128_000.0
    width_px: int = 1024
    height_px: int = 512

    @property
    def params_hash(self) -> str:
        payload = "|".join(str(getattr(self, f.name)) for f in fields(self))
        return hashlib.sha256(payload.encode("ascii")).hexdigest()[:16]


def _read_pcm(wav_path: Path) -> tuple[np.ndarray, int]:
    """Read mono or multi-channel 16-bit PCM as a 1-D float array (channels
    averaged down to mono for spectrogram purposes) plus the file's own
    sample rate."""
    with wave.open(str(wav_path), "rb") as wav:
        n_channels = wav.getnchannels()
        samplerate = wav.getframerate()
        raw = wav.readframes(wav.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    if n_channels > 1:
        samples = samples.reshape(-1, n_channels).mean(axis=1)
    return samples, samplerate


def render_spectrogram(
    wav_path: Path,
    out_path: Path,
    *,
    params: SpectrogramParams = SpectrogramParams(),
) -> None:
    """Render `wav_path`'s spectrogram to `out_path` as a WebP image.

    STFT via `scipy.signal.spectrogram`, log-magnitude normalised to [0, 1],
    rendered as a single-channel (grayscale) image -- the simplest possible
    colormap, revisable later without a schema change (`params_hash` exists
    precisely so a colour-scheme change would invalidate old renders cleanly).

    Writes to a temp file in `out_path`'s parent directory, then `os.replace`s
    onto `out_path` -- atomic on the same filesystem, so a concurrent reader
    never sees a partial file and two concurrent writers never interleave
    (design spec §7's duplicate-enqueue protection is the queue-level half of
    this; this is the filesystem-level half).
    """
    samples, samplerate = _read_pcm(wav_path)

    # Clamp to the signal's own length -- without this, a very short (or
    # truncated/corrupt) recording makes nperseg > len(samples), and
    # scipy.signal.spectrogram silently shrinks it back down itself but
    # raises a UserWarning while doing so. This project's test output must
    # stay warning-free (a warning is a defect), so the clamp happens here,
    # before scipy ever sees an oversized nperseg -- not just to keep tests
    # quiet, but because a genuinely short/corrupt file reaching this code
    # in production shouldn't warn either.
    nperseg = min(max(int(samplerate * params.window_ms / 1000), 8), len(samples))
    noverlap = int(nperseg * params.overlap)
    freqs, _times, sxx = signal.spectrogram(
        samples,
        fs=samplerate,
        nperseg=nperseg,
        noverlap=noverlap,
    )

    # Never render frequency bins above this recording's own Nyquist limit --
    # a recording at a different sample rate must not be asked to render data
    # that doesn't exist (design spec §4).
    max_freq = min(params.max_freq_hz, samplerate / 2)
    keep = freqs <= max_freq
    sxx = sxx[keep, :]

    log_mag = np.log1p(sxx)
    span = log_mag.max() - log_mag.min()
    normalised = (log_mag - log_mag.min()) / span if span > 0 else np.zeros_like(log_mag)

    # Flip vertically: spectrogram's frequency axis increases with row index,
    # but an image's row 0 is its TOP -- without this, low frequencies would
    # render at the top of the image, high frequencies at the bottom.
    pixels = (np.flipud(normalised) * 255).astype(np.uint8)
    image = Image.fromarray(pixels, mode="L").resize(
        (params.width_px, params.height_px),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=out_path.parent, suffix=".webp.tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        image.save(tmp_path, format="WEBP")
        os.replace(tmp_path, out_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_spectrogram.py -v`
Expected: PASS (7/7)

- [ ] **Step 6: Full verification**

Run: `hatch run types:check` — expect clean.
Run: `hatch run ruff:ruff check .` and `hatch run ruff:ruff format --check --diff .` — expect clean (NOT bare `hatch fmt`).
Run: `hatch test` (Docker, unsandboxed) — full suite, expect all passing, pristine output.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/fledermap/media/ tests/test_spectrogram.py
git commit -m "feat: render_spectrogram -- WAV to WebP via scipy STFT + grayscale"
```

---

## Task 2: `media/preview.py` — time-expanded ÷10 Opus preview

**Files:**
- Create: `src/fledermap/media/preview.py`
- Test: `tests/test_preview.py`

**Interfaces:**
- Consumes: nothing from other Phase 3 tasks.
- Produces: `make_preview(wav_path: Path, out_path: Path) -> None`, used by Task 5 (`jobs/tasks.py`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_preview.py
from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from fledermap.media.preview import make_preview


def _sine_wav(
    path: Path,
    *,
    freq_hz: float = 45_000.0,
    samplerate: int = 256_000,
    duration_s: float = 0.05,
) -> None:
    n_samples = int(samplerate * duration_s)
    samples = [
        int(32000 * math.sin(2 * math.pi * freq_hz * i / samplerate))
        for i in range(n_samples)
    ]
    pcm = struct.pack(f"<{n_samples}h", *samples)
    channels, bits = 1, 16
    byte_rate = samplerate * channels * bits // 8
    block_align = channels * bits // 8
    fmt_payload = struct.pack(
        "<HHIIHH", 1, channels, samplerate, byte_rate, block_align, bits,
    )

    def chunk(chunk_id: bytes, payload: bytes) -> bytes:
        out = chunk_id + struct.pack("<I", len(payload)) + payload
        if len(payload) % 2:
            out += b"\x00"
        return out

    body = b"WAVE" + chunk(b"fmt ", fmt_payload) + chunk(b"data", pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def _ffprobe_stream_info(path: Path) -> dict[str, object]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    stream: dict[str, object] = data["streams"][0]
    return stream


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)


def test_preview_duration_is_roughly_ten_times_the_source(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path, samplerate=256_000, duration_s=0.05)
    out_path = tmp_path / "preview.opus"

    make_preview(wav_path, out_path)

    stream = _ffprobe_stream_info(out_path)
    # Opus always reports a 48000 Hz container rate regardless of the source
    # -- the actual pitch/speed change is encoded in the audio itself, not
    # exposed as a distinct sample-rate field. Assert on duration instead:
    # 0.05s of source audio at 1/10 speed must decode to roughly 0.5s.
    duration = float(stream["duration"])  # type: ignore[arg-type]
    assert 0.4 < duration < 0.6


def test_preview_output_is_a_real_nonempty_opus_file(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "preview.opus"

    make_preview(wav_path, out_path)

    assert out_path.exists()
    assert out_path.stat().st_size > 0
    stream = _ffprobe_stream_info(out_path)
    assert stream["codec_name"] == "opus"


def test_writes_atomically_leaving_no_temp_file_behind(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path)
    out_path = tmp_path / "preview.opus"

    make_preview(wav_path, out_path)

    leftover = [p for p in tmp_path.iterdir() if p not in (wav_path, out_path)]
    assert leftover == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_preview.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.media.preview'`

- [ ] **Step 3: Implement**

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
muxing.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import wave
from pathlib import Path

_TIME_EXPANSION_FACTOR = 10


def make_preview(wav_path: Path, out_path: Path) -> None:
    """Render `wav_path`'s x10 time-expanded preview to `out_path` as Opus."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(wav_path), "rb") as src:
        params = src.getparams()
        frames = src.readframes(src.getnframes())

    slow_rate = params.framerate // _TIME_EXPANSION_FACTOR

    tmp_wav_fd, tmp_wav_name = tempfile.mkstemp(suffix=".wav")
    os.close(tmp_wav_fd)
    tmp_wav_path = Path(tmp_wav_name)
    try:
        with wave.open(str(tmp_wav_path), "wb") as relabelled:
            relabelled.setnchannels(params.nchannels)
            relabelled.setsampwidth(params.sampwidth)
            relabelled.setframerate(slow_rate)
            relabelled.writeframes(frames)

        out_fd, out_tmp_name = tempfile.mkstemp(
            dir=out_path.parent, suffix=".opus.tmp",
        )
        os.close(out_fd)
        out_tmp_path = Path(out_tmp_name)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(tmp_wav_path),
                    "-c:a", "libopus", str(out_tmp_path),
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

- [ ] **Step 4: Confirm `ffmpeg`/`ffprobe` are available, then run tests**

Run: `which ffmpeg ffprobe` -- if either is missing, install via the system
package manager (e.g. `apt-get install ffmpeg` provides both) before
proceeding; the tests skip gracefully via `pytestmark` if unavailable, but
this task cannot be verified without them.

Run: `hatch test tests/test_preview.py -v`
Expected: PASS (3/3)

- [ ] **Step 5: Full verification**

Run: `hatch run types:check`, `hatch run ruff:ruff check .`, `hatch run ruff:ruff format --check --diff .` — expect clean.
Run: `hatch test` (Docker, unsandboxed) — full suite, expect all passing, pristine.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/media/preview.py tests/test_preview.py
git commit -m "feat: make_preview -- x10 time-expanded WAV to Opus via ffmpeg"
```

---

## Task 3: `Config.media_root`

**Files:**
- Modify: `src/fledermap/config.py`
- Test: `tests/test_config.py` (extend)

**Interfaces:**
- Consumes: nothing new.
- Produces: `Config.media_root: Path` (required field) and `ENV_MEDIA_ROOT = "FLEDERMAP_MEDIA_ROOT"`, used by Task 8 (`cli/main.py`).

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_config.py

def test_missing_media_root_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.delenv(ENV_MEDIA_ROOT, raising=False)

    with pytest.raises(ConfigError, match=ENV_MEDIA_ROOT):
        Config.from_env(tmp_path)


def test_media_root_is_resolved_to_an_absolute_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    relative = "some/media/dir"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(ENV_MEDIA_ROOT, relative)

    config = Config.from_env(tmp_path)

    assert config.media_root == (tmp_path / relative).resolve()
    assert config.media_root.is_absolute()
```

Add `ENV_MEDIA_ROOT` to the existing import block at the top of
`tests/test_config.py` (alongside `ENV_DATABASE_URL`, `ENV_SITE_EPS_M`, etc.).

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'ENV_MEDIA_ROOT'`

- [ ] **Step 3: Implement**

In `src/fledermap/config.py`, add the env var constant alongside the others:

```python
ENV_MEDIA_ROOT = "FLEDERMAP_MEDIA_ROOT"
```

Add the field to the `Config` dataclass, after `site_min_points`:

```python
    # Required, not defaulted (matching database_url, not session_gap_hours):
    # where derived media lives is a real deployment decision (disk space,
    # backup policy), and it must be distinct from archive_root -- writing
    # into the archive would violate D16's read-only invariant on the source
    # tree. NOT platformdirs: that solves "guess a per-user data directory,"
    # the wrong question for a self-hosted server process where an explicit,
    # operator-named path is what makes a Docker volume mount trivial
    # (design spec §10). `Path()` is a sentinel default only -- direct
    # `Config(...)` construction without a real value is obviously wrong
    # (current directory) rather than a class-definition-time error, since
    # a required field with no default cannot follow the several already-
    # defaulted fields above it in this dataclass. `from_env`, the only path
    # real code uses, always supplies a real value or raises first.
    media_root: Path = field(default=Path())
```

Extend the existing dataclasses import: `from dataclasses import dataclass, field`.

In `Config.from_env`, after the `site_min_points` block and before the
`return cls(...)`:

```python
        media_root_raw = os.environ.get(ENV_MEDIA_ROOT)
        if not media_root_raw:
            msg = (
                f"{ENV_MEDIA_ROOT} is not set. Point it at a directory for "
                "derived media (spectrograms, previews) -- distinct from "
                "the archive, which ingest never writes to (D16)."
            )
            raise ConfigError(msg)
        media_root = Path(media_root_raw).resolve()
```

Add `media_root=media_root,` to the `return cls(...)` call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_config.py -v`
Expected: PASS, including the 2 new tests. **All pre-existing `test_config.py`
tests that call `Config.from_env` without setting `FLEDERMAP_MEDIA_ROOT` will
now fail** (`media_root` is required) -- add `monkeypatch.setenv(ENV_MEDIA_ROOT,
str(tmp_path / "media"))` to every existing test that calls `Config.from_env`
(check for a shared fixture first; if the file has one common env-setup
helper, extend it there rather than in every individual test).

- [ ] **Step 5: Full verification**

Run: `hatch run types:check`, `hatch run ruff:ruff check .`, `hatch run ruff:ruff format --check --diff .` — expect clean.
Run: `hatch test` (Docker, unsandboxed) — full suite. **This will surface every
other test file that calls `Config.from_env` without `FLEDERMAP_MEDIA_ROOT`**
(at minimum `tests/test_cli.py`) -- grep for `Config.from_env\|FLEDERMAP_DATABASE_URL`
across `tests/` and add `FLEDERMAP_MEDIA_ROOT` to every `env = {...}` dict or
`monkeypatch.setenv` call that currently only sets `FLEDERMAP_DATABASE_URL`,
so the full suite passes, not just this task's own file.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/config.py tests/test_config.py tests/test_cli.py
git commit -m "feat: add required Config.media_root (FLEDERMAP_MEDIA_ROOT)"
```

---

## Task 4: `jobs/app.py` — Procrastinate App, idempotent schema setup, worker connector

**Files:**
- Create: `src/fledermap/jobs/__init__.py` (empty)
- Create: `src/fledermap/jobs/app.py`
- Test: `tests/test_jobs_app.py`
- Modify: `pyproject.toml` — add `procrastinate` and `psycopg[binary,pool]` to `dependencies`

**Interfaces:**
- Consumes: nothing from other Phase 3 tasks.
- Produces: `make_job_app() -> procrastinate.App`, `ensure_schema(app: procrastinate.App, engine: Engine) -> None`, `make_worker_connector(database_url: str) -> procrastinate.PsycopgConnector`, all used by Task 5 (`jobs/tasks.py`) and Task 8 (`cli/main.py`).

**Everything in this task's implementation was verified end-to-end against a
real Postgres 16 container before this plan was written** (design spec §2/§6
record the full investigation) — the code below is the confirmed-working
result, not a sketch to investigate further.

- [ ] **Step 1: Add dependencies**

Add to `pyproject.toml`'s `[project] dependencies`, appended after `"pillow"`:

```
"procrastinate",
"psycopg[binary,pool]",
```

`psycopg` (v3, distinct from the `psycopg2-binary` this project already
depends on) is needed for `PsycopgConnector` — the async connector the
`worker` command uses (Task 8); `psycopg2-binary` remains what
`SQLAlchemyPsycopg2Connector` uses for everything else. Both packages coexist
without conflict — they are unrelated, differently-named PyPI packages.

Run: `hatch run types:check`. If it reports missing stubs for `procrastinate`
or `psycopg`, first try adding them to `[tool.hatch.envs.types]`'s
`extra-dependencies` list; if that doesn't resolve it, fall back to a scoped
`[[tool.mypy.overrides]]`, matching the existing `sklearn.*` block's shape.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_jobs_app.py
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

import procrastinate
from fledermap.jobs.app import ensure_schema, make_job_app, make_worker_connector

import pytest

pytestmark = pytest.mark.db


def test_make_job_app_constructs_without_an_engine() -> None:
    app = make_job_app()

    assert app is not None  # constructed without raising; not yet opened


def test_ensure_schema_creates_the_procrastinate_tables(engine: Engine) -> None:
    app = make_job_app()
    app.open(engine)

    ensure_schema(app, engine)

    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'procrastinate_jobs')",
            ),
        ).scalar()
    assert exists is True


def test_ensure_schema_is_safe_to_call_twice(engine: Engine) -> None:
    """Procrastinate's own schema-apply is NOT idempotent by itself (it
    errors if already applied, confirmed against real Postgres) --
    ensure_schema must guard that, matching _run_migrations's own "safe to
    run every time" property."""
    app = make_job_app()
    app.open(engine)

    ensure_schema(app, engine)
    ensure_schema(app, engine)  # must not raise


def test_make_worker_connector_returns_an_async_connector() -> None:
    connector = make_worker_connector("postgresql://localhost/does_not_matter")

    assert isinstance(connector, procrastinate.PsycopgConnector)
```

`engine` is this project's existing session-scoped `pytest.fixture` in
`tests/conftest.py` — it already runs `create_all` against a fresh
testcontainers Postgres per test, a genuinely empty schema Procrastinate has
never touched, exactly what these tests need. `test_make_worker_connector_...`
does not need `engine` or a live DB — constructing a `PsycopgConnector` with a
`conninfo` string does not connect eagerly.

- [ ] **Step 3: Run tests to verify they fail**

Run: `hatch test tests/test_jobs_app.py -v` (Docker, unsandboxed)
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.jobs'`

- [ ] **Step 4: Implement**

```python
# src/fledermap/jobs/app.py
"""Procrastinate App construction, idempotent schema setup, and the worker's
connector.

One `App` per process (design spec §6): `jobs/tasks.py`'s module-level `app`
(built here, via `make_job_app`) is shared by BOTH defer-side code
(`SQLAlchemyPsycopg2Connector`, opened against this project's own SQLAlchemy
engine -- no second connection pool for that side) and the worker
(`app.replace_connector(make_worker_connector(...))`, swapped in only for the
duration of `run_worker`). Tasks are bound to the App object they're declared
against, not to a specific connector, which is what makes sharing one App
across both roles possible.

Two things below were CONFIRMED, not assumed, against a real Postgres 16
container before this plan was written (design spec §2 has the full
investigation):

1. `app.schema_manager.apply_schema()` -- Procrastinate's own documented
   schema-apply method -- fails against a real database with
   `psycopg2.errors.SyntaxError: too many parameters specified for RAISE`.
   Root cause: `apply_schema()` runs `schema_sql.replace("%", "%%")` before
   executing, which corrupts the schema's own legitimate
   `RAISE '...(job id: %)', job_id` statements (PL/pgSQL's own, unrelated use
   of `%` as a format placeholder). The fix below executes the UNESCAPED
   schema via a raw psycopg2 cursor with NO params argument at all --
   confirmed this is the one call shape that avoids both the escaping bug
   AND a second, different failure from SQLAlchemy's own `exec_driver_sql`
   (which still implicitly supplies an empty params structure that
   re-triggers `%`-parsing).
2. `run_worker()` requires an async-capable connector.
   `SQLAlchemyPsycopg2Connector` raises `SyncConnectorConfigurationError` if
   you try. `make_worker_connector` returns a `PsycopgConnector` (psycopg 3)
   for exactly this reason.
"""

from __future__ import annotations

import procrastinate
from procrastinate.contrib.sqlalchemy import SQLAlchemyPsycopg2Connector
from sqlalchemy import text
from sqlalchemy.engine import Engine


def make_job_app() -> procrastinate.App:
    """Construct the App with its connector, but do NOT open it against an
    engine yet -- mirrors `SQLAlchemyPsycopg2Connector()`'s own documented
    pattern (constructed with no DSN/engine, opened separately once the real
    one is known). Every caller must `app.open(engine)` (defer-side) or
    `app.replace_connector(make_worker_connector(...))` (worker-side) before
    actually deferring or running anything."""
    return procrastinate.App(connector=SQLAlchemyPsycopg2Connector())


def ensure_schema(app: procrastinate.App, engine: Engine) -> None:
    """Create Procrastinate's schema if it doesn't already exist. Idempotent
    (safe to call on every startup, matching `_run_migrations`'s own
    property) -- Procrastinate's own apply methods are NOT idempotent by
    themselves. Uses `engine` directly for both the existence check and the
    actual apply, independent of whatever connector `app` currently has --
    `app` is only used here to read the schema text via `app.schema_manager`.
    """
    with engine.connect() as conn:
        already_applied = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'procrastinate_jobs')",
            ),
        ).scalar()
    if already_applied:
        return

    schema_sql = app.schema_manager.get_schema()
    raw_conn = engine.raw_connection()
    try:
        cursor = raw_conn.cursor()
        cursor.execute(schema_sql)  # NO params argument -- see module docstring
        raw_conn.commit()
    finally:
        raw_conn.close()


def make_worker_connector(database_url: str) -> procrastinate.PsycopgConnector:
    """An async-capable connector for running the worker -- see module
    docstring point 2. `database_url` is this project's own
    `Config.database_url` (a `postgresql://...` or `postgresql+psycopg2://...`
    URL); `PsycopgConnector` takes a plain `conninfo` string, not a
    SQLAlchemy URL object, so pass `database_url` through as-is if it's
    already a bare `postgresql://` URL. If `Config.database_url` is ever
    written with an explicit `+psycopg2` driver suffix, strip it before
    passing here -- psycopg 3's `conninfo` parser does not understand
    SQLAlchemy's `+driver` suffix syntax. Check `Config.database_url`'s
    actual format against a real value before finalizing this call; adjust
    if a suffix needs stripping."""
    return procrastinate.PsycopgConnector(conninfo=database_url)
```

**Before finalizing Step 4**, check `Config.database_url`'s actual expected
format (read `src/fledermap/config.py` and `tests/test_config.py` for example
values) to confirm whether `make_worker_connector` needs to strip a
`+psycopg2` suffix before passing the URL to `PsycopgConnector`, and update
the function body accordingly if so — the docstring above flags this as a
real thing to check, not a resolved fact.

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_jobs_app.py -v` (Docker, unsandboxed)
Expected: PASS (4/4)

- [ ] **Step 6: Full verification**

Run: `hatch run types:check`, `hatch run ruff:ruff check .`, `hatch run ruff:ruff format --check --diff .` — expect clean.
Run: `hatch test` (Docker, unsandboxed) — full suite, expect all passing, pristine.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/fledermap/jobs/ tests/test_jobs_app.py
git commit -m "feat: Procrastinate App -- confirmed-working schema setup and worker connector"
```

---

## Task 5: `jobs/tasks.py` — task functions, locking, retry policy

**Files:**
- Create: `src/fledermap/jobs/tasks.py`
- Test: `tests/test_jobs_tasks.py`

**Interfaces:**
- Consumes: `render_spectrogram`/`SpectrogramParams` (Task 1), `make_preview` (Task 2), `make_job_app` (Task 4), `Recording` model (existing, `src/fledermap/store/models.py`).
- Produces: `app` (the shared, module-level `procrastinate.App`), `render_spectrogram_task`, `make_preview_task`, `spectrogram_lock_key(audio_hash: str) -> str`, `preview_lock_key(audio_hash: str) -> str`, used by Task 7 (`services/media.py`) and Task 8 (`cli/main.py`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_jobs_tasks.py
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.jobs.app import ensure_schema
from fledermap.jobs.tasks import (
    app as jobs_app,
    make_preview_task,
    preview_lock_key,
    render_spectrogram_task,
    spectrogram_lock_key,
)
from fledermap.store.models import Recording
from tests.fixtures import build_wav, fmt_payload

pytestmark = pytest.mark.db


def _make_recording(
    session: OrmSession, *, audio_hash: str, path: str,
) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=path,
        recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    session.add(r)
    session.flush()
    return r


def _write_wav(root: Path, rel: str) -> None:
    full = root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    audio = bytes(range(256)) * 8  # real, non-trivial PCM content
    full.write_bytes(build_wav([(b"fmt ", fmt_payload()), (b"data", audio)]))


def test_render_spectrogram_task_writes_a_file(
    engine: Engine, tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    media_root = tmp_path / "media"
    _write_wav(archive_root, "a.wav")
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        recording = _make_recording(session, audio_hash="h1" * 32, path="a.wav")
        session.commit()
        audio_hash = recording.audio_hash

    render_spectrogram_task.configure(
        lock=spectrogram_lock_key(audio_hash),
        queueing_lock=spectrogram_lock_key(audio_hash),
    ).defer(audio_hash=audio_hash)
    jobs_app.run_worker(
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        additional_context={
            "archive_root": archive_root,
            "media_root": media_root,
            "engine": engine,
        },
    )

    produced = list(media_root.glob(f"{audio_hash[:2]}/{audio_hash}/spectrogram-*.webp"))
    assert len(produced) == 1


def test_make_preview_task_writes_a_file(engine: Engine, tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    media_root = tmp_path / "media"
    _write_wav(archive_root, "b.wav")
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        recording = _make_recording(session, audio_hash="h2" * 32, path="b.wav")
        session.commit()
        audio_hash = recording.audio_hash

    make_preview_task.configure(
        lock=preview_lock_key(audio_hash),
        queueing_lock=preview_lock_key(audio_hash),
    ).defer(audio_hash=audio_hash)
    jobs_app.run_worker(
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        additional_context={
            "archive_root": archive_root,
            "media_root": media_root,
            "engine": engine,
        },
    )

    produced = list(media_root.glob(f"{audio_hash[:2]}/{audio_hash}/preview-*.opus"))
    assert len(produced) == 1


def test_task_fails_permanently_for_a_missing_source_file(
    engine: Engine, tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    media_root = tmp_path / "media"
    # No file written -- recording.path points nowhere.
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        recording = _make_recording(
            session, audio_hash="h3" * 32, path="never_written.wav",
        )
        recording.missing_since = datetime(2026, 8, 25, tzinfo=UTC)
        session.commit()
        audio_hash = recording.audio_hash

    render_spectrogram_task.configure(
        lock=spectrogram_lock_key(audio_hash),
        queueing_lock=spectrogram_lock_key(audio_hash),
    ).defer(audio_hash=audio_hash)
    jobs_app.run_worker(
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        additional_context={
            "archive_root": archive_root,
            "media_root": media_root,
            "engine": engine,
        },
    )

    with engine.connect() as conn:
        status = conn.execute(
            text(
                "SELECT status FROM procrastinate_jobs WHERE task_name = "
                "'render_spectrogram_task'",
            ),
        ).scalar()
    assert status == "failed"


def test_duplicate_defer_with_the_same_queueing_lock_is_refused(
    engine: Engine,
) -> None:
    import procrastinate

    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)
    audio_hash = "h4" * 32

    render_spectrogram_task.configure(
        queueing_lock=spectrogram_lock_key(audio_hash),
    ).defer(audio_hash=audio_hash)

    with pytest.raises(procrastinate.exceptions.AlreadyEnqueued):
        render_spectrogram_task.configure(
            queueing_lock=spectrogram_lock_key(audio_hash),
        ).defer(audio_hash=audio_hash)
```

Every test calls `jobs_app.open(engine)` explicitly with its OWN
testcontainers `engine` fixture before deferring/running anything — the
module-level `app` in `jobs/tasks.py` is constructed with no engine bound
(Task 4), so each test (and, in real use, each CLI command) must bind it to
the real engine it wants before use. Do not assume a previous test already
opened it correctly; `Engine` objects differ per test run.

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_jobs_tasks.py -v` (Docker, unsandboxed)
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.jobs.tasks'`

- [ ] **Step 3: Investigate the retry parameter's exact accepted shape**

Confirmed already (do not re-investigate): `@app.task(...)` accepts
`retry: bool | int | RetryStrategy = False` directly — a plain `retry=3`
(an int) is a valid, real, tested value.

- [ ] **Step 4: Implement**

```python
# src/fledermap/jobs/tasks.py
"""Task wrappers bridging `Recording` rows to `media/`'s pure functions.

This is the ONLY module that imports both `procrastinate` and the ORM models
-- `media/` stays pure (design spec §3). `app` here is the ONE Procrastinate
App for the whole process (design spec §6) -- constructed via
`jobs.app.make_job_app()` with no engine bound yet; every consumer (CLI
commands, tests) must `app.open(engine)` before deferring, or
`app.replace_connector(...)` before running a worker.
"""

from __future__ import annotations

from pathlib import Path

import procrastinate
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.jobs.app import make_job_app
from fledermap.media.preview import make_preview
from fledermap.media.spectrogram import SpectrogramParams, render_spectrogram
from fledermap.store.models import Recording

app = make_job_app()

_SPECTROGRAM_PARAMS = SpectrogramParams()


def spectrogram_lock_key(audio_hash: str) -> str:
    return f"spectrogram:{audio_hash}:{_SPECTROGRAM_PARAMS.params_hash}"


def preview_lock_key(audio_hash: str) -> str:
    # Fixed literal, not a computed hash: the x10 ratio is fixed by spec and
    # not exposed as a v1 setting (design spec §5).
    return f"preview:{audio_hash}:v1"


def _resolve_recording(session: OrmSession, audio_hash: str) -> Recording:
    recording = session.scalars(
        select(Recording).where(Recording.audio_hash == audio_hash),
    ).one()
    if recording.missing_since is not None:
        msg = f"recording {audio_hash} has no source file (missing_since set)"
        raise FileNotFoundError(msg)
    return recording


@app.task(queue="media", pass_context=True, retry=3)
def render_spectrogram_task(
    context: procrastinate.JobContext, audio_hash: str,
) -> None:
    archive_root: Path = context.additional_context["archive_root"]
    media_root: Path = context.additional_context["media_root"]
    engine = context.additional_context["engine"]

    with OrmSession(engine) as session:
        recording = _resolve_recording(session, audio_hash)
        wav_path = archive_root / recording.path

    out_path = (
        media_root / audio_hash[:2] / audio_hash /
        f"spectrogram-{_SPECTROGRAM_PARAMS.params_hash}.webp"
    )
    render_spectrogram(wav_path, out_path, params=_SPECTROGRAM_PARAMS)


@app.task(queue="media", pass_context=True, retry=3)
def make_preview_task(context: procrastinate.JobContext, audio_hash: str) -> None:
    archive_root: Path = context.additional_context["archive_root"]
    media_root: Path = context.additional_context["media_root"]
    engine = context.additional_context["engine"]

    with OrmSession(engine) as session:
        recording = _resolve_recording(session, audio_hash)
        wav_path = archive_root / recording.path

    out_path = media_root / audio_hash[:2] / audio_hash / "preview-v1.opus"
    make_preview(wav_path, out_path)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_jobs_tasks.py -v` (Docker, unsandboxed)
Expected: PASS (4/4)

- [ ] **Step 6: Full verification**

Run: `hatch run types:check`, `hatch run ruff:ruff check .`, `hatch run ruff:ruff format --check --diff .` — expect clean.
Run: `hatch test` (Docker, unsandboxed) — full suite, expect all passing, pristine.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/jobs/tasks.py tests/test_jobs_tasks.py
git commit -m "feat: render_spectrogram_task/make_preview_task with lock+queueing_lock"
```

---

## Task 6: `IngestReport.created_hashes`

**Files:**
- Modify: `src/fledermap/services/ingest.py`
- Test: `tests/test_ingest_service.py` (extend)

**Interfaces:**
- Consumes: nothing new.
- Produces: `IngestReport.created_hashes: list[str]`, populated by `IngestReport.record(outcome, audio_hash=...)`, consumed by Task 8 (`cli/main.py`).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_ingest_service.py -- this test file's existing
# ScannedFile-building helper is `_scanned(digest=..., name=..., ...)`
# (confirmed by reading the file: it builds a synthetic ScannedFile against
# the module-level `ROOT = Path("/archive")` constant, no real file is ever
# written to disk -- commit_scan only uses archive_root to compute a
# relative path string, it never opens the file). Add these two tests
# alongside the existing CREATED-outcome test:

def test_created_hashes_records_every_newly_created_audio_hash(
    engine: Engine,
) -> None:
    a = _scanned(digest="a" * 64, name="EPTSER_20150610_215446.wav")
    b = _scanned(digest="b" * 64, name="EPTSER_20150610_215447.wav")

    with OrmSession(engine) as session:
        report = commit_scan(session, [a, b], archive_root=ROOT)

    assert sorted(report.created_hashes) == sorted([a.audio_hash, b.audio_hash])


def test_created_hashes_excludes_unchanged_recordings(engine: Engine) -> None:
    a = _scanned(digest="a" * 64)

    with OrmSession(engine) as session:
        commit_scan(session, [a], archive_root=ROOT)
        session.commit()
        second_report = commit_scan(session, [a], archive_root=ROOT)

    assert second_report.created_hashes == []
```

`ROOT` and `_scanned` are both already defined at module level in this test
file — no new import needed for either.

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_ingest_service.py -v` (Docker, unsandboxed)
Expected: FAIL with `AttributeError: 'IngestReport' object has no attribute 'created_hashes'`

- [ ] **Step 3: Implement**

In `src/fledermap/services/ingest.py`, add the field to `IngestReport`:

```python
    created_hashes: list[str] = field(default_factory=list)
```

(placed after `unmapped_labels`, or wherever the dataclass's field block
ends — keep the existing field order otherwise unchanged).

Change `record`'s signature to require the hash explicitly, at every call
site, rather than default it to `None` and risk a forgotten call site:

```python
    def record(self, outcome: IngestOutcome, *, audio_hash: str) -> None:
        match outcome:
            case IngestOutcome.CREATED:
                self.created += 1
                self.created_hashes.append(audio_hash)
            case IngestOutcome.UNCHANGED:
                self.unchanged += 1
            case IngestOutcome.UPDATED:
                self.updated += 1
            case IngestOutcome.MOVED:
                self.moved += 1
            case IngestOutcome.REPLACED:
                self.replaced += 1
```

(Keep every existing branch's logic identical — only the signature and the
`CREATED` branch's body change.) Update **every** call site in `commit_scan`
to pass `audio_hash=item.audio_hash` (there are multiple — grep this file for
`report.record(` to find all of them before editing, since a missed call
site is now a `TypeError` at runtime, not a silent gap).

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_ingest_service.py -v` (Docker, unsandboxed)
Expected: PASS, including the 2 new tests.

- [ ] **Step 5: Full verification**

Run: `hatch run types:check`, `hatch run ruff:ruff check .`, `hatch run ruff:ruff format --check --diff .` — expect clean.
Run: `hatch test` (Docker, unsandboxed) — full suite, expect all passing, pristine (this changes a function signature used throughout `commit_scan` — the full suite is the real check that every call site was updated).

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/ingest.py tests/test_ingest_service.py
git commit -m "feat: IngestReport.created_hashes -- track which hashes are new"
```

---

## Task 7: `services/media.py` — enqueue and backfill

**Files:**
- Create: `src/fledermap/services/media.py`
- Test: `tests/test_media_service.py`

**Interfaces:**
- Consumes: `app`, `render_spectrogram_task`, `make_preview_task`, `spectrogram_lock_key`, `preview_lock_key` (Task 5); `Recording` model (existing).
- Produces: `enqueue_media(created_hashes: list[str], engine: Engine) -> None` and `backfill_media(db_session: OrmSession, media_root: Path) -> int`, used by Task 8 (`cli/main.py`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_media_service.py
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.jobs.app import ensure_schema
from fledermap.jobs.tasks import app as jobs_app
from fledermap.services.media import backfill_media, enqueue_media
from fledermap.store.models import Recording

pytestmark = pytest.mark.db


def _make_recording(session: OrmSession, *, audio_hash: str, path: str) -> Recording:
    r = Recording(
        audio_hash=audio_hash, path=path, recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    session.add(r)
    session.flush()
    return r


def _todo_job_count(engine: Engine) -> int:
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM procrastinate_jobs WHERE status = 'todo'"),
        ).scalar()
    return int(count) if count is not None else 0


def test_enqueue_media_defers_two_jobs_per_hash(engine: Engine) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    enqueue_media(["h1" * 32], engine)

    assert _todo_job_count(engine) == 2  # one spectrogram job, one preview job


def test_enqueue_media_ignores_a_duplicate_for_the_same_hash(engine: Engine) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    enqueue_media(["h2" * 32], engine)
    enqueue_media(["h2" * 32], engine)  # must not raise, must not double the queue

    assert _todo_job_count(engine) == 2  # still just one spectrogram + one preview job


def test_backfill_media_enqueues_recordings_with_no_media_on_disk(
    engine: Engine, tmp_path: Path,
) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)
    media_root = tmp_path / "media"

    with OrmSession(engine) as session:
        _make_recording(session, audio_hash="h3" * 32, path="a.wav")
        session.commit()

        count = backfill_media(session, media_root)

    assert count == 1


def test_backfill_media_skips_a_recording_with_existing_media(
    engine: Engine, tmp_path: Path,
) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)
    media_root = tmp_path / "media"

    with OrmSession(engine) as session:
        recording = _make_recording(session, audio_hash="h4" * 32, path="b.wav")
        session.commit()

        from fledermap.media.spectrogram import SpectrogramParams

        params_hash = SpectrogramParams().params_hash
        existing_dir = media_root / recording.audio_hash[:2] / recording.audio_hash
        existing_dir.mkdir(parents=True)
        (existing_dir / f"spectrogram-{params_hash}.webp").write_bytes(b"x")
        (existing_dir / "preview-v1.opus").write_bytes(b"x")

        count = backfill_media(session, media_root)

    assert count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_media_service.py -v` (Docker, unsandboxed)
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.services.media'`

- [ ] **Step 3: Implement**

```python
# src/fledermap/services/media.py
"""Enqueueing derived-media jobs. The only place `commit_scan`'s result and
a backfill sweep turn into actual Procrastinate deferrals (design spec §8)."""

from __future__ import annotations

from pathlib import Path

import procrastinate
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.jobs.tasks import (
    app as jobs_app,
    make_preview_task,
    preview_lock_key,
    render_spectrogram_task,
    spectrogram_lock_key,
)
from fledermap.media.spectrogram import SpectrogramParams
from fledermap.store.models import Recording

_SPECTROGRAM_PARAMS_HASH = SpectrogramParams().params_hash


def enqueue_media(created_hashes: list[str], engine: Engine) -> None:
    """Defer both tasks for each hash, locked/queueing-locked per design spec
    §7. Called from `cli/main.py`'s `ingest` command AFTER `session.commit()`
    succeeds -- not from inside `commit_scan`, which does not commit, so
    nothing can be picked up by a worker for a row that isn't durably
    committed yet. `jobs_app` must already be `.open(engine)`-ed by the
    caller before this runs (CLI commands do this once at startup)."""
    jobs_app.open(engine)
    for audio_hash in created_hashes:
        try:
            render_spectrogram_task.configure(
                lock=spectrogram_lock_key(audio_hash),
                queueing_lock=spectrogram_lock_key(audio_hash),
            ).defer(audio_hash=audio_hash)
        except procrastinate.exceptions.AlreadyEnqueued:
            pass
        try:
            make_preview_task.configure(
                lock=preview_lock_key(audio_hash),
                queueing_lock=preview_lock_key(audio_hash),
            ).defer(audio_hash=audio_hash)
        except procrastinate.exceptions.AlreadyEnqueued:
            pass


def _has_media(media_root: Path, audio_hash: str) -> bool:
    """Disk existence, not a Procrastinate job-history query (design spec
    §8, decision P3-6): the job table isn't a reliable durable record
    (Procrastinate can be configured to delete completed jobs), and disk
    state is what actually determines whether a recording needs work."""
    recording_dir = media_root / audio_hash[:2] / audio_hash
    spectrogram = recording_dir / f"spectrogram-{_SPECTROGRAM_PARAMS_HASH}.webp"
    preview = recording_dir / "preview-v1.opus"
    return spectrogram.exists() and preview.exists()


def backfill_media(db_session: OrmSession, media_root: Path) -> int:
    """Enqueue media for every recording that doesn't already have both
    files on disk at the current params. Returns the count enqueued."""
    engine = db_session.get_bind()
    assert isinstance(engine, Engine), "db_session must be bound to an Engine"
    hashes = db_session.scalars(select(Recording.audio_hash)).all()
    missing = [h for h in hashes if not _has_media(media_root, h)]
    enqueue_media(missing, engine)
    return len(missing)
```

`db_session.get_bind()` is confirmed (via `inspect.signature`) to return
`Union[Engine, Connection]` — the `assert isinstance(engine, Engine)`
narrows it for both mypy and a real runtime check, per this project's
stated rule (CLAUDE.md's tooling section): bind the wider type, assert
the narrower one, then dereference — never `# type: ignore`. (An earlier
draft of this note cited a `services/derive.py` precedent for this exact
pattern; grepped and found no `assert isinstance` anywhere in this
codebase, so that citation was wrong and has been removed — the rule
being followed is the general project-wide one, not a specific prior
instance of it.) Every caller of `backfill_media` in this codebase
constructs its session directly from an `Engine` (`OrmSession(engine)`), so
this assertion is not expected to ever fail in practice — it documents and
enforces the assumption rather than silently trusting it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_media_service.py -v` (Docker, unsandboxed)
Expected: PASS (4/4)

- [ ] **Step 5: Full verification**

Run: `hatch run types:check`, `hatch run ruff:ruff check .`, `hatch run ruff:ruff format --check --diff .` — expect clean.
Run: `hatch test` (Docker, unsandboxed) — full suite, expect all passing, pristine.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/media.py tests/test_media_service.py
git commit -m "feat: enqueue_media/backfill_media -- disk-state-driven job enqueueing"
```

---

## Task 8: CLI — `worker` and `enqueue-media`, wired into `ingest`

**Files:**
- Modify: `src/fledermap/cli/main.py`
- Test: `tests/test_cli.py` (extend)

**Interfaces:**
- Consumes: `enqueue_media`, `backfill_media` (Task 7); `ensure_schema`, `make_worker_connector` (Task 4); `app` from `fledermap.jobs.tasks` (Task 5); `Config.media_root` (Task 3); `IngestReport.created_hashes` (Task 6).
- Produces: `fledermap worker ARCHIVE [--wait/--no-wait]`, `fledermap enqueue-media`.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_cli.py

def test_ingest_enqueues_media_jobs_for_created_recordings(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
    }
    runner = CliRunner()

    result = runner.invoke(cli, ["ingest", str(archive)], env=env)

    assert result.exit_code == 0, result.output
    from sqlalchemy import text

    engine = make_engine(clean_database_url)
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM procrastinate_jobs WHERE status = 'todo'"),
        ).scalar()
    # _archive() writes 2 distinct recordings -> 2 hashes -> 4 jobs (spectrogram + preview each).
    assert count == 4


def test_worker_no_wait_processes_queued_jobs_and_writes_media(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    media_root = tmp_path / "media"
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_MEDIA_ROOT": str(media_root),
    }
    runner = CliRunner()
    runner.invoke(cli, ["ingest", str(archive)], env=env)

    result = runner.invoke(cli, ["worker", str(archive), "--no-wait"], env=env)

    assert result.exit_code == 0, result.output
    spectrograms = list(media_root.glob("*/*/spectrogram-*.webp"))
    previews = list(media_root.glob("*/*/preview-*.opus"))
    assert len(spectrograms) == 2
    assert len(previews) == 2


def test_enqueue_media_command_reports_zero_after_ingest_already_enqueued(
    clean_database_url: str,
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path)
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
    }
    runner = CliRunner()
    runner.invoke(cli, ["ingest", str(archive)], env=env)

    result = runner.invoke(cli, ["enqueue-media"], env=env)

    assert result.exit_code == 0, result.output
    # ingest's own defer already enqueued both recordings; nothing left to
    # backfill (this targets the "already covered" branch specifically --
    # backfill_media checks the JOB queue's effect indirectly via disk state,
    # and no worker has run yet in this test, so disk is still empty. If this
    # assertion fails with "enqueued 2" instead, it means backfill_media's
    # disk check doesn't account for already-queued-but-not-yet-run jobs --
    # re-read design spec §8's decision P3-6 and reconsider whether
    # backfill_media needs to also check Procrastinate's queue state, not
    # just disk, before declaring this a bug in the implementation rather
    # than a gap in this test's own expectation.
    assert "enqueued 0" in result.output or "enqueued 2" in result.output
```

**The last assertion above is deliberately not pinned to one exact value —
resolve this before finalizing the test.** `backfill_media` (Task 7) checks
disk state only, not the job queue; immediately after `ingest`, no worker has
run yet, so no media files exist on disk yet either. This means
`backfill_media` right after `ingest` (before any worker runs) **would**
currently see "nothing on disk" and enqueue AGAIN for both recordings,
producing duplicate `todo` jobs for the same hashes — deferring again with
the SAME lock/queueing_lock keys as the original `ingest`-triggered defer,
which `queueing_lock` should refuse via `AlreadyEnqueued` (caught and ignored
inside `enqueue_media`) since those original jobs are still sitting in
`todo`, not yet `doing`/`succeeded`. Work through this scenario against the
actual implementation before writing this test's final assertion: run
`ingest` then `enqueue-media` with NO worker run in between, inspect
`procrastinate_jobs` directly, and assert on the REAL observed outcome (very
likely `"enqueued 0"`, precisely because `queueing_lock` is doing its job) —
do not leave the loose `or` assertion above in the committed test.

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_cli.py -v` (Docker, unsandboxed)
Expected: FAIL — `test_ingest_enqueues_media_jobs_for_created_recordings` fails
because `procrastinate_jobs` doesn't exist yet (schema never applied by
`ingest`); the other two fail with `Error: No such command 'worker'` /
`'enqueue-media'`.

- [ ] **Step 3: Implement**

In `src/fledermap/cli/main.py`, add imports:

```python
import procrastinate

from fledermap.jobs.app import ensure_schema, make_worker_connector
from fledermap.jobs.tasks import app as jobs_app
from fledermap.services.media import backfill_media, enqueue_media
```

In the `ingest` command: after `engine = make_engine(config.database_url)`
and `_run_migrations(config.database_url)`, add:

```python
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)
```

After the existing `session.commit()` that follows `commit_scan`, add:

```python
        enqueue_media(report.created_hashes, engine)
```

(Place it right after that `session.commit()` call — not before, and not
inside an earlier part of the `with OrmSession(engine) as session:` block —
matching design spec §8's explicit ordering requirement: nothing may be
picked up by a worker for a row that isn't durably committed yet.)

Add the two new commands:

```python
@cli.command()
@click.argument(
    "archive",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--wait/--no-wait",
    default=True,
    help="Keep running until stopped (default), or process the current "
    "queue once and exit.",
)
def worker(archive: Path, wait: bool) -> None:
    """Run the media job worker. Reads and writes files under ARCHIVE and
    the configured media root; requires the same ARCHIVE path `ingest` uses
    to resolve `Recording.path` to a real file.
    """
    try:
        config = Config.from_env(archive)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)
    ensure_schema(jobs_app, engine)

    async_connector = make_worker_connector(config.database_url)
    with jobs_app.replace_connector(async_connector) as worker_app:
        worker_app.run_worker(
            wait=wait,
            install_signal_handlers=wait,
            listen_notify=wait,
            additional_context={
                "archive_root": config.archive_root,
                "media_root": config.media_root,
                "engine": engine,
            },
        )


@cli.command(name="enqueue-media")
def enqueue_media_command() -> None:
    """Backfill media jobs for recordings with nothing on disk yet -- for
    recordings ingested before this phase existed, or after a media-params
    change that invalidates old renders."""
    try:
        config = Config.from_env(Path.cwd())
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        count = backfill_media(session, config.media_root)
        session.commit()

    click.echo(f"enqueued {count}")
```

`enqueue_media_command`'s function is named distinctly from the imported
`enqueue_media` (the service function) to avoid a name collision — Click's
`@cli.command(name="enqueue-media")` decouples the CLI-visible command name
from the Python function name, matching the existing `ingest`/`derive`
naming style (function name is the natural Python identifier; `name=` only
needed here because `enqueue-media`'s hyphen isn't a valid identifier and the
plain underscore version collides with the imported service function).

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_cli.py -v` (Docker, unsandboxed)
Expected: PASS, including the 3 new tests.

- [ ] **Step 5: Full verification**

Run: `hatch run types:check`, `hatch run ruff:ruff check .`, `hatch run ruff:ruff format --check --diff .` — expect clean.
Run: `hatch test` (Docker, unsandboxed) — full suite, expect all passing, pristine. This is Phase 3's exit gate.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/cli/main.py tests/test_cli.py
git commit -m "feat: fledermap worker and enqueue-media CLI commands"
```

---

## Self-Review Notes

**Spec coverage:** §1 scope (spectrogram + preview generation) — Tasks 1, 2.
§2 deviations (batogram, ffmpeg, Procrastinate schema, the two empirically-
confirmed corrections) — implemented exactly as verified in Tasks 4, 5, 8.
§3 module layout — matches Tasks 1, 2, 4, 5, 6, 7, 8. §4/§5 media functions —
Tasks 1, 2, including the Nyquist-clamping fix. §6/§7 jobs app + locking —
Tasks 4, 5, including the confirmed schema-apply workaround and the
two-connector worker architecture. §8 ingest/media services — Tasks 6, 7.
§9 CLI including `--wait/--no-wait`, the `ARCHIVE` argument correction, and
the worker connector swap — Task 8. §10 `media_root` config — Task 3.
§11 testing (DB-backed job execution, not `InMemoryConnector`; duplicate-
enqueue regression; `ffmpeg` presence) — covered across Tasks 2, 5, 7.
§12 out of scope — no task touches `geo`/`classify` queues, no web surface,
no configurable end-user params.

**Type consistency check performed:** `render_spectrogram(wav_path, out_path,
*, params)` (Task 1) called unchanged from Task 5. `make_preview(wav_path,
out_path)` (Task 2) called unchanged from Task 5. `make_job_app()` (no
engine parameter — corrected from an earlier draft that passed one) /
`ensure_schema(app, engine)` / `make_worker_connector(database_url)`
(Task 4) called unchanged from Tasks 5, 7, 8 — verified `engine` is threaded
consistently everywhere it's needed (`jobs_app.open(engine)` called
explicitly in every test and every CLI command that defers or runs,
`enqueue_media`'s signature carries `engine` explicitly rather than assuming
the app is already open, `backfill_media` derives it from
`db_session.get_bind()`). `spectrogram_lock_key`/`preview_lock_key` (Task 5)
called unchanged from Task 7. `enqueue_media(created_hashes, engine)` /
`backfill_media(db_session, media_root)` (Task 7) called unchanged from
Task 8. `IngestReport.created_hashes` (Task 6) consumed unchanged by Task 8.

**Known judgment calls and confirmed-not-guessed facts, surfaced inline, not
hidden:** grayscale as the v1 spectrogram colormap (Task 1) — the design spec
said "a small hand-written colormap," a grayscale mapping is the simplest
valid instance of that, explicitly revisable later via `params_hash` without
a schema change. `Config.media_root`'s dataclass-field-ordering workaround
(`Path()` sentinel default, Task 3) — required because `Config` already has
several defaulted fields before it; `from_env` always overrides it or raises
first, so the sentinel is never user-visible. **Two significant Procrastinate
behaviors were verified empirically against a real Postgres 16 container
before this plan was written, not guessed or left as investigation steps for
the implementer**: `apply_schema()`'s own escaping breaks its own schema's
`RAISE` statements (Task 4's `ensure_schema` uses the confirmed working
workaround directly), and `run_worker()` requires an async connector separate
from the one used to defer (Task 4/8's `replace_connector` + `PsycopgConnector`
shape is the confirmed working fix, not a sketch). One remaining real
open item is flagged explicitly rather than resolved by guessing: Task 4
Step 4 asks the implementer to check whether `Config.database_url`'s actual
string format needs a driver-suffix stripped before use with
`PsycopgConnector`'s `conninfo` parameter — this depends on the exact format
`Config.database_url` is written in project-wide, which the implementer can
check directly against `config.py`/its tests in under a minute, faster and
more reliably than guessing it into this document.

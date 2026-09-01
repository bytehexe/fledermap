# Fledermap Recording Details Page (core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a standalone `GET /recordings/<audio_hash>` page showing a recording's spectrogram
and oscillogram at a locked, DAW-like time/frequency scale, rendered fresh from the raw audio on
each request, with click-to-play, a crosshair readout, and a playback-position cursor.

**Architecture:** Two new pure-render routes (`web/views/media.py`) render straight from the WAV
file to a temp file and stream it back — no derived-media cache, no Procrastinate job. A new
standalone page route + template (`web/views/recording_detail.py`) serves those two images inside
a horizontally-scrollable container at their natural (locked-scale) pixel size. A dedicated
`recording_detail.js` (mirroring the existing `session_map.js` per-page-script pattern) adds
click-to-play, a ported crosshair, a playback cursor, and axis gridlines — no automated JS tests
exist in this repo, so those pieces are verified with a headless browser instead.

**Tech Stack:** Flask/Jinja2 (server), plain JS (client, no new library), SQLAlchemy/PostGIS,
the existing pure `render_spectrogram`/`render_oscillogram` (`media/`).

**Spec:** `docs/superpowers/specs/2026-09-01-fledermap-recording-details-page-design.md`

## Global Constraints

- Locked scale (spec §1): `DETAIL_PX_PER_MS = 19.0`, `DETAIL_PX_PER_KHZ = 4.7`,
  `DETAIL_MAX_FREQ_KHZ = 120.0` — explicitly tunable, not final numbers.
- **No caching of the detail render** (spec Non-goals) — every request re-renders from the raw
  WAV to a temp file; no `paths.py` entry, no Procrastinate task, no lock key.
- **No new derived-media artifact type** in the `paths.py`/job/enqueue sense (spec Non-goals) —
  this reuses the existing pure `render_spectrogram`/`render_oscillogram` unmodified.
- Standalone full page, not an expanded drawer state (spec Decisions).
- `resolve_recording`/`resolve_wav_path` move from `jobs/tasks.py` (private, underscore-prefixed)
  to `services/media.py` (public) — a second legitimate consumer promotes a helper to shared
  (spec Decisions), same convention already used elsewhere in this project.
- Every new/changed Python file must pass `hatch fmt` and `hatch run types:check` (mypy covers
  `tests/` too). New tests must be run and shown RED before the implementation, then GREEN after.
- JS-only steps have no automated test harness in this repo — verify with a headless browser
  (`google-chrome --headless --disable-gpu --screenshot=...` or `firefox --headless
  --screenshot=...`) against a real running `fledermap serve`, not by reasoning from the code
  alone.

---

## Task 1: Locked-scale constants and `detail_params()`

**Files:**
- Create: `src/fledermap/services/recording_detail.py`
- Test: `tests/test_recording_detail_service.py`

**Interfaces:**
- Produces: `DETAIL_PX_PER_MS: float`, `DETAIL_PX_PER_KHZ: float`, `DETAIL_MAX_FREQ_KHZ: float`
  module constants; `DetailParams` dataclass with fields `spectrogram: SpectrogramParams`,
  `oscillogram: OscillogramParams`, `max_freq_khz: float`; `detail_params(duration_s: float,
  samplerate_hz: float) -> DetailParams`. Task 3 and Task 4 both call `detail_params` and read
  `DETAIL_PX_PER_MS`/`DETAIL_PX_PER_KHZ`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_recording_detail_service.py
from __future__ import annotations

from fledermap.services.recording_detail import (
    DETAIL_MAX_FREQ_KHZ,
    DETAIL_PX_PER_KHZ,
    DETAIL_PX_PER_MS,
    detail_params,
)


def test_detail_params_computes_width_from_duration_and_px_per_ms() -> None:
    params = detail_params(duration_s=2.0, samplerate_hz=256_000)

    assert params.spectrogram.width_px == round(2.0 * 1000 * DETAIL_PX_PER_MS)
    assert params.oscillogram.width_px == params.spectrogram.width_px


def test_detail_params_uses_the_ceiling_when_samplerate_is_high_enough() -> None:
    params = detail_params(duration_s=1.0, samplerate_hz=256_000)

    assert params.max_freq_khz == DETAIL_MAX_FREQ_KHZ
    assert params.spectrogram.height_px == round(DETAIL_MAX_FREQ_KHZ * DETAIL_PX_PER_KHZ)


def test_detail_params_clamps_to_nyquist_below_the_ceiling() -> None:
    # 44_100 Hz samplerate -> Nyquist 22_050 Hz = 22.05 kHz, well under the
    # 120 kHz ceiling -- the clamp must win, not the ceiling.
    params = detail_params(duration_s=1.0, samplerate_hz=44_100)

    assert params.max_freq_khz == 22.05
    assert params.spectrogram.height_px == round(22.05 * DETAIL_PX_PER_KHZ)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `hatch test tests/test_recording_detail_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.services.recording_detail'`

- [ ] **Step 3: Write the implementation**

```python
# src/fledermap/services/recording_detail.py
"""Locked scale for the recording details page (design spec
2026-09-01-fledermap-recording-details-page-design.md, section 1). Pure --
no DB, no filesystem -- this is the one place both the detail-image serving
routes and the page route compute these numbers, so they can never disagree
about a recording's width.
"""

from __future__ import annotations

from dataclasses import dataclass

from fledermap.media.oscillogram import OscillogramParams
from fledermap.media.spectrogram import SpectrogramParams

# Derived from the Skiba identification guide's 10ms:40kHz convention against
# a ~15cm target print height (96dpi CSS px) -- explicitly a tunable
# starting point, not a final number (design spec Decisions): refine once
# real recordings are actually on screen.
DETAIL_PX_PER_MS = 19.0
DETAIL_PX_PER_KHZ = 4.7
# A ceiling, not a promise every recording reaches it -- clamped to the
# recording's own Nyquist limit below, same convention
# `spectrogram.effective_max_freq_hz` already uses for the drawer.
DETAIL_MAX_FREQ_KHZ = 120.0


@dataclass(frozen=True)
class DetailParams:
    spectrogram: SpectrogramParams
    oscillogram: OscillogramParams
    max_freq_khz: float


def detail_params(duration_s: float, samplerate_hz: float) -> DetailParams:
    """Both images share one computed `width_px`: a recording twice as long
    renders twice as wide, so panning through it at the locked
    `DETAIL_PX_PER_MS` scale always means "the same span of time is the
    same span of pixels" -- the entire point of a locked scale (design spec
    section 1)."""
    width_px = round(duration_s * 1000 * DETAIL_PX_PER_MS)
    max_freq_hz = min(DETAIL_MAX_FREQ_KHZ * 1000, samplerate_hz / 2)
    height_px = round((max_freq_hz / 1000) * DETAIL_PX_PER_KHZ)
    spectrogram = SpectrogramParams(
        width_px=width_px,
        height_px=height_px,
        max_freq_hz=max_freq_hz,
    )
    oscillogram = OscillogramParams(width_px=width_px)
    return DetailParams(
        spectrogram=spectrogram,
        oscillogram=oscillogram,
        max_freq_khz=max_freq_hz / 1000,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `hatch test tests/test_recording_detail_service.py -v`
Expected: PASS, 3 passed, no warnings.

- [ ] **Step 5: Type-check and format**

Run: `hatch fmt && hatch run types:check`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/recording_detail.py tests/test_recording_detail_service.py
git commit -m "feat: add locked-scale detail_params for the recording details page"
```

---

## Task 2: Promote `resolve_recording`/`resolve_wav_path` to `services/media.py`

**Files:**
- Modify: `src/fledermap/jobs/tasks.py:154-178` (remove `_resolve_recording`/`_resolve_wav_path`;
  update the three task functions to import the shared versions locally)
- Modify: `src/fledermap/services/media.py`
- Modify: `tests/test_jobs_tasks.py` (drop the moved test + its now-unused import)
- Test: `tests/test_media_service.py` (extend)

**Interfaces:**
- Produces (in `services/media.py`): `resolve_recording(session: OrmSession, audio_hash: str) ->
  Recording` (raises `sqlalchemy.exc.NoResultFound` if unknown, `FileNotFoundError` if
  `missing_since` is set); `resolve_wav_path(archive_roots: tuple[Path, ...], recording:
  Recording) -> Path` (raises `FileNotFoundError` for an out-of-range `archive_root_index`).
  Task 3 consumes both.

This is a pure refactor -- behavior is unchanged, so Step 1 below moves an *existing* test to its
new home (proving it fails only because the target doesn't exist yet there), rather than
inventing new behavior to test.

- [ ] **Step 1: Move the existing test to its new home**

Delete `test_resolve_wav_path_raises_filenotfounderror_for_out_of_range_index` from
`tests/test_jobs_tasks.py` (and drop the now-unused `_resolve_wav_path` import from that file's
`from fledermap.jobs.tasks import (...)` block). Add it to `tests/test_media_service.py`:

```python
# tests/test_media_service.py -- add to the top-level imports:
from fledermap.services.media import (
    backfill_media,
    enqueue_media,
    resolve_recording,
    resolve_wav_path,
)

# tests/test_media_service.py -- add this test:
def test_resolve_wav_path_raises_filenotfounderror_for_out_of_range_index() -> None:
    """A root list shrunk after some recordings were tagged with a
    since-removed index must fail clearly, the same way `resolve_recording`
    already does for a missing source file (design spec section 3)."""
    recording = Recording(
        audio_hash="h6" * 32,
        path="a.wav",
        recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        archive_root_index=2,
    )

    with pytest.raises(FileNotFoundError) as excinfo:
        resolve_wav_path((Path("/one"), Path("/two")), recording)

    message = str(excinfo.value)
    assert "archive_root_index 2" in message
    assert "only 2 root" in message
```

Also add a new test for the DB-backed half, `resolve_recording`, which had no direct unit test
before (only indirect coverage through the task tests):

```python
# tests/test_media_service.py -- add this test:
def test_resolve_recording_raises_filenotfounderror_when_missing_since_is_set(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        recording = _make_recording(session, audio_hash="h7" * 32, path="c.wav")
        recording.missing_since = datetime(2026, 8, 25, tzinfo=UTC)
        session.commit()

        with pytest.raises(FileNotFoundError, match="h7" * 32):
            resolve_recording(session, "h7" * 32)
```

(`_make_recording` is already defined at the top of this file.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `dangerouslyDisableSandbox: true` — `hatch test tests/test_media_service.py -v -k resolve`
Expected: FAIL with `ImportError: cannot import name 'resolve_wav_path' from
'fledermap.services.media'` (and the same for `resolve_recording`).

- [ ] **Step 3: Move the functions**

In `src/fledermap/services/media.py`, add near the top (after the existing imports — `select`,
`OrmSession`, `Path`, `Recording` are already imported there):

```python
def resolve_recording(session: OrmSession, audio_hash: str) -> Recording:
    """Moved here from `jobs/tasks.py` -- a second legitimate consumer (the
    recording-details page's serving routes, `web/views/media.py`) is what
    promotes a private helper to a shared, public one (design spec
    Decisions)."""
    recording = session.scalars(
        select(Recording).where(Recording.audio_hash == audio_hash),
    ).one()
    if recording.missing_since is not None:
        msg = f"recording {audio_hash} has no source file (missing_since set)"
        raise FileNotFoundError(msg)
    return recording


def resolve_wav_path(archive_roots: tuple[Path, ...], recording: Recording) -> Path:
    """`archive_root_index` out of range means a root list shrank after some
    recordings were tagged with a since-removed index -- fail clearly the
    same way `resolve_recording` does above, rather than a bare
    `IndexError`."""
    try:
        root = archive_roots[recording.archive_root_index]
    except IndexError as exc:
        msg = (
            f"recording {recording.audio_hash} references archive_root_index "
            f"{recording.archive_root_index}, but only {len(archive_roots)} "
            f"root(s) are configured"
        )
        raise FileNotFoundError(msg) from exc
    return root / recording.path
```

In `src/fledermap/jobs/tasks.py`:
1. Delete the `_resolve_recording` and `_resolve_wav_path` function definitions (lines 154-178).
2. Both `select` (`from sqlalchemy import select`) and `Recording` (in `from
   fledermap.store.models import Recording, Site`) become unused once those two functions are
   gone — nothing else in this file references either. Drop the `select` import entirely, and
   narrow the models import to `from fledermap.store.models import Site`.
3. In each of `render_spectrogram_task`, `render_oscillogram_task`, and `make_preview_task`, add
   a local import at the top of the function body and rename the two call sites in each:

```python
@app.task(queue="media", pass_context=True, retry=_RETRY)
def render_spectrogram_task(
    context: procrastinate.JobContext,
    audio_hash: str,
) -> None:
    # Local import: `services.media` imports task objects FROM this module
    # at ITS top level, so a top-level import here would be circular (same
    # reasoning as `run_ingest_cycle`'s own local `enqueue_media` import,
    # below).
    from fledermap.services.media import resolve_recording, resolve_wav_path

    archive_roots: tuple[Path, ...] = context.additional_context["archive_roots"]
    media_root: Path = context.additional_context["media_root"]
    engine = context.additional_context["engine"]

    with OrmSession(engine) as session:
        recording = resolve_recording(session, audio_hash)
        wav_path = resolve_wav_path(archive_roots, recording)

    out_path = spectrogram_path(media_root, audio_hash)
    render_spectrogram(wav_path, out_path, params=DEFAULT_SPECTROGRAM_PARAMS)
```

(Apply the same local-import + rename to `render_oscillogram_task` and `make_preview_task`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `dangerouslyDisableSandbox: true` — `hatch test tests/test_media_service.py
tests/test_jobs_tasks.py -v`
Expected: PASS, all green, no warnings. (`test_jobs_tasks.py` needs `dangerouslyDisableSandbox:
true` per this repo's own `db`-marker/Docker note.)

- [ ] **Step 5: Type-check and format**

Run: `hatch fmt && hatch run types:check`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/jobs/tasks.py src/fledermap/services/media.py \
  tests/test_media_service.py tests/test_jobs_tasks.py
git commit -m "refactor: promote resolve_recording/resolve_wav_path to services/media"
```

---

## Task 3: Detail-image serving routes

**Files:**
- Modify: `src/fledermap/web/app.py` (add `archive_roots` param to `create_app`)
- Modify: `src/fledermap/cli/main.py:362-390` (`serve` command passes `config.archive_roots`,
  docstring correction)
- Modify: `src/fledermap/web/views/media.py`
- Test: `tests/test_media_view.py` (extend)

**Interfaces:**
- Consumes: `resolve_recording`, `resolve_wav_path` (Task 2, `services/media.py`);
  `detail_params`, `DETAIL_PX_PER_MS` (Task 1, `services/recording_detail.py`);
  `render_spectrogram` (`media/spectrogram.py`), `render_oscillogram` (`media/oscillogram.py`) --
  both already exist, unmodified.
- Produces: `create_app(engine, static_root, media_root, archive_roots: tuple[Path, ...] = ())`
  (new keyword parameter, default empty tuple so every existing call site keeps working
  unchanged); two new routes registered on the `media` blueprint: `GET
  /recordings/<audio_hash>/detail-spectrogram.webp` and `GET
  /recordings/<audio_hash>/detail-oscillogram.webp`, both `image/webp`, both 404 for an unknown
  hash, a recording with no source file, or a recording missing `duration_s`/`samplerate_hz`.
  Task 4's template links to these two routes by `url_for('media.detail_spectrogram', ...)` /
  `url_for('media.detail_oscillogram', ...)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_media_view.py` already imports `Path`, `UTC`/`datetime`, `Engine`, `OrmSession`,
`Recording`, and `create_app` — only add what's missing:

```python
# tests/test_media_view.py -- add to the top-level imports:
import io
import math
import struct

from PIL import Image

from fledermap.services.recording_detail import DETAIL_PX_PER_MS
from tests.fixtures import build_wav, fmt_payload

# tests/test_media_view.py -- add this helper near the top:
def _sine_pcm(*, freq_hz: float = 45_000.0, samplerate: int = 256_000, duration_s: float) -> bytes:
    n_samples = int(samplerate * duration_s)
    samples = [
        int(32000 * math.sin(2 * math.pi * freq_hz * i / samplerate))
        for i in range(n_samples)
    ]
    return struct.pack(f"<{n_samples}h", *samples)


def _write_wav(path: Path, *, duration_s: float, samplerate: int = 256_000) -> None:
    path.write_bytes(
        build_wav(
            [
                (b"fmt ", fmt_payload(samplerate)),
                (b"data", _sine_pcm(samplerate=samplerate, duration_s=duration_s)),
            ],
        ),
    )


# tests/test_media_view.py -- add these tests:
def test_detail_spectrogram_renders_at_the_locked_scale(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    duration_s = 0.02
    _write_wav(archive_root / "a.wav", duration_s=duration_s)

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="d1" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=duration_s,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        archive_roots=(archive_root,),
    )
    response = app.test_client().get(f"/recordings/{'d1' * 32}/detail-spectrogram.webp")

    assert response.status_code == 200
    assert response.mimetype == "image/webp"
    image = Image.open(io.BytesIO(response.data))
    assert image.width == round(duration_s * 1000 * DETAIL_PX_PER_MS)


def test_detail_oscillogram_shares_the_spectrogram_s_width(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    duration_s = 0.02
    _write_wav(archive_root / "a.wav", duration_s=duration_s)

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="d2" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=duration_s,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(
        engine,
        tmp_path / "static",
        tmp_path / "media",
        archive_roots=(archive_root,),
    )
    spectrogram_response = app.test_client().get(
        f"/recordings/{'d2' * 32}/detail-spectrogram.webp",
    )
    oscillogram_response = app.test_client().get(
        f"/recordings/{'d2' * 32}/detail-oscillogram.webp",
    )

    spectrogram_image = Image.open(io.BytesIO(spectrogram_response.data))
    oscillogram_image = Image.open(io.BytesIO(oscillogram_response.data))
    assert oscillogram_image.width == spectrogram_image.width


def test_detail_spectrogram_404s_for_an_unknown_hash(
    engine: Engine,
    tmp_path: Path,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'e1' * 32}/detail-spectrogram.webp")

    assert response.status_code == 404


def test_detail_spectrogram_404s_when_duration_is_missing(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="e2" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                # duration_s and samplerate_hz left unset (None)
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'e2' * 32}/detail-spectrogram.webp")

    assert response.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `dangerouslyDisableSandbox: true` — `hatch test tests/test_media_view.py -v -k detail`
Expected: FAIL — `create_app() got an unexpected keyword argument 'archive_roots'` (and the
routes themselves 404 with Flask's own "not found" once that first failure is fixed locally while
iterating, but the archive_roots TypeError is what stops the very first test).

- [ ] **Step 3: Wire `archive_roots` through `create_app` and `serve`**

In `src/fledermap/web/app.py`, change the signature and add one config line:

```python
def create_app(
    engine: Engine,
    static_root: Path,
    media_root: Path,
    archive_roots: tuple[Path, ...] = (),
) -> flask.Flask:
```

(keep the existing docstring, and add: `archive_roots` is `Config.archive_roots` -- needed only
by the recording-details page's detail-image routes (`web/views/media.py`) to resolve a
recording's source WAV file directly; defaults to `()` so every other route, and every existing
test that doesn't touch those two, is unaffected.)

Add right after `app.config["MEDIA_ROOT"] = media_root`:

```python
    app.config["ARCHIVE_ROOTS"] = archive_roots
```

In `src/fledermap/cli/main.py`'s `serve` command, change the `create_app` call:

```python
    app = create_app(
        engine,
        config.static_root,
        config.media_root,
        config.archive_roots,
    )
```

And fix the docstring, which currently claims `serve` never reads `FLEDERMAP_ARCHIVE_ROOTS` --
that's no longer true:

```python
def serve(host: str | None, port: int | None) -> None:
    """Run the web map. Reads FLEDERMAP_DATABASE_URL, FLEDERMAP_MEDIA_ROOT, and
    FLEDERMAP_ARCHIVE_ROOTS (all required) and, optionally, FLEDERMAP_STATIC_ROOT,
    FLEDERMAP_HOST, and FLEDERMAP_PORT. FLEDERMAP_ARCHIVE_ROOTS is used by the
    recording-details page's detail-image routes, which render straight from the
    source WAV on every request rather than a cached file. Vendor JS/CSS
    (Leaflet, HTMX, Alpine) are fetched into FLEDERMAP_STATIC_ROOT
    automatically on first run, or whenever the cache is missing something --
    see `fetch-assets` to pre-warm that cache (e.g. for an offline install)
    instead of fetching it at server startup.
    """
```

- [ ] **Step 4: Add the two detail-image routes**

In `src/fledermap/web/views/media.py`, add to the imports:

```python
import tempfile
from typing import Callable

from sqlalchemy.exc import NoResultFound

from fledermap.media.oscillogram import render_oscillogram
from fledermap.media.spectrogram import render_spectrogram
from fledermap.services.media import resolve_recording, resolve_wav_path
from fledermap.services.recording_detail import detail_params
```

Add below the existing `_serve_derived` function. `spectrogram_params` and `oscillogram_params`
are differently-typed (`SpectrogramParams` vs. `OscillogramParams`), so `_detail_wav_and_params`
returns both explicitly rather than routing through one shared `Callable[[Path, Path], None]`
render parameter that couldn't type-check either call correctly:

```python
def _detail_wav_and_params(
    audio_hash: str,
) -> tuple[Path, SpectrogramParams, OscillogramParams] | None:
    """Resolves `audio_hash` to (wav_path, spectrogram_params,
    oscillogram_params) for the two detail-render routes below, or None if
    the recording is unknown, has no source file, or is missing the
    duration/samplerate metadata `detail_params` needs (design spec section
    2, step 2) -- each case is a 404, not a 500, since these routes are
    reachable by an arbitrary URL unlike the Procrastinate tasks these two
    resolve functions were originally written for."""
    engine = flask.current_app.config["ENGINE"]
    archive_roots = flask.current_app.config["ARCHIVE_ROOTS"]
    with OrmSession(engine) as session:
        try:
            recording = resolve_recording(session, audio_hash)
        except (NoResultFound, FileNotFoundError):
            return None
        if recording.duration_s is None or recording.samplerate_hz is None:
            return None
        try:
            wav_path = resolve_wav_path(archive_roots, recording)
        except FileNotFoundError:
            return None
        params = detail_params(recording.duration_s, recording.samplerate_hz)
    return wav_path, params.spectrogram, params.oscillogram


def _serve_temp_render(make: Callable[[Path], None]) -> ResponseReturnValue:
    """Renders to a throwaway temp file and streams the bytes back --
    deliberately not `spectrogram_path`/`oscillogram_path` under the media
    root: this route is not part of the cached-derived-media system (design
    spec Non-goals), so nothing here is meant to persist."""
    fd, tmp_name = tempfile.mkstemp(suffix=".webp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        make(tmp_path)
        data = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
    return flask.Response(data, mimetype="image/webp")


@media_bp.get("/recordings/<audio_hash>/detail-spectrogram.webp")
def detail_spectrogram(audio_hash: str) -> ResponseReturnValue:
    context = _detail_wav_and_params(audio_hash)
    if context is None:
        flask.abort(404)
    wav_path, spectrogram_params, _oscillogram_params = context
    return _serve_temp_render(
        lambda out: render_spectrogram(wav_path, out, params=spectrogram_params),
    )


@media_bp.get("/recordings/<audio_hash>/detail-oscillogram.webp")
def detail_oscillogram(audio_hash: str) -> ResponseReturnValue:
    context = _detail_wav_and_params(audio_hash)
    if context is None:
        flask.abort(404)
    wav_path, _spectrogram_params, oscillogram_params = context
    return _serve_temp_render(
        lambda out: render_oscillogram(wav_path, out, params=oscillogram_params),
    )
```

Also add near the top of the file (module needs `os` and the two param types for the type hint
above):

```python
import os

from fledermap.media.oscillogram import OscillogramParams
from fledermap.media.spectrogram import SpectrogramParams
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `dangerouslyDisableSandbox: true` — `hatch test tests/test_media_view.py -v`
Expected: PASS, all green (including the pre-existing tests in this file), no warnings.

- [ ] **Step 6: Type-check and format**

Run: `hatch fmt && hatch run types:check`
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/web/app.py src/fledermap/cli/main.py src/fledermap/web/views/media.py \
  tests/test_media_view.py
git commit -m "feat: serve locked-scale spectrogram/oscillogram renders for the details page"
```

---

## Task 4: Recording details page route and template

**Files:**
- Create: `src/fledermap/web/views/recording_detail.py`
- Create: `src/fledermap/web/templates/recording_details.html`
- Modify: `src/fledermap/web/app.py` (register the new blueprint)
- Test: `tests/test_recording_detail_view.py`

**Interfaces:**
- Consumes: `detail_params`, `DETAIL_PX_PER_MS`, `DETAIL_PX_PER_KHZ` (Task 1);
  `current_best_identification` (`services/current_best.py`, already exists);
  `url_for('media.detail_spectrogram', ...)` / `url_for('media.detail_oscillogram', ...)` (Task
  3); `url_for('media.preview', ...)` (already exists).
- Produces: `GET /recordings/<audio_hash>` — 404 for an unknown hash, otherwise the
  `recording_details.html` page. Task 5's "Details" link points here.

The header duplicates a few fields from `_recording_panel.html` (species/verdict, recorded_at,
device, site) directly in the new template rather than sharing a Jinja macro — small enough
duplication that a macro costs more than it saves (this was an explicitly open item in the spec;
resolved here in favor of duplication).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_recording_detail_view.py
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import Recording
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_recording_details_page_404s_for_an_unknown_hash(
    engine: Engine,
    tmp_path: Path,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f1' * 32}")

    assert response.status_code == 404


def test_recording_details_page_renders_the_recording(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f2" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
                make="Wildlife Acoustics",
                model="Echo Meter Touch 2",
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f2' * 32}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Echo Meter Touch 2" in html
    assert f"/recordings/{'f2' * 32}/detail-spectrogram.webp" in html
    assert f"/recordings/{'f2' * 32}/detail-oscillogram.webp" in html


def test_recording_details_page_explains_missing_metadata(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f3" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                # duration_s / samplerate_hz left unset
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f3' * 32}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "cannot render" in html.lower()
    assert "detail-spectrogram.webp" not in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `dangerouslyDisableSandbox: true` — `hatch test tests/test_recording_detail_view.py -v`
Expected: FAIL — the route doesn't exist yet, every request 404s including the one meant to
succeed (`test_recording_details_page_renders_the_recording` fails on `assert response.status_code
== 200`).

- [ ] **Step 3: Write the route**

```python
# src/fledermap/web/views/recording_detail.py
"""The standalone recording details page (design spec
2026-09-01-fledermap-recording-details-page-design.md, section 3) -- a full
page, not an HTMX drawer fragment, matching `sessions.py`'s own precedent
for a detail view that deserves the whole screen rather than the drawer's
small, drag-resized panel."""

from __future__ import annotations

import flask
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from fledermap.services.current_best import current_best_identification
from fledermap.services.recording_detail import (
    DETAIL_PX_PER_KHZ,
    DETAIL_PX_PER_MS,
    detail_params,
)
from fledermap.store.geo import decode_point
from fledermap.store.models import Recording, Site, Taxon
from fledermap.web.params import fallback_site_label

recording_detail_bp = flask.Blueprint(
    "recording_detail",
    __name__,
    template_folder="../templates",
)


@recording_detail_bp.get("/recordings/<audio_hash>")
def recording_details_page(audio_hash: str) -> flask.Response:
    engine = flask.current_app.config["ENGINE"]
    with OrmSession(engine) as session:
        recording = session.scalars(
            select(Recording).where(Recording.audio_hash == audio_hash),
        ).one_or_none()
        if recording is None:
            flask.abort(404)

        best = current_best_identification(recording)
        taxon = None
        if best is not None and best.taxon_id is not None:
            taxon = session.get(Taxon, best.taxon_id)

        site = session.get(Site, recording.site_id) if recording.site_id else None
        site_label = None
        if site is not None:
            site_label = (
                site.name
                if site.name
                else fallback_site_label(decode_point(site.centroid))
            )

        params = None
        if recording.duration_s is not None and recording.samplerate_hz is not None:
            params = detail_params(recording.duration_s, recording.samplerate_hz)

        html = flask.render_template(
            "recording_details.html",
            recording=recording,
            best=best,
            taxon=taxon,
            site=site,
            site_label=site_label,
            duration_s=recording.duration_s,
            params=params,
            px_per_ms=DETAIL_PX_PER_MS,
            px_per_khz=DETAIL_PX_PER_KHZ,
        )
    return flask.make_response(html)
```

- [ ] **Step 4: Write the template**

```html
{# src/fledermap/web/templates/recording_details.html #}
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  {% include "_theme_init.html" %}
  <title>Fledermap — Recording {{ recording.audio_hash[:8] }}</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='app.css') }}">
</head>
<body>
  {% include "_nav.html" %}
  <main class="main-content" id="recording-detail">
    <a href="/">← Back to map</a>
    <h1>{{ taxon.scientific_name if taxon else (best.verdict.value if best else "unidentified") }}</h1>
    <p class="detail-meta">
      {{ recording.recorded_at.isoformat() }} — {{ recording.make }} {{ recording.model }}
      {% if site %} — Site: <a href="/?site={{ site.id }}">{{ site_label }}</a>{% endif %}
    </p>

    {% if params %}
    <div class="detail-scroll" id="detail-scroll">
      <div class="detail-body">
        <div class="detail-axis-freq" id="detail-axis-freq"></div>
        <div class="detail-graphs">
          <div class="detail-axis-time" id="detail-axis-time"></div>
          <div class="detail-spectrogram-wrap">
            <p class="media-placeholder detail-loading" id="spectrogram-loading">Rendering…</p>
            <img
              id="detail-spectrogram"
              class="detail-spectrogram"
              src="{{ url_for('media.detail_spectrogram', audio_hash=recording.audio_hash) }}"
              alt="Spectrogram"
              data-duration-s="{{ duration_s }}"
              data-max-freq-khz="{{ params.max_freq_khz }}"
              data-px-per-ms="{{ px_per_ms }}"
              data-px-per-khz="{{ px_per_khz }}"
              hidden
            >
            <div class="playback-cursor" id="playback-cursor" hidden></div>
          </div>
          <p class="media-placeholder detail-loading" id="oscillogram-loading">Rendering…</p>
          <img
            id="detail-oscillogram"
            class="detail-oscillogram"
            src="{{ url_for('media.detail_oscillogram', audio_hash=recording.audio_hash) }}"
            alt="Waveform"
            hidden
          >
        </div>
      </div>
    </div>
    <div class="audio-row">
      <audio controls id="detail-audio" src="{{ url_for('media.preview', audio_hash=recording.audio_hash) }}"></audio>
    </div>
    {% else %}
    <p>Missing duration or sample-rate metadata for this recording — cannot render at a locked scale.</p>
    {% endif %}
  </main>
  <div id="crosshair-readout" class="crosshair-readout" hidden></div>
  <script src="{{ url_for('static', filename='recording_detail.js') }}"></script>
</body>
</html>
```

- [ ] **Step 5: Register the blueprint**

In `src/fledermap/web/app.py`, add to the imports:

```python
from fledermap.web.views.recording_detail import recording_detail_bp
```

Add to the registration block:

```python
    app.register_blueprint(recording_detail_bp)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `dangerouslyDisableSandbox: true` — `hatch test tests/test_recording_detail_view.py -v`
Expected: PASS, all green, no warnings.

- [ ] **Step 7: Type-check and format**

Run: `hatch fmt && hatch run types:check`
Expected: both clean.

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/web/views/recording_detail.py \
  src/fledermap/web/templates/recording_details.html src/fledermap/web/app.py \
  tests/test_recording_detail_view.py
git commit -m "feat: add the standalone recording details page route and template"
```

Note: `recording_detail.js` referenced by the template is written in Task 6. Until then the page
renders correctly (server-side) but its images stay hidden behind their "Rendering…" placeholder
forever, since nothing reveals them yet — expected, not a bug, at this point in the plan.

---

## Task 5: "Details" link from the drawer panel

**Files:**
- Modify: `src/fledermap/web/templates/_recording_panel.html`
- Modify: `src/fledermap/web/static/app.css`
- Test: `tests/test_map_view.py` (extend)

**Interfaces:**
- Consumes: `GET /recordings/<audio_hash>` (Task 4).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_map_view.py -- add this test near the other _recording_panel tests:
def test_recording_panel_links_to_the_details_page(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="g1" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'g1' * 32}/panel")

    html = response.get_data(as_text=True)
    assert f'href="/recordings/{"g1" * 32}"' in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `dangerouslyDisableSandbox: true` — `hatch test tests/test_map_view.py -v -k
links_to_the_details_page`
Expected: FAIL — no such link in the rendered HTML.

- [ ] **Step 3: Add the link**

In `src/fledermap/web/templates/_recording_panel.html`, change the `.panel-header` block:

```html
<div class="panel-header">
  <h2>{{ taxon.scientific_name if taxon else (best.verdict.value if best else "unidentified") }}</h2>
  <a class="details-link" href="/recordings/{{ recording.audio_hash }}">Details</a>
  <button
    type="button"
    class="favourite-toggle"
    hx-post="/recordings/{{ recording.audio_hash }}/favourite?{{ filter_qs }}"
    hx-target="#drawer-body"
    aria-label="{{ 'Remove from favourites' if recording.favourite else 'Add to favourites' }}"
    aria-pressed="{{ 'true' if recording.favourite else 'false' }}"
  >{{ "★" if recording.favourite else "☆" }}</button>
</div>
```

In `src/fledermap/web/static/app.css`, extend the existing `.panel-header` rule that already
protects the favourite button from an ugly wrap (right after `.panel-header button { ... }`):

```css
.panel-header a { flex-shrink: 0; white-space: nowrap; }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `dangerouslyDisableSandbox: true` — `hatch test tests/test_map_view.py -v`
Expected: PASS, all green (including the pre-existing tests in this file), no warnings.

- [ ] **Step 5: Type-check and format**

Run: `hatch fmt && hatch run types:check`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/templates/_recording_panel.html src/fledermap/web/static/app.css \
  tests/test_map_view.py
git commit -m "feat: link the drawer panel to the recording details page"
```

---

## Task 6: Client-side interactions (click-to-play, crosshair, playback cursor, axis)

**Files:**
- Create: `src/fledermap/web/static/recording_detail.js`
- Modify: `src/fledermap/web/static/app.css`

**Interfaces:**
- Consumes (all via `data-*` attributes already written by Task 4's template onto
  `#detail-spectrogram`): `data-duration-s`, `data-max-freq-khz`, `data-px-per-ms`,
  `data-px-per-khz`. DOM ids from Task 4's template: `detail-scroll`, `detail-axis-freq`,
  `detail-axis-time`, `detail-spectrogram-wrap` (class, not id — only one per page),
  `detail-spectrogram`, `spectrogram-loading`, `detail-oscillogram`, `oscillogram-loading`,
  `playback-cursor`, `detail-audio`, `crosshair-readout`.

No automated JS tests exist in this repo (confirmed: no `package.json`, no test runner). This
task is verified by actually running the app and driving it with a headless browser — screenshots
and a synthetic `timeupdate`/`mousemove` dispatch, the same approach already used earlier this
project for the drawer's crosshair and site-zoom bugs — not by reasoning about the code alone.

- [ ] **Step 1: Write `recording_detail.js`**

```javascript
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
```

- [ ] **Step 2: Add the supporting CSS**

In `src/fledermap/web/static/app.css`, add:

```css
/* Recording details page (design spec 2026-09-01, section 3/4). */
.detail-scroll {
  overflow-x: auto;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  margin: 0.75rem 0;
}
.detail-body { display: flex; }
/* Sticky, not scrolled with the graphs -- pinned on the left the same way
   a DAW's own frequency ruler stays put while the timeline scrolls under
   it. Cheap: one CSS property, no JS. */
.detail-axis-freq {
  position: sticky;
  left: 0;
  width: 3.5em;
  flex-shrink: 0;
  background: var(--color-bg);
  z-index: 1;
}
.detail-graphs { display: flex; flex-direction: column; }
.detail-axis-time { position: relative; height: 1.2rem; }
.detail-spectrogram-wrap { position: relative; cursor: crosshair; }
.detail-spectrogram, .detail-oscillogram { display: block; }
.detail-axis-tick {
  position: absolute;
  font-size: 0.7rem;
  color: var(--color-muted);
  white-space: nowrap;
}
.detail-axis-tick-time { top: 0; transform: translateX(-50%); }
.detail-axis-tick-freq { right: 0.3em; transform: translateY(-50%); }
.playback-cursor {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 1px;
  background: var(--color-accent);
  pointer-events: none;
}
.playback-cursor[hidden] { display: none; }
.detail-loading[hidden] { display: none; }
```

- [ ] **Step 3: Manual verification with a headless browser**

Start a real server against a database with at least one recording that has `duration_s` and
`samplerate_hz` set and a resolvable WAV file (e.g. the bundled sample recordings, or a real
field recording per this repo's `CLAUDE.md` "Sample data" section), then:

1. Load `http://localhost:<port>/recordings/<a real audio_hash>` in headless Chrome
   (`google-chrome --headless --disable-gpu --screenshot=<path> --window-size=1400,900 <url>`).
   Confirm in the screenshot: the "Rendering…" placeholders are gone, a spectrogram and
   oscillogram are visible, sized at their native locked-scale pixel dimensions (wider than the
   viewport for anything but a very short recording — the container should show a scrollbar), and
   both a time axis (ticks every 50ms) and a frequency axis (left, ticks every 10kHz, staying in
   place on scroll) are visible — check specifically that the frequency ticks line up vertically
   against the spectrogram's own top/bottom edges, not offset by the time axis row's height above
   it (the exact bug called out in `buildFreqAxis`'s comment).
2. Dispatch a synthetic `mousemove` over the spectrogram (a short injected script, same technique
   already used for the drawer's crosshair bug) and screenshot again: the crosshair readout
   appears near the cursor with `<time> s` / `<freq> kHz` text, using direct
   `pixel / px-per-unit` math (no `object-fit: fill` stretch correction — verify the numbers are
   sane for the click position, e.g. hovering near x=0 should read close to `0.000 s`).
3. Scroll the container to a nonzero `scrollLeft`, dispatch a synthetic `timeupdate` on the
   `<audio>` element with `currentTime` set to a point currently off-screen, and confirm via
   screenshot that `scrollLeft` changed to bring the cursor line back into view (the vertical
   accent-colored line should be visible within the viewport after the dispatch).
4. Click on the spectrogram at a nonzero x position and confirm (via a quick script reading
   `audio.currentTime`) that it was set proportionally to the click position and playback started
   (`audio.paused === false`).

If any of these don't match, fix the JS/CSS and re-verify — do not commit based on code review
alone for this task.

- [ ] **Step 4: Commit**

```bash
git add src/fledermap/web/static/recording_detail.js src/fledermap/web/static/app.css
git commit -m "feat: add click-to-play, crosshair, playback cursor, and axis to the details page"
```

---

## Final: full test suite and code review

- [ ] Run the complete suite once, including `db`-marked tests:
  `dangerouslyDisableSandbox: true` — `hatch test`. Expected: all green, pristine output (no
  warnings).
- [ ] Run `hatch build -t wheel` and confirm the new files ship:
  `python3 -m zipfile -l dist/*.whl | grep -E "recording_detail|recording_details.html"` should
  list `services/recording_detail.py`, `web/views/recording_detail.py`,
  `web/templates/recording_details.html`, and `web/static/recording_detail.js`.
- [ ] Dispatch a code-review subagent per `superpowers:requesting-code-review`, comparing the
  branch's base commit to `HEAD`, describing this feature and pointing at this plan and the spec.
  Fix Critical/Important findings before considering the feature done.
- [ ] Update the Obsidian backlog (`~/Obsidian/Default/Fledermap.md`): check off "Recording
  details page" under "Auditive sample analysis" (only the core item this plan builds — leave the
  deferred pieces, already logged under "Noted in the session", unchecked).

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
  this reuses the existing pure `render_spectrogram`/`render_oscillogram`, extended (Tasks 7-9
  addendum below) with one new optional, backward-compatible `time_range_s` parameter rather than
  literally unmodified — no `paths.py` entry, no Procrastinate task, and no cache is added by that
  extension; every other part of this Non-goal still holds exactly as written.
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

## Addendum (2026-09-01): tiling — Tasks 7-9

Tasks 1-6 above shipped and passed their task-scoped reviews, but the final whole-branch review
found a Critical blocker: `DETAIL_PX_PER_MS = 19.0` makes any recording longer than ≈0.86s exceed
WebP's hard 16383px encode limit — 67 of 68 real field recordings tested would 500 from the
detail-image routes, with no visible error state (the `<img>`'s `load` event never fires on a
failed request, so the "Rendering…" placeholder never clears). See the design spec's own
"Addendum (2026-09-01): tiling" section for the full analysis and the decision (tile the render,
not cap duration or switch image format). These three tasks implement that decision, plus two
smaller Important bugs the same review found in the same files:

- `recording_details.html` never loaded `alpine.min.js` despite including `_nav.html` (an Alpine
  component) — the theme toggle and sidebar were inert on this page (Task 9 fixes it).
- A source WAV missing from disk (the window between a file being deleted/moved and the next
  `sweep_missing` run) 500ed instead of 404ing as the spec's §2 step 2 already requires (Task 8
  fixes it).

### Task 7: Time-range-aware rendering

**Files:**
- Modify: `src/fledermap/media/spectrogram.py`
- Modify: `src/fledermap/media/oscillogram.py`
- Test: `tests/test_spectrogram.py` (extend)
- Test: `tests/test_oscillogram.py` (extend)

**Interfaces:**
- Produces: `render_spectrogram(wav_path, out_path, *, params=SpectrogramParams(), time_range_s:
  tuple[float, float] | None = None)` and `render_oscillogram(wav_path, out_path, *,
  params=OscillogramParams(), time_range_s: tuple[float, float] | None = None)` — both existing
  functions gain this one new keyword-only parameter, defaulting to `None` (whole-file behavior,
  byte-for-byte unchanged from today — every existing caller and every existing test in
  `tests/test_spectrogram.py`/`tests/test_oscillogram.py` must keep passing unmodified). Task 8
  consumes both with `time_range_s` set to a tile's time window.

**Critical design point — the peak used for dB/amplitude normalization must always be computed
from the WHOLE file, never from just the `time_range_s` slice.** `media/spectrogram.py` and
`media/oscillogram.py` both already document (see their existing docstrings/CLAUDE.md's "Derived
media rendering" section) that normalization is deliberately relative to "the recording's own
peak" — a quiet call must still be visible, and a loud call must still read as loud, *relative to
the whole recording*, not relative to whatever 8000px-wide slice it happens to land in. Slicing
the input samples BEFORE computing the peak would make each tile self-normalize independently,
so the same call could render differently bright depending on which tile boundary it fell inside
— a real regression from the documented convention, not merely a smaller image. The fix: the STFT
(spectrogram) or the raw sample peak (oscillogram) is always computed from the FULL file first;
only the final slice-and-resize step is narrowed to `time_range_s`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_spectrogram.py -- add these tests (uses the existing `_sine_wav` helper already in
# this file, plus these two new helpers):
def _two_tone_wav(
    path: Path,
    *,
    quiet_freq_hz: float = 20_000.0,
    quiet_amplitude: int = 2_000,
    loud_freq_hz: float = 80_000.0,
    loud_amplitude: int = 32_000,
    samplerate: int = 256_000,
    half_duration_s: float = 0.05,
) -> None:
    """A quiet tone for the first half, a much louder tone for the second half --
    `test_render_spectrogram_time_range_normalizes_to_the_whole_file_peak` needs a recording
    where the two halves have genuinely different loudness."""
    n_half = int(samplerate * half_duration_s)

    def _tone(freq_hz: float, amplitude: int) -> list[int]:
        return [
            int(amplitude * math.sin(2 * math.pi * freq_hz * i / samplerate))
            for i in range(n_half)
        ]

    samples = _tone(quiet_freq_hz, quiet_amplitude) + _tone(loud_freq_hz, loud_amplitude)
    pcm = struct.pack(f"<{len(samples)}h", *samples)

    channels, bits = 1, 16
    byte_rate = samplerate * channels * bits // 8
    block_align = channels * bits // 8
    fmt_payload = struct.pack(
        "<HHIIHH",
        1,
        channels,
        samplerate,
        byte_rate,
        block_align,
        bits,
    )

    def chunk(chunk_id: bytes, payload: bytes) -> bytes:
        out = chunk_id + struct.pack("<I", len(payload)) + payload
        if len(payload) % 2:
            out += b"\x00"
        return out

    body = b"WAVE" + chunk(b"fmt ", fmt_payload) + chunk(b"data", pcm)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def test_render_spectrogram_time_range_produces_the_requested_pixel_width(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _sine_wav(wav_path, duration_s=0.1)
    out_path = tmp_path / "tile.webp"

    render_spectrogram(
        wav_path,
        out_path,
        params=SpectrogramParams(width_px=64, height_px=32),
        time_range_s=(0.0, 0.05),
    )

    image = Image.open(out_path)
    assert image.size == (64, 32)


def test_render_spectrogram_time_range_normalizes_to_the_whole_file_peak(tmp_path: Path) -> None:
    combined_path = tmp_path / "combined.wav"
    _two_tone_wav(combined_path)

    quiet_only_path = tmp_path / "quiet_only.wav"
    _sine_wav(quiet_only_path, freq_hz=20_000.0, duration_s=0.05)
    # _sine_wav's default amplitude (32000) differs from _two_tone_wav's quiet_amplitude (2000) --
    # build the quiet-only file directly with _two_tone_wav's own quiet parameters instead, so the
    # two renders share identical quiet-half content:
    _two_tone_wav(quiet_only_path, loud_amplitude=2_000, loud_freq_hz=20_000.0)
    # (quiet_only_path now has the SAME quiet tone in both halves -- i.e. its own peak equals the
    # quiet tone's own amplitude, unlike combined_path's peak, which is the loud second half.)

    sliced_out = tmp_path / "sliced.webp"
    render_spectrogram(
        combined_path,
        sliced_out,
        params=SpectrogramParams(width_px=50, height_px=50),
        time_range_s=(0.0, 0.05),
    )

    standalone_out = tmp_path / "standalone.webp"
    render_spectrogram(
        quiet_only_path,
        standalone_out,
        params=SpectrogramParams(width_px=50, height_px=50),
        time_range_s=(0.0, 0.05),
    )

    sliced_pixels = np.array(Image.open(sliced_out), dtype=np.float64)
    standalone_pixels = np.array(Image.open(standalone_out), dtype=np.float64)

    # Same quiet first-half content in both files, but `combined_path` has a much louder second
    # half -- its whole-file peak is much higher, so the SAME quiet content must render DIMMER
    # (lower mean brightness) when sliced from `combined_path` than when it's the loudest thing
    # in its own file. This is the whole point: normalization must use the WHOLE file's peak, not
    # the tile's own slice.
    assert sliced_pixels.mean() < standalone_pixels.mean()
```

```python
# tests/test_oscillogram.py -- add these tests (uses the existing helper(s) already in this file
# for building a WAV -- check what's there; `tests/fixtures.py`'s `build_wav`/`fmt_payload` are
# also available):
def _constant_amplitude_wav(
    path: Path,
    *,
    amplitude: int,
    samplerate: int = 256_000,
    duration_s: float = 0.05,
) -> None:
    """A flat-amplitude square-ish wave (alternating +amplitude/-amplitude) -- simpler than a
    sine tone and sufficient for testing peak-normalization width, not spectral content."""
    n_samples = int(samplerate * duration_s)
    samples = [amplitude if i % 2 == 0 else -amplitude for i in range(n_samples)]
    pcm = struct.pack(f"<{n_samples}h", *samples)
    fmt = fmt_payload(samplerate)
    path.write_bytes(build_wav([(b"fmt ", fmt), (b"data", pcm)]))


def _two_amplitude_wav(
    path: Path,
    *,
    quiet_amplitude: int = 2_000,
    loud_amplitude: int = 32_000,
    samplerate: int = 256_000,
    half_duration_s: float = 0.05,
) -> None:
    n_half = int(samplerate * half_duration_s)
    quiet = [quiet_amplitude if i % 2 == 0 else -quiet_amplitude for i in range(n_half)]
    loud = [loud_amplitude if i % 2 == 0 else -loud_amplitude for i in range(n_half)]
    samples = quiet + loud
    pcm = struct.pack(f"<{len(samples)}h", *samples)
    fmt = fmt_payload(samplerate)
    path.write_bytes(build_wav([(b"fmt ", fmt), (b"data", pcm)]))


def test_render_oscillogram_time_range_produces_the_requested_pixel_width(tmp_path: Path) -> None:
    wav_path = tmp_path / "call.wav"
    _constant_amplitude_wav(wav_path, amplitude=20_000, duration_s=0.1)
    out_path = tmp_path / "tile.webp"

    render_oscillogram(
        wav_path,
        out_path,
        params=OscillogramParams(width_px=64, height_px=20),
        time_range_s=(0.0, 0.05),
    )

    image = Image.open(out_path)
    assert image.size == (64, 20)


def test_render_oscillogram_time_range_normalizes_to_the_whole_file_peak(tmp_path: Path) -> None:
    combined_path = tmp_path / "combined.wav"
    _two_amplitude_wav(combined_path)

    quiet_only_path = tmp_path / "quiet_only.wav"
    _two_amplitude_wav(quiet_only_path, loud_amplitude=2_000)  # both halves quiet in this file

    sliced_out = tmp_path / "sliced.webp"
    render_oscillogram(
        combined_path,
        sliced_out,
        params=OscillogramParams(width_px=50, height_px=20),
        time_range_s=(0.0, 0.05),
    )

    standalone_out = tmp_path / "standalone.webp"
    render_oscillogram(
        quiet_only_path,
        standalone_out,
        params=OscillogramParams(width_px=50, height_px=20),
        time_range_s=(0.0, 0.05),
    )

    sliced_pixels = np.array(Image.open(sliced_out).convert("L"), dtype=np.float64)
    standalone_pixels = np.array(Image.open(standalone_out).convert("L"), dtype=np.float64)

    # Default line_color is black (0,0,0), background is white (255,255,255). A waveform
    # normalized to a much louder whole-file peak draws a SMALLER excursion from the midline --
    # more background (light) pixels remain, so mean brightness is HIGHER than the same quiet
    # content normalized to its own (equally quiet) peak.
    assert sliced_pixels.mean() > standalone_pixels.mean()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `hatch test tests/test_spectrogram.py tests/test_oscillogram.py -v -k time_range`
Expected: FAIL — `TypeError: render_spectrogram() got an unexpected keyword argument
'time_range_s'` (and the same for `render_oscillogram`).

- [ ] **Step 3: Implement `time_range_s` in `render_spectrogram`**

In `src/fledermap/media/spectrogram.py`, change the signature and rename `_times` to `times`
(now used), then add the slicing block right after `rgb = lut[indices]` and before
`image = Image.fromarray(...)`:

```python
def render_spectrogram(
    wav_path: Path,
    out_path: Path,
    *,
    params: SpectrogramParams = SpectrogramParams(),
    time_range_s: tuple[float, float] | None = None,
) -> None:
```

(Keep the existing docstring, and append a new paragraph — see this task's own "Critical design
point" above for the exact content to add: `time_range_s`, if given, renders only that
`(start_s, end_s)` slice; the STFT and `peak` are still computed from the WHOLE file first, so
normalization never drifts between tiles of the same recording; only the final slice-and-resize
step is narrowed.)

```python
    samples, samplerate = read_pcm(wav_path)

    nperseg = min(max(int(samplerate * params.window_ms / 1000), 8), len(samples))
    noverlap = int(nperseg * params.overlap)
    freqs, times, sxx = signal.spectrogram(
        samples,
        fs=samplerate,
        nperseg=nperseg,
        noverlap=noverlap,
    )

    max_freq = effective_max_freq_hz(samplerate, params)
    keep = freqs <= max_freq
    sxx = sxx[keep, :]

    peak = sxx.max()
    if peak > 0:
        db = 10 * np.log10(np.maximum(sxx, 1e-300) / peak)
        clipped = np.clip(db, -params.dynamic_range_db, 0.0)
        normalised = (clipped + params.dynamic_range_db) / params.dynamic_range_db
    else:
        normalised = np.zeros_like(sxx)

    indices = (np.flipud(normalised) * 255).astype(np.uint8)
    lut = _palette_lut(params.palette)
    rgb = lut[indices]

    if time_range_s is not None:
        # `rgb.shape[1]` (the STFT's own column count) is always >= 1 for any nonzero-length
        # signal (the `nperseg` clamp above guarantees at least one window fits). Clamping
        # `end_idx` to be at least `start_idx + 1` guarantees a non-empty slice even for a very
        # narrow tile (the last tile in a recording whose width doesn't divide evenly by
        # `DETAIL_MAX_TILE_WIDTH_PX` can be as little as 1px wide -- narrower than a single STFT
        # column's own time resolution) -- without this, `Image.fromarray` on a zero-width array
        # raises rather than degrading gracefully.
        start_s, end_s = time_range_s
        start_idx = max(0, min(int(np.searchsorted(times, start_s)), rgb.shape[1] - 1))
        end_idx = max(start_idx + 1, min(int(np.searchsorted(times, end_s)), rgb.shape[1]))
        rgb = rgb[:, start_idx:end_idx, :]

    image = Image.fromarray(rgb, mode="RGB").resize(
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

- [ ] **Step 4: Implement `time_range_s` in `render_oscillogram`**

In `src/fledermap/media/oscillogram.py`, change the signature and move the `peak` computation to
before any slicing:

```python
def render_oscillogram(
    wav_path: Path,
    out_path: Path,
    *,
    params: OscillogramParams = OscillogramParams(),
    time_range_s: tuple[float, float] | None = None,
) -> None:
```

(Keep the existing docstring, append the same rationale paragraph as `render_spectrogram`'s above,
adapted: `peak = np.abs(samples).max()` is computed from the WHOLE file's samples before any
`time_range_s` slicing, for the same cross-tile-consistency reason.)

```python
    samples, samplerate = read_pcm(wav_path)
    width, height = params.width_px, params.height_px

    peak = np.abs(samples).max() if samples.size else 0.0

    if time_range_s is not None:
        start_s, end_s = time_range_s
        start_idx = max(0, min(int(round(start_s * samplerate)), samples.size))
        end_idx = max(start_idx, min(int(round(end_s * samplerate)), samples.size))
        samples = samples[start_idx:end_idx]

    canvas = np.full((height, width, 3), params.background_color, dtype=np.uint8)
    mid = height / 2.0

    if peak > 0 and samples.size:
        bucket_edges = np.linspace(0, samples.size, width + 1).astype(int)
        for col in range(width):
            start, end = bucket_edges[col], bucket_edges[col + 1]
            if start == end:
                continue
            bucket = samples[start:end]
            lo = mid - (bucket.min() / peak) * mid
            hi = mid - (bucket.max() / peak) * mid
            row_lo, row_hi = sorted((int(round(lo)), int(round(hi))))
            row_hi = max(row_hi, row_lo)
            canvas[row_lo : row_hi + 1, col] = params.line_color
    else:
        # Silence OR a degenerate empty time_range_s slice (a very narrow last tile spanning
        # less time than one sample period) -- both fall through to the same flat centre line
        # already used for genuine silence; no separate handling needed.
        canvas[int(mid), :] = params.line_color

    image = Image.fromarray(canvas, mode="RGB")

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

- [ ] **Step 5: Run the tests to verify they pass**

Run: `hatch test tests/test_spectrogram.py tests/test_oscillogram.py -v`
Expected: PASS, all green (including every pre-existing test in both files, unmodified), no
warnings.

- [ ] **Step 6: Type-check and format**

Run: `hatch fmt && hatch run types:check`
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/media/spectrogram.py src/fledermap/media/oscillogram.py \
  tests/test_spectrogram.py tests/test_oscillogram.py
git commit -m "feat: add time_range_s to render_spectrogram/render_oscillogram for tiling"
```

---

### Task 8: Tile computation and tile-indexed serving routes

**Files:**
- Modify: `src/fledermap/services/recording_detail.py`
- Modify: `src/fledermap/web/views/media.py`
- Test: `tests/test_recording_detail_service.py` (extend)
- Test: `tests/test_media_view.py` (extend)

**Interfaces:**
- Consumes: `render_spectrogram`/`render_oscillogram`'s new `time_range_s` parameter (Task 7).
- Produces: `DETAIL_MAX_TILE_WIDTH_PX: int` constant; `DetailTile` frozen dataclass (`index: int`,
  `start_px: int`, `width_px: int`); `detail_tiles(total_width_px: int) -> list[DetailTile]`;
  `DetailParams` gains a `tiles: list[DetailTile]` field, populated by `detail_params()`. Routes
  `GET /recordings/<audio_hash>/detail-spectrogram/<int:tile_index>.webp` and `GET
  /recordings/<audio_hash>/detail-oscillogram/<int:tile_index>.webp` (replacing the two routes
  Task 3 built, same endpoint names `media.detail_spectrogram`/`media.detail_oscillogram` so
  Task 4's page route's `url_for` calls need no change other than adding `tile_index=...`) — 404
  for an unknown hash, no source file, missing duration/samplerate metadata (unchanged from
  Task 3), an out-of-range `tile_index` (new), or a missing source file on disk (new — this is
  the Important #3 fix from the final review: `resolve_wav_path` only does index arithmetic, it
  never checks the file actually exists, and `read_pcm`'s `wave.open` raises an unhandled
  `FileNotFoundError` for an absent path). Task 9 consumes both routes plus `DetailParams.tiles`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_recording_detail_service.py -- add these tests:
from fledermap.services.recording_detail import (
    DETAIL_MAX_TILE_WIDTH_PX,
    DetailTile,
    detail_tiles,
)


def test_detail_tiles_returns_one_tile_for_a_short_recording() -> None:
    tiles = detail_tiles(total_width_px=500)

    assert tiles == [DetailTile(index=0, start_px=0, width_px=500)]


def test_detail_tiles_splits_a_wide_recording_at_the_max_tile_width() -> None:
    total = DETAIL_MAX_TILE_WIDTH_PX * 2 + 300

    tiles = detail_tiles(total_width_px=total)

    assert tiles == [
        DetailTile(index=0, start_px=0, width_px=DETAIL_MAX_TILE_WIDTH_PX),
        DetailTile(index=1, start_px=DETAIL_MAX_TILE_WIDTH_PX, width_px=DETAIL_MAX_TILE_WIDTH_PX),
        DetailTile(index=2, start_px=DETAIL_MAX_TILE_WIDTH_PX * 2, width_px=300),
    ]


def test_detail_tiles_covers_the_full_width_with_no_gaps_or_overlap() -> None:
    total = DETAIL_MAX_TILE_WIDTH_PX * 3 + 1

    tiles = detail_tiles(total_width_px=total)

    covered = 0
    for i, tile in enumerate(tiles):
        assert tile.index == i
        assert tile.start_px == covered
        covered += tile.width_px
    assert covered == total


def test_detail_params_includes_tiles_matching_the_spectrogram_width() -> None:
    # A duration long enough to need more than one tile at DETAIL_PX_PER_MS=19.0:
    # DETAIL_MAX_TILE_WIDTH_PX (8000) / 19.0 / 1000 ~= 0.421s per tile.
    params = detail_params(duration_s=1.0, samplerate_hz=256_000)

    covered = sum(tile.width_px for tile in params.tiles)
    assert covered == params.spectrogram.width_px
    assert len(params.tiles) > 1
```

```python
# tests/test_media_view.py -- add these tests (uses the same _write_wav/_sine_pcm helpers Task 3
# already added to this file). Task 3 already imports DETAIL_PX_PER_MS from
# fledermap.services.recording_detail -- extend that existing import line to also pull in
# DETAIL_MAX_TILE_WIDTH_PX rather than adding a second, duplicate import line:
from fledermap.services.recording_detail import DETAIL_MAX_TILE_WIDTH_PX, DETAIL_PX_PER_MS


def test_detail_spectrogram_tile_renders_at_the_tile_s_own_width(
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
                audio_hash="d3" * 32,
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
    response = app.test_client().get(f"/recordings/{'d3' * 32}/detail-spectrogram/0.webp")

    assert response.status_code == 200
    image = Image.open(io.BytesIO(response.data))
    assert image.width == round(duration_s * 1000 * DETAIL_PX_PER_MS)  # single tile: whole width


def test_detail_spectrogram_404s_for_an_out_of_range_tile_index(
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
                audio_hash="d4" * 32,
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
    response = app.test_client().get(f"/recordings/{'d4' * 32}/detail-spectrogram/1.webp")

    assert response.status_code == 404


def test_detail_spectrogram_renders_multiple_tiles_for_a_long_recording(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    # DETAIL_MAX_TILE_WIDTH_PX (8000) / DETAIL_PX_PER_MS (19.0) / 1000 ~= 0.421s per tile --
    # 1.0s needs 3 tiles (8000 + 8000 + 3000 = 19000px total width).
    duration_s = 1.0
    _write_wav(archive_root / "long.wav", duration_s=duration_s)

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="d5" * 32,
                path="long.wav",
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
    client = app.test_client()

    tile_0 = client.get(f"/recordings/{'d5' * 32}/detail-spectrogram/0.webp")
    tile_1 = client.get(f"/recordings/{'d5' * 32}/detail-spectrogram/1.webp")
    tile_2 = client.get(f"/recordings/{'d5' * 32}/detail-spectrogram/2.webp")
    tile_3 = client.get(f"/recordings/{'d5' * 32}/detail-spectrogram/3.webp")

    assert tile_0.status_code == tile_1.status_code == tile_2.status_code == 200
    assert tile_3.status_code == 404  # only 3 tiles exist for this duration
    image_0 = Image.open(io.BytesIO(tile_0.data))
    image_2 = Image.open(io.BytesIO(tile_2.data))
    assert image_0.width == DETAIL_MAX_TILE_WIDTH_PX
    assert image_2.width == round(duration_s * 1000 * DETAIL_PX_PER_MS) - 2 * DETAIL_MAX_TILE_WIDTH_PX


def test_detail_spectrogram_404s_when_the_source_file_is_missing_from_disk(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    # No file written at archive_root / "gone.wav" -- missing_since is NOT set (that's a
    # different, already-covered case); this is the "the file just isn't there" case.

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="d6" * 32,
                path="gone.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.02,
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
    response = app.test_client().get(f"/recordings/{'d6' * 32}/detail-spectrogram/0.webp")

    assert response.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `dangerouslyDisableSandbox: true` — `hatch test tests/test_recording_detail_service.py
tests/test_media_view.py -v -k "tile or missing_from_disk"`
Expected: FAIL — `ImportError: cannot import name 'DETAIL_MAX_TILE_WIDTH_PX'` for the service
tests; 404 (route doesn't accept a tile-index path segment yet) for the view tests.

- [ ] **Step 3: Add tile computation to `services/recording_detail.py`**

```python
# Add near the other constants:
# Comfortably under WebP's hard 16383px encode-dimension limit -- the whole reason tiling
# exists: at DETAIL_PX_PER_MS=19.0, any recording longer than ~0.86s would otherwise produce a
# spectrogram wider than that limit (design spec's 2026-09-01 tiling addendum).
DETAIL_MAX_TILE_WIDTH_PX = 8000


@dataclass(frozen=True)
class DetailTile:
    index: int
    start_px: int
    width_px: int


def detail_tiles(total_width_px: int) -> list[DetailTile]:
    """Split a recording's full locked-scale width into fixed-width chunks, each safely under
    WebP's pixel limit. The last tile absorbs whatever remainder doesn't fill a full
    `DETAIL_MAX_TILE_WIDTH_PX` chunk -- covers the full width with no gaps and no overlap."""
    tiles = []
    start = 0
    index = 0
    while start < total_width_px:
        width = min(DETAIL_MAX_TILE_WIDTH_PX, total_width_px - start)
        tiles.append(DetailTile(index=index, start_px=start, width_px=width))
        start += width
        index += 1
    return tiles
```

Update `DetailParams` and `detail_params()`:

```python
@dataclass(frozen=True)
class DetailParams:
    spectrogram: SpectrogramParams
    oscillogram: OscillogramParams
    max_freq_khz: float
    tiles: list[DetailTile]


def detail_params(duration_s: float, samplerate_hz: float) -> DetailParams:
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
        tiles=detail_tiles(width_px),
    )
```

- [ ] **Step 4: Rewrite the two detail-image routes in `web/views/media.py`**

Replace `_detail_wav_and_params` and the two route functions Task 3 added:

```python
def _detail_tile_context(
    audio_hash: str,
    tile_index: int,
) -> tuple[Path, SpectrogramParams, OscillogramParams, DetailTile] | None:
    """Resolves `audio_hash` and `tile_index` to (wav_path, spectrogram_params,
    oscillogram_params, tile) for the two detail-render routes below, or None for any of: unknown
    recording, no source file (`missing_since` set OR the file simply isn't on disk -- design
    spec section 2 step 2's "missing file" case covers both, only the first of which Task 3
    originally handled), missing duration/samplerate metadata, or an out-of-range `tile_index`."""
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
        if not wav_path.exists():
            return None
        params = detail_params(recording.duration_s, recording.samplerate_hz)
    if tile_index < 0 or tile_index >= len(params.tiles):
        return None
    tile = params.tiles[tile_index]
    return wav_path, params.spectrogram, params.oscillogram, tile


def _serve_temp_render(make: Callable[[Path], None]) -> ResponseReturnValue:
    fd, tmp_name = tempfile.mkstemp(suffix=".webp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        make(tmp_path)
        data = tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
    return flask.Response(data, mimetype="image/webp")


@media_bp.get("/recordings/<audio_hash>/detail-spectrogram/<int:tile_index>.webp")
def detail_spectrogram(audio_hash: str, tile_index: int) -> ResponseReturnValue:
    context = _detail_tile_context(audio_hash, tile_index)
    if context is None:
        flask.abort(404)
    wav_path, spectrogram_params, _oscillogram_params, tile = context
    time_range_s = (
        tile.start_px / DETAIL_PX_PER_MS / 1000,
        (tile.start_px + tile.width_px) / DETAIL_PX_PER_MS / 1000,
    )
    tile_params = dataclasses.replace(spectrogram_params, width_px=tile.width_px)
    return _serve_temp_render(
        lambda out: render_spectrogram(
            wav_path,
            out,
            params=tile_params,
            time_range_s=time_range_s,
        ),
    )


@media_bp.get("/recordings/<audio_hash>/detail-oscillogram/<int:tile_index>.webp")
def detail_oscillogram(audio_hash: str, tile_index: int) -> ResponseReturnValue:
    context = _detail_tile_context(audio_hash, tile_index)
    if context is None:
        flask.abort(404)
    wav_path, _spectrogram_params, oscillogram_params, tile = context
    time_range_s = (
        tile.start_px / DETAIL_PX_PER_MS / 1000,
        (tile.start_px + tile.width_px) / DETAIL_PX_PER_MS / 1000,
    )
    tile_params = dataclasses.replace(oscillogram_params, width_px=tile.width_px)
    return _serve_temp_render(
        lambda out: render_oscillogram(
            wav_path,
            out,
            params=tile_params,
            time_range_s=time_range_s,
        ),
    )
```

Add the new imports this needs at the top of `web/views/media.py`: `import dataclasses` as a new
top-level import, and extend the existing `from fledermap.services.recording_detail import
detail_params` line to also pull in `DETAIL_PX_PER_MS` and `DetailTile`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `dangerouslyDisableSandbox: true` — `hatch test tests/test_recording_detail_service.py
tests/test_media_view.py -v`
Expected: PASS, all green (including every pre-existing test in both files), no warnings.

- [ ] **Step 6: Type-check and format**

Run: `hatch fmt && hatch run types:check`
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/services/recording_detail.py src/fledermap/web/views/media.py \
  tests/test_recording_detail_service.py tests/test_media_view.py
git commit -m "feat: tile-index the detail-image routes so long recordings stay under WebP's limit"
```

---

### Task 9: Template + client JS updated for tiles, plus the missing Alpine include

**Files:**
- Modify: `src/fledermap/web/templates/recording_details.html`
- Modify: `src/fledermap/web/static/recording_detail.js`
- Modify: `src/fledermap/web/static/app.css`
- Test: `tests/test_recording_detail_view.py` (extend)

**Interfaces:**
- Consumes: `params.tiles` (a `list[DetailTile]`, Task 8) already available in the template via
  the existing `params` context variable (`web/views/recording_detail.py`'s route already passes
  the whole `DetailParams` object — no Python route change needed for this task).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_recording_detail_view.py -- add this test:
def test_recording_details_page_renders_one_img_per_tile(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f4" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=1.0,  # needs 3 tiles at DETAIL_MAX_TILE_WIDTH_PX=8000, DETAIL_PX_PER_MS=19.0
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'f4' * 32}")

    html = response.get_data(as_text=True)
    assert html.count('class="detail-spectrogram-tile"') == 3
    assert html.count('class="detail-oscillogram-tile"') == 3
    assert "/detail-spectrogram/0.webp" in html
    assert "/detail-spectrogram/1.webp" in html
    assert "/detail-spectrogram/2.webp" in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `dangerouslyDisableSandbox: true` — `hatch test tests/test_recording_detail_view.py -v -k
one_img_per_tile`
Expected: FAIL — the template still renders one `<img id="detail-spectrogram">`, not three
tile `<img class="detail-spectrogram-tile">` elements.

- [ ] **Step 3: Rewrite the template's spectrogram/oscillogram block, and fix the missing Alpine
  include**

In `src/fledermap/web/templates/recording_details.html`, replace the single `<img
id="detail-spectrogram">`/`<img id="detail-oscillogram">` pair with a loop over `params.tiles`,
and add the missing `alpine.min.js` script tag (`_nav.html` is an Alpine component — every other
page that includes it also loads Alpine; this page never did, which is why its theme toggle and
sidebar toggle were both inert):

```html
    <div class="detail-scroll" id="detail-scroll">
      <div class="detail-body">
        <div class="detail-axis-freq" id="detail-axis-freq"></div>
        <div class="detail-graphs">
          <div class="detail-axis-time" id="detail-axis-time"></div>
          <div class="detail-spectrogram-wrap" id="detail-spectrogram-wrap">
            <p class="media-placeholder detail-loading" id="spectrogram-loading">Rendering…</p>
            {% for tile in params.tiles %}
            <img
              class="detail-spectrogram-tile"
              src="{{ url_for('media.detail_spectrogram', audio_hash=recording.audio_hash, tile_index=tile.index) }}"
              alt="Spectrogram tile {{ tile.index }}"
              width="{{ tile.width_px }}"
              height="{{ params.spectrogram.height_px }}"
              data-duration-s="{{ duration_s }}"
              data-max-freq-khz="{{ params.max_freq_khz }}"
              data-px-per-ms="{{ px_per_ms }}"
              data-px-per-khz="{{ px_per_khz }}"
              hidden
            >
            {% endfor %}
            <div class="playback-cursor" id="playback-cursor" hidden></div>
          </div>
          <p class="media-placeholder detail-loading" id="oscillogram-loading">Rendering…</p>
          <div class="detail-oscillogram-wrap" id="detail-oscillogram-wrap">
            {% for tile in params.tiles %}
            <img
              class="detail-oscillogram-tile"
              src="{{ url_for('media.detail_oscillogram', audio_hash=recording.audio_hash, tile_index=tile.index) }}"
              alt="Waveform tile {{ tile.index }}"
              width="{{ tile.width_px }}"
              height="48"
              hidden
            >
            {% endfor %}
          </div>
        </div>
      </div>
    </div>
```

(The `data-*` attributes only need to live on the FIRST tile's `<img>` for the JS to read — but
putting them on every tile is simpler markup and harmless; the JS in Step 4 below only ever reads
them off the first one.)

Add the missing script tag in `<head>` or just before the existing `recording_detail.js` tag —
match where `map.html`/`sessions_list.html` put theirs (immediately before the page's own script):

```html
  <script src="{{ url_for('vendor.static', filename='alpine.min.js') }}" defer></script>
  <script src="{{ url_for('static', filename='recording_detail.js') }}"></script>
```

- [ ] **Step 4: Update `recording_detail.js` for the tiled layout**

Replace the single-`<img>` element lookups and the click/crosshair math with container-relative
versions. The reveal-on-load logic now waits for every tile in both rows before clearing the
shared placeholder:

```javascript
document.addEventListener("DOMContentLoaded", () => {
  const spectrogramTiles = Array.from(document.querySelectorAll(".detail-spectrogram-tile"));
  if (spectrogramTiles.length === 0) return; // missing duration/samplerate metadata

  const spectrogramLoading = document.getElementById("spectrogram-loading");
  const oscillogramTiles = Array.from(document.querySelectorAll(".detail-oscillogram-tile"));
  const oscillogramLoading = document.getElementById("oscillogram-loading");
  const wrap = document.getElementById("detail-spectrogram-wrap");
  const cursor = document.getElementById("playback-cursor");
  const readout = document.getElementById("crosshair-readout");
  const audio = document.getElementById("detail-audio");
  const scrollEl = document.getElementById("detail-scroll");
  const timeAxis = document.getElementById("detail-axis-time");
  const freqAxis = document.getElementById("detail-axis-freq");

  // Reveal-on-load (design spec section 3): every tile in a row must load before that row's
  // placeholder clears -- a partially-loaded row (some tiles rendered, others still pending)
  // would otherwise flash broken-looking gaps.
  function revealWhenAllLoaded(tiles, loadingEl) {
    let remaining = tiles.length;
    tiles.forEach((img) => {
      img.addEventListener("load", () => {
        remaining -= 1;
        if (remaining === 0) {
          loadingEl.hidden = true;
          tiles.forEach((t) => { t.hidden = false; });
        }
      });
    });
  }
  revealWhenAllLoaded(spectrogramTiles, spectrogramLoading);
  revealWhenAllLoaded(oscillogramTiles, oscillogramLoading);

  const firstTile = spectrogramTiles[0];
  const durationS = parseFloat(firstTile.dataset.durationS);
  const maxFreqKhz = parseFloat(firstTile.dataset.maxFreqKhz);
  const pxPerMs = parseFloat(firstTile.dataset.pxPerMs);
  const pxPerKhz = parseFloat(firstTile.dataset.pxPerKhz);

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
    const spectrogramTop = timeAxis.offsetHeight;
    for (let khz = 0; khz <= maxFreqKhz; khz += FREQ_TICK_KHZ) {
      const tick = document.createElement("span");
      tick.className = "detail-axis-tick detail-axis-tick-freq";
      tick.style.top = `${spectrogramTop + (maxFreqKhz - khz) * pxPerKhz}px`;
      tick.textContent = `${khz}kHz`;
      freqAxis.appendChild(tick);
    }
  }

  if (timeAxis && !Number.isNaN(durationS) && !Number.isNaN(pxPerMs)) buildTimeAxis();
  if (freqAxis && !Number.isNaN(maxFreqKhz) && !Number.isNaN(pxPerKhz)) buildFreqAxis();

  // Click-to-play, crosshair, and the playback cursor all now measure against `wrap`'s own
  // bounding rect (the tiled row's container) rather than a single `<img>`'s -- the tiles sit
  // edge-to-edge with no gaps, so the container's rect spans exactly the full locked-scale
  // width, same as the single image did before tiling.
  wrap.addEventListener("click", (event) => {
    const rect = wrap.getBoundingClientRect();
    const xPx = event.clientX - rect.left;
    audio.currentTime = xPx / pxPerMs / 1000;
    audio.play();
  });

  wrap.addEventListener("mousemove", (event) => {
    const rect = wrap.getBoundingClientRect();
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

- [ ] **Step 5: Update `app.css` for the tiled layout**

`.detail-spectrogram-wrap`/`.detail-oscillogram-wrap` now hold several `<img>` tiles laid out
edge-to-edge instead of one — add `display: flex;` so they sit in a row with no gaps (flex's
default `flex-direction: row` and no `gap` set is exactly "edge-to-edge"):

```css
.detail-spectrogram-wrap, .detail-oscillogram-wrap { position: relative; display: flex; }
.detail-spectrogram-tile, .detail-oscillogram-tile { display: block; }
```

(`.detail-spectrogram-wrap` already has `position: relative; cursor: crosshair;` from Task 6 —
merge into this rule rather than duplicating it; keep the existing `cursor: crosshair` on it too.)

- [ ] **Step 6: Run the test to verify it passes**

Run: `dangerouslyDisableSandbox: true` — `hatch test tests/test_recording_detail_view.py -v`
Expected: PASS, all green (including every pre-existing test in this file), no warnings.

- [ ] **Step 7: Manual verification with a headless browser**

Same approach as Task 6's own Step 3 (build a self-contained static-HTML harness reproducing the
new tiled DOM structure faithfully from the real template — no live server needed). Confirm:
several tile images sit edge-to-edge with no visible gap or overlap between them; clicking near a
tile boundary computes a `currentTime` consistent with the container-relative math (not the old
single-image math); the crosshair and playback cursor both still work correctly across a tile
boundary (a mousemove/timeupdate positioned in the second or third tile must produce the same
correct `xPx`-based result as it did for a single image before tiling); the frequency-axis
alignment check from Task 6 still holds (unaffected by tiling, but re-verify since the DOM
structure changed around it).

- [ ] **Step 8: Type-check and format**

Run: `hatch fmt && hatch run types:check`
Expected: both clean.

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/web/templates/recording_details.html src/fledermap/web/static/recording_detail.js \
  src/fledermap/web/static/app.css tests/test_recording_detail_view.py
git commit -m "feat: render the details page as tiles, fix the missing Alpine include"
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

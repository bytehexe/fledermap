# Fledermap Detail Page: WAV Guard + Scale/FFT Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close two follow-ups from the recording details page's "Noted in the session" backlog
(`~/Obsidian/Default/Fledermap.md`): (1) a corrupt/truncated source WAV 500s instead of 404ing
like every other unreadable-recording case on this page, and (2) the locked scale and FFT window
are stretching far more display pixels than the STFT has real time resolution for, producing the
tile-seam blur — pick and ship new defaults backed by a real visual comparison, not a numbers-only
guess.

**Architecture:** Task 1 is a straight bugfix: `media/wav_pcm.py` gains a narrow exception type
for the handful of concrete failure modes a malformed WAV can hit, and the two detail-tile routes
in `web/views/media.py` catch it and 404, exactly like the existing missing-file case. Task 2 is a
spike: a throwaway comparison script renders a few `window_ms`/`overlap`/scale combinations
against a real field recording into a single contact-sheet image for Janna to eyeball, then the
chosen combination becomes the new constants in `services/recording_detail.py` and
`media/spectrogram.py`'s `SpectrogramParams` defaults, with a dated deviation note in the design
spec (project convention: a pinned "D-decision" number changes only with a spec update alongside
the code, per `CLAUDE.md`'s recording-details section).

**Tech Stack:** Python/Flask, `wave` (stdlib), numpy, Pillow — same stack as the existing
`media/` and `web/views/media.py` code this touches.

**Spec:** `docs/superpowers/specs/2026-09-01-fledermap-recording-details-page-design.md`
(locked-scale decision, §1/§4; the tiling addendum's render-cost numbers, which Task 2 revises)

## Global Constraints

- Every new/changed Python file must pass `hatch fmt` and `hatch run types:check` (mypy covers
  `tests/` too).
- New tests must be run and shown RED before the implementation, then GREEN after (Task 1; Task 2
  has one small test of its own, see below).
- `hatch test -m "not db"` is the fast pre-commit subset; both new tests in this plan are
  `db`-marked (they go through the Flask app + a real `Recording` row), so also run the full
  `hatch test` (needs `dangerouslyDisableSandbox: true`, Docker) before considering either task
  done — pre-commit will not catch a `db`-marked regression.
- Do not touch `jobs/tasks.py`'s cached-drawer render path in either task — both tasks are scoped
  to the standalone detail-page routes only (`web/views/media.py`'s `detail_spectrogram`/
  `detail_oscillogram`, and the constants `detail_params` computes from).
- Task 2 changes a spec-pinned constant (design spec §1: "explicitly provisional... refine after
  real recordings are on screen, not treated as a final spec-locked number") — the spec itself
  must be updated in the same task, not left to drift from the code the way `CLAUDE.md`'s
  migrations section warns about for schema drift.

---

### Task 1: Corrupt/truncated WAV 404s instead of 500ing

**Files:**
- Modify: `src/fledermap/media/wav_pcm.py`
- Modify: `src/fledermap/web/views/media.py:143-183` (`detail_spectrogram`, `detail_oscillogram`)
- Test: `tests/test_media_view.py`

**Interfaces:**
- Consumes: `fledermap.media.wav_pcm.read_pcm(wav_path: Path) -> tuple[np.ndarray, int]` (existing
  signature, unchanged).
- Produces: `fledermap.media.wav_pcm.UnreadableWavError(Exception)` — raised by `read_pcm` in
  place of letting `wave.Error`/`ValueError` propagate raw. `web/views/media.py`'s two detail
  routes catch it.

Three concrete failure modes reach `read_pcm` for a real corrupt/truncated file (verified against
this repo's own `wave`/numpy versions, not assumed):
1. A file that isn't a valid RIFF/WAVE container at all → `wave.open` raises `wave.Error("file
   does not start with RIFF id")` (or similar) immediately.
2. A file truncated mid-sample (an odd number of PCM bytes actually on disk, however many the
   header claims) → `wave.readframes` returns however many bytes are actually there — `wave`
   itself does **not** raise — but `np.frombuffer(raw, dtype=np.int16)` then raises
   `ValueError("buffer size must be a multiple of element size")`.
3. A multi-channel file truncated to a sample count not divisible by its channel count →
   `samples.reshape(-1, n_channels)` raises `ValueError("cannot reshape array of size ... into
   shape (...)")`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_media_view.py` (it already imports `build_wav`, `fmt_payload` from
`tests.fixtures`, and follows the exact pattern of
`test_detail_spectrogram_404s_when_the_source_file_is_missing_from_disk` just above where this
goes):

```python
def test_detail_spectrogram_404s_for_a_truncated_source_file(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    wav_bytes = build_wav(
        [(b"fmt ", fmt_payload(256_000)), (b"data", _sine_pcm(duration_s=0.02))],
    )
    # Drop an odd number of trailing bytes: the header still claims the original data length,
    # but the file itself ends mid-sample -- `wave.readframes` returns the (odd-length) bytes
    # actually present rather than raising, so this only fails downstream in
    # `np.frombuffer(raw, dtype=np.int16)`. This is the real corrupt/truncated-file shape, not a
    # synthetic exception.
    (archive_root / "truncated.wav").write_bytes(wav_bytes[:-501])

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="e7" * 32,
                path="truncated.wav",
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
    response = app.test_client().get(
        f"/recordings/{'e7' * 32}/detail-spectrogram/0.webp"
    )
    assert response.status_code == 404


def test_detail_oscillogram_404s_for_a_truncated_source_file(
    engine: Engine,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    wav_bytes = build_wav(
        [(b"fmt ", fmt_payload(256_000)), (b"data", _sine_pcm(duration_s=0.02))],
    )
    (archive_root / "truncated.wav").write_bytes(wav_bytes[:-501])

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="e8" * 32,
                path="truncated.wav",
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
    response = app.test_client().get(
        f"/recordings/{'e8' * 32}/detail-oscillogram/0.webp"
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_media_view.py -k truncated_source_file -v`
Expected: FAIL — both requests currently return 500 (an uncaught `ValueError` propagating out of
Flask's request handler), not 404.

- [ ] **Step 3: Add `UnreadableWavError` and raise it from `read_pcm`**

Replace the full contents of `src/fledermap/media/wav_pcm.py`:

```python
"""Reading 16-bit PCM WAV audio as a plain float array. Shared by
`spectrogram.py` and `oscillogram.py` -- both need the same raw samples, and
a second, drifted copy of this is exactly the kind of thing that goes stale
silently (see `media/paths.py`'s docstring on writer/reader formula
agreement for the general shape of that risk).
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


class UnreadableWavError(Exception):
    """Raised by `read_pcm` for a WAV file that exists on disk but can't be
    decoded as PCM audio -- a corrupt header, or a file truncated mid-sample.
    Callers that already 404 for a *missing* source file (recording detail
    page's tile routes) should treat this the same way rather than letting
    it surface as a raw 500: from a client's perspective "the file is there
    but unreadable" and "the file isn't there" are the same unusable state.
    """


def read_pcm(wav_path: Path) -> tuple[np.ndarray, int]:
    """Read mono or multi-channel 16-bit PCM as a 1-D float array (channels
    averaged down to mono) plus the file's own sample rate.

    Raises `UnreadableWavError` for a file that isn't a valid RIFF/WAVE
    container (`wave.open` itself raises `wave.Error`/`EOFError`), or one
    truncated mid-sample -- `wave.readframes` silently returns however many
    bytes are actually on disk rather than raising, so a short read only
    surfaces once `np.frombuffer`/`reshape` see a byte count that doesn't
    evenly divide into samples (`ValueError`).
    """
    try:
        with wave.open(str(wav_path), "rb") as wav:
            n_channels = wav.getnchannels()
            samplerate = wav.getframerate()
            raw = wav.readframes(wav.getnframes())
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
        if n_channels > 1:
            samples = samples.reshape(-1, n_channels).mean(axis=1)
    except (wave.Error, EOFError, ValueError) as exc:
        raise UnreadableWavError(f"cannot read PCM from {wav_path}: {exc}") from exc
    return samples, samplerate
```

- [ ] **Step 4: Catch it in the two detail-tile routes**

In `src/fledermap/web/views/media.py`, add the import and wrap both render calls:

```python
from fledermap.media.wav_pcm import UnreadableWavError
```

Change `detail_spectrogram`:

```python
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
    try:
        return _serve_temp_render(
            lambda out: render_spectrogram(
                wav_path,
                out,
                params=tile_params,
                time_range_s=time_range_s,
            ),
        )
    except UnreadableWavError:
        flask.abort(404)
```

Change `detail_oscillogram` the same way:

```python
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
    try:
        return _serve_temp_render(
            lambda out: render_oscillogram(
                wav_path,
                out,
                params=tile_params,
                time_range_s=time_range_s,
            ),
        )
    except UnreadableWavError:
        flask.abort(404)
```

(`flask.abort(404)` raises `werkzeug.exceptions.NotFound`, so the `except` block never falls
through to a `return None` — mypy already treats `_serve_temp_render`'s call as the function's
return in the non-error path, unchanged from before.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_media_view.py -v`
Expected: PASS — the full file, not just the two new tests, to confirm nothing else in this file
regressed (`_serve_temp_render`'s temp-file cleanup in particular: the `finally: tmp_path.unlink()`
must still run when `make()` raises `UnreadableWavError`, since that exception now propagates
through `_serve_temp_render` unchanged before the route's own `try/except` catches it one frame
up).

- [ ] **Step 6: Type-check and format**

Run: `hatch fmt && hatch run types:check`
Expected: no changes needed beyond what Step 3/4 already wrote; no new mypy errors.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/media/wav_pcm.py src/fledermap/web/views/media.py tests/test_media_view.py
git commit -m "fix: corrupt/truncated source WAV 404s on the detail page instead of 500ing"
```

---

### Task 2: Scale/FFT spike — pick real defaults from a visual comparison

This task is a spike, not a mechanical constant change (see this plan's Goal, above): the values
themselves are trivial to edit, but which values are right needs eyes on real output first. Steps
1-3 produce that comparison; Steps 4-7 apply whatever Janna picks after seeing it.

**Files:**
- Create: `scripts/detail_scale_spike.py` (dev-only, not part of the shipped wheel — same category
  as the other `scripts/` git-hook tooling per `CLAUDE.md`'s "Environment gotchas"; this one is a
  one-off comparison generator, not wired into any hook)
- Modify: `src/fledermap/services/recording_detail.py` (`DETAIL_PX_PER_MS`, `DETAIL_PX_PER_KHZ`)
- Modify: `src/fledermap/media/spectrogram.py:32-33` (`SpectrogramParams.window_ms`,
  `SpectrogramParams.overlap` defaults)
- Modify: `docs/superpowers/specs/2026-09-01-fledermap-recording-details-page-design.md` (dated
  deviation note, §1)
- Test: `tests/test_recording_detail.py` (new constant-value assertion; the file likely already
  exists for `detail_params`/`detail_tiles` — check with `ls tests/test_recording_detail.py`
  before assuming this is a new file)

**Interfaces:**
- Consumes: `fledermap.media.spectrogram.render_spectrogram` (existing signature, unchanged —
  the spike script is just another caller, like `jobs/tasks.py`).
- Produces: nothing new for other code — this task only changes the *values* of
  `DETAIL_PX_PER_MS`, `DETAIL_PX_PER_KHZ`, `SpectrogramParams.window_ms`, and
  `SpectrogramParams.overlap`, which every existing caller of `detail_params`/`SpectrogramParams`
  already consumes by name.

- [ ] **Step 1: Write the comparison script**

Create `scripts/detail_scale_spike.py`:

```python
"""One-off visual comparison for the recording-details page's locked scale and FFT window (backlog:
'FFT params for the detail page', 'reconsider the locked scale itself'). Not part of the shipped
package -- dev-only, run manually against a real field recording, same category as this
directory's other git-hook tooling per CLAUDE.md.

Renders a fixed ~1s slice of a real recording at several `(window_ms, overlap, px_per_ms)`
combinations side by side as rows in one contact-sheet PNG, so the choice is a visual comparison,
not a numbers-only guess.

Usage:
    hatch run python scripts/detail_scale_spike.py <path-to-wav> [--start-s 0.0] [--duration-s 1.0]
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

from PIL import Image, ImageDraw

from fledermap.media.spectrogram import SpectrogramParams, render_spectrogram

# (window_ms, overlap, px_per_ms) -- the current shipped default first, as a baseline row, then
# candidates. px_per_ms is varied too since the backlog names it as an independent, related lever
# (less display stretch per real STFT column even at a fixed FFT resolution).
CANDIDATES: list[tuple[float, float, float]] = [
    (3.0, 0.5, 19.0),  # current shipped default (services/recording_detail.py + spectrogram.py)
    (3.0, 0.85, 19.0),  # more overlap, same window: more real columns/sec, same freq resolution
    (1.5, 0.5, 19.0),  # narrower window: finer time resolution, coarser freq resolution
    (3.0, 0.5, 12.0),  # unchanged FFT, less display stretch
    (1.5, 0.5, 12.0),  # both levers together
]

ROW_LABEL_HEIGHT_PX = 24


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wav_path", type=Path)
    parser.add_argument("--start-s", type=float, default=0.0)
    parser.add_argument("--duration-s", type=float, default=1.0)
    parser.add_argument(
        "--out", type=Path, default=Path("detail_scale_spike_contact_sheet.png")
    )
    args = parser.parse_args()

    rows: list[Image.Image] = []
    for window_ms, overlap, px_per_ms in CANDIDATES:
        width_px = round(args.duration_s * 1000 * px_per_ms)
        params = SpectrogramParams(
            window_ms=window_ms,
            overlap=overlap,
            width_px=width_px,
            height_px=282,  # half the shipped DETAIL_PX_PER_KHZ height -- plenty to compare shape
        )
        tmp_out = Path(f"_spike_tile_{window_ms}_{overlap}_{px_per_ms}.webp")
        render_spectrogram(
            args.wav_path,
            tmp_out,
            params=params,
            time_range_s=(args.start_s, args.start_s + args.duration_s),
        )
        tile = Image.open(tmp_out).convert("RGB")
        tmp_out.unlink()

        row = Image.new(
            "RGB", (tile.width, tile.height + ROW_LABEL_HEIGHT_PX), (255, 255, 255)
        )
        row.paste(tile, (0, ROW_LABEL_HEIGHT_PX))
        draw = ImageDraw.Draw(row)
        draw.text(
            (4, 4),
            f"window_ms={window_ms} overlap={overlap} px_per_ms={px_per_ms}",
            fill=(0, 0, 0),
        )
        rows.append(row)

    max_width = max(r.width for r in rows)
    total_height = sum(r.height for r in rows)
    sheet = Image.new("RGB", (max_width, total_height), (255, 255, 255))
    y = 0
    for row in rows:
        sheet.paste(row, (0, y))
        y += row.height
    sheet.save(args.out)
    print(f"wrote {args.out} ({sheet.width}x{sheet.height})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against a real field recording**

Run:
```bash
hatch run python scripts/detail_scale_spike.py \
  ~/Bat\ Sessions/Session_20260826_173533/<a-file-with-a-visible-call>.wav \
  --start-s <call-start> --duration-s 1.0
```

Pick `--start-s` from a file already known to contain a call — check
`docs/superpowers/specs/2026-08-23-fledermap-fledermap-design.md`'s R1-R3 section or the app's own
map/drawer for which of the 10 sample files has one (per `CLAUDE.md`, 9 of the 10 are "No ID"/
"Noise", not silence — pick one with an actual visible call shape in the existing drawer
spectrogram, not just any file).

Expected: `detail_scale_spike_contact_sheet.png` written, 5 labelled rows, one per candidate.

- [ ] **Step 3: Get Janna's read on the contact sheet**

Show her the PNG (or its path) and ask which row's call shape reads most clearly — sharp edges,
distinguishable frequency sweep, least visible blur/smear — against the tradeoffs already named in
the backlog note this task closes: more `overlap` costs more render CPU per tile; a smaller
`window_ms` trades away frequency resolution; a smaller `px_per_ms` shrinks tile count/render cost
as a side benefit but makes the on-screen image physically smaller. This is the decision point —
do not pick a row unilaterally and proceed past this step without her answer.

- [ ] **Step 4: Apply the chosen values**

Using Janna's picked `(window_ms, overlap, px_per_ms)` — for illustration below, assume she picks
row 2 `(3.0, 0.85, 19.0)` (more overlap, same window and scale); substitute whatever she actually
picked:

In `src/fledermap/media/spectrogram.py`, change the `SpectrogramParams` default:

```python
    window_ms: float = 3.0
    overlap: float = 0.85
```

(Leave `window_ms` at whatever she picked — this example keeps it unchanged. If she picked a row
that also changes `px_per_ms`, edit `DETAIL_PX_PER_MS` in
`src/fledermap/services/recording_detail.py` to match — do not leave the spike script's value and
the shipped constant disagreeing.)

Note: this default is also what `jobs/tasks.py`'s cached drawer pipeline renders with — the Global
Constraints section above scopes this plan to the detail-page routes, but `SpectrogramParams`'
default is shared by construction (it's the one dataclass both callers build from). If Janna wants
the drawer's cached render left at the old default and only the detail page's fresh renders
changed, that needs an explicit second `SpectrogramParams` instance passed at the two detail routes
instead of touching the shared default — flag this back to her rather than silently picking one;
don't decide it here.

- [ ] **Step 5: Update the spec's dated deviation note**

In `docs/superpowers/specs/2026-09-01-fledermap-recording-details-page-design.md`, find §1's scale
discussion (`DETAIL_PX_PER_MS`/`DETAIL_PX_PER_KHZ`, currently documented as "explicitly a tunable
starting point, not a final number... refine once real recordings are actually on screen") and add
a dated addendum immediately after it, following this doc's existing addendum convention (see the
"Addendum (2026-09-01): tiling" section further down in the same file for the exact shape/tone to
match):

```markdown
## Addendum (2026-09-02): FFT window and locked scale, revised from a real-recording comparison

Quantified against a real bat-call screenshot 2026-09-02 (see backlog): at the original
`window_ms=3.0`/`overlap=0.5`/`DETAIL_PX_PER_MS=19.0`, each real STFT column (1.5ms at
`nperseg=768`, 256kHz) was stretched to 28.5 display px -- the visible tile-boundary blur was this,
not a rendering defect. Time resolution was the actual bottleneck, not frequency (333Hz/bin against
564 display px for 0-120kHz was comparatively well-matched).

Changed defaults (`media/spectrogram.py`'s `SpectrogramParams`, `services/recording_detail.py`'s
`DETAIL_PX_PER_MS`/`DETAIL_PX_PER_KHZ`): <fill in exactly what Step 4 set, and the new
px-per-real-STFT-column stretch ratio computed the same way as the paragraph above, so a future
reader doesn't have to re-derive it>.
```

Fill in the placeholder with the real chosen numbers before committing — this plan cannot know
Janna's answer in advance, but the committed spec must not contain a placeholder (a bare `<fill
in...>` left in committed docs is exactly the kind of gap `CLAUDE.md`'s "No Placeholders" convention
exists to prevent — resolve it here, don't leave it for a later pass).

- [ ] **Step 6: Add a constant-value regression test**

Check first whether `tests/test_recording_detail.py` exists:

```bash
ls tests/test_recording_detail.py
```

If it exists, add this test to it; if not, this step's test is likely already covered by an
existing `detail_params`-shape test elsewhere — search before creating a new file
(`grep -rln DETAIL_PX_PER_MS tests/`). Either way, add (adjusting the expected values to whatever
was actually picked in Step 4):

```python
def test_locked_scale_constants_match_the_spec_decision() -> None:
    """Pins the values chosen by the 2026-09-02 scale/FFT spike (see the design spec's dated
    addendum) -- a future change to these constants should be a deliberate spec update, not an
    accidental edit that silently drifts from what's documented."""
    from fledermap.services.recording_detail import DETAIL_PX_PER_KHZ, DETAIL_PX_PER_MS

    assert DETAIL_PX_PER_MS == 19.0  # replace with the actually-chosen value
    assert DETAIL_PX_PER_KHZ == 4.7  # replace with the actually-chosen value


def test_default_spectrogram_params_match_the_spec_decision() -> None:
    from fledermap.media.spectrogram import SpectrogramParams

    params = SpectrogramParams()
    assert params.window_ms == 3.0  # replace with the actually-chosen value
    assert params.overlap == 0.85  # replace with the actually-chosen value
```

Run: `hatch test tests/test_recording_detail.py -v` (or wherever Step 6 placed it)
Expected: PASS, asserting the exact values Step 4 wrote — this test's only job is to catch future
drift between the code and the spec's dated addendum, not to validate the numbers are "correct"
(there's no correctness check possible here beyond "matches what was decided").

- [ ] **Step 7: Full verification and commit**

Run: `hatch fmt && hatch run types:check && hatch test` (the full suite, including `-m db`,
`dangerouslyDisableSandbox: true` — this touches shared defaults, so the whole suite needs to stay
green, not just the fast subset).

```bash
git add src/fledermap/media/spectrogram.py src/fledermap/services/recording_detail.py \
  scripts/detail_scale_spike.py tests/test_recording_detail.py \
  docs/superpowers/specs/2026-09-01-fledermap-recording-details-page-design.md
git commit -m "fix: revise detail-page FFT window and locked scale from a real-recording comparison"
```

---

## Self-Review Notes

- **Spec coverage:** Task 1 covers the backlog's "guard against a corrupt/truncated ... source WAV
  file" item exactly (both detail routes, both failure shapes actually reachable — verified against
  this repo's `wave`/numpy behavior, not assumed). Task 2 covers both "reconsider the locked scale
  itself" and "FFT params for the detail page" as one spike, per the conversation's own framing —
  they're the same underlying "too much stretch, not enough real STFT data" finding.
- **Explicit deviation from strict TDD (Task 2):** there is no "correct" expected pixel output to
  assert against ahead of time — the whole point is a human visual judgment call. Step 6's test
  pins whatever was chosen as a drift guard, not a correctness check; this is the honest shape of a
  spike task, not a shortcut around testing.
- **Placeholder scan:** Step 5's spec-addendum template and Step 6's example asserted values are
  deliberately marked as needing the real chosen numbers substituted in — flagged explicitly in the
  step text as a required fill-in before commit, not left silent.
- **Cached-drawer-pipeline interaction (Task 2 Step 4):** flagged explicitly as a decision to route
  back to Janna, not resolved unilaterally in either direction, since `SpectrogramParams`'s default
  is shared by both the drawer's cached pipeline and the detail page's fresh renders.

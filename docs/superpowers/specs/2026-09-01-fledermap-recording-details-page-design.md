# Fledermap Recording Details Page (core) — Design

**Status:** draft — sections approved individually in chat during brainstorming; awaiting the
user's review of this written spec (see brainstorming skill's user-review gate) before writing
an implementation plan.
**Date:** 2026-09-01

## Problem

The Obsidian backlog's "Recording details page" item bundles several independent pieces (locked
scale + higher-resolution rendering + a tool framework + batdetect2 call-box overlays + a
call-distance histogram + a sound-level-spectrum view + HET playback). Several of those depend on
work that doesn't exist yet (a batdetect2 classifier backend) or are genuinely separate additions
(HET playback is already its own backlog item). This spec covers only the load-bearing core: a
standalone details page with a locked, DAW-like time/frequency scale, rendered directly from the
raw audio, with one interaction tool (click-to-play, pan, crosshair). Everything else is deferred
to its own future spec once this exists.

The drawer's existing spectrogram/oscillogram (`_recording_panel.html`) are deliberately NOT
locked-scale: they stretch to whatever box CSS gives them (`object-fit: fill`, see this repo's
own CLAUDE.md on "Derived media rendering"), which is right for an overview but wrong for
comparing calls by shape — a recording's actual frequency-time slope is only meaningful at a
consistent, known scale.

## Goals

- A standalone page at `GET /recordings/<audio_hash>` showing that recording's spectrogram and
  oscillogram at a **locked** time/frequency scale (not stretched to a container), wide/tall
  enough that the browser's own scrolling handles panning through a long recording.
- The scale is a tunable constant, not hard-coded reasoning baked into the renderer: **≈19
  px/ms** horizontal, **≈4.7 px/kHz** vertical over a fixed 0–120kHz (Nyquist-clamped) height
  (≈567px, "≈15cm" at 96dpi CSS px) — derived from the Skiba identification guide's 10ms:40kHz
  convention against the user's target print height, explicitly a starting point to refine once
  real recordings are on screen, not a final number.
- One interaction tool on the spectrogram: click seeks the existing `<audio>` preview element to
  that time and plays; native browser scroll handles panning; the crosshair readout already built
  for the drawer is ported over (same freq/time math, simpler here since there's no
  `object-fit: fill` stretch to account for).
- A playback cursor: a line tracking the `<audio>` element's current position during playback
  (not the mouse — that's the crosshair, above). If it scrolls out of the visible area, the view
  snaps back just enough to bring it into view again — no continuous auto-follow/centering (see
  "Future slots" below for that, as an explicit later toggle rather than default behavior).
- Rendered directly from the raw audio on each request — no derived-media cache file, no
  Procrastinate job. A visible loading state while that render happens.
- A "Details" link from the drawer's recording panel to this page.

## Non-goals

- **No caching of the detail render.** Deliberate, not an oversight: rendering from raw audio on
  every view keeps the door open for future per-render processing (filters, gain, denoising) that
  a cached-by-params-hash file would otherwise need its own invalidation scheme for. If this
  turns out too slow in practice, switching to the existing `spectrogram_path`/`oscillogram_path`
  cache mechanism (already `params`-aware, no changes needed there) is a contained follow-up, not
  a redesign.
- **No tool-switching UI.** Only the one tool exists; a toolbar with nothing to switch between is
  premature. See "Future slots" below for what a toolbar area will eventually need to hold.
- **No batdetect2 overlays, no call-distance histogram, no sound-level-spectrum panel, no HET
  playback controls, no ruler tool.** Each is either blocked on work that doesn't exist yet or a
  genuinely separate addition — own future spec, once this page exists to build on.
- **No new derived-media artifact type in the `paths.py`/job/enqueue sense.** Nothing is persisted,
  so none of the five places CLAUDE.md's "third artifact type" warning covers (path helper,
  Procrastinate task, lock key, enqueue call, `_has_media` check) apply here — this reuses the
  existing pure `render_spectrogram`/`render_oscillogram` functions unmodified, targeting a
  per-request temp file instead of the persistent media root.

## Design

### 1. Scale constants

New module-level constants (`web/views/` or a small `services/` module — see §2):

```python
DETAIL_PX_PER_MS = 19.0
DETAIL_PX_PER_KHZ = 4.7
DETAIL_MAX_FREQ_KHZ = 120.0  # Nyquist-clamped per-recording, same convention
                              # as effective_max_freq_hz already uses elsewhere
```

Per-recording params, both spectrogram and oscillogram sharing the same computed width (matching
`oscillogram.py`'s existing "same width as the spectrogram" convention, just with a computed
width here instead of the fixed default):

```python
def detail_params(duration_s: float, samplerate_hz: float) -> tuple[SpectrogramParams, OscillogramParams]:
    width_px = round(duration_s * 1000 * DETAIL_PX_PER_MS)
    max_freq_hz = min(DETAIL_MAX_FREQ_KHZ * 1000, samplerate_hz / 2)
    height_px = round((max_freq_hz / 1000) * DETAIL_PX_PER_KHZ)
    spectrogram = SpectrogramParams(width_px=width_px, height_px=height_px, max_freq_hz=max_freq_hz)
    oscillogram = OscillogramParams(width_px=width_px)  # height stays its own small fixed default
    return spectrogram, oscillogram
```

Pure function, trivially unit-testable (duration → width_px, samplerate → clamped height_px) with
no DB/filesystem involved.

### 2. Serving route(s)

Two new routes, e.g. `GET /recordings/<audio_hash>/detail-spectrogram.webp` and
`GET /recordings/<audio_hash>/detail-oscillogram.webp` (`web/views/media.py`, alongside the
existing three `_serve_derived`-backed routes, though these two don't go through that helper
since they don't read a persistent path):

1. Resolve the `Recording` and its WAV path — reusing (not duplicating) `jobs/tasks.py`'s
   `_resolve_recording`/`_resolve_wav_path`. Since a web route reaching into another module's
   underscore-private functions is exactly the kind of boundary violation this project avoids
   elsewhere, promote both to public functions in `services/media.py` (their natural shared
   home — the "second consumer promotes it to shared" convention already used for
   `.filter-bar`'s CSS in `docs/style-guide.md`), with `jobs/tasks.py` importing them from there
   instead of defining them locally. Pure refactor, no behavior change — existing job tests must
   still pass unmodified.
2. `Recording` not found, or `_resolve_recording`/`_resolve_wav_path` raise (missing file, bad
   `archive_root_index`) → 404, not a raw 500 (a route reached by an arbitrary URL needs this;
   the existing Procrastinate tasks don't, since they're only ever triggered for known-good
   recordings — this is new handling, not something to copy from the tasks unchanged).
3. Compute `detail_params(recording.duration_s, recording.samplerate_hz)` (§1).
4. Render to a temp path (`tempfile.NamedTemporaryFile` or equivalent) via the existing
   `render_spectrogram`/`render_oscillogram` — unmodified, no new parameters on those functions.
5. Read the rendered bytes, respond `image/webp`, delete the temp file in a `finally`.

### 3. Page route and template

`GET /recordings/<audio_hash>` (`web/views/map.py` or a new `web/views/recording_detail.py` if
`map.py` is getting large — decide at implementation time by its actual size then, not guessed
now): 404 for an unknown hash (reuse the same resolve-or-404 as §2, or just let a missing
recording's `<img>` 404 individually — open item, see below). Otherwise renders a new
`recording_details.html`:

- Header: recording metadata + current-best identification, reusing what `_recording_panel.html`
  already shows (species/verdict, session, site) rather than re-deriving it — exact reuse
  mechanism (shared Jinja macro/include vs. just accepting some duplication) is an implementation
  detail, not a design decision.
- A horizontally-scrollable container (plain CSS `overflow-x: auto`, no custom pan/drag JS — the
  browser's native scrollbar and trackpad/wheel scrolling already do this) holding the
  fixed-width spectrogram `<img>` above the oscillogram `<img>`, sharing one time axis.
- A dense axis: gridlines at fixed ms/kHz intervals (computed from `DETAIL_PX_PER_MS`/
  `DETAIL_PX_PER_KHZ`, not the drawer's fixed 3-label axis), covering the recording's actual
  duration/frequency range.
- The spectrogram `<img>` sits inside a `position: relative` wrapper — the one concrete
  architectural commitment for "future slots" (see below): costs nothing now, is what
  batdetect2 call-box overlays and time-marker annotations will need later without restructuring.
- Both `<img>` elements start hidden behind a "rendering…" placeholder (same visual idiom as the
  drawer's existing "not processed yet" placeholder, swapped for "processing now"), revealed on
  the image's `load` event — necessary here specifically because there is no cache-hit fast path
  (§ Non-goals): every visit renders fresh, so the page cannot assume the image is ready
  immediately the way the drawer's already-rendered thumbnails can.
- A "Details" link added to `_recording_panel.html`, pointing here.

### 4. Default tool (client-side JS)

Extends `app.js`'s existing patterns (no new library):

- **Click** on the spectrogram: compute `time_s = click_x_px / DETAIL_PX_PER_MS / 1000`, set the
  page's `<audio>` element's `currentTime` to that, call `.play()`.
- **Pan**: native scroll, nothing to build.
- **Crosshair**: port the drawer's existing readout logic. Actually simpler here than the
  drawer's version — the drawer's math has to undo `object-fit: fill`'s independent-axis
  stretching (`relative-position * range`); this page's fixed scale means it's a direct
  `pixel / px-per-unit` computation, no stretch to account for.
- **Playback cursor**: a `timeupdate` listener on the page's `<audio>` element positions an
  absolutely-placed line inside the same `position: relative` spectrogram wrapper already
  committed to for overlays (§3/§5) — `x = currentTime * 1000 * DETAIL_PX_PER_MS`, same unit
  conversion the click handler uses in reverse. Snap-into-view-when-off-screen only (no
  continuous auto-follow — see "Future slots" below): on each `timeupdate`, if the cursor's `x`
  falls outside the scroll container's current visible range, scroll just enough to bring it
  back into view (equivalent to `scrollIntoView` with nearest-edge behavior), otherwise leave
  the scroll position alone.

### 5. Future slots (not built, kept in mind)

No placeholder markup for any of these — an empty div reserved for something unspecified is its
own kind of clutter. What's actually committed to now, and why it's enough:

- **Overlays** (batdetect2 call boxes, time-marker annotations): the `position: relative`
  spectrogram wrapper (§3) is the one thing that needs deciding now rather than retrofitted.
- **Tool-switching UI, processing/filter controls**: will need a toolbar strip above the graphs;
  not built since there's nothing to switch/control yet. No CSS space reserved — a toolbar can be
  inserted into the existing header/graph flow later without fighting today's layout.
- **Sound-level-spectrum panel**: a separate chart, likely below or beside the main pair; today's
  layout doesn't preclude adding a panel there.
- **HET playback controls**: per the backlog's own plantuml sketch, a row near the audio player.
- **Continuous auto-scroll-follow** (the view stays centered/pinned on the playback cursor
  throughout playback, rather than only snapping when it goes off-screen): explicitly considered
  and deferred, not rejected — for this data (long, mostly-quiet recordings with sparse calls),
  snap-only may well be the better default long-term too, not just the simpler starting point.
  If wanted later, add it as an explicit user-facing toggle rather than replacing snap-only
  outright.

## Decisions

- Scale is a tunable constant (§1), explicitly provisional — refine after real recordings are on
  screen, not treated as a final spec-locked number.
- No caching (Non-goals) — flexibility for future per-render processing outweighs render latency
  for what's expected to be an occasional, deliberate one-recording-at-a-time visit; a loading
  state makes the latency acceptable rather than confusing.
- `_resolve_recording`/`_resolve_wav_path` move from `jobs/tasks.py` (private) to
  `services/media.py` (public) — a second legitimate consumer is what promotes a helper to shared,
  same convention already used elsewhere in this project.
- Standalone page, not an expanded drawer state — the drawer's small, drag-resized panel isn't
  built for wide pannable locked-scale images; full screen real estate is.

## Open items

- Exact reuse mechanism for the header info (Jinja macro/include vs. accepted duplication between
  `_recording_panel.html` and `recording_details.html`) — implementation-time call, not a design
  decision.
- Whether the page route itself 404s up front for an unknown hash, or lets each `<img>`'s own
  detail-render route 404 independently (simpler page route, but then the page renders around a
  recording that turns out not to exist) — small enough to resolve during implementation.
- Real-world render latency is unverified — the ≈567px-tall, duration-scaled-width renders have
  not been timed against actual field recordings. If this turns out to be slow enough that the
  loading state feels bad rather than merely present, switching to the existing cache mechanism
  (Non-goals) is the documented escape hatch, not a redesign.

## Addendum (2026-09-01): tiling — WebP's pixel limit was never checked against real durations

Found by the final whole-branch review after Tasks 1–6 shipped, not during design: at
`DETAIL_PX_PER_MS = 19.0`, any recording longer than ≈0.86s produces a spectrogram wider than
WebP's hard 16383px encode limit. Verified against real field recordings
(`~/Bat Sessions/Session_20260826_173533`): 67 of 68 files would 500 from the detail-image routes,
and — worse — the page's "Rendering…" placeholder never clears, since the `<img>`'s `load` event
never fires on a failed request, so there is no visible error state at all. Neither task's tests
caught this because both used a synthetic 0.02s test recording, three orders of magnitude under
the boundary.

**Decision: tile the render**, not cap duration or switch format. Cap-duration or cap-width both
compromise the feature's actual point (seeing an entire recording at a true locked scale);
PNG raises the pixel ceiling but trades one hard failure (WebP's limit) for another (a 30s
recording is ~482MB of uncompressed RGB per request, plus browsers have their own decode limits).
Tiling is the only option that keeps the locked-scale invariant intact for recordings of any
length, and fixes the *pixel-limit* half of the problem, which is the actual blocker.

**Correction (final whole-branch review, 2026-09-01):** the paragraph above originally also
claimed tiling fixes the *memory* half of the problem — that a tile only ever decodes/processes
its own slice of the WAV. That is false for the shipped implementation. Peak normalization is
computed from the WHOLE file, a deliberate correctness choice (see Task 7's design), so every
tile request still runs the full STFT (spectrogram) or reads the full sample array (oscillogram)
over the ENTIRE file — only the final resize/encode step is narrowed to `time_range_s`. Measured
on the shipped code: a single 8000px tile for a 15s/256kHz recording takes ~0.48s CPU and peaks
at ~180MB RSS. A full page view of that recording issues 36 spectrogram + 36 oscillogram tile
requests — roughly 20–35s of total server CPU and ~1GB RSS with a browser's ~6 parallel
connections. A 30s recording roughly doubles both. This is a known, accepted tradeoff of
prioritizing whole-file-peak-normalization correctness over per-request cost, not a bug to fix in
this fix wave. Two real options exist for a future optimization (documented as follow-up, not
required now): compute the whole-file peak once and thread it into the renderer instead of
recomputing the full STFT per tile-request, or fall back to the feature's own documented escape
hatch — switch to the existing params-hash-based derived-media cache, which the original spec's
Non-goals section already names as the contained follow-up if render cost becomes a problem.

**Shape of the fix:**

- `render_spectrogram`/`render_oscillogram` (`media/`) gain an optional `time_range_s: tuple[float,
  float] | None = None` parameter. When set, only that slice of the decoded PCM is processed —
  the STFT (spectrogram) or peak-envelope bucketing (oscillogram) never sees samples outside the
  window. Defaults to `None` (whole file), so every existing caller (the cached drawer pipeline in
  `jobs/tasks.py`) is unaffected — this is additive, not a behavior change for existing callers,
  and the Global Constraint below is revised to reflect exactly that scope.
- A new pure `detail_tiles(total_width_px: int) -> list[DetailTile]` in `services/recording_detail.py`
  splits a recording's full locked-scale width into fixed-width chunks (a new
  `DETAIL_MAX_TILE_WIDTH_PX` constant, comfortably under WebP's 16383px limit — 8000px chosen: wide
  margin, still few enough tiles for a typical recording that the per-tile request count stays
  reasonable). `DetailParams` gains a `tiles: list[DetailTile]` field.
- The two detail-image routes become tile-indexed: `GET
  /recordings/<audio_hash>/detail-spectrogram/<int:tile_index>.webp` and the oscillogram
  equivalent — 404 for an out-of-range tile index, same as every other 404 case this page's routes
  already handle.
- The template renders one `<img>` per tile, laid out edge-to-edge (no gaps) in a flex row inside
  the existing scrollable container, instead of one single (previously oversized) image.
- The client JS's click/crosshair/playback-cursor math switches from measuring against a single
  `<img>`'s `getBoundingClientRect()` to measuring against the tiled row's — the tiles have no
  gaps between them, so this is "measure the container instead of one image," not "make every
  computation tile-aware."

**Global Constraint revision:** "No new derived-media artifact type... this reuses the existing
pure `render_spectrogram`/`render_oscillogram` unmodified" (original Non-goals) is revised to: the
renderers gain one new *optional*, backward-compatible parameter (`time_range_s`, defaulting to
`None`) rather than staying literally unmodified — every other Non-goal (no cache, no
Procrastinate job, no `paths.py` entry) still holds exactly as written.

Two smaller bugs surfaced by the same review are folded into this addendum's implementation since
they land in the same files: `recording_details.html` never loaded `alpine.min.js` despite
including `_nav.html` (an Alpine component) — the theme toggle and sidebar were inert on this page
— and a source WAV missing from disk (the window between a file being deleted/moved and the next
`sweep_missing` run) 500ed instead of 404ing as this spec's §2 step 2 already requires.

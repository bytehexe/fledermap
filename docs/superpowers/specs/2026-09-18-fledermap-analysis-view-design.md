# Fledermap Recording Details Page — Analysis View (Sound-Level Spectrum) — Design

**Status:** draft — sections approved individually in chat during brainstorming (including a
visual-companion mockup pass on toolbar/button placement); awaiting the user's review of this
written spec (see brainstorming skill's user-review gate) before writing an implementation plan.
**Date:** 2026-09-18

## Problem

The recording details page (2026-09-01 spec, extended by the 2026-09-04 tool-switching spec) shows
exactly one pair of renderings — oscillogram + spectrogram — with no way to see a call's spectral
content as a proper power spectrum (amplitude/dB vs. frequency), which the Obsidian backlog's
"Sound level spectrum + call distance graph" item and the DAW-style-call-view research (Skiba's
*Ultraschallrufe* figure) both call out as a genuinely different, complementary representation:
oscillogram/spectrogram show *time-frequency* structure, a sound-level spectrum shows *frequency
content* precisely, integrated over some span of the recording.

This spec covers the sound-level spectrum, the new view it lives in, and the plumbing that lets a
user select which part of the recording it's computed over. It also **reserves layout and naming**
for a second, future graph — call-distance — without building it: that graph needs batdetect2 call
annotations (start/end time + frequency per call) that don't exist in the schema yet, and building
it now would mean guessing at data this project doesn't have.

## Goals

- A second, mutually-exclusive **view** on the recording details page, alongside today's
  oscillogram+spectrogram view (newly named **Playback** — see §1, not a rename of any existing
  label): **Analysis**, showing the sound-level spectrum and a reserved call-distance graph side by
  side. Both views stay under the page's existing, unchanged `<h2>Audio</h2>` heading.
- A single toggle button, fixed at the right end of the toolbar, same position and size in both
  states, whose icon+label always name the *other* view (an icon + "Analysis view" while in
  Playback, a different icon + "Playback" while in Analysis) — an unambiguous, always-in-the-same-
  place way back. Icons only, per `docs/style-guide.md`'s "no emoji or Unicode dingbats in the
  UI" rule — inline Tabler SVGs via the existing `icon()` Jinja global, the same mechanism
  `view-lock-toggle`/`denoise-toggle` already use (`recording_details.html`).
- A three-zone toolbar that generalizes to hold Analysis's own future tools without restructuring:
  **left** (view-specific tools — Default/Ruler/Select/View Lock in Playback, empty/reserved in
  Analysis), **middle** (shared controls — Denoise, identical behavior in both views), **right**
  (the view toggle).
- A new **Select** tool (third entry alongside Default/Ruler) that marks a time range the sound-
  level spectrum can be computed over, and that persists across a Playback↔Analysis switch (unlike
  Ruler's existing transient, tool-switch-cleared measurement).
- The sound-level spectrum itself: Welch's-method PSD in dB, normalized relative to the selected
  span's own peak (matching this project's existing self-relative dB convention in
  `media/spectrogram.py`), with a marked peak frequency; respects the page's Denoise toggle;
  computed on-demand (no disk-cached derived-media artifact, unlike the tiled spectrogram/
  oscillogram) and rendered client-side via Chart.js from a JSON endpoint.
- An explicit **Whole file / Selection** control inside Analysis view, independent of the actual
  Select-tool state — defaults to Selection when one exists, else Whole file — so a user can peek
  at either without losing their selection.

## Non-goals

- **The call-distance graph itself.** Layout and a grayed placeholder only ("reserved — needs
  batdetect2 call annotations, not yet available"). No data, no computation, no annotation schema
  work here.
- **Multi-peak / harmonic detection** in the sound-level spectrum. V1 marks only the single global
  peak frequency; identifying secondary local peaks is a real idea (raised during brainstorming)
  but explicitly deferred — revisit once the single-peak version is in use.
- **Computing the spectrum over batdetect2-identified calls specifically.** Raised and explicitly
  rejected for now: not enough annotation data is stored today to do this meaningfully; revisit
  once the annotation feature (backlog: "Annotations") exists.
- **Playing back audio limited to a Select-tool selection.** Raised as a promising idea during
  brainstorming (selection could auto-constrain playback the way View Lock does), and the
  View-Lock-override behavior below is *part* of that idea, but actually playing only the selected
  span is not part of this spec — the override only concerns which window View Lock would otherwise
  constrain playback to.
- **Touch/keyboard support**, **cross-page/reload persistence of view mode or selection** — same
  scope boundary the 2026-09-04 tools spec drew for Default/Ruler: in-memory JS state only, mouse
  only.

## Future directions (not designed, not committed)

Noted here only so a future spec doesn't have to rediscover the idea — nothing below constrains
this spec's implementation, and none of it should be built now:

Once the annotation feature exists, per-call batdetect2 data could add further representations
*within the sound-level spectrum graph itself*, not just as the separate call-distance graph —
e.g. overlaying individual per-call spectra (all calls in view or scoped to the current selection),
optionally broken out by species, and/or switchable between an aggregate view and a per-call view.
Whether that's a toggle, an overlay, small multiples, or something else entirely is genuinely
unspecified — this is a reminder that the shape may grow, not a requirement to keep §5/§6's
endpoint or chart-mounting code generic against it today.

## Design

### 1. Naming the existing view: "Playback"

There is no rename here — nothing in `recording_details.html` currently labels the
oscillogram+spectrogram pair "Overview" or anything else; the page's existing `<h2>Audio</h2>`
heading (unchanged by this spec) wraps both views. "Overview" was only this spec's own shorthand
during brainstorming for "today's oscillogram+spectrogram thing," and was rejected as the actual
name for exactly that reason it could be confused for one: this project already uses "overview" to
mean the map drawer's lower-fidelity, stretched spectrogram/oscillogram, explicitly contrasted with
the details page's higher-fidelity, locked-scale rendering (`docs/superpowers/specs/2026-09-01-*`
and the DAW-style-call-view research). Introducing "Overview" as a real, on-screen label here would
collide with that established meaning. **Playback** is the new name introduced by this spec for
what was previously unnamed UI — chosen because it names what a user *does* there: click-to-play,
drag-to-pan, Ruler, Select, View Lock, HET/TE controls all live only in this view — which also
happens to be the exact boundary the toolbar's left zone needs (see §3).

### 2. View toggle

`recording_details.html` gains an `activeView` concept (`"playback"` | `"analysis"`), mirrored by a
single button at the toolbar's right edge:

```html
<button type="button" id="view-toggle-button" data-target-view="analysis" title="Switch to Analysis view">
  <span class="view-toggle-icon-to-analysis">{{ icon("chart-histogram") }}</span>
  <span class="view-toggle-icon-to-playback" hidden>{{ icon("player-play") }}</span>
  <span class="view-toggle-label">Analysis view</span>
</button>
```

Icons follow the exact pattern `view-lock-toggle` already uses (two icon `<span>`s, one `hidden`,
toggled by JS) — `chart-histogram`/`player-play` are illustrative choices, to be confirmed against
Tabler's actual catalog and added to `services/vendor_assets.py`'s `ASSETS` when implemented,
same as `noise-reduction` was added for Denoise. Unlike View Lock's static "Lock view" label, this
button's label text itself must also change (it names the *target* view, not a fixed action), so
`recording_detail.js` additionally rewrites `.view-toggle-label`'s `textContent` on toggle — a
minor difference from the icon-only swap View Lock does.

`recording_detail.js` tracks `activeView`, and on toggle:
- flips `data-target-view`, the visible icon span, and the label text to name the *other* view,
- toggles a class on the page's main content wrapper (`view-playback` / `view-analysis`) that CSS
  uses to show/hide the two content blocks (`.detail-scroll` for Playback; a new
  `.analysis-panel` for Analysis) and the two toolbar-zone variants (§3),
- does **not** touch Select-tool selection state, View Lock state, or Denoise state — all three are
  designed to survive the switch (§4, §5).

### 3. Toolbar, three zones

`#detail-toolbar` (today: a flat row of Default/Ruler buttons, per the 2026-09-04 spec) becomes
three flex/grid zones:

```html
<div class="detail-toolbar" id="detail-toolbar">
  <div class="toolbar-zone toolbar-zone-tools">
    <!-- Playback: Default, Ruler, Select, View Lock buttons (existing + new, below) -->
    <!-- Analysis: empty today; reserved for future analysis-specific tools -->
  </div>
  <div class="toolbar-zone toolbar-zone-shared">
    <!-- Denoise button, unchanged, present in both views -->
  </div>
  <div class="toolbar-zone toolbar-zone-view">
    <button id="view-toggle-button" ...>...</button>
  </div>
</div>
```

The left zone's contents are swapped by `activeView` (via the CSS class from §2, e.g.
`.view-analysis .toolbar-zone-tools .playback-only { display: none; }`) rather than individually
disabling each button — this is a full content-area swap (the graphs beneath change completely at
the same time), not the "a button silently vanishes while everything else looks the same" pattern
`docs/style-guide.md` warns against; the visual context makes the swap self-evident. The middle
(Denoise) and right (view toggle) zones are unaffected by `activeView`.

**Zone widths are fixed, equal thirds** — `#detail-toolbar { display: grid; grid-template-columns:
1fr 1fr 1fr; }`, not `1fr auto 1fr`. This is deliberate: an `auto` middle column would size to its
content, but nothing here should ever resize the toolbar based on what's currently inside a zone —
the left zone emptying out in Analysis view, the view-toggle button's icon+label changing length
("Analysis view" vs. "Playback", each with its own icon), or a future Analysis-specific tool
appearing in the left zone
must never shift where the other zones' contents sit. Equal fixed thirds guarantee that regardless
of content changes in any zone.

### 4. Select tool

Registered in `recording_detail.js`'s existing `tools` strategy-object registry (2026-09-04 spec
§2), alongside `default`/`ruler`:

- **Drag**: horizontal-only — spans the full height of `.detail-spectrogram-wrap`, following only
  the horizontal component of the drag (unlike Ruler's genuine 2D rectangle). Shows a live duration
  readout (`Δt: X.XX ms`) using the same conversion helpers Ruler already uses.
- **On drag end**: the resulting `{startTimeS, endTimeS}` is written to a new, durable piece of
  state — `currentSelection` — distinct from Ruler's private, tool-switch-cleared `rulerBox`/
  `rulerBoxLocal` locals. `currentSelection` is a plain object on the page's shared JS scope (not
  nulled by `setActiveTool`), so it survives a Playback↔Analysis toggle.
- **Plain click** (no drag) while Select is active: clears `currentSelection` (mirrors Ruler's
  clear-on-click) and removes the overlay.
- **Switching to a different toolbar tool** (Default or Ruler) also clears `currentSelection` —
  same clear semantics as a plain click, just triggered by `setActiveTool` instead. Switching
  *views* does **not** clear it (that's the entire point of making it durable).
- **View Lock interaction**: while `currentSelection` is set, it overrides View Lock for playback
  purposes (Select's range wins over whatever View Lock's own locked window was). The View Lock
  button is rendered `disabled` (not hidden — this is a single button changing state within an
  otherwise-unchanged view, exactly the case `docs/style-guide.md`'s "disable, don't hide" rule is
  for) while a selection is active. Clearing the selection (click, or switching to Default/Ruler)
  re-enables the View Lock button and reactivates View Lock's own previously-set window, if any —
  View Lock's own state is never cleared by a selection, only suppressed.

### 5. Sound-level spectrum: computation and rendering

**New pure renderer**, `src/fledermap/media/sound_level_spectrum.py`, following the existing
`media/` convention (pure, no I/O, takes samples in) — analogous in shape to `spectrogram.py`'s
`render_full_spectrogram_image` but returns data, not an image:

```python
@dataclass(frozen=True)
class SoundLevelSpectrum:
    freqs_hz: np.ndarray
    db: np.ndarray          # relative to this spectrum's own peak, 0 dB at the peak
    peak_freq_hz: float

def compute_sound_level_spectrum(
    samples: np.ndarray, samplerate_hz: float, dynamic_range_db: float = 80.0
) -> SoundLevelSpectrum: ...
```

- Uses `scipy.signal.welch` (segmented, averaged periodogram) over whatever `samples` slice is
  passed in — the caller (the route, §6) is responsible for slicing to the selected span or passing
  the whole file.
- dB conversion follows `spectrogram.py`'s exact convention: `10 * log10(psd / psd.max())`, clipped
  to `-dynamic_range_db`, same `dynamic_range_db` default (80.0) as `SpectrogramParams` for visual
  consistency between the two renderings.
- `peak_freq_hz` is simply `freqs_hz[np.argmax(psd)]` — the single global peak, per the "no
  multi-peak detection yet" non-goal.

**Client-side rendering**: a new pure reshaping module, `src/fledermap/web/static/
analysis_chart.js`, following the `statistics_charts.js` split precedent (CLAUDE.md's JavaScript
tooling section: pure JSON→Chart.js-dataset shaping, no DOM access, unit-testable via `node:test`,
loaded via its own `<script>` tag before the file that mounts the canvas). It turns the endpoint's
JSON into a Chart.js line-chart dataset plus an annotation/marker for the peak frequency.
`recording_detail.js` gains the DOM-touching half: on entering Analysis view (or on Whole
file/Selection toggle change while already in Analysis view), `fetch`es the endpoint and mounts/
updates the chart.

### 6. Sound-level spectrum endpoint

New route in `src/fledermap/web/views/media.py`, alongside `detail_spectrogram`/
`detail_oscillogram` (same file, since it needs the same shared `_denoised_samples_cache` those two
already use — avoiding a third independent denoise pass per the "third derived-media artifact type"
lesson in this project's `CLAUDE.md`, even though this artifact isn't cached itself):

```
GET /recordings/<audio_hash>/detail/sound-level-spectrum?start_s=<float>&end_s=<float>&denoise=<bool>
```

- `start_s`/`end_s` omitted → whole file. Present → slice `samples` to that span before calling
  `compute_sound_level_spectrum`. This is what makes the Whole file/Selection control (§7)
  independent of the Select tool's own persisted `currentSelection`: the client decides which span
  to request, the server just computes over whatever span it's given.
- `denoise` follows the exact same query-param convention `detail_spectrogram`/`detail_oscillogram`
  already use, and reuses `_denoised_samples_cache.get_or_compute(...)` with the same cache key
  shape (`wav_path`, `denoise`, `cutoff_hz`) — no new caching mechanism.
- Returns JSON: `{"freqs_hz": [...], "db": [...], "peak_freq_hz": ...}`. No image, no disk cache —
  computed fresh per request, consistent with this being driven by an open-ended, user-chosen span
  rather than a fixed set of render params (unlike the tiled spectrogram/oscillogram, this
  deliberately does **not** go through `media/paths.py`/a Procrastinate task/an enqueue check — see
  "Non-goals" implicitly: this is not a fourth "third derived-media artifact type touches five
  places" instance, precisely because it isn't cached media).

### 7. Analysis view layout and the Whole file / Selection control

```html
<div class="analysis-panel">
  <div class="analysis-graph">
    <div class="analysis-graph-controls">
      <button data-span="whole-file">Whole file</button>
      <button data-span="selection" disabled>Selection</button>  <!-- enabled iff currentSelection is set -->
    </div>
    <canvas id="sound-level-spectrum-chart"></canvas>
  </div>
  <div class="analysis-graph analysis-graph-reserved">
    <p>Call-distance graph — reserved. Needs batdetect2 call annotations (not yet available).</p>
  </div>
</div>
```

- The Whole file/Selection buttons default: Selection is pre-selected if `currentSelection` is set
  at the moment Analysis view is entered, else Whole file. The "Selection" button is disabled
  (grayed, not hidden — same style-guide rule as View Lock in §4) whenever `currentSelection` is
  unset, so it's visibly unavailable rather than absent.
- Choosing between them re-fetches from the endpoint (§6) with or without `start_s`/`end_s`; it does
  **not** touch `currentSelection` itself — switching back to Playback still shows whatever
  selection was there before, regardless of which span Analysis was displaying.
- The reserved call-distance graph slot has no interactivity and no data; it exists purely to lock
  in the two-graphs-side-by-side layout now so a future spec doesn't need to redesign this view's
  structure, only fill in the second half.

## Files touched

- `src/fledermap/web/templates/recording_details.html` — three-zone toolbar markup; Select/View
  Lock buttons added to the Playback-only tools zone; the view-toggle button; the
  `.analysis-panel` markup (spectrum chart + controls, reserved call-distance placeholder).
- `src/fledermap/web/static/recording_detail.js` — `activeView` state and toggle handling;
  `currentSelection` state (distinct from Ruler's private locals); the `select` tool object
  (`onClick`/`onDrag`/`onDragEnd`); View Lock enable/disable tied to `currentSelection`; Whole
  file/Selection control wiring; fetch + chart-mount calls into `analysis_chart.js`.
- `src/fledermap/web/static/analysis_chart.js` (new) — pure JSON→Chart.js-dataset reshaping for the
  sound-level spectrum, `node:test`-covered, following the `statistics_charts.js` split pattern.
- `src/fledermap/web/static/app.css` — `.toolbar-zone*` layout, `.analysis-panel`/`.analysis-graph*`
  styling, `.playback-only`/`.analysis-only` visibility classes driven by `view-playback`/
  `view-analysis` on the page wrapper, disabled-button styling reused from existing conventions.
- `src/fledermap/media/sound_level_spectrum.py` (new) — `compute_sound_level_spectrum`, pure,
  Welch's-method PSD + self-relative dB + peak frequency.
- `src/fledermap/web/views/media.py` — new `detail_sound_level_spectrum` route, reusing
  `_denoised_samples_cache`.

## Testing

- `tests/test_sound_level_spectrum.py` (new) — pure unit tests for
  `compute_sound_level_spectrum`: a synthetic single-tone signal's peak lands at the expected
  frequency bin; the returned `db` array's maximum is exactly `0.0` (self-relative); values are
  clipped at `-dynamic_range_db`; a shorter `samples` slice (simulating a Select-tool span) still
  returns sane output rather than erroring on Welch's default segment length.
- `tests/test_media_views.py` (or wherever `detail_spectrogram`/`detail_oscillogram` are already
  tested) gains a case for the new route: whole-file (no `start_s`/`end_s`) vs. a span, `denoise`
  on/off, and that it reuses `_denoised_samples_cache` rather than re-decoding (same style as
  existing cache-reuse assertions for the other two routes, if any exist — check before assuming).
- `tests/js/analysis_chart.test.js` (new) — `node --test`-covered reshaping logic, per this
  project's JS-testing convention (CLAUDE.md's "JavaScript tooling" section): the pure function
  only, no DOM.
- Interactive behavior (Select tool drag/clear, View Lock disable/re-enable, view toggle, Whole
  file/Selection switching) verified live against the real running instance with headless Chrome,
  same as every other JS-touching change per CLAUDE.md's "headless-Chrome live-verification
  technique" — **mandatory**, not optional, per the manual-classification Task 5 lesson this
  project's own `CLAUDE.md` already records.

## Open questions

None outstanding — every decision flagged during brainstorming (view names, toolbar zone
placement/grouping, Select-tool mechanics and its View Lock interaction, on-demand vs. cached
rendering, Welch's method vs. single FFT, the independent Whole file/Selection control) was
resolved in chat, including a visual-companion pass confirming the toggle-button placement and
toolbar zone layout.

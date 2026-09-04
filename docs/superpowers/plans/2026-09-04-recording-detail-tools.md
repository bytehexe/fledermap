# Recording Details Toolbar: Default Pan + Ruler Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tool-switching toolbar to the recording details page with two tools — Default
(click-to-play, now-working pan-by-drag, existing crosshair) and Ruler (drag to measure Δt/Δf,
click to clear) — fixing the "drag shows a cursor but doesn't drag" bug in the process.

**Architecture:** A shared mousedown/mousemove/mouseup state machine on the spectrogram wrap
distinguishes a click from a drag (4px movement threshold) and dispatches to whichever tool is
active. Each tool is a plain object with `onClick`/`onDrag`/`onDragEnd` methods. All state is
in-memory JS, no server round-trip, no persistence.

**Tech Stack:** Flask + Jinja templates, vanilla JS (no framework — matches this page's existing
`recording_detail.js`), plain CSS.

**Spec:** `docs/superpowers/specs/2026-09-04-fledermap-recording-detail-tools-design.md`

## Global Constraints

- No JS test infrastructure exists in this project and none is introduced by this plan (verified
  during brainstorming, consistent across every other interactive script here). JS behavior is
  verified live against the real running instance, not by an automated JS test.
- No persistence of active tool or measurements across reload — in-memory only.
- No keyboard shortcuts, no touch/pointer-event support — mouse only, matching the rest of this
  page.
- `hatch fmt`, `hatch run types:check`, and `hatch test` (both `-m "not db"` and `-m db`) must
  stay clean after every task — this repo's pre-commit hook already enforces the fast subset;
  the full `db` suite is a manual check before considering the plan done (CLAUDE.md's "Tooling"
  section).

---

## File Structure

- **Modify `src/fledermap/web/templates/recording_details.html`** — adds the toolbar's two
  buttons and `draggable="false"` on both tile `<img>` loops. No new files: this page is a
  single template, matching its existing structure.
- **Modify `src/fledermap/web/static/recording_detail.js`** — replaces the existing bare
  `click`/no-pan behavior with the tool state machine, `defaultTool`, and `rulerTool`. Stays one
  file, matching this page's existing "one script per page" convention (see its own top-of-file
  comment).
- **Modify `src/fledermap/web/static/app.css`** — toolbar layout/button-active styling, the
  ruler overlay box, and tool-dependent cursor rules (replacing the current unconditional
  `cursor: crosshair` on `.detail-spectrogram-wrap`).
- **Modify `tests/test_recording_detail_view.py`** — route-level assertions for what Task 1
  changes render (toolbar markup, `draggable="false"`).

---

## Task 1: Toolbar markup + non-draggable tiles

**Files:**
- Modify: `src/fledermap/web/templates/recording_details.html`
- Test: `tests/test_recording_detail_view.py`

**Interfaces:**
- Produces: a `<div class="detail-toolbar" id="detail-toolbar">` containing two
  `<button type="button" class="tool-button" data-tool="default">` /
  `data-tool="ruler"` elements (the `default` one starts `aria-pressed="true"`, `ruler` starts
  `aria-pressed="false"`) — Task 2 queries these by `#detail-toolbar .tool-button` and reads
  `.dataset.tool`. Every `.detail-spectrogram-tile` and `.detail-oscillogram-tile` `<img>` now
  carries `draggable="false"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_recording_detail_view.py` (add `import re` to the existing import block at
the top of the file, alongside the other stdlib imports):

```python
def test_recording_details_page_renders_the_tool_toolbar(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="fa" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'fa' * 32}")

    html = response.get_data(as_text=True)
    assert (
        '<button type="button" class="tool-button" data-tool="default" '
        'aria-pressed="true">Default</button>' in html
    )
    assert (
        '<button type="button" class="tool-button" data-tool="ruler" '
        'aria-pressed="false">Ruler</button>' in html
    )


def test_recording_details_page_tiles_are_not_natively_draggable(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """A plain <img> is draggable by default -- clicking and dragging it triggers the
    browser's native image drag-and-drop instead of the page's own pan-by-drag, which is
    exactly the reported bug ("shows a drag cursor but does not drag")."""
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="fb" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'fb' * 32}")

    html = response.get_data(as_text=True)
    tile_tags = [
        tag
        for tag in re.findall(r"<img[^>]*>", html)
        if "detail-spectrogram-tile" in tag or "detail-oscillogram-tile" in tag
    ]
    assert tile_tags, "expected at least one tile <img>"
    for tag in tile_tags:
        assert 'draggable="false"' in tag
```

- [ ] **Step 2: Run tests to verify they fail**

Run (needs Docker, see CLAUDE.md's "Environment gotchas" — `dangerouslyDisableSandbox: true` if
running through an agent sandbox):
```bash
hatch test tests/test_recording_detail_view.py -k "toolbar or not_natively_draggable" -v
```
Expected: both FAIL — `test_recording_details_page_renders_the_tool_toolbar` because no
`.tool-button` exists yet; `test_recording_details_page_tiles_are_not_natively_draggable` on the
`assert tile_tags` line failing is wrong (tiles already render) — it should fail on the
`draggable="false"` assertion instead. If it fails on `assert tile_tags`, the recording fixture
is wrong (check `duration_s`/`samplerate_hz` are set) — fix the test, not the page.

- [ ] **Step 3: Add the toolbar and `draggable="false"` to the template**

In `src/fledermap/web/templates/recording_details.html`, immediately after the `{% if params %}`
line and before `<div class="detail-scroll" id="detail-scroll">`, add:

```html
    <div class="detail-toolbar" id="detail-toolbar">
      <button type="button" class="tool-button" data-tool="default" aria-pressed="true">Default</button>
      <button type="button" class="tool-button" data-tool="ruler" aria-pressed="false">Ruler</button>
    </div>
```

Then add `draggable="false"` to both tile `<img>` tags (inside their respective `{% for tile in
params.tiles %}` loops):

```html
            <img
              class="detail-oscillogram-tile"
              src="{{ url_for('media.detail_oscillogram', audio_hash=recording.audio_hash, tile_index=tile.index) }}"
              alt="Waveform tile {{ tile.index }}"
              width="{{ tile.width_px }}"
              height="{{ params.oscillogram.height_px }}"
              draggable="false"
              hidden
            >
```

```html
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
              data-time-expansion-factor="{{ time_expansion_factor }}"
              draggable="false"
              hidden
            >
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
hatch test tests/test_recording_detail_view.py -v
```
Expected: all PASS, including the pre-existing tests in this file (regression check).

- [ ] **Step 5: Run the full quality gate**

```bash
hatch fmt
hatch run types:check
```
Expected: no changes needed from `hatch fmt` beyond what you already wrote cleanly, no mypy
errors (this task touches no Python logic, but the gate must stay green).

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/templates/recording_details.html tests/test_recording_detail_view.py
git commit -m "feat: add tool toolbar markup and disable native tile dragging

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Toolbar wiring + Default tool (click-to-play + working pan)

**Files:**
- Modify: `src/fledermap/web/static/recording_detail.js`
- Modify: `src/fledermap/web/static/app.css`

**Interfaces:**
- Consumes: `#detail-toolbar` and its `.tool-button` elements (Task 1); `wrap`
  (`#detail-spectrogram-wrap`), `scrollEl` (`#detail-scroll`), `audio`, `pxPerMs`,
  `timeExpansionFactor` — all already local `const`s in `recording_detail.js`'s
  `DOMContentLoaded` handler.
- Produces: a module-scoped-within-the-handler `activeTool` string (`"default"` | `"ruler"`), a
  `tools` object keyed by those two strings each exposing `onClick(event)`,
  `onDrag(event, dragStart)`, `onDragEnd()`, and a `setActiveTool(tool)` function that Task 3's
  ruler-clearing logic calls into. `wrap` gains CSS classes `tool-default` / `tool-ruler` (exactly
  one at a time) and `dragging` (only while a drag is in progress) — Task 3 relies on
  `tool-ruler` for its own CSS, this task defines the class-toggling machinery both use.

- [ ] **Step 1: Replace the existing click/pan-less logic with the tool state machine**

In `src/fledermap/web/static/recording_detail.js`, replace this existing block (currently lines
160–170):

```javascript
  // Click-to-play, crosshair, and the playback cursor all now measure against `wrap`'s own
  // bounding rect (the tiled row's container) rather than a single `<img>`'s -- the tiles sit
  // edge-to-edge with no gaps, so the container's rect spans exactly the full locked-scale
  // width, same as the single image did before tiling.
  wrap.addEventListener("click", (event) => {
    const rect = wrap.getBoundingClientRect();
    const xPx = event.clientX - rect.left;
    const spectrogramTimeS = xPx / pxPerMs / 1000;
    audio.currentTime = spectrogramTimeS * timeExpansionFactor;
    audio.play();
  });
```

with:

```javascript
  // Tool switching (design spec 2026-09-04-fledermap-recording-detail-tools-design.md):
  // `tools[activeTool]` implements onClick/onDrag/onDragEnd for whichever tool is selected.
  // Click-to-play, crosshair, and the playback cursor all still measure against `wrap`'s own
  // bounding rect (the tiled row's container) rather than a single `<img>`'s -- the tiles sit
  // edge-to-edge with no gaps, so the container's rect spans exactly the full locked-scale
  // width, same as the single image did before tiling.
  const toolbar = document.getElementById("detail-toolbar");
  const toolButtons = Array.from(toolbar.querySelectorAll(".tool-button"));
  let activeTool = "default";

  function setActiveTool(tool) {
    activeTool = tool;
    toolButtons.forEach((btn) => {
      const isActive = btn.dataset.tool === tool;
      btn.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
    wrap.classList.toggle("tool-default", tool === "default");
    wrap.classList.toggle("tool-ruler", tool === "ruler");
  }

  toolButtons.forEach((btn) => {
    btn.addEventListener("click", () => setActiveTool(btn.dataset.tool));
  });
  setActiveTool("default");

  const tools = {
    default: {
      onClick(event) {
        const rect = wrap.getBoundingClientRect();
        const xPx = event.clientX - rect.left;
        const spectrogramTimeS = xPx / pxPerMs / 1000;
        audio.currentTime = spectrogramTimeS * timeExpansionFactor;
        audio.play();
      },
      onDrag(event, dragStart) {
        scrollEl.scrollLeft = dragStart.scrollLeft - (event.clientX - dragStart.x);
      },
      onDragEnd() {},
    },
  };

  // Shared drag-vs-click state machine: mousedown starts tracking, then mousemove/mouseup are
  // bound on `document` (not `wrap`) for the duration of the gesture -- a plain `wrap`-scoped
  // listener would never see mouseup if the drag ends outside `wrap`'s bounds (a fast or wide
  // drag), leaving the gesture stuck "in progress". 4px threshold: below it, treat as a click
  // even if the mouse moved a hair between mousedown and mouseup.
  const DRAG_THRESHOLD_PX = 4;

  wrap.addEventListener("mousedown", (event) => {
    const dragStart = { x: event.clientX, y: event.clientY, scrollLeft: scrollEl.scrollLeft };
    let dragging = false;

    function onMove(moveEvent) {
      const movedPx = Math.hypot(moveEvent.clientX - dragStart.x, moveEvent.clientY - dragStart.y);
      if (!dragging && movedPx > DRAG_THRESHOLD_PX) {
        dragging = true;
        wrap.classList.add("dragging");
      }
      if (dragging) tools[activeTool].onDrag(moveEvent, dragStart);
    }

    function onUp(upEvent) {
      if (dragging) {
        tools[activeTool].onDragEnd();
      } else {
        tools[activeTool].onClick(upEvent);
      }
      wrap.classList.remove("dragging");
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
```

Leave the existing `wrap.addEventListener("mousemove", ...)` crosshair block and the
`wrap.addEventListener("mouseleave", ...)` block immediately below it completely untouched —
they're a separate, always-on listener per the design spec (§2).

- [ ] **Step 2: Add toolbar and cursor CSS**

In `src/fledermap/web/static/app.css`, find this existing rule:

```css
.detail-spectrogram-wrap { position: relative; cursor: crosshair; display: flex; }
```

Replace it with (removing the unconditional `cursor: crosshair` — cursor is now tool-dependent):

```css
.detail-spectrogram-wrap { position: relative; display: flex; }
.detail-spectrogram-wrap.tool-default { cursor: grab; }
.detail-spectrogram-wrap.tool-default.dragging { cursor: grabbing; }
.detail-spectrogram-wrap.tool-ruler { cursor: crosshair; }
```

Then add the toolbar's own layout/active-state styling, reusing the plain `button` rule already
defined earlier in this file (padding/border/hover) — this only adds the row layout and the
pressed-state highlight:

```css
.detail-toolbar { display: flex; gap: 0.4rem; margin: 0.5rem 0; }
.tool-button[aria-pressed="true"] {
  background: var(--color-accent);
  color: var(--color-bg);
  border-color: var(--color-accent);
}
```

- [ ] **Step 3: Manual verification (no JS test infra in this project — see Global Constraints)**

This step can't be automated; note in your task report exactly what you did and saw. From a
checkout with `hatch run fledermap serve` running (or the deployed instance, if you have one —
see CLAUDE.md's "Fledermap systemd install" memory note if working on the machine that has one),
open a recording's `/recordings/<hash>` page in a real browser and check:

1. Two buttons, "Default" and "Ruler", appear above the spectrogram; "Default" starts visually
   highlighted.
2. Clicking directly on the spectrogram (no drag) still seeks and plays the audio, exactly as
   before this task.
3. Click-and-drag horizontally on the spectrogram now pans the view (the image slides under the
   cursor, following it) instead of showing the browser's native image-drag ghost cursor and
   doing nothing.
4. The mouse cursor over the spectrogram is an open hand (`grab`) at rest and a closed hand
   (`grabbing`) while actively dragging.
5. The existing crosshair readout (time/freq following the mouse) still works, both while
   hovering and immediately after a drag ends.
6. Dragging the mouse out past the edge of the spectrogram (or the browser window) and releasing
   there does not leave the page in a stuck state — clicking normally afterward still plays from
   that point.

- [ ] **Step 4: Run the full quality gate**

```bash
hatch fmt
hatch run types:check
hatch test -m "not db"
```
Expected: all clean (this task touches no Python).

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/web/static/recording_detail.js src/fledermap/web/static/app.css
git commit -m "feat: wire up tool toolbar; Default tool now actually pans on drag

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Ruler tool

**Files:**
- Modify: `src/fledermap/web/static/recording_detail.js`
- Modify: `src/fledermap/web/static/app.css`

**Interfaces:**
- Consumes: `tools` object, `setActiveTool`, `wrap`, `pxPerMs`, `pxPerKhz` (Task 2's state
  machine and this file's existing top-level `const`s).
- Produces: `tools.ruler` (same `onClick`/`onDrag`/`onDragEnd` shape as `tools.default`), plus
  `clearRulerBox()` — called both by `tools.ruler.onClick` and by `setActiveTool` (so switching
  away from Ruler doesn't leave a stale measurement behind under a different tool's cursor).

- [ ] **Step 1: Add the ruler tool's box-drawing logic and wire it in**

In `src/fledermap/web/static/recording_detail.js`, add this immediately BEFORE the `const
toolbar = document.getElementById("detail-toolbar");` line Task 2 added — i.e., as the first
thing in the tool-switching section, before the toolbar/`setActiveTool` block, not after it.
This ordering matters: Task 2's `setActiveTool("default")` call runs immediately after
`setActiveTool` is defined (before `const tools` is even declared), and Task 3 below changes
`setActiveTool` to call `clearRulerBox()`. `function` declarations are hoisted, but the `let
rulerBox = null;` statement is not usable before it actually executes (temporal dead zone) — so
`rulerBox` must be declared before `setActiveTool("default")` runs, not after.

```javascript
  // Ruler tool: drag draws a live measurement box; a plain click (no drag) clears it. `rulerBox`
  // is the one live DOM node for the current measurement -- created lazily on first drag, reused
  // (repositioned) on subsequent drags, removed entirely on clear.
  let rulerBox = null;

  function clearRulerBox() {
    if (rulerBox) {
      rulerBox.remove();
      rulerBox = null;
    }
  }

  function updateRulerBox(dragStart, event) {
    const rect = wrap.getBoundingClientRect();
    const startX = dragStart.x - rect.left;
    const startY = dragStart.y - rect.top;
    const curX = event.clientX - rect.left;
    const curY = event.clientY - rect.top;

    const left = Math.min(startX, curX);
    const top = Math.min(startY, curY);
    const width = Math.abs(curX - startX);
    const height = Math.abs(curY - startY);

    const deltaTMs = Math.abs(curX - startX) / pxPerMs;
    const deltaFKhz = Math.abs(curY - startY) / pxPerKhz;
    // Pulse-repetition-rate reading (bat-call literature convention: inter-pulse interval -> Hz,
    // the same relationship as note-duration -> BPM). A purely vertical drag has deltaTMs === 0
    // -- guard the division rather than showing Infinity.
    const hzText = deltaTMs > 0 ? `${(1000 / deltaTMs).toFixed(1)} Hz` : "—";

    if (!rulerBox) {
      rulerBox = document.createElement("div");
      rulerBox.className = "ruler-box";
      const label = document.createElement("span");
      label.className = "ruler-label";
      rulerBox.appendChild(label);
      wrap.appendChild(rulerBox);
    }
    rulerBox.style.left = `${left}px`;
    rulerBox.style.top = `${top}px`;
    rulerBox.style.width = `${width}px`;
    rulerBox.style.height = `${height}px`;
    rulerBox.querySelector(".ruler-label").textContent =
      `Δt: ${deltaTMs.toFixed(2)} ms (${hzText})\nΔf: ${deltaFKhz.toFixed(1)} kHz`;
  }
```

Then add `ruler` to the `tools` object from Task 2, so it reads:

```javascript
  const tools = {
    default: {
      onClick(event) {
        const rect = wrap.getBoundingClientRect();
        const xPx = event.clientX - rect.left;
        const spectrogramTimeS = xPx / pxPerMs / 1000;
        audio.currentTime = spectrogramTimeS * timeExpansionFactor;
        audio.play();
      },
      onDrag(event, dragStart) {
        scrollEl.scrollLeft = dragStart.scrollLeft - (event.clientX - dragStart.x);
      },
      onDragEnd() {},
    },
    ruler: {
      onClick() {
        clearRulerBox();
      },
      onDrag(event, dragStart) {
        updateRulerBox(dragStart, event);
      },
      onDragEnd() {},
    },
  };
```

Finally, make `setActiveTool` (Task 2) clear a stale measurement on every tool switch — change:

```javascript
  function setActiveTool(tool) {
    activeTool = tool;
    toolButtons.forEach((btn) => {
      const isActive = btn.dataset.tool === tool;
      btn.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
    wrap.classList.toggle("tool-default", tool === "default");
    wrap.classList.toggle("tool-ruler", tool === "ruler");
  }
```

to:

```javascript
  function setActiveTool(tool) {
    activeTool = tool;
    toolButtons.forEach((btn) => {
      const isActive = btn.dataset.tool === tool;
      btn.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
    wrap.classList.toggle("tool-default", tool === "default");
    wrap.classList.toggle("tool-ruler", tool === "ruler");
    clearRulerBox();
  }
```

(This is exactly why the ruler block goes before `const toolbar = ...` per the placement note at
the top of this step — `rulerBox` must already be declared by the time `setActiveTool("default")`
runs during Task 2's own setup, a few lines below.)

- [ ] **Step 2: Add ruler-box CSS**

In `src/fledermap/web/static/app.css`, add near the `.playback-cursor` rules:

```css
.ruler-box {
  position: absolute;
  border: 1px dashed var(--color-accent);
  background: color-mix(in srgb, var(--color-accent) 12%, transparent);
  pointer-events: none;
}
.ruler-label {
  position: absolute;
  left: 0;
  top: -2.6em;
  white-space: pre;
  font-size: 0.7rem;
  line-height: 1.3;
  padding: 0.15rem 0.4rem;
  color: var(--color-text);
  background: color-mix(in srgb, var(--color-bg) 85%, transparent);
  border: 1px solid var(--color-border);
  border-radius: 4px;
}
```

(`white-space: pre` is required for the label's `\n` between the Δt and Δf lines to actually
break the line — `textContent` doesn't interpret `\n` as HTML, so without this the two readings
would render on one line.)

- [ ] **Step 3: Manual verification**

Same caveat as Task 2 Step 3 — no JS test infra, report exactly what you checked. With the page
open in a browser:

1. Click "Ruler" — it becomes highlighted, "Default" is not.
2. Click-and-drag diagonally across the spectrogram: a dashed box appears spanning from the drag
   start to the live cursor position, with a two-line label showing `Δt: X.XX ms (Y.Y Hz)` and
   `Δf: X.X kHz`, both updating live as you drag.
3. Release the mouse: the box stays exactly where it was, visible.
4. A plain click (no drag) on the spectrogram while Ruler is active clears the box.
5. Start a new drag without clicking first: the old box is replaced by the new one (not both
   shown at once).
6. Drag purely vertically (same x start and end): the label shows `Δt: 0.00 ms (—)` rather than
   `Infinity Hz` or a JS error (check the browser console for errors here specifically).
7. Switch to "Default" while a measurement is showing: the box disappears. Switch back to
   "Ruler": still gone (not restored).
8. With Ruler active, click-to-play does NOT happen (only Default plays audio) — confirms the
   tools are properly isolated.

- [ ] **Step 4: Run the full quality gate**

```bash
hatch fmt
hatch run types:check
hatch test -m "not db"
```

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/web/static/recording_detail.js src/fledermap/web/static/app.css
git commit -m "feat: add Ruler tool (drag to measure Δt/Δf, click to clear)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Full regression, live smoke test, and backlog update

**Files:** none (verification + documentation only)

**Interfaces:** none — this task consumes everything built in Tasks 1–3 and produces no new code.

- [ ] **Step 1: Full automated suite**

```bash
hatch fmt
hatch run types:check
hatch test -m "not db"
hatch test -m db   # needs dangerouslyDisableSandbox: true if run through an agent sandbox
```
Expected: everything clean, per this repo's CLAUDE.md ("Tooling": pre-commit only runs the
non-`db` subset; the full suite including `db` is a manual check before merging).

- [ ] **Step 2: Live end-to-end smoke test**

If working on the machine with the persistent systemd install (see this repo's own memory note
on that, if using an agent with access to it): reinstall the package from this checkout (`pipx
install --force .`) and restart the service (`systemctl --user restart fledermap.target`) so the
real running instance reflects this branch, the same way the back-link fix in this project was
verified. Otherwise run `hatch run fledermap serve` locally. Then, in a real browser, repeat
Task 2 Step 3 and Task 3 Step 3's checks back-to-back on the same page load (switch Default →
Ruler → Default at least once) to confirm the two tools don't interfere with each other's state.

- [ ] **Step 3: Update the Obsidian backlog**

In `~/Obsidian/Default/Fledermap.md`, find the two open items:
```
- [ ] Details view: Dragging shows a drag cursor but does not drag (default tool)
- [ ] Implement more tools for the details page: Ruler, ... `[!!image:mockup]`
```
Mark both `[x]`, with a short dated note (today's date) summarizing what shipped and pointing at
this plan's spec (`docs/superpowers/specs/2026-09-04-fledermap-recording-detail-tools-design.md`)
for the full writeup — follow this project's existing convention for backlog writeups (see other
`[x]` entries in the same file for the level of detail expected, e.g. the "Back to map" entry).

If Task 1–3's live verification (Step 2 above) surfaced anything that works but isn't quite
right (a rough edge, not a blocker), log it as a new backlog item rather than silently accepting
it or fixing it unplanned — same rule as everywhere else in this project.

- [ ] **Step 4: Final commit (if the backlog edit is the only remaining change)**

The backlog file lives outside this git repo (`~/Obsidian/Default/Fledermap.md`), so there is
nothing further to `git commit` here once Steps 1–3 pass — this task's "commit" is the backlog
edit itself, already made in Step 3.

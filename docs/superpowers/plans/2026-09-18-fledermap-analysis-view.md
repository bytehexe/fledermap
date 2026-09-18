# Fledermap Analysis View (Sound-Level Spectrum) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second, mutually-exclusive "Analysis" view to the recording details page, showing
an on-demand sound-level spectrum (plus a reserved, non-functional slot for a future call-distance
graph), reachable via a toggle button, with a new Select tool for choosing which part of the
recording the spectrum covers.

**Architecture:** A three-zone toolbar (view-specific tools / shared controls / view toggle)
gates two content blocks in `recording_details.html` via a CSS class on the page wrapper. The
spectrum itself is computed on-demand (Welch's method PSD, self-relative dB) by a new pure
renderer and served as JSON from a new Flask route that reuses the existing denoised-samples
cache; a new pure JS module reshapes that JSON into a Chart.js dataset. A new Select tool joins
the existing Default/Ruler tools, contributing a durable time-range selection that survives the
Playback↔Analysis toggle and temporarily overrides View Lock.

**Tech Stack:** Flask, SQLAlchemy, NumPy/SciPy (`scipy.signal.welch`), vanilla JS (no build step),
Chart.js (already vendored), pytest (`hatch test`), Node's built-in test runner (`node --test`),
Tabler Icons (inline SVG via the `icon()` Jinja global).

**Spec:** `docs/superpowers/specs/2026-09-18-fledermap-analysis-view-design.md`

## Global Constraints

- No emoji or Unicode dingbats anywhere in the UI — icons are inline Tabler SVGs via the `icon()`
  Jinja global (`docs/style-guide.md`).
- Disable, don't hide, a single control that becomes temporarily inactive within an otherwise
  unchanged view (the View Lock button while a selection is active; the Analysis view's Selection
  button while no selection exists). A full content-area swap (Playback↔Analysis, or the toolbar's
  left zone contents) is not this case and may show/hide its contents (spec §3).
- Toolbar zones are fixed, equal thirds (`grid-template-columns: 1fr 1fr 1fr`), never `auto`-sized
  — nothing may shift position when a zone's content changes.
- Every JS-touching task must be verified live against the real running instance with headless
  Chrome before being considered done (CLAUDE.md's "headless-Chrome live-verification technique" —
  mandatory, not optional). This project has no DOM-based JS test infrastructure (no jsdom); only
  pure, DOM-independent logic gets a `node --test` unit test.
- `hatch test -m "not db"` for the fast subset; the full `hatch test` (including `db`-marked tests,
  `dangerouslyDisableSandbox: true`, ~120s for a fresh PostGIS testcontainer) must be run by the
  controlling session itself before this plan is considered complete — never dispatch a subagent
  to run `-m db` tests unassisted (CLAUDE.md's "Environment gotchas").
- `hatch run types:check` (mypy over `src/`, `tests/`, and `scripts/`) must pass with no
  `# type: ignore`.
- Run `git` unsandboxed (`dangerouslyDisableSandbox: true`) — sandboxed git config writes leave a
  stale `.git/config.lock`.

---

### Task 1: Vendor the two new Tabler icons

**Files:**
- Modify: `src/fledermap/services/vendor_assets.py`
- Test: `tests/test_vendor_assets.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `icons/outline/player-play.svg` and `icons/outline/chart-histogram.svg` available to
  `icon("player-play")` / `icon("chart-histogram")` (used by Task 3).

- [ ] **Step 1: Write the failing test**

Add two relative paths to the `expected` set in
`tests/test_vendor_assets.py::test_assets_includes_every_icon_the_app_uses`:

```python
        "icons/outline/x.svg",
        "icons/outline/player-play.svg",
        "icons/outline/chart-histogram.svg",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_vendor_assets.py::test_assets_includes_every_icon_the_app_uses -v`
Expected: FAIL — `assert expected <= relative_paths` fails, missing the two new paths.

- [ ] **Step 3: Add the two `VendorAsset` entries**

In `src/fledermap/services/vendor_assets.py`, add to the `ASSETS` tuple (both hashes were fetched
and verified directly against unpkg.com's pinned `3.46.0` release before this plan was written —
do not re-guess them):

```python
    # The Analysis-view toggle button's two icons (design spec
    # docs/superpowers/specs/2026-09-18-fledermap-analysis-view-design.md) -- outline style, to
    # match the rest of the toolbar's icon-based toggles (Lock, Denoise). A FILLED player-play
    # already exists above for the play/pause button; this is a separate, outline-style asset.
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/player-play.svg",
        sha256="9726d5358929fbfe4a4cbd08131a5f1b4246ca01125f5108e7221b9c73e101ff",
        relative_path="icons/outline/player-play.svg",
    ),
    VendorAsset(
        url="https://unpkg.com/@tabler/icons@3.46.0/icons/outline/chart-histogram.svg",
        sha256="da6052d6f928b5de5975a04f8fa3519edc84b6602513d4c3081cef18e294edeb",
        relative_path="icons/outline/chart-histogram.svg",
    ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch test tests/test_vendor_assets.py -v`
Expected: PASS — all `test_vendor_assets.py` tests pass, including
`test_assets_includes_every_relative_path_exactly_once` (no duplicate).

- [ ] **Step 5: Fetch the assets into the local static root so the app can actually render them**

Run: `hatch run fledermap fetch-assets` (uses whatever `FLEDERMAP_STATIC_ROOT`/default cache
directory this environment resolves to) — confirms the real fetch+hash-verify path also succeeds,
not just the in-memory `ASSETS` tuple.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/vendor_assets.py tests/test_vendor_assets.py
git commit -m "feat: vendor player-play and chart-histogram outline icons"
```

---

### Task 2: Restructure the toolbar into three fixed, equal-width zones

No new buttons or behavior — this task only regroups the existing Default/Ruler/View Lock/Denoise
buttons into the three zones the spec defines, with fixed equal-thirds widths, before later tasks
add new content to each zone.

**Files:**
- Modify: `src/fledermap/web/templates/recording_details.html:57-72`
- Modify: `src/fledermap/web/static/app.css:805`
- Test: `tests/test_recording_detail_view.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `.toolbar-zone-tools`, `.toolbar-zone-shared`, `.toolbar-zone-view` container
  elements inside `#detail-toolbar`, consumed by Tasks 3 and 4 to place new buttons.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_recording_detail_view.py`, modeled exactly on the existing
`test_recording_details_page_renders_the_tool_toolbar` in the same file (same
`engine`/`tmp_path` fixtures, same `Recording`/`create_app`/`app.test_client()` setup — this
project's tests don't use a shared client fixture, each test builds its own):

```python
def test_toolbar_has_three_zones(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="zn" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'zn' * 32}")
    html = response.get_data(as_text=True)

    assert 'class="toolbar-zone toolbar-zone-tools"' in html
    assert 'class="toolbar-zone toolbar-zone-shared"' in html
    assert 'class="toolbar-zone toolbar-zone-view"' in html
    # Default/Ruler/View Lock stay in the tools zone; Denoise moves to the shared zone.
    tools_zone_start = html.index('class="toolbar-zone toolbar-zone-tools"')
    shared_zone_start = html.index('class="toolbar-zone toolbar-zone-shared"')
    assert tools_zone_start < html.index('data-tool="default"') < shared_zone_start
    assert tools_zone_start < html.index('data-tool="ruler"') < shared_zone_start
    assert tools_zone_start < html.index('id="view-lock-toggle"') < shared_zone_start
    assert shared_zone_start < html.index('id="denoise-toggle"')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_recording_detail_view.py::test_toolbar_has_three_zones -v`
Expected: FAIL — no `toolbar-zone` classes exist yet.

- [ ] **Step 3: Restructure the template**

Replace `src/fledermap/web/templates/recording_details.html:57-72`:

```html
    <div class="detail-toolbar" id="detail-toolbar">
      <button type="button" class="tool-button" data-tool="default" aria-pressed="true">Default</button>
      <button type="button" class="tool-button" data-tool="ruler" aria-pressed="false">Ruler</button>
      {# recording_detail.js toggles `hidden` on the two icon spans -- it never rewrites
         .textContent here any more (that couldn't carry SVG markup anyway), and the label
         text is now static, never touched by JS at all. #}
      <button type="button" id="view-lock-toggle" aria-pressed="false" title="Lock the current view: no scrolling, playback stops at the right edge">
        <span class="lock-icon-unlocked">{{ icon("lock") }}</span>
        <span class="lock-icon-locked" hidden>{{ icon("lock", filled=True) }}</span>
        <span class="lock-label">Lock view</span>
      </button>
      <button type="button" id="denoise-toggle" aria-pressed="false" title="Filter out audio below 5kHz and reduce background noise in the spectrogram and in playback">
        {{ icon("noise-reduction") }}
        <span class="filter-label">Denoise</span>
      </button>
    </div>
```

with:

```html
    <div class="detail-toolbar" id="detail-toolbar">
      <div class="toolbar-zone toolbar-zone-tools">
        <button type="button" class="tool-button" data-tool="default" aria-pressed="true">Default</button>
        <button type="button" class="tool-button" data-tool="ruler" aria-pressed="false">Ruler</button>
        {# recording_detail.js toggles `hidden` on the two icon spans -- it never rewrites
           .textContent here any more (that couldn't carry SVG markup anyway), and the label
           text is now static, never touched by JS at all. #}
        <button type="button" id="view-lock-toggle" aria-pressed="false" title="Lock the current view: no scrolling, playback stops at the right edge">
          <span class="lock-icon-unlocked">{{ icon("lock") }}</span>
          <span class="lock-icon-locked" hidden>{{ icon("lock", filled=True) }}</span>
          <span class="lock-label">Lock view</span>
        </button>
      </div>
      <div class="toolbar-zone toolbar-zone-shared">
        <button type="button" id="denoise-toggle" aria-pressed="false" title="Filter out audio below 5kHz and reduce background noise in the spectrogram and in playback">
          {{ icon("noise-reduction") }}
          <span class="filter-label">Denoise</span>
        </button>
      </div>
      <div class="toolbar-zone toolbar-zone-view">
      </div>
    </div>
```

(The `toolbar-zone-view` button itself is added in Task 3 — this task only creates the empty
zone so the grid layout can be verified independently first.)

- [ ] **Step 4: Update the CSS**

Replace `src/fledermap/web/static/app.css:805`:

```css
.detail-toolbar { display: flex; gap: 0.4rem; margin: 0.5rem 0; }
```

with:

```css
/* Three fixed, equal-width zones (design spec
   2026-09-18-fledermap-analysis-view-design.md §3) -- `1fr 1fr 1fr`, never `auto`, so a zone's
   own content changing (the tools zone emptying out in Analysis view, the view-toggle button's
   icon+label changing length) never shifts where another zone's contents sit. */
.detail-toolbar { display: grid; grid-template-columns: 1fr 1fr 1fr; margin: 0.5rem 0; }
.toolbar-zone { display: flex; gap: 0.4rem; align-items: center; }
.toolbar-zone-shared { justify-content: center; }
.toolbar-zone-view { justify-content: flex-end; }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `hatch test tests/test_recording_detail_view.py -v`
Expected: PASS for the new test and every pre-existing test in that file (a toolbar-presence
regression here would show up as an existing test failing, e.g. one asserting
`data-tool="default"` appears — it still does, just nested one level deeper).

- [ ] **Step 6: Live-verify no visual regression**

Start the dev server (see this project's `run` skill or `fledermap serve`) and load a recording
details page with headless Chrome. Confirm: Default/Ruler/Lock view still visible on the left,
Denoise now visually centered, nothing changed about button behavior (click Default/Ruler/Lock
view/Denoise, confirm each still works exactly as before).

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/web/templates/recording_details.html src/fledermap/web/static/app.css tests/test_recording_detail_view.py
git commit -m "refactor: split the recording-detail toolbar into three fixed zones"
```

---

### Task 3: View toggle button and Playback/Analysis switching

**Files:**
- Modify: `src/fledermap/web/templates/recording_details.html`
- Modify: `src/fledermap/web/static/recording_detail.js`
- Modify: `src/fledermap/web/static/app.css`
- Test: `tests/test_recording_detail_view.py`

**Interfaces:**
- Consumes: `.toolbar-zone-view` from Task 2.
- Produces: an `.analysis-panel` element (empty stub — filled in by Task 8) and a `view-playback`/
  `view-analysis` class on `main#recording-detail`, both read by Task 4 (View Lock/Select-tool
  wiring is Playback-only in spirit, though nothing in Task 4 needs to check this class directly)
  and Task 8 (chart mounting only happens while `view-analysis` is active).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_recording_detail_view.py`:

```python
def test_view_toggle_button_is_present_and_targets_analysis(
    engine: Engine, tmp_path: Path
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="vt" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'vt' * 32}")
    html = response.get_data(as_text=True)

    assert 'id="view-toggle-button"' in html
    assert 'data-target-view="analysis"' in html
    assert 'class="analysis-panel"' in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_recording_detail_view.py::test_view_toggle_button_is_present_and_targets_analysis -v`
Expected: FAIL — neither element exists yet.

- [ ] **Step 3: Add the toggle button and the (empty) analysis panel**

In `src/fledermap/web/templates/recording_details.html`, fill the empty `toolbar-zone-view` div
added in Task 2:

```html
      <div class="toolbar-zone toolbar-zone-view">
        <button type="button" id="view-toggle-button" data-target-view="analysis" title="Switch to Analysis view">
          <span class="view-toggle-icon-to-analysis">{{ icon("chart-histogram") }}</span>
          <span class="view-toggle-icon-to-playback" hidden>{{ icon("player-play") }}</span>
          <span class="view-toggle-label">Analysis view</span>
        </button>
      </div>
```

Immediately after the closing `</div>` of `#detail-scroll` (the block that starts at
`recording_details.html:73`, `<div class="detail-scroll" id="detail-scroll">`), add the sibling
panel (still inside the `{% if params %}` block):

```html
    <div class="analysis-panel" hidden></div>
```

(Left as an empty, `hidden` placeholder — Task 8 fills in its real contents. `hidden` here is a
static template attribute for "not shown on initial load," distinct from the `.playback-only`/
`.analysis-only` visibility classes the JS toggles below, which govern which of the two blocks
shows once JS has run; without JS, showing nothing here is safer than showing an empty box.)

- [ ] **Step 4: Add the JS toggle logic**

In `src/fledermap/web/static/recording_detail.js`, add near the other toolbar wiring (after the
`viewLockToggle` block ending around line 686, before the `tools` object):

```javascript
  const mainContentEl = document.getElementById("recording-detail");
  const viewToggleButton = document.getElementById("view-toggle-button");
  const analysisPanel = document.querySelector(".analysis-panel");
  const viewToggleIconToAnalysis = viewToggleButton.querySelector(".view-toggle-icon-to-analysis");
  const viewToggleIconToPlayback = viewToggleButton.querySelector(".view-toggle-icon-to-playback");
  const viewToggleLabel = viewToggleButton.querySelector(".view-toggle-label");
  let activeView = "playback";

  function setActiveView(view) {
    activeView = view;
    const targetView = view === "playback" ? "analysis" : "playback";
    viewToggleButton.dataset.targetView = targetView;
    viewToggleButton.title = `Switch to ${targetView === "analysis" ? "Analysis" : "Playback"} view`;
    viewToggleIconToAnalysis.hidden = view !== "playback";
    viewToggleIconToPlayback.hidden = view !== "analysis";
    viewToggleLabel.textContent = targetView === "analysis" ? "Analysis view" : "Playback";
    mainContentEl.classList.toggle("view-playback", view === "playback");
    mainContentEl.classList.toggle("view-analysis", view === "analysis");
    scrollEl.hidden = view !== "playback";
    analysisPanel.hidden = view !== "analysis";
  }

  viewToggleButton.addEventListener("click", () => {
    setActiveView(viewToggleButton.dataset.targetView);
  });
  setActiveView("playback");
```

- [ ] **Step 5: Add the CSS class hooks**

Append to `src/fledermap/web/static/app.css`:

```css
.analysis-panel[hidden] { display: none; }
```

(No other CSS needed yet — `scrollEl.hidden`/`analysisPanel.hidden` are plain DOM `hidden`
attributes, which every browser already renders as `display: none` by default; this rule only
exists so a future rule targeting `.analysis-panel` without `[hidden]` doesn't need to fight the
UA stylesheet's specificity — harmless to add now, and Task 8 will rely on it.)

- [ ] **Step 6: Run test to verify it passes**

Run: `hatch test tests/test_recording_detail_view.py -v`
Expected: PASS.

- [ ] **Step 7: Live-verify the toggle**

Headless-Chrome check against the real running instance: load a recording details page, click the
view-toggle button — confirm the Playback content (oscillogram/spectrogram/toolbar tools zone)
hides, the (still-empty) Analysis panel becomes visible, the button's icon and label both flip to
"Playback", and clicking again reverses all of it. Confirm the toolbar's three zone widths do not
visibly shift during the toggle (Global Constraints).

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/web/templates/recording_details.html src/fledermap/web/static/recording_detail.js src/fledermap/web/static/app.css tests/test_recording_detail_view.py
git commit -m "feat: add the Playback/Analysis view toggle"
```

---

### Task 4: Select tool and its View Lock interaction

**Files:**
- Modify: `src/fledermap/web/templates/recording_details.html`
- Modify: `src/fledermap/web/static/recording_detail.js`
- Test: `tests/test_recording_detail_view.py`

**Interfaces:**
- Consumes: the existing `tools` registry, `setActiveTool`, `viewLockToggle` (all in
  `recording_detail.js`, unmodified in shape from before this plan).
- Produces: `currentSelection` (`{startTimeS: number, endTimeS: number} | null`, a variable in
  `recording_detail.js`'s `DOMContentLoaded` scope), consumed by Task 8's Whole file/Selection
  control to decide which span to request from the endpoint.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_recording_detail_view.py`:

```python
def _create_app_with_one_recording(
    engine: Engine, tmp_path: Path, audio_hash: str
) -> tuple[object, str]:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash=audio_hash,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    return app, audio_hash


def test_select_tool_button_is_present(engine: Engine, tmp_path: Path) -> None:
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "se" * 32)
    response = app.test_client().get(f"/recordings/{audio_hash}")
    html = response.get_data(as_text=True)
    assert 'data-tool="select"' in html


def test_view_lock_toggle_is_not_disabled_on_load(engine: Engine, tmp_path: Path) -> None:
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "vl" * 32)
    response = app.test_client().get(f"/recordings/{audio_hash}")
    html = response.get_data(as_text=True)
    lock_button_start = html.index('id="view-lock-toggle"')
    lock_button_tag_end = html.index(">", lock_button_start)
    assert "disabled" not in html[lock_button_start:lock_button_tag_end]
```

`_create_app_with_one_recording` is a small local helper introduced in this task's test file to
avoid repeating the same six-line setup in every new test added by this task and Task 8 below —
add it once here, near the top of `tests/test_recording_detail_view.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch test tests/test_recording_detail_view.py::test_select_tool_button_is_present -v`
Expected: FAIL — no `data-tool="select"` button exists yet. (The second test passes already —
that's fine, it's a baseline assertion this task must not break.)

- [ ] **Step 3: Add the Select button to the template**

In `src/fledermap/web/templates/recording_details.html`'s `toolbar-zone-tools` div (from Task 2),
add a third tool button after Ruler:

```html
        <button type="button" class="tool-button" data-tool="ruler" aria-pressed="false">Ruler</button>
        <button type="button" class="tool-button" data-tool="select" aria-pressed="false">Select</button>
```

- [ ] **Step 4: Add `currentSelection` state and the `select` tool object**

In `src/fledermap/web/static/recording_detail.js`, declare the new state near the top of
`DOMContentLoaded`, alongside `rulerBox`/`rulerBoxLocal` (around line 74-75):

```javascript
  let currentSelection = null; // {startTimeS, endTimeS} | null -- durable across a view toggle,
  // unlike rulerBox/rulerBoxLocal above, which setActiveTool clears on every tool switch.
  let selectionBox = null;
```

Add a `clearSelectionBox` function alongside `clearRulerBox` (near line 559-566):

```javascript
  function clearSelectionBox() {
    if (selectionBox) {
      selectionBox.remove();
      selectionBox = null;
    }
    currentSelection = null;
    viewLockToggle.disabled = false;
  }
```

(`viewLockToggle` is declared further down the file today, around line 672 — move its `const
viewLockToggle = document.getElementById("view-lock-toggle");` declaration up to just before this
new function, since `clearSelectionBox` now references it. Everything else about the
`viewLockToggle` block stays where it is.)

Add the `select` tool's drag/click handlers, modeled on `updateRulerBox` but horizontal-only (no
vertical component, spans the full height of `wrap`):

```javascript
  function updateSelectionBox(dragStart, event) {
    const rect = wrap.getBoundingClientRect();
    const scrollDriftPx = scrollEl.scrollLeft - dragStart.scrollLeft;
    const startX = (dragStart.x - scrollDriftPx - rect.left) / currentScale;
    const curX = (event.clientX - rect.left) / currentScale;
    const left = Math.min(startX, curX);
    const width = Math.abs(curX - startX);

    if (!selectionBox) {
      selectionBox = document.createElement("div");
      selectionBox.className = "selection-box";
      wrap.appendChild(selectionBox);
    }
    selectionBox.style.left = `${left}px`;
    selectionBox.style.width = `${width}px`;

    const startTimeS = Math.min(startX, curX) / pxPerMs / 1000;
    const endTimeS = Math.max(startX, curX) / pxPerMs / 1000;
    currentSelection = { startTimeS, endTimeS };
    viewLockToggle.disabled = true;
  }
```

Add `select` to the `tools` registry (alongside `default`/`ruler`, around line 712-749):

```javascript
    select: {
      onClick() {
        clearSelectionBox();
      },
      onDrag(event, dragStart) {
        updateSelectionBox(dragStart, event);
      },
      onDragEnd() {},
    },
```

Update `setActiveTool` (line 656-665) to also clear a pending selection when switching to a
*different* tool (Default or Ruler), matching Ruler's existing clear-on-switch behavior:

```javascript
  function setActiveTool(tool) {
    activeTool = tool;
    toolButtons.forEach((btn) => {
      const isActive = btn.dataset.tool === tool;
      btn.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
    wrap.classList.toggle("tool-default", tool === "default");
    wrap.classList.toggle("tool-ruler", tool === "ruler");
    wrap.classList.toggle("tool-select", tool === "select");
    clearRulerBox();
    if (tool !== "select") clearSelectionBox();
  }
```

(`if (tool !== "select") clearSelectionBox()` avoids clearing the selection you're actively
mid-drag-creating the instant you click the Select button itself — `setActiveTool("select")` runs
once, on the click that activates the tool, before any drag has happened.)

- [ ] **Step 5: Add minimal CSS for the selection overlay**

Append to `src/fledermap/web/static/app.css`, modeled on `.ruler-box` (spans full height,
horizontal-only, distinct color from Ruler's `--color-overlay` so the two are visually
distinguishable if a reader compares screenshots — reuse `--color-accent`, already used elsewhere
in this file for "this is active/selected"):

```css
.detail-spectrogram-wrap.tool-select { cursor: crosshair; }
.selection-box {
  position: absolute;
  top: 0;
  bottom: 0;
  border-left: 1px dashed var(--color-accent);
  border-right: 1px dashed var(--color-accent);
  background: color-mix(in srgb, var(--color-accent) 12%, transparent);
  pointer-events: none;
  z-index: 2;
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `hatch test tests/test_recording_detail_view.py -v`
Expected: PASS.

- [ ] **Step 7: Run mypy and the full fast test suite**

Run: `hatch run types:check` and `hatch test -m "not db"`
Expected: both pass (this task only touched JS/HTML/CSS + one Python test file, so this is a
regression check, not expected to surface new mypy issues).

- [ ] **Step 8: Live-verify Select tool behavior**

Headless-Chrome check against the real running instance:
- Click Select, drag across the spectrogram — confirm a full-height, horizontal band overlay
  appears and the View Lock button becomes visually disabled (grayed, per the existing generic
  `button:disabled` CSS).
- Click elsewhere (no drag) while Select is active — confirm the overlay disappears and View Lock
  re-enables.
- Make a selection, then switch to Ruler — confirm the selection overlay disappears and View Lock
  re-enables (same as a plain click).
- Make a selection, then click the view-toggle button (Task 3) to enter Analysis and back —
  confirm the selection overlay is still present and View Lock is still disabled after returning
  to Playback (durability across the view switch — this is the one behavior with no automated
  test, since it depends on `currentSelection`'s in-memory persistence across `setActiveView`
  calls, which never clear it).

- [ ] **Step 9: Commit**

```bash
git add src/fledermap/web/templates/recording_details.html src/fledermap/web/static/recording_detail.js src/fledermap/web/static/app.css tests/test_recording_detail_view.py
git commit -m "feat: add the Select tool and its View Lock override"
```

---

### Task 5: `compute_sound_level_spectrum` pure renderer

**Files:**
- Create: `src/fledermap/media/sound_level_spectrum.py`
- Test: `tests/test_sound_level_spectrum.py`

**Interfaces:**
- Consumes: nothing from other tasks (pure function, `numpy`/`scipy` only).
- Produces: `SoundLevelSpectrum` (dataclass: `freqs_hz: np.ndarray`, `db: np.ndarray`,
  `peak_freq_hz: float`) and `compute_sound_level_spectrum(samples, samplerate_hz,
  dynamic_range_db=80.0) -> SoundLevelSpectrum`, consumed by Task 6's route.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sound_level_spectrum.py`:

```python
from __future__ import annotations

import numpy as np

from fledermap.media.sound_level_spectrum import compute_sound_level_spectrum


def _sine(freq_hz: float, samplerate_hz: float, duration_s: float) -> np.ndarray:
    n = int(samplerate_hz * duration_s)
    t = np.arange(n) / samplerate_hz
    return np.sin(2 * np.pi * freq_hz * t)


def test_peak_lands_near_the_tone_s_frequency() -> None:
    samplerate_hz = 256_000.0
    result = compute_sound_level_spectrum(_sine(40_000.0, samplerate_hz, 0.02), samplerate_hz)
    assert abs(result.peak_freq_hz - 40_000.0) < 2_000.0


def test_db_is_self_relative_with_zero_at_the_peak() -> None:
    samplerate_hz = 256_000.0
    result = compute_sound_level_spectrum(_sine(40_000.0, samplerate_hz, 0.02), samplerate_hz)
    assert result.db.max() == 0.0


def test_db_is_clipped_to_the_dynamic_range() -> None:
    samplerate_hz = 256_000.0
    result = compute_sound_level_spectrum(
        _sine(40_000.0, samplerate_hz, 0.02), samplerate_hz, dynamic_range_db=40.0
    )
    assert result.db.min() >= -40.0


def test_a_short_slice_still_returns_output_without_erroring() -> None:
    # Simulates a narrow Select-tool span -- shorter than scipy.signal.welch's default
    # nperseg (256), which would otherwise raise if not handled.
    samplerate_hz = 256_000.0
    short_samples = _sine(40_000.0, samplerate_hz, 0.0005)  # 128 samples
    result = compute_sound_level_spectrum(short_samples, samplerate_hz)
    assert len(result.freqs_hz) > 0
    assert len(result.db) == len(result.freqs_hz)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_sound_level_spectrum.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fledermap.media.sound_level_spectrum'`.

- [ ] **Step 3: Write the implementation**

Create `src/fledermap/media/sound_level_spectrum.py`:

```python
"""On-demand sound-level spectrum for the recording-details page's Analysis view (design spec
docs/superpowers/specs/2026-09-18-fledermap-analysis-view-design.md §5). Pure, no I/O -- unlike
`spectrogram.py`/`oscillogram.py`, this returns data for a JSON endpoint to serialize, not a
rendered image, since it's computed fresh per request rather than cached derived media."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import welch


@dataclass(frozen=True)
class SoundLevelSpectrum:
    freqs_hz: np.ndarray
    db: np.ndarray  # relative to this spectrum's own peak -- 0 dB at the peak, matching
    # spectrogram.py's self-relative convention (CLAUDE.md's "Derived media rendering").
    peak_freq_hz: float


def compute_sound_level_spectrum(
    samples: np.ndarray,
    samplerate_hz: float,
    dynamic_range_db: float = 80.0,
) -> SoundLevelSpectrum:
    # `nperseg` capped at the sample count: scipy.signal.welch's own default (256) raises for a
    # shorter input (e.g. a narrow Select-tool span), and Welch's method needs at least one full
    # segment to produce any output at all.
    nperseg = min(256, len(samples))
    freqs_hz, psd = welch(samples, fs=samplerate_hz, nperseg=nperseg)
    peak_index = int(np.argmax(psd))
    peak_freq_hz = float(freqs_hz[peak_index])
    db = 10 * np.log10(np.maximum(psd, 1e-300) / psd[peak_index])
    db = np.clip(db, -dynamic_range_db, 0.0)
    return SoundLevelSpectrum(freqs_hz=freqs_hz, db=db, peak_freq_hz=peak_freq_hz)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_sound_level_spectrum.py -v`
Expected: PASS.

- [ ] **Step 5: Run mypy**

Run: `hatch run types:check`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/media/sound_level_spectrum.py tests/test_sound_level_spectrum.py
git commit -m "feat: add compute_sound_level_spectrum (Welch's-method PSD)"
```

---

### Task 6: `detail_sound_level_spectrum` route

**Files:**
- Modify: `src/fledermap/web/views/media.py`
- Test: `tests/test_media_view.py`

**Interfaces:**
- Consumes: `compute_sound_level_spectrum`/`SoundLevelSpectrum` (Task 5); `resolve_recording`,
  `resolve_wav_path` (`services/media.py`, unmodified); `_denoised_samples_cache`,
  `_denoised_samples_cache_key`, `_read_denoised_samples` (all pre-existing in `media.py`,
  unmodified).
- Produces: `GET /recordings/<audio_hash>/detail/sound-level-spectrum` returning
  `{"freqs_hz": [...], "db": [...], "peak_freq_hz": ...}`, consumed by Task 7/8's client-side fetch.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_media_view.py` (reuses this file's existing `_write_wav`/`_sine_pcm` helpers and
`pytestmark = pytest.mark.db`):

```python
def test_sound_level_spectrum_returns_whole_file_by_default(
    engine: Engine, tmp_path: Path
) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    duration_s = 0.02
    _write_wav(archive_root / "a.wav", duration_s=duration_s, samplerate=256_000)

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="s1" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=duration_s,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(
        engine, tmp_path / "static", tmp_path / "media", archive_roots=(archive_root,)
    )
    response = app.test_client().get(
        f"/recordings/{'s1' * 32}/detail/sound-level-spectrum"
    )

    assert response.status_code == 200
    body = response.get_json()
    assert len(body["freqs_hz"]) > 0
    assert len(body["db"]) == len(body["freqs_hz"])
    assert abs(body["peak_freq_hz"] - 45_000.0) < 5_000.0  # _sine_pcm's default freq_hz


def test_sound_level_spectrum_honors_a_start_end_span(engine: Engine, tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    duration_s = 0.02
    _write_wav(archive_root / "a.wav", duration_s=duration_s, samplerate=256_000)

    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="s2" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                duration_s=duration_s,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    app = create_app(
        engine, tmp_path / "static", tmp_path / "media", archive_roots=(archive_root,)
    )
    response = app.test_client().get(
        f"/recordings/{'s2' * 32}/detail/sound-level-spectrum?start_s=0&end_s=0.01"
    )

    assert response.status_code == 200
    assert response.get_json()["freqs_hz"]


def test_sound_level_spectrum_404s_for_unknown_hash(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(
        f"/recordings/{'s3' * 32}/detail/sound-level-spectrum"
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_media_view.py -k sound_level_spectrum -v`
Expected: FAIL — 404 for all three (route doesn't exist yet).

- [ ] **Step 3: Add a lightweight resolve helper**

`_detail_tile_context` (media.py:194) requires a `tile_index` this route doesn't have. Add a
narrower helper just above it:

```python
def _resolve_detail_wav(audio_hash: str) -> tuple[Path, float] | None:
    """Resolves `audio_hash` to (wav_path, samplerate_hz) for routes that don't tile -- narrower
    than `_detail_tile_context` below, which additionally requires a valid `tile_index`."""
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
        samplerate_hz = recording.samplerate_hz
    return wav_path, samplerate_hz
```

- [ ] **Step 4: Add the route**

Add the two new imports at the top of `media.py`:

```python
from fledermap.media.denoise import DEFAULT_CUTOFF_HZ
from fledermap.media.sound_level_spectrum import compute_sound_level_spectrum
```

Add the route, after `detail_oscillogram` (following it directly, alongside the other two
`/recordings/<audio_hash>/detail-*` routes):

```python
@media_bp.get("/recordings/<audio_hash>/detail/sound-level-spectrum")
def detail_sound_level_spectrum(audio_hash: str) -> ResponseReturnValue:
    context = _resolve_detail_wav(audio_hash)
    if context is None:
        flask.abort(404)
    wav_path, samplerate_hz = context
    denoise = parse_bool(flask.request.args.get("denoise"))
    start_s = flask.request.args.get("start_s", type=float)
    end_s = flask.request.args.get("end_s", type=float)

    try:
        samples, _samplerate, _full_scale = _denoised_samples_cache.get_or_compute(
            _denoised_samples_cache_key(wav_path, denoise=denoise, cutoff_hz=DEFAULT_CUTOFF_HZ),
            lambda: _read_denoised_samples(
                wav_path, denoise=denoise, cutoff_hz=DEFAULT_CUTOFF_HZ
            ),
        )
    except UnreadableWavError as exc:
        logger.warning("unreadable source WAV for %s: %s", audio_hash, exc)
        flask.abort(404)

    if start_s is not None and end_s is not None:
        start_index = max(0, int(start_s * samplerate_hz))
        end_index = min(len(samples), int(end_s * samplerate_hz))
        samples = samples[start_index:end_index]

    spectrum = compute_sound_level_spectrum(samples, samplerate_hz)
    return flask.jsonify(
        {
            "freqs_hz": spectrum.freqs_hz.tolist(),
            "db": spectrum.db.tolist(),
            "peak_freq_hz": spectrum.peak_freq_hz,
        }
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_media_view.py -k sound_level_spectrum -v`
Expected: PASS.

- [ ] **Step 6: Run the full fast test suite and mypy**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: both pass. (The three new tests are `db`-marked via the file's module-level
`pytestmark`, so they aren't in the fast subset — run the full `hatch test` per this plan's Global
Constraints before considering this task's test coverage verified, using
`dangerouslyDisableSandbox: true`.)

- [ ] **Step 7: Run the full test suite including `db`-marked tests**

Run (unsandboxed, ~120s for the PostGIS testcontainer): `hatch test`
Expected: PASS, including the three new tests in `test_media_view.py`.

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/web/views/media.py tests/test_media_view.py
git commit -m "feat: add the sound-level-spectrum JSON endpoint"
```

---

### Task 7: `analysis_chart.js` pure reshaping module

**Files:**
- Create: `src/fledermap/web/static/analysis_chart.js`
- Test: `tests/js/analysis_chart.test.js`

**Interfaces:**
- Consumes: nothing from other tasks (pure function).
- Produces: `soundLevelSpectrumToChartData(spectrum)` returning a Chart.js-shaped
  `{labels, datasets}` object (`labels`: frequency values in kHz as strings/numbers; one dataset:
  the dB line), consumed by Task 8's chart-mounting code.

- [ ] **Step 1: Write the failing test**

Create `tests/js/analysis_chart.test.js`:

```javascript
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { soundLevelSpectrumToChartData } = require(
  "../../src/fledermap/web/static/analysis_chart.js"
);

test("soundLevelSpectrumToChartData converts Hz labels to kHz", () => {
  const result = soundLevelSpectrumToChartData({
    freqs_hz: [0, 20000, 40000],
    db: [-80, -20, 0],
    peak_freq_hz: 40000,
  });
  assert.deepEqual(result.labels, [0, 20, 40]);
});

test("soundLevelSpectrumToChartData carries the dB values through unchanged", () => {
  const result = soundLevelSpectrumToChartData({
    freqs_hz: [0, 20000, 40000],
    db: [-80, -20, 0],
    peak_freq_hz: 40000,
  });
  assert.deepEqual(result.datasets[0].data, [-80, -20, 0]);
});

test("soundLevelSpectrumToChartData names the dataset", () => {
  const result = soundLevelSpectrumToChartData({
    freqs_hz: [0],
    db: [0],
    peak_freq_hz: 0,
  });
  assert.equal(result.datasets[0].label, "Sound level (dB)");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/js/analysis_chart.test.js`
Expected: FAIL — `Cannot find module '../../src/fledermap/web/static/analysis_chart.js'`.

- [ ] **Step 3: Write the implementation**

Create `src/fledermap/web/static/analysis_chart.js`:

```javascript
// src/fledermap/web/static/analysis_chart.js -- pure reshaping logic, no DOM access, following
// the split app.js/statistics_charts.js pattern already established (CLAUDE.md's JavaScript
// tooling section): turns the sound-level-spectrum endpoint's JSON into the exact shape
// Chart.js's dataset API wants. Loaded via its own <script> tag, before recording_detail.js
// (which mounts the actual canvas).

function soundLevelSpectrumToChartData(spectrum) {
  return {
    labels: spectrum.freqs_hz.map((hz) => hz / 1000),
    datasets: [
      {
        label: "Sound level (dB)",
        data: spectrum.db,
        borderColor: "currentColor",
        pointRadius: 0,
      },
    ],
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { soundLevelSpectrumToChartData };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test tests/js/analysis_chart.test.js`
Expected: PASS.

- [ ] **Step 5: Run the full JS test suite**

Run: `node --test tests/js/`
Expected: PASS (no regression in any other JS test file).

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/web/static/analysis_chart.js tests/js/analysis_chart.test.js
git commit -m "feat: add analysis_chart.js's pure Chart.js reshaping function"
```

---

### Task 8: Analysis panel content, Whole file/Selection control, and chart mounting

**Files:**
- Modify: `src/fledermap/web/templates/recording_details.html`
- Modify: `src/fledermap/web/static/recording_detail.js`
- Modify: `src/fledermap/web/static/app.css`
- Test: `tests/test_recording_detail_view.py`

**Interfaces:**
- Consumes: `.analysis-panel` (Task 3), `currentSelection`/`clearSelectionBox` (Task 4),
  `detail_sound_level_spectrum` endpoint (Task 6), `soundLevelSpectrumToChartData` (Task 7).
- Produces: nothing further consumed by another task — this is the last task before final
  verification.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_recording_detail_view.py`:

```python
def test_analysis_panel_has_whole_file_and_selection_controls(
    engine: Engine, tmp_path: Path
) -> None:
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "ap" * 32)
    response = app.test_client().get(f"/recordings/{audio_hash}")
    html = response.get_data(as_text=True)
    assert 'data-span="whole-file"' in html
    assert 'data-span="selection"' in html
    assert 'id="sound-level-spectrum-chart"' in html
    assert "Call-distance graph" in html


def test_selection_span_button_is_disabled_on_load(engine: Engine, tmp_path: Path) -> None:
    app, audio_hash = _create_app_with_one_recording(engine, tmp_path, "sd" * 32)
    response = app.test_client().get(f"/recordings/{audio_hash}")
    html = response.get_data(as_text=True)
    selection_button_start = html.index('data-span="selection"')
    button_tag_start = html.rindex("<button", 0, selection_button_start)
    button_tag_end = html.index(">", selection_button_start)
    assert "disabled" in html[button_tag_start:button_tag_end]
```

(`_create_app_with_one_recording` was added to this test file in Task 4 — reuse it, don't
redefine it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_recording_detail_view.py -k analysis_panel -v` and
`hatch test tests/test_recording_detail_view.py -k selection_span -v`
Expected: FAIL — none of this markup exists yet (`.analysis-panel` is still empty from Task 3).

- [ ] **Step 3: Fill in the Analysis panel markup**

Replace `<div class="analysis-panel" hidden></div>` (added in Task 3) with:

```html
    <div class="analysis-panel" hidden data-spectrum-url="{{ url_for('media.detail_sound_level_spectrum', audio_hash=recording.audio_hash) }}">
      <div class="analysis-graph">
        <div class="analysis-graph-controls">
          <button type="button" class="analysis-span-button" data-span="whole-file" aria-pressed="true">Whole file</button>
          <button type="button" class="analysis-span-button" data-span="selection" aria-pressed="false" disabled>Selection</button>
        </div>
        <canvas id="sound-level-spectrum-chart"></canvas>
      </div>
      <div class="analysis-graph analysis-graph-reserved">
        <p>Call-distance graph — reserved. Needs batdetect2 call annotations (not yet available).</p>
      </div>
    </div>
```

`data-spectrum-url` follows this template's existing convention (`data-preview-url`,
`data-het-preview-url-template` on `#detail-audio-controls`) of a server-rendered `url_for(...)`
in a data attribute, rather than reconstructing the URL by hand in JS from a bare hash.

- [ ] **Step 4: Load Chart.js and `analysis_chart.js` on this page**

`recording_details.html`'s `{% block scripts %}` (near the end of the file) does not load Chart.js
today. Replace it:

```html
{% block scripts %}
  <script src="{{ url_for('vendor.static', filename='htmx.min.js') }}"></script>
  <script src="{{ url_for('static', filename='audio_controls.js') }}"></script>
  <script src="{{ url_for('static', filename='url_params.js') }}"></script>
  <script src="{{ url_for('static', filename='tile_visibility.js') }}"></script>
  <script src="{{ url_for('static', filename='recording_detail.js') }}"></script>
  <script src="{{ url_for('static', filename='classifier_logic.js') }}"></script>
  <script src="{{ url_for('static', filename='classifier_box.js') }}"></script>
{% endblock %}
```

with:

```html
{% block scripts %}
  <script src="{{ url_for('vendor.static', filename='htmx.min.js') }}"></script>
  <script src="{{ url_for('vendor.static', filename='chart.js') }}"></script>
  <script src="{{ url_for('static', filename='audio_controls.js') }}"></script>
  <script src="{{ url_for('static', filename='url_params.js') }}"></script>
  <script src="{{ url_for('static', filename='tile_visibility.js') }}"></script>
  <script src="{{ url_for('static', filename='analysis_chart.js') }}"></script>
  <script src="{{ url_for('static', filename='recording_detail.js') }}"></script>
  <script src="{{ url_for('static', filename='classifier_logic.js') }}"></script>
  <script src="{{ url_for('static', filename='classifier_box.js') }}"></script>
{% endblock %}
```

(`vendor.static` is the exact vendor-asset endpoint name this project already uses — confirmed
against `statistics_species.html`'s own `{{ url_for('vendor.static', filename='chart.js') }}`.
`analysis_chart.js` loads before `recording_detail.js`, matching the established split-module
convention: the file defining pure functions loads first, the file calling them loads second.)

- [ ] **Step 5: Wire up fetching and chart mounting in `recording_detail.js`**

Add near the end of `DOMContentLoaded`, after the Select tool wiring from Task 4:

```javascript
  const spanButtons = Array.from(document.querySelectorAll(".analysis-span-button"));
  const chartCanvas = document.getElementById("sound-level-spectrum-chart");
  let spectrumChart = null;
  let activeSpan = "whole-file";

  function updateSelectionButtonAvailability() {
    const selectionButton = spanButtons.find((btn) => btn.dataset.span === "selection");
    selectionButton.disabled = currentSelection === null;
    if (currentSelection === null && activeSpan === "selection") {
      setActiveSpan("whole-file");
    }
  }

  function setActiveSpan(span) {
    activeSpan = span;
    spanButtons.forEach((btn) => {
      btn.setAttribute("aria-pressed", btn.dataset.span === span ? "true" : "false");
    });
    fetchAndRenderSpectrum();
  }

  spanButtons.forEach((btn) => {
    btn.addEventListener("click", () => setActiveSpan(btn.dataset.span));
  });

  function fetchAndRenderSpectrum() {
    if (activeView !== "analysis") return;
    const params = new URLSearchParams();
    if (denoiseOn) params.set("denoise", "true");
    if (activeSpan === "selection" && currentSelection !== null) {
      params.set("start_s", String(currentSelection.startTimeS));
      params.set("end_s", String(currentSelection.endTimeS));
    }
    const url = `${analysisPanel.dataset.spectrumUrl}?${params}`;
    fetch(url)
      .then((response) => response.json())
      .then((spectrum) => {
        const chartData = soundLevelSpectrumToChartData(spectrum);
        if (spectrumChart) {
          spectrumChart.data = chartData;
          spectrumChart.update();
        } else {
          spectrumChart = new Chart(chartCanvas, { type: "line", data: chartData });
        }
      });
  }
```

`analysisPanel.dataset.spectrumUrl` reads the `data-spectrum-url` attribute added to the
template in Step 3 above (`analysisPanel` itself is already declared in Task 3's Step 4).

Now update `setActiveView` (Task 3) to call `updateSelectionButtonAvailability()` and
`fetchAndRenderSpectrum()` when entering Analysis, and update `clearSelectionBox` (Task 4) to call
`updateSelectionButtonAvailability()` too:

```javascript
  function setActiveView(view) {
    // ... (unchanged body from Task 3) ...
    if (view === "analysis") {
      updateSelectionButtonAvailability();
      fetchAndRenderSpectrum();
    }
  }
```

```javascript
  function clearSelectionBox() {
    if (selectionBox) {
      selectionBox.remove();
      selectionBox = null;
    }
    currentSelection = null;
    viewLockToggle.disabled = false;
    updateSelectionButtonAvailability();
  }
```

(`updateSelectionButtonAvailability`/`setActiveSpan`/`fetchAndRenderSpectrum` must be declared
*before* `clearSelectionBox` and `setActiveView` in the file for this to work as plain function
declarations hoist, so their relative order doesn't actually matter in practice — but keep them
grouped together near the other Analysis-view code added in Task 3, for readability.)

- [ ] **Step 6: Add layout CSS**

Append to `src/fledermap/web/static/app.css`:

```css
.analysis-panel { display: flex; gap: 1rem; margin: 0.5rem 0; }
.analysis-graph { flex: 1; display: flex; flex-direction: column; gap: 0.4rem; }
.analysis-graph-controls { display: flex; gap: 0.4rem; }
.analysis-span-button[aria-pressed="true"] {
  background: var(--color-accent);
  color: var(--color-bg);
  border-color: var(--color-accent);
}
.analysis-graph-reserved {
  align-items: center;
  justify-content: center;
  color: var(--color-muted);
  border: 1px dashed var(--color-border);
  border-radius: 4px;
  padding: 1rem;
  text-align: center;
}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `hatch test tests/test_recording_detail_view.py -v`
Expected: PASS.

- [ ] **Step 8: Run the full fast test suite and mypy**

Run: `hatch test -m "not db"` and `hatch run types:check`
Expected: both pass.

- [ ] **Step 9: Run the full JS test suite**

Run: `node --test tests/js/`
Expected: PASS.

- [ ] **Step 10: Live-verify the whole Analysis view end-to-end**

Headless-Chrome check against the real running instance:
- Toggle to Analysis view — confirm the sound-level spectrum chart renders (a line from 0 to the
  recording's Nyquist frequency in kHz), the peak roughly matches what's visible in the
  spectrogram, and the reserved call-distance box shows its placeholder text.
- With no selection made, confirm "Whole file" is pressed and "Selection" is disabled.
- Switch to Playback, make a Select-tool selection, switch back to Analysis — confirm "Selection"
  is now enabled and clicking it re-fetches and redraws the chart over a narrower frequency
  content (harder to eyeball precisely, but confirm the network request actually carries
  `start_s`/`end_s` via the browser's network panel or a `console.log` added temporarily).
- Toggle Denoise on while in Analysis view, click "Whole file" again (or otherwise trigger a
  re-fetch) — confirm the request carries `denoise=true`.
- Switch back to Playback — confirm the selection overlay and View Lock's disabled state are both
  still exactly as they were before switching to Analysis.

- [ ] **Step 11: Commit**

```bash
git add src/fledermap/web/templates/recording_details.html src/fledermap/web/static/recording_detail.js src/fledermap/web/static/app.css tests/test_recording_detail_view.py
git commit -m "feat: render the sound-level spectrum in the Analysis view"
```

---

### Task 9: Final verification pass

**Files:** none (verification only).

**Interfaces:** none.

- [ ] **Step 1: Run the full test suite, including `db`-marked tests**

Run (unsandboxed, ~120s): `hatch test`
Expected: PASS, zero warnings (a warning is a defect per this project's `CLAUDE.md`).

- [ ] **Step 2: Run mypy**

Run: `hatch run types:check`
Expected: PASS, no `# type: ignore`.

- [ ] **Step 3: Run ruff**

Run: `hatch fmt`
Expected: no changes needed, or apply and re-verify tests still pass if it reformats anything.

- [ ] **Step 4: Run the full JS test suite**

Run: `node --test tests/js/`
Expected: PASS.

- [ ] **Step 5: Full headless-Chrome walkthrough against the real running instance**

Covering everything the per-task live-verification steps checked individually, once more as one
continuous session (catches interaction effects between tasks that isolated per-task checks might
miss):
- Toolbar zone widths stay visually fixed across every state change (view toggle, tool switch,
  Denoise on/off).
- Default/Ruler/Select/View Lock all behave exactly as they did before this plan, when Select
  isn't the active tool.
- Select → View Lock override → clear → View Lock restored, in both the plain-click and
  tool-switch clearing paths.
- View toggle preserves the Select-tool selection and View Lock's disabled state across a full
  Playback → Analysis → Playback round trip.
- Analysis view's Whole file/Selection control, Denoise interaction, and the reserved
  call-distance placeholder all render correctly.
- No emoji/dingbats anywhere in the new UI — every icon is the real inline SVG.

- [ ] **Step 6: Re-read the design spec once more and confirm no requirement was missed**

Open `docs/superpowers/specs/2026-09-18-fledermap-analysis-view-design.md` side by side with the
final diff (`git diff main`) and check off each numbered design section (§1-§7) and each Goals
bullet against what was actually built. Report any gap found rather than silently closing it out.

- [ ] **Step 7: Final commit (if any cleanup was needed in Steps 3-6)**

```bash
git add -A
git commit -m "chore: final verification pass for the Analysis view feature"
```

(Skip this commit entirely if nothing needed changing.)

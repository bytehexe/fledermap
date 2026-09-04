# Fledermap Recording Details Page — Tool Switching + Default/Ruler Tools — Design

**Status:** draft — sections approved individually in chat during brainstorming; awaiting the
user's review of this written spec (see brainstorming skill's user-review gate) before writing
an implementation plan.
**Date:** 2026-09-04

## Problem

The recording details page (2026-09-01 spec) shipped with exactly one interaction mode
hard-wired onto the spectrogram: click seeks+plays, native browser scroll pans, and a crosshair
readout follows the mouse. That spec explicitly deferred a tool-switching UI as premature ("a
toolbar with nothing to switch between"). Two things have since made it premature no longer:

- **Dragging looks broken.** Obsidian backlog: *"Details view: Dragging shows a drag cursor but
  does not drag (default tool)."* Root cause, found by reading `recording_detail.js`: pan was a
  deliberate decision to rely on native browser scrolling only (`// Pan is native browser
  scrolling -- nothing to build`) — no click-and-drag pan logic exists. What a user actually sees
  when they try to click-drag the spectrogram is the browser's own native `<img>`
  drag-and-drop ghost cursor (images are `draggable` by default), which does nothing useful here
  and reads exactly like a broken drag.
- **A second tool is wanted now.** Obsidian backlog: *"Implement more tools for the details page:
  Ruler, ..."*, with the tool's own open questions already logged: *"Ruler: Click = ?; drag =
  measure distance in kHz/ms."*

This spec covers both together rather than fixing pan in isolation and reworking it into a tool
system later: introduces the toolbar the 09-01 spec deferred, with two tools — **Default**
(today's click-to-play + crosshair, plus the pan-by-drag that was missing) and **Ruler** (new).

## Goals

- A small toolbar above the spectrogram with two mutually-exclusive tool buttons: **Default**
  (active on page load) and **Ruler**. Built to hold more tools later (batdetect2 overlays etc.,
  per backlog) without restructuring.
- A shared drag-vs-click state machine on the spectrogram wrap, so every tool gets the same
  "was this a click or a drag" distinction for free, and the tile `<img>`s stop hijacking the
  gesture into a native image drag.
- **Default tool:** click-to-play unchanged; drag now actually pans (`scrollLeft` follows the
  cursor, "grab" convention); crosshair readout unchanged.
- **Ruler tool:** drag draws a live rubber-band measurement (Δt in ms **and** a derived
  pulse-rate reading in Hz, Δf in kHz) between the drag start and current position; the finished
  measurement freezes in place after mouseup and stays visible; a plain click (no drag) clears
  it.

## Non-goals

- **No persistence of the active tool or any measurement** across a page reload/navigation —
  in-memory JS state only, same lifetime as the rest of this page's interaction state (e.g. the
  playback cursor).
- **No keyboard shortcuts, no touch/pointer-event support beyond mouse.** The existing crosshair
  and click-to-play are mouse-only today; this doesn't change that scope.
- **No batdetect2 overlays, no sound-level-spectrum panel, no HET controls.** Still their own
  future specs, per the 09-01 spec's own non-goals.
- **No vertical measurement snapping/alignment aids, no unit toggle (ms vs. s, kHz vs. Hz).**
  Fixed display units as specified below; revisit only if it turns out to matter in practice.

## Design

### 1. Toolbar markup and state

`recording_details.html` gains a button row between the metadata `<p>` and `.detail-scroll`:

```html
<div class="detail-toolbar" id="detail-toolbar">
  <button type="button" class="tool-button" data-tool="default" aria-pressed="true">Default</button>
  <button type="button" class="tool-button" data-tool="ruler" aria-pressed="false">Ruler</button>
</div>
```

`recording_detail.js` tracks `activeTool` (`"default"` or `"ruler"`), toggles `aria-pressed` and
an `.active` class on click, and swaps a class on `.detail-spectrogram-wrap`
(`tool-default`/`tool-ruler`) for CSS cursor styling. Switching tools clears any in-progress or
frozen ruler measurement (simplest correct behavior — a stale measurement under a different
tool's cursor styling would be confusing).

### 2. Shared drag-vs-click plumbing

Replaces the existing bare `click`/`mousemove` listeners on `wrap` with a small state machine,
still on the same element:

- `mousedown`: record `{x: event.clientX, y: event.clientY, scrollLeft: scrollEl.scrollLeft}` as
  `dragStart`; set `dragging = false`.
- `mousemove` (while a button is held, i.e. `dragStart` is set): if
  `Math.hypot(event.clientX - dragStart.x, event.clientY - dragStart.y)` exceeds a **4px**
  threshold, set `dragging = true`. Once `dragging`, call the active tool's `onDrag(event,
  dragStart)` every move.
- `mouseup`: if `dragging`, call the active tool's `onDragEnd()`; otherwise call `onClick(event)`.
  Clear `dragStart`.
- The existing crosshair `mousemove` listener (position readout) stays a separate, always-on
  listener — unaffected by which tool is active or whether a drag is in progress, same as today.

Each tile `<img>` gains `draggable="false"` in the template — this alone removes the native
ghost-drag cursor that is the literal symptom in the backlog report. (Verified: `app.css` defines
no `grab`/`grabbing` cursor today, so the cursor a user currently sees is unambiguously the
browser's native image-drag affordance, not anything this app draws.)

### 3. Default tool

- `onClick(event)`: exactly today's logic (seek `<audio>` to the clicked x-position, converted
  through `pxPerMs`/`timeExpansionFactor`, then `.play()`).
- `onDrag(event, dragStart)`: `scrollEl.scrollLeft = dragStart.scrollLeft - (event.clientX -
  dragStart.x)` — content follows the cursor (drag right reveals earlier content), the standard
  "grab pan" convention also used by map/DAW tools. Vertical drag is a no-op (`.detail-scroll` is
  horizontal-only; per the still-open backlog item about eliminating its vertical scrollbar
  entirely, out of scope here).
- `onDragEnd()`: no-op, the scroll position is already wherever the last `onDrag` left it.
- Cursor: `.detail-spectrogram-wrap.tool-default { cursor: grab; }`, and a `.dragging` class
  (added on `mousedown`, removed on `mouseup`) sets `cursor: grabbing`.

### 4. Ruler tool

- `onClick(event)` (no drag occurred): clear the current measurement, if any (hide/remove its
  overlay element).
- `onDrag(event, dragStart)`: draw/update a single overlay `<div class="ruler-box">` positioned
  absolutely within `wrap`, spanning the rectangle from `dragStart` to the current
  `(event.clientX, event.clientY)` (translated to wrap-relative coordinates the same way the
  crosshair already does). A label inside or beside the box shows two lines:

  ```
  Δt: 12.34 ms (81.1 Hz)
  Δf: 23.4 kHz
  ```

  - `Δt` and `Δf` are absolute differences (`Math.abs`) between start and current position, using
    the exact same `xPx / pxPerMs / 1000` / `maxFreqKhz - yPx / pxPerKhz` conversions the
    crosshair already uses — same units, same precision conventions (3 decimals for seconds
    today; ms here reads better as the crosshair's `timeS * 1000` with 2 decimals, matching the
    “12.34 ms” example above).
  - The Hz reading is the derived pulse-repetition-rate reading requested: `1000 / Δt_ms`. Bat
    literature commonly reports pulse-repetition-rate this way (inter-pulse interval → Hz), the
    same relationship as note-duration → BPM. Guarded against `Δt_ms === 0` (a purely vertical
    drag): render `—` instead of dividing by zero.
- `onDragEnd()`: no-op — the box drawn by the last `onDrag` call is already the frozen final
  state; nothing further to do until the next click (clear) or drag (redraw).
- Cursor: reuse the existing `cursor: crosshair` already set on `.detail-spectrogram-wrap` —
  appropriate for a measuring tool, no new cursor needed.

### 5. Files touched

- `src/fledermap/web/templates/recording_details.html` — toolbar markup; `draggable="false"` on
  both tile `<img>` loops (spectrogram and oscillogram — the oscillogram wrap isn't part of the
  drag/tool surface, but leaving its images natively draggable would be an inconsistent, easily
  forgotten gap).
- `src/fledermap/web/static/recording_detail.js` — the drag-vs-click state machine; `defaultTool`
  and `rulerTool` objects implementing `onClick`/`onDrag`/`onDragEnd`; toolbar button wiring.
- `src/fledermap/web/static/app.css` — `.detail-toolbar` + `.tool-button` styling (reuse existing
  button conventions), `.ruler-box` overlay styling, `grab`/`grabbing` cursor classes.

### 6. Testing

This project has no JS test infrastructure anywhere (checked before writing this spec, not a gap
introduced here) — every existing interactive behavior on this page (crosshair, click-to-play,
the playback cursor) is likewise untested by an automated JS suite. Consistent with that:

- Python route-level tests (`tests/test_recording_detail_view.py`) cover what the server
  actually renders: the toolbar buttons are present with correct initial `aria-pressed` state,
  and `draggable="false"` is present on tile `<img>` elements.
- Interactive behavior (pan, ruler drag, click-to-clear) is verified live against the real
  running instance before this is considered done, the same way the back-link fix was verified
  (pipx reinstall from this checkout + service restart), now using the Chrome extension the user
  has since connected.

## Open questions

None outstanding — the two decisions flagged during brainstorming (ruler's plain-click behavior,
and whether a finished measurement persists) were resolved in chat: click clears, and a finished
measurement stays visible until cleared or redrawn.

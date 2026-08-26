# Fledermap Phase 5a (Recording & Site Detail Drawer) — Design

## 1. Scope

The first slice of Phase 5 per the parent spec's phasing table (`docs/superpowers/specs/2026-08-23-fledermap-design.md`
§15: "Recording and site drawers, sessions list with notes and merge proposals, job
status strip"), decomposed deliberately rather than built as one unit — that phase
bundles a drawer, four standalone views, and a job status strip, more than one
implementation slice's worth of work. This slice is **only** the bottom drawer and its
two recording/site detail panels. Ordering and rationale for the remaining slices
(job status strip, `/sessions`, then the remaining read-only views) are a
brainstorming-session decision, not re-litigated here.

**Supersedes P4-8.** Phase 4 shipped "marker click opens a native Leaflet popup...
no drawer (that's Phase 5)" as a deliberate placeholder. This phase replaces that
popup outright — clicking a recording or site marker now opens the drawer instead,
per §9 of the parent spec ("Same HTMX target, two fragments").

## 2. Module layout

Two new fragment routes, matching the parent spec's endpoint names exactly:

```
GET /recordings/{hash}/panel   -> renders the recording panel fragment
GET /sites/{id}/panel          -> renders the site panel fragment
```

Both live in `web/views/map.py` alongside the existing map view (small enough not to
justify a new module yet; revisit if `/sessions`'s own view code makes this file grow
past a comfortable size). Two new templates, `_recording_panel.html` and
`_site_panel.html`, under `web/templates/` — fragments, not full pages, matching the
existing GeoJSON-endpoint pattern of "no `<html>` wrapper, HTMX swaps it straight in."

The drawer's chrome (header, resize handle, collapse/close controls, the empty body
that HTMX targets) lives in `map.html` itself, always present in the DOM and hidden by
default — not templated per-panel, since it's identical for both panel types.

## 3. Drawer chrome and interaction states

Three states, owned by Alpine (`x-data` on the drawer container, matching Phase 4's
existing split of "Alpine owns UI state, vanilla JS owns Leaflet, HTMX owns
fragments"):

- **Closed** (default): drawer hidden, body empty.
- **Open**: full drawer visible at its last-dragged height (or a sensible default on
  first open), body holds the current panel's fragment.
- **Collapsed**: drawer shrunk to a thin header-only bar; the fetched panel content
  stays in the DOM (a pure CSS/Alpine state toggle, no re-fetch on re-expand).

Header controls: a collapse chevron (Open ⇄ Collapsed, selection retained) and a close
`×` (→ Closed, clears both the state and the drawer body's HTML so nothing stale
flashes on the next open). Both controls are kept — cheap to build (one boolean, one
button each), so dropping either would be a UX simplification, not an effort saving,
and this phase keeps both.

A full-width drag handle sits on the drawer's top edge for the resize (spec: "full
width, drag-resizable"). No history — this is deliberately **one drawer, one active
selection**, matching the parent spec's singular "a bottom drawer." Navigating (a new
marker click, or prev/next inside the panel) replaces the current selection in place;
nothing is minimized to the side to return to later. A "recently viewed" stack was
considered and explicitly deferred (§6).

## 4. Recording panel

Per parent spec §9: full-width spectrogram, then three columns (Identifications
widest — all sources, superseded rows struck through; Recording metadata; Context —
session, site, previous/next in time).

**Audio playback gets its own row, below the spectrogram, above the three columns**
(mockup-validated choice over overlaying transport controls on the spectrogram
itself). Chosen over the overlay specifically because it leaves room to grow: a
future auto-heterodyne / manually-tuned-heterodyne playback mode (mentioned as
likely, not built here) needs its own controls (at minimum a tuning value, plausibly
a toggle), and a dedicated row can absorb that as a small toolbar later without
fighting for space on top of spectrogram colors. Building those modes is explicitly
out of scope for this slice (§6) — the row is sized and positioned to make room for
them, nothing more.

**Prev/next** (Context column) steps through the **currently filtered set** — same
date range, taxon, verdict, session, and source filters active on the map — ordered
by `recorded_at`, so the drawer never lands on a recording that isn't part of what
you're actually looking at. Deliberately **excludes `bbox`**: that filter follows the
viewport, and since prev/next already pans the map to the neighbor (below), computing
"next" against the pre-pan viewport would fight with that pan — a recording could be
correctly "next" by every semantic filter and still get excluded because the map
hadn't panned to it yet. **Stops at either end** rather than wrapping — "next" past
the last recording would jump backward to the earliest one, which reads as a bug for
a control framed as "in time," not a feature; the button disables/hides once its
respective end is reached.

Mechanically: opening the drawer (marker click, or navigating prev/next) issues an
`hx-get` carrying the same filter querystring shape already used by the GeoJSON
endpoints (`date_from`/`date_to`/`taxon`/`verdict`/`session_id`/`source`, no `bbox`)
against the target recording's panel URL, targeting the drawer body. The panel route
re-runs `filtered_recordings` with those params to locate the current recording's
neighbors for rendering the prev/next controls (and to 404 into the not-found fragment,
§7, if the recording itself no longer matches — e.g. a filter changed while the drawer
was open). The response also carries the shown recording's `{hash, latitude,
longitude}` via an `HX-Trigger` response header, which `app.js`'s existing marker/pan
code listens for as a `recording-selected` DOM event to pan the map and highlight the
marker. This keeps ownership of Leaflet entirely in `app.js` (per Phase 4's own
established split) rather than teaching HTMX a second way to touch the map.

## 5. Site panel

No spectrogram, no audio row — header, then the three columns per parent spec §9:
species breakdown, site stats (poiidx name + admin path — currently the Phase 4
rounded-coordinate fallback per P4-1, until poiidx naming ships), sessions that
touched this site.

The "show only this site" action (parent spec: "the drawer carries a 'show only this
site' action") sits in the panel header next to the title. Clicking it drives the
*existing* session/site filter state already wired up in Phase 4 (the same
Alpine-bound filter form) — no new filtering code path. This is the only control in
the drawer that changes the map's filters; every other interaction (opening,
collapsing, closing, navigating prev/next) leaves filters untouched, per the parent
spec's "clicking always means show me this, filtering stays deliberate."

## 6. Explicitly out of scope (this slice)

- The job status strip, `/sessions`, `/recordings/{hash}` as a standalone (non-drawer)
  page, `/taxa`, `/sites` — later Phase 5 slices, decomposed and ordered in the
  brainstorming session that produced this doc.
- A minimized-history stack of previously-viewed recordings — considered directly
  (in response to "does swap imply multiple drawers, or at least minimized ones?"),
  explicitly declined in favor of the single-drawer model in §3. Revisit only if the
  single-selection model turns out to be missed in practice.
- Auto-heterodyne / manually-tuned-heterodyne playback modes — the audio-player row's
  position (§4) is chosen to leave room for these, but no such control is built here.
- Graceful degradation for missing media (spectrogram/preview not yet rendered) is
  handled by this panel (§7), but the *visibility* of job progress generally is the
  job-status-strip slice's job, not this one's.

## 7. Error handling

- **Recording/site not found** (bad id, or a recording swept as missing between the
  map fetch and the panel fetch): the fragment route returns a small in-drawer "not
  found" fragment — HTTP 200 with drawer-appropriate content, not a bare HTTP error
  left for HTMX's default (invisible) error handling to swallow.
- **Media not yet rendered** (spectrogram/preview job hasn't run, or failed): the
  recording panel still renders fully, substituting a "not processed yet" placeholder
  for the missing spectrogram image and/or audio player rather than a broken
  image icon or a silently absent player. This panel must degrade gracefully today,
  independent of whichever later slice makes job status visible elsewhere.

## 8. Testing

View tests for both new fragment routes, following `test_map_view.py`'s existing
pattern: normal rendering (recording with full media, site with populated columns),
the not-found case, and the media-missing degraded case for the recording panel.

No JS test framework — consistent with Phase 4's explicit choice (parent design doc
§9, "No JS test framework introduced"). The interactive pieces (collapse/close,
prev/next swap-and-pan, drag-resize, the "show only this site" filter action) get
manual verification via the `run` skill, matching how Phase 4's own Alpine/Leaflet
interactions were verified.

## 9. Decisions

| ID | Decision |
|---|---|
| P5a-1 | This slice is the drawer only; job status strip / `/sessions` / remaining views are separate, later slices (decomposition decided in brainstorming, not re-derived here). |
| P5a-2 | Marker click (recording or site) opens the drawer, replacing Phase 4's native Leaflet popup outright (supersedes P4-8). |
| P5a-3 | One drawer, one active selection, no minimized-history stack. Navigating replaces the current selection in place. |
| P5a-4 | Drawer keeps both collapse (retains selection) and close (clears selection) as separate controls — cheap to build, so kept for UX rather than dropped for effort. |
| P5a-5 | Recording panel's audio player is a separate row below the spectrogram, not overlaid on it — chosen to leave room for a future heterodyne-playback toolbar, not built in this slice. |
| P5a-6 | Prev/next swaps the drawer's content and pans/highlights the map, via an `HX-Trigger` header consumed as a DOM event by `app.js` — keeps Leaflet ownership solely in `app.js`, matching Phase 4's established split. |
| P5a-7 | "Show only this site" drives the existing Phase 4 filter state; no new filtering code path. |
| P5a-8 | Missing media (spectrogram/preview not yet rendered) degrades in-panel with a placeholder; not blocked on the separate job-status-strip slice. |
| P5a-9 | Prev/next steps through the current semantic filters (date/taxon/verdict/session/source) ordered by `recorded_at`, deliberately excluding `bbox` since it's viewport-following and would fight with prev/next's own pan-to-neighbor behavior. |
| P5a-10 | Prev/next stops at either end of the filtered sequence rather than wrapping — wrapping would make "next" jump backward in time. |

## 10. Correction found while writing the implementation plan

**P5a-7 overstated what Phase 4 built.** It claims "show only this site" drives
"the *existing* session/site filter state already wired up in Phase 4... no new
filtering code path." Checked against the actual code (`services/map_query.py`,
`web/api/geojson.py`, `web/templates/map.html`) while writing
`docs/superpowers/plans/2026-08-26-fledermap-phase5a-drawer.md`: Phase 4 built a
`session_id` filter, but no `site_id` filter exists anywhere — not on
`filtered_recordings`/`filtered_sites`, not as a query param, not as a form field.
The intent behind P5a-7 stands (site filtering should share the same
form/query/service-layer shape every other filter uses, not a bespoke mechanism);
only the "already exists" claim was wrong. The implementation plan's Task 2 builds
it, following the `session_id` filter's exact existing pattern.

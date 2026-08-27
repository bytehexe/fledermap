# Fledermap Phase 5b (Sessions List + Detail) — Design

## 1. Scope

The second slice of Phase 5 per the parent spec's phasing table (`docs/superpowers/specs/2026-08-23-fledermap-design.md`
§15: "Recording and site drawers, sessions list with notes and merge proposals, job
status strip"). Phase 5a shipped the drawer (recording/site panels). This slice is
`/sessions` (list) and `/sessions/{id}` (detail) — per the parent spec's route list
(§9): "`/sessions` + detail (edit kind and notes, resolve merge proposals)".

Ordering rationale (decided in the brainstorming session that produced this doc,
against the remaining Phase 5 pieces — job status strip, this slice, `/recordings/{hash}`
standalone, `/taxa`, `/sites` read-only): `/sessions` goes first because it is the
only remaining piece that is a *workflow* (edit + resolve) rather than a read-only
view or a status widget, and because the data it acts on has been sitting ready
the longest.

**Grown during review** (still pre-implementation) to add four pieces that belong
with this slice rather than a later one: a small per-session map on the detail
page (§7), a real persisted auto-classification of `kind` at derivation time (§6),
dropping the never-used `effort` column outright (§6), and the app's first piece
of global navigation (§8) — `/sessions` is also the app's first standalone page
ever, so something has to let a person reach it. §6 in particular needed two
rounds of correction against an initial "non-persisted UI suggestion" framing that
turned out to have a real bug (below).

## 2. Module layout

Two new routes, matching the parent spec's endpoint naming:

```
GET  /sessions                          -> list, with filters
GET  /sessions/{id}                     -> detail (view + edit form + merge banner)
POST /sessions/{id}                     -> save kind/note/weather
POST /sessions/merge-proposals/{id}/resolve  -> accept (merged) or reject a proposal
```

New module `web/views/sessions.py` (this is a standalone-page workflow, not a map
fragment — doesn't belong in `web/views/map.py`, which phase5a already flagged as
close to a comfortable size). New service functions in a new
`services/sessions.py`: `filtered_sessions(...)` (list query, mirrors
`filtered_recordings`'s shape and reuses `web/params.py`'s shared query-param
parsing from 5a) and `resolve_merge_proposal(...)` (the merge/reject transaction,
§5). Two new full-page templates, `sessions_list.html` and `session_detail.html`,
following the same page-shell pattern as `map.html` (not fragments — `/sessions` is
a first-class standalone view per the parent spec's view list, unlike the drawer
panels). A new shared template partial, `_nav.html` (§8), included by `map.html`
and both new templates.

No new API routes needed for the mini-map (§7) or the "view on full map" link
(§7): `/api/recordings.geojson?session_id=` and the map page's own `?session=`
query param both already exist from Phase 4.

**Schema changes** (one new migration, autogenerate-then-verify against
`tests/test_migrations.py` — plain column add/drop, no enum/CHECK involved so no
hand-written DDL needed unlike the `Verdict`/`kind`-CHECK cases elsewhere in this
project):

```
session
  - effort           -- dropped (§6): never written by any code, no domain
                         definition exists anywhere in the repo or docs/references.md
  + kind_locked  bool NOT NULL DEFAULT false   -- true once a human saves kind
                                                   through the edit form (§6)
```

**`derive/sessions.py` changes** (§6): all four places `partition_sessions`
assigns a recording to a session — fresh overlap-join, forward-extend,
backward-extend, and creation — need to (re)classify that session's `kind` from
its *complete* current set of GPS-bearing recordings (a fresh query by
`session_id`, not just the recordings in the current batch, since earlier `derive`
runs may have already assigned others), unless `kind_locked` is set. A new
`Config.transect_distance_m` field (following `site_eps_m`'s exact existing
shape: env `FLEDERMAP_TRANSECT_DISTANCE_M`, TOML key `transect_distance_m`,
default `150.0`) supplies the threshold — this is real derivation logic, unlike
the discarded UI-only suggestion, so it earns the same operational-tuning
treatment as `site_eps_m`/`session_gap_hours` rather than a code constant.

`resolve_merge_proposal` (§5) also reassigns recordings between sessions —
`session_a` gains every one of `session_b`'s recordings — so it re-runs the same
reclassification for `session_a` (respecting `session_a.kind_locked`) after the
reassignment, for the same reason: a session's recording membership changed, and
that's the trigger for reclassifying, regardless of which code path caused it.

Two small cross-links ride along, serving this slice's own navigation into content
Phase 5a already built:

- `_site_panel.html`'s session list (currently plain text, `{{ s.started_at... }}`)
  becomes `<a href="/sessions/{{ s.id }}">`.
- `_recording_panel.html`'s session line (currently plain text) becomes the same
  kind of link.

## 3. List view (`GET /sessions`)

Table, newest-first by `started_at`. Columns: date range (`started_at`–`ended_at`),
detector (`detector_key`), kind, recording count, and a merge-proposal indicator — a
badge linking straight into the resolution UI (§5) when the session is `session_a`
or `session_b` of an open (`resolution IS NULL`) proposal.

Filters, in the same bar convention the map page established: detector (substring
match on `detector_key`), date range (`from`/`to` against `started_at`, reusing
`web/params.py`), and an "open merge proposals only" checkbox. `kind` is shown as a
column but not filterable this slice — cheap to add later if it turns out to matter,
YAGNI for now.

No pagination machinery. Real deployments here are a handful of detectors, not
thousands of sessions — a simple `LIMIT 200` (newest-first, same idea as the map's
"feature count capped" per parent spec §9) stands in. Revisit if a real dataset ever
approaches the cap.

## 4. Detail view (`GET /sessions/{id}`)

Layout (desktop width; columns stack on narrow screens, matching the drawer
panels' existing convention):

```
+--------------------------------------------------------------+
| ← Back to sessions                                            |
|                                                                |
| Session: 2026-08-26 20:15 – 23:40   Detector: EMT2-04A2C1     |
|                                                                |
| +----------------------+  +--------------------------------+ |
| |                      |  | Kind:    [Stationary v]           | |
| |    mini map (§7)     |  | Note:    [_______________________]| |
| |                      |  |          [_______________________]| |
| |  [View on full map]  |  | Weather: [_______________________]| |
| |                      |  |                          [Save]  | |
| +----------------------+  +--------------------------------+ |
|                                                                |
| ⚠ This session may merge with session #142 (20:10–20:15 gap)  |
|   [ view combined note/weather → Accept / Reject ]            |
|                                                                |
| Recordings in this session (14)                               |
|  2026-08-26 20:15   Pipistrellus pipistrellus                 |
|  2026-08-26 20:18   No ID                                     |
|  ...                                                           |
+--------------------------------------------------------------+
```

Four sections:

- **Mini map** (§7): the session's recording locations, plus a link back to the
  full map filtered to this session.
- **Edit form** (`POST /sessions/{id}`): `kind` dropdown, `note`/`weather`
  textareas, single Save button. The dropdown always shows the session's actual
  current `kind` — the real, persisted value from derivation-time classification
  (§6), never a phantom guess that could differ from what's stored — so saving
  the form to change only `note`/`weather` is a true no-op on `kind`. Submitting
  this form at all (whether or not the dropdown's value was changed) sets
  `kind_locked = true`: once a human has looked at and confirmed a session's
  `kind` through this form, it stops being auto-reclassified by future `derive`
  runs, permanently.
- **Merge-proposal banner** (§5), shown only when this session is part of an open
  proposal.
- **Recordings in this session**: timestamp and current best identification, listed
  plain (unlinked). Linking each row to its own recording view is deferred
  deliberately: neither a standalone `/recordings/{hash}` page nor any map
  deep-linking exists yet, and adding deep-linking to `map.html` just to serve this
  list would pull scope from a later slice into this one. Revisit once
  `/recordings/{hash}` ships.

## 5. Merge resolution

The banner appears on **both** affected sessions' detail pages (a human might land
on either one first) and shows both sessions' current `note`/`weather` side by
side, pre-filled into a single editable combined-text area per field — not
auto-concatenated, per the parent spec's own warning ("silently concatenating field
notes is data loss noticed months later"). Two buttons:

- **Accept merge** → `POST /sessions/merge-proposals/{id}/resolve` with the
  human-edited combined text. In one transaction: reassign every `session_b`
  recording's `session_id` to `session_a`; extend `session_a.started_at`/`ended_at`
  to span both; write the combined `note`/`weather` onto `session_a`;
  reclassify `session_a.kind` per §2/§6 (respecting its own `kind_locked`); delete
  `session_b`; set `resolution='merged'`, `resolved_at=now()` on the proposal.
- **Reject** → same route, no text payload needed: set `resolution='rejected'`,
  `resolved_at=now()`, change nothing else.

Resolved from either page, always ending up as one shared route/transaction — no
duplicate merge logic per page.

**Edge case: chained proposals.** A bridging recording could in principle connect a
session that is *already* `session_a` or `session_b` of a second, still-open
proposal. `session_merge_proposal.session_a_id`/`session_b_id` carry no `ON DELETE`
clause, so Postgres rejects the `DELETE FROM session WHERE id = :session_b_id` with
an FK violation rather than silently orphaning the other proposal's foreign key.
`resolve_merge_proposal` catches this and turns it into a clean user-facing error
("resolve the other pending proposal on this session first") rather than letting a
raw `IntegrityError` surface as a 500.

**Correction found during implementation (Task 6).** This edge case as originally
written was incomplete in a way that mattered a lot more than it let on: the FK
hazard above isn't only a *second, chained* proposal's problem — the proposal
*being resolved* also references `session_b` via its own `session_b_id`, and that
reference is still live at the moment `session_b` would be deleted. Without
repointing it first, `DELETE FROM session WHERE id = :session_b_id` fails on
**every** merge, not just a chained one — the implementer found this via TDD when
the plan's own basic single-proposal merge test failed. The fix: `resolve_merge_proposal`
repoints the resolving proposal's own `session_b_id` to `session_a.id` *before*
deleting `session_b`, so only a genuinely separate, still-open proposal can still
raise the chained-proposal `MergeConflictError` this section describes. Verified
safe by grepping every reader of `session_a_id`/`session_b_id` in the codebase:
nothing reads a *resolved* proposal's `session_b_id` expecting it to still equal
the deleted session's id (`open_proposal_session_ids`/`session_detail` both filter
on `resolution IS NULL` already, so a resolved row's repointed value is never
surfaced as if it were still open).

## 6. Kind: persisted auto-classification, and dropping `effort`

**`effort` is dropped outright**, not carried into this slice's edit form. Grepped
every reference in the repo before deciding: the *only* code that touches it is
the model column declaration itself (`store/models.py:196`) — no service, no
view, no test, no migration test asserts it, and no data was ever written to it.
`docs/references.md` (this project's own authority for domain definitions) has
nothing on it either; the only trace anywhere is "note, weather, effort" listed
together in the original parent-spec schema sketch with no further elaboration.
Rather than build UI for a field whose intended meaning nobody can currently
state, it's removed. (`weather` is kept — its meaning is self-evident for a bat
survey: rain and wind suppress activity, so conditions during a session are
meaningful annotation context. Not flagged for removal, noted here only so the
boundary is explicit rather than silently assumed.)

**`kind` classification is real and persisted**, computed by `derive/sessions.py`
(§2) rather than guessed non-destructively in the UI — a UI-only suggestion was
this doc's first draft, and turned out to have a real bug: pre-filling the edit
form's dropdown with a computed guess that could differ from the actually-stored
value meant an incidental save (e.g., just adding a note) could silently persist
a `kind` the human never chose. Persisting the classification at derivation time
means the form always reflects the true stored value, so an incidental save is
never destructive.

The classifier itself: the maximum pairwise distance among a session's
GPS-bearing recordings, projected via the same `LocalProjection` Phase 2 already
uses for site clustering (so the threshold is in metres, not degrees) — above
`Config.transect_distance_m` suggests `TRANSECT`, at or below suggests
`STATIONARY`. Zero or one GPS-bearing recording (not enough signal) leaves it at
`STATIONARY`, matching today's existing default. This runs every time a
recording is newly assigned to a session — in any of `partition_sessions`'s four
join/create paths, and in `resolve_merge_proposal` — **unless `kind_locked` is
set**, which happens exactly once a human saves the session detail form (§4),
regardless of whether they actually changed the dropdown's value. After that, no
automatic process touches `kind` again; only another explicit save can change it.

This is real, reviewed Phase 2 derivation code being reopened, not a UI nicety —
called out explicitly as the reason this piece grew past "seed a form field" into
its own section. The alternative (a non-persisted, non-locking UI-only guess) was
considered and rejected for the correctness reason above, not for being smaller;
see the git history of this document for the discarded draft.

## 7. Session mini-map

A small Leaflet map on the detail page (§4's layout), fetching
`/api/recordings.geojson?session_id={id}` — an endpoint that already exists,
built in Phase 4. Plain recording markers only: no polyline connecting them (the
parent spec's v1-excludes list already rules out transect track rendering) and no
site circle overlay (a session's own recordings are the point, not the site they
happen to belong to). Below the map, a "View on full map" link to
`/?session={id}` — also free: the map page already reads a `session` query param
on load (Phase 4, `web/views/map.py`), so this is a plain link, not new code.

This also sets up the natural extension point once KML ingest exists: the same
widget gains a track-polyline layer fed by the session's KML data, without
changing its role on the page. (KML ingest itself is not part of this slice —
§9.)

**Correction found during implementation (Task 10):** the claim above that "the
map page already reads a `session` query param on load" is wrong. Phase 4 built
`/api/recordings.geojson?session=` — the *API* reads that param, server-side,
per fetch — but `views_bp.get("/")` (`web/views/map.py`'s `map_page`) reads no
query args at all, and `app.js`'s `filterForm()` (the Alpine state backing the
filter bar, including the `session` `<select>`) always initializes `session: ""`
regardless of the page's URL. Nothing connects an incoming `?session={id}` on
`/` to the filter form or triggers a fetch with it. The "View on full map" link
was therefore dead on arrival: it would load the unfiltered map. Fixed as part
of Task 10 by seeding `filterForm()`'s initial `session` value from
`URLSearchParams(window.location.search)` — Alpine's `x-model` then reflects it
into the `<select>` on init, and the existing `refresh()` call (already reading
the form via `FormData`) picks it up on the very first fetch. No other filter
field had this gap because nothing else links to `/` with a pre-set filter.

## 8. Global navigation

The app's first standalone page (`/sessions`) needs a way to reach it that isn't
buried in a drawer cross-link. A **collapsible left sidebar** (Map, Sessions;
room to grow into the later Phase 5 slices — job status strip, `/taxa`, `/sites`),
included via `_nav.html` on `map.html` and both new session templates.

Collapsible specifically because a persistent sidebar trades away exactly the
thing Phase 5a's drawer placement was chosen to protect: that design explicitly
picked a *bottom* drawer over a side one "because a bottom drawer keeps the map's
width — the axis you pan across." **Expanded by default** on normal widths —
unlike the drawer, the sidebar is small and this project has no other standing
nav, so there's no established width pressure to default against yet; collapsing
by default would just make `/sessions` harder to find the first time. It
auto-collapses only below a responsive breakpoint (phone/narrow-viewport width,
matching the "columns stack on narrow screens" convention Phase 5a's panels
already use), where screen width is the scarce resource and the map genuinely
needs it back. A manual collapse toggle remains available at any width for
someone who wants the space back on desktop too.

## 9. Explicitly out of scope (this slice)

- The job status strip, `/recordings/{hash}` standalone, `/taxa`, `/sites` — later
  Phase 5 slices (though the sidebar from §8 already has room reserved for their
  nav entries).
- A separate merge-proposals index page. Open proposals are reachable via the list's
  "open merge proposals only" filter and via the badge on each affected session's own
  row/detail page — no additional listing surface.
- Real pagination for the sessions list (§3) — a capped `LIMIT` stands in.
- `kind` as a list filter (§3) — shown as a column only.
- Linking the detail page's per-recording rows to their own recording view (§4) —
  no target exists yet (`/recordings/{hash}` standalone is a later slice; map
  deep-linking doesn't exist). Rows are listed plain until then.
- KML ingest itself, and any track-polyline rendering (§7) — noted as a natural
  extension point, not built here. The classifier in §6 is deliberately the
  audio-only interim signal, not a placeholder waiting on KML — it stands on its
  own and is expected to keep working (as a fallback, or for detectors with no
  accompanying KML) after KML ingest exists.

## 10. Error handling

- **Session not found** (`/sessions/{id}` for a bad id): standard 404 page — this is
  a full standalone view, not an HTMX fragment target, so there's no drawer-style
  "in-place not-found fragment" concern the way Phase 5a's panels had.
- **Merge-proposal FK conflict** (§5): caught and surfaced as a clean in-page error
  message, not a raw 500.
- **Concurrent resolution** (two people/tabs resolve the same proposal): the second
  `POST` finds `resolution` already set and returns a clean "already resolved by
  someone else" message rather than double-applying the merge.
- **Session with no (or exactly one) GPS-bearing recording** (§6): the classifier
  has no basis to suggest `TRANSECT` — stays `STATIONARY` (today's existing
  default), not an error. Not locked by this alone; a later recording arriving
  with GPS can still reclassify it, until a human saves the form.
- **Mini map with no GPS-bearing recordings** (§7): renders with no markers rather
  than erroring — same "degrade in place" convention Phase 5a used for missing
  media.

## 11. Testing

View tests for both new routes following `test_map_view.py`'s existing pattern:
list rendering with each filter, detail rendering with and without an open
proposal, the edit form's save path (including that it sets `kind_locked`), and
the not-found case.

`derive/sessions.py` tests (extending `test_partition_sessions.py`): classification
crossing the threshold in each of the four join/create paths, a locked session's
`kind` surviving a `derive` run that would otherwise reclassify it, and the
zero/one-GPS-recording fallback to `STATIONARY`. `Config.transect_distance_m` gets
its own `Config.from_env` test asserting the constructed `Config`'s attribute
value (this project's own documented gotcha: parsing without asserting the final
attribute has silently dropped a field on the floor before).

Service tests for `resolve_merge_proposal` covering: normal merge (recordings
reassigned, `session_b` deleted, timestamps extended correctly, `session_a.kind`
reclassified when unlocked and left alone when locked), reject (nothing but
`resolution`/`resolved_at` changes), the chained-proposal FK conflict, and
concurrent double-resolution.

`tests/test_migrations.py` covers the new migration (column add/drop, no
CHECK/enum involved so no special-case exclusion needed).

Database tests via testcontainers + PostGIS, matching the project's existing
convention — both the reclassification logic and `resolve_merge_proposal`'s
reassignment/delete/extend logic need a real transaction to verify against, not a
mock.

No JS test framework, consistent with Phase 4/5a's established choice — the mini
map and sidebar collapse/expand get manual verification via the `run` skill.

## 12. Decisions

| ID | Decision |
|---|---|
| P5b-1 | This slice is `/sessions` list + detail, plus a session mini-map, persisted kind classification, and global nav; job status strip / `/recordings/{hash}` standalone / `/taxa` / `/sites` / KML ingest are separate, later slices. |
| P5b-2 | `/sessions` and `/sessions/{id}` are full standalone pages, not HTMX drawer fragments — matches the parent spec treating `/sessions` as a first-class view, distinct from the map's drawer. |
| P5b-3 | `kind` and `note`/`weather` are edited together in one form on one save action. |
| P5b-4 | Accepting a merge proposal performs a real structural merge (reassign recordings, extend timestamps, reclassify `kind`, delete `session_b`) rather than only flipping `resolution` — the proposal exists to make two sessions actually become one. |
| P5b-5 | Merge note/weather reconciliation is human-edited and pre-filled, never auto-concatenated, per the parent spec's explicit data-loss warning. |
| P5b-6 | The merge banner and its resolve action are reachable from either affected session's detail page, sharing one route/transaction — no per-page duplication. |
| P5b-7 | No separate merge-proposals index; the list's filter plus per-session badges are the only surfaces. |
| P5b-8 | Sessions list uses a capped `LIMIT`, not real pagination, matching this tool's real deployment scale. |
| P5b-9 | Recordings listed on a session's detail page are unlinked plain text this slice — no `/recordings/{hash}` page or map deep-linking exists yet to link to, and adding deep-linking here would pull a later slice's scope forward. |
| P5b-10 | `effort` is dropped (migration, column removal) — never referenced by any code or domain doc since its Phase 2 introduction, and no one could state what it was meant to record. |
| P5b-11 | `kind` classification is persisted at derivation time (`derive/sessions.py`, GPS-spread heuristic vs. `Config.transect_distance_m`), not guessed non-destructively in the UI — reverses this doc's own first draft, which pre-filled a non-persisted guess into the edit form and turned out to risk silently persisting an unchosen `kind` on an incidental save. |
| P5b-12 | `Session.kind_locked` freezes `kind` against future automatic reclassification the moment a human saves the detail form — regardless of whether they changed the dropdown's value — so a save is never later silently undone by a subsequent `derive` run. |
| P5b-13 | The session detail page gets a small Leaflet mini-map (session's own recording markers, via the existing `/api/recordings.geojson?session_id=`) plus a "View on full map" link (the existing `?session=` map filter) — no new API surface, no polyline, no site circle. |
| P5b-14 | Global navigation is a left sidebar (Map, Sessions, room for later Phase 5 entries), not a top nav bar. Expanded by default at normal widths; auto-collapses below a responsive breakpoint, plus a manual toggle at any width — preserves the map's full width under the same reasoning as Phase 5a's bottom-drawer choice, without hiding the app's only nav by default on desktop. |

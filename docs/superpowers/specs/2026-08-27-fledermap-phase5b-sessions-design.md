# Fledermap Phase 5b (Sessions List + Detail) — Design

## 1. Scope

The second slice of Phase 5 per the parent spec's phasing table (`docs/superpowers/specs/2026-08-23-fledermap-design.md`
§15: "Recording and site drawers, sessions list with notes and merge proposals, job
status strip"). Phase 5a shipped the drawer (recording/site panels). This slice is
`/sessions` (list) and `/sessions/{id}` (detail) — per the parent spec's route list
(§9): "`/sessions` + detail (edit kind and notes, resolve merge proposals)".

No new columns and no migration. Every field this slice touches already exists,
unused since Phase 2: `Session.kind`/`note`/`weather`/`effort`,
`SessionMergeProposal.resolution`/`resolved_at`. `SessionMergeProposal` was built
explicitly for "a future UI (Phase 5) to accept or reject" and has sat inert since
2026-08-24.

Ordering rationale (decided in the brainstorming session that produced this doc,
against the remaining Phase 5 pieces — job status strip, this slice, `/recordings/{hash}`
standalone, `/taxa`, `/sites` read-only): `/sessions` goes first because it is the
only remaining piece that is a *workflow* (edit + resolve) rather than a read-only
view or a status widget, and because the data it acts on has been sitting ready
the longest.

## 2. Module layout

Two new routes, matching the parent spec's endpoint naming:

```
GET  /sessions                          -> list, with filters
GET  /sessions/{id}                     -> detail (view + edit form + merge banner)
POST /sessions/{id}                     -> save kind/note/weather/effort
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
panels).

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

Three sections:

- **Edit form** (`POST /sessions/{id}`): `kind` dropdown (`stationary`/`transect`),
  `note`/`weather`/`effort` textareas, single Save button. All four share one form
  and one save action — they're all free-text/enum annotation fields on the same
  model, not four separate concerns.
- **Recordings in this session**: timestamp and current best identification, listed
  plain (unlinked). Linking each row to its own recording view is deferred
  deliberately: neither a standalone `/recordings/{hash}` page nor any map
  deep-linking exists yet, and adding deep-linking to `map.html` just to serve this
  list would pull scope from a later slice into this one. Revisit once
  `/recordings/{hash}` ships.
- **Merge-proposal banner** (§5), shown only when this session is part of an open
  proposal.

## 5. Merge resolution

The banner appears on **both** affected sessions' detail pages (a human might land
on either one first) and shows both sessions' current `note`/`weather`/`effort` side
by side, pre-filled into a single editable combined-text area per field — not
auto-concatenated, per the parent spec's own warning ("silently concatenating field
notes is data loss noticed months later"). Two buttons:

- **Accept merge** → `POST /sessions/merge-proposals/{id}/resolve` with the
  human-edited combined text. In one transaction: reassign every `session_b`
  recording's `session_id` to `session_a`; extend `session_a.started_at`/`ended_at`
  to span both; write the combined `note`/`weather`/`effort` onto `session_a`;
  delete `session_b`; set `resolution='merged'`, `resolved_at=now()` on the
  proposal.
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

## 6. Explicitly out of scope (this slice)

- The job status strip, `/recordings/{hash}` standalone, `/taxa`, `/sites` — later
  Phase 5 slices.
- A separate merge-proposals index page. Open proposals are reachable via the list's
  "open merge proposals only" filter and via the badge on each affected session's own
  row/detail page — no additional listing surface.
- Real pagination for the sessions list (§3) — a capped `LIMIT` stands in.
- `kind` as a list filter (§3) — shown as a column only.
- Linking the detail page's per-recording rows to their own recording view (§4) —
  no target exists yet (`/recordings/{hash}` standalone is a later slice; map
  deep-linking doesn't exist). Rows are listed plain until then.

## 7. Error handling

- **Session not found** (`/sessions/{id}` for a bad id): standard 404 page — this is
  a full standalone view, not an HTMX fragment target, so there's no drawer-style
  "in-place not-found fragment" concern the way Phase 5a's panels had.
- **Merge-proposal FK conflict** (§5): caught and surfaced as a clean in-page error
  message, not a raw 500.
- **Concurrent resolution** (two people/tabs resolve the same proposal): the second
  `POST` finds `resolution` already set and returns a clean "already resolved by
  someone else" message rather than double-applying the merge.

## 8. Testing

View tests for both new routes following `test_map_view.py`'s existing pattern:
list rendering with each filter, detail rendering with and without an open
proposal, the edit form's save path, and the not-found case. Service tests for
`resolve_merge_proposal` covering: normal merge (recordings reassigned, `session_b`
deleted, timestamps extended correctly), reject (nothing but `resolution`/
`resolved_at` changes), the chained-proposal FK conflict, and concurrent
double-resolution.

Database tests via testcontainers + PostGIS, matching the project's existing
convention — `resolve_merge_proposal`'s reassignment/delete/extend logic needs a
real transaction to verify against, not a mock.

## 9. Decisions

| ID | Decision |
|---|---|
| P5b-1 | This slice is `/sessions` list + detail only; job status strip / `/recordings/{hash}` standalone / `/taxa` / `/sites` are separate, later slices. |
| P5b-2 | `/sessions` and `/sessions/{id}` are full standalone pages, not HTMX drawer fragments — matches the parent spec treating `/sessions` as a first-class view, distinct from the map's drawer. |
| P5b-3 | `kind`, `note`, `weather`, `effort` are edited together in one form on one save action — all four are annotation fields on the same model. |
| P5b-4 | Accepting a merge proposal performs a real structural merge (reassign recordings, extend timestamps, delete `session_b`) rather than only flipping `resolution` — the proposal exists to make two sessions actually become one. |
| P5b-5 | Merge note/weather/effort reconciliation is human-edited and pre-filled, never auto-concatenated, per the parent spec's explicit data-loss warning. |
| P5b-6 | The merge banner and its resolve action are reachable from either affected session's detail page, sharing one route/transaction — no per-page duplication. |
| P5b-7 | No separate merge-proposals index; the list's filter plus per-session badges are the only surfaces. |
| P5b-8 | Sessions list uses a capped `LIMIT`, not real pagination, matching this tool's real deployment scale. |
| P5b-9 | Recordings listed on a session's detail page are unlinked plain text this slice — no `/recordings/{hash}` page or map deep-linking exists yet to link to, and adding deep-linking here would pull a later slice's scope forward. |

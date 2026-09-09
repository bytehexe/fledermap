# Fledermap Flagged for Review — Design

**Status:** design, not yet implemented.
**Date:** 2026-09-09

## Problem

The "Improve data quality" v1.x goal's first item is "flagged for review": a way to surface
recordings whose assigned species is suspect enough to deserve a human look, and a workflow to
cycle through and fix them. Two other goal items — a noise classifier and a batdetect2
classifier — will eventually add more evidence this feature can use, but neither exists yet.
This design ships what's buildable today and leaves clear room to extend the criteria set once
those classifiers land, without restructuring anything built here.

The feature is specifically about the **assigned species being wrong**, not a generic
"something's off with this recording" flag (which would overlap with the existing
`missing_since`/unmapped-species-code concepts, both already their own thing).

## Goals

- Two independent flag sources feed one "needs review" signal, unioned (either is enough):
  - **Computed criteria**, evaluated live from existing data, never stored, never dismissible
    directly — clearing one means fixing the underlying data (mapping a species code, adding a
    manual classification), the same philosophy `docs/superpowers/specs/2026-08-23-fledermap-
    design.md` already uses for the unmapped-species review queue.
  - **A manual flag** (`Recording.flagged_for_review`), a human-set/cleared boolean, structurally
    identical to `Recording.favourite`, for cases no rule catches.
  - Clearing the manual flag never affects a computed match, and a computed match never sets the
    manual flag. No coupling between them.
- Computed criteria apply only to recordings whose current-best identification is `Verdict.SPECIES`
  (a real species to be suspicious of) with **no standing `MANUAL` claim** (a human classifying the
  recording counts as already-reviewed, per the goal note "not classified manually"). Two criteria
  are buildable now:
  - **Rarity**: the assigned taxon has ≤2 recordings at this site, or ≤5 recordings dataset-wide.
  - **Misattribution**: the claiming source has been contradicted by a `MANUAL` claim on >50% of
    the recordings where both that source's claim of this taxon and *some* `MANUAL` verdict exist,
    with at least 3 such disagreements observed (avoids flagging on a single correction). Given a
    classifier claim of taxon X and a standing `MANUAL` verdict on the same recording:
    - `MANUAL` verdict `SPECIES` with taxon set containing X → **correct**, X is not overturned
      even if the human also claimed other species alongside it (a classifier only ever names one
      species per claim, so a broader human multi-species call still confirms X).
    - `MANUAL` verdict `SPECIES` with a taxon set that does *not* contain X → **misattribution**.
    - `MANUAL` verdict `NOISE` → **misattribution** (the human found no bat at all where the
      classifier claimed X).
    - `MANUAL` verdict `NO_ID` → **ignored entirely** — excluded from both the numerator and the
      denominator. A human explicitly declining to call it isn't evidence the classifier was
      wrong, just that the file was hard.
  - Two more criteria are explicitly deferred, not built: classifier disagreement and
    likely-multi-species, both requiring the batdetect2/noise classifiers this design doesn't
    include.
- Each computed match carries a human-readable reason string (e.g. "rare species (2 recordings at
  this site)"); the UI shows *why*, not just a boolean.
- "Needs review" becomes a filter dimension (`needs_review`) alongside the existing ones in
  `services/map_query.py`, so it composes with the map's other filters and — because
  `neighbor_recordings` already builds prev/next generically over whatever `filtered_recordings`
  returns — prev/next over the flagged set requires no new navigation machinery.
- Review happens on the recording **details page**, not the map drawer: the drawer's rendering is
  an overview, not the tiled spectrogram a reviewer actually needs to judge a call (see
  `docs/superpowers/specs/2026-08-25-fledermap-phase3-media-jobs-design.md`'s original detail-view
  reasoning, still true here). The details page therefore gains prev/next for the first time,
  scoped by whatever filter context is in its URL — the same filter-query-string pattern the
  drawer already uses. Landing on the page from anywhere with an active filter (species-filtered
  map view, the Reviews page, etc.) gets a prev/next scoped to that same set.
- A new "Reviews" section (nav item, own page) is the dedicated entry point: shows the flagged
  count and a "Start reviewing (N)" button that jumps straight into the first flagged recording's
  details page (filter state baked into the URL), and, below that, a table listing every flagged
  recording individually (site, time, assigned species, matching reasons) — each row links
  directly into its own details-page review, so a reviewer isn't limited to starting from the
  first one.
- Drawer/details parity (CLAUDE.md): the drawer panel gets the same flag badge, reasons, and
  manual-flag toggle as the details page — it just isn't the review *workflow* surface itself.
- When navigation is specifically scoped to `needs_review` (as opposed to an ordinary
  species/session-filtered browse), the details page makes that explicit — a banner naming it as a
  review session, a position indicator ("3 of 12"), and an "Exit review" link back to the Reviews
  page — so prev/next buttons don't appear with no explanation of why they're there.

## Non-goals

- **No dismiss/resolve action on computed matches.** Fixing the underlying data (map the species
  code, add a manual classification) is the only way a computed match stops firing — consistent
  with how the existing unmapped-species review queue already works. Only the manual flag has an
  explicit clear action.
- **No history table for the misattribution criterion.** Since `replace_claims` only touches its
  own source's claims, a classifier's claim and a `MANUAL` claim on the same recording coexist as
  live rows — the misattribution rate is a query over current claims, not retained history.
- **No classifier-disagreement or likely-multi-species criteria yet.** Both need batdetect2/noise
  classifiers that don't exist. The criteria function's shape (a list of independent rule
  functions, each returning zero or more reason strings) is deliberately additive so these can be
  dropped in later without touching the rules already here.
- **No prev/next default when no filter context is present** (e.g. a bare bookmarked link to a
  details page). Matches today's drawer behavior exactly — prev/next only appears when the page
  was reached via some active filter.
- **No wraparound at the ends of a review session's list.** Buttons disable, a "no more flagged
  recordings" note appears, with a link back to the Reviews page.
- **No new `Verdict`, `IdSource`, or change to `replace_claims`/`current_best_identification`.**
  This design reads existing identification data; it doesn't change how claims are resolved.
- **No taxonomic hierarchy awareness in the misattribution match.** A manual genus/group-level
  claim (e.g. `Myotis`/`MYSP`) and an automatic species-level claim it could arguably subsume
  (e.g. `Myotis daubentonii`) are compared by plain `taxon_id` equality only, same as any other
  taxon pair — a genus claim never "counts as agreeing with" a species claim underneath it. This
  is a real taxonomic subtlety (a genus-level human call isn't actually wrong just because the
  classifier went more specific) but resolving it needs a `Taxon.parent_id` walk that isn't worth
  the complexity for what is otherwise a fairly small feature; treating every taxon as a distinct,
  unrelated ID keeps the rule simple and easy to reason about.

## Design

### 1. Data model

One new column: `Recording.flagged_for_review: bool`, `default=False`, `nullable=False` — same
shape as `Recording.favourite` (`store/models.py`). A hand-written Alembic migration adds it
(`test_migrations.py`'s drift check will fail loudly if it's missing or mismatched, per CLAUDE.md's
migrations section).

No other schema changes. Both computed criteria are queries over `Identification`/`Taxon`/`Site`,
already in the schema.

### 2. Computed criteria (`services/review_flags.py`, new module)

A pure function, `review_reasons(session, recording) -> list[str]`, called for a recording whose
current-best identification is `Verdict.SPECIES` and has no standing `MANUAL` claim (checked via
`current_manual_state`, mirroring how `manual_classification.py` already reads that state). Empty
list means no computed match.

Two rule functions compose into it:

- **`_rarity_reason`**: counts recordings of this taxon at `recording.site_id` and dataset-wide.
  Returns a reason string if either count is at or under its threshold (site ≤2, dataset ≤5).
- **`_misattribution_reason`**: for the claiming source and taxon, counts (a) recordings where that
  source claims this taxon *and* a `MANUAL` verdict of `SPECIES` or `NOISE` exists on the same
  recording (a `MANUAL` `NO_ID` is excluded entirely, per Goals), and (b) of those, how many are a
  misattribution — `MANUAL` verdict `NOISE`, or `MANUAL` verdict `SPECIES` whose taxon set doesn't
  contain this taxon (a `MANUAL` multi-species claim that *does* contain it still counts as
  correct). Returns a reason string (`"<SOURCE> is often wrong about <species>"`) if the
  disagreement count is ≥3 and is a strict majority of (a).

Because a caller needing this for many recordings at once (the Reviews page, the `needs_review`
filter) would otherwise run these per-recording queries once per row, the aggregate lookups
(per-taxon site/dataset counts, per-source-per-taxon disagreement rates) are computed once per
request as plain dicts and passed into the rule functions — avoiding an N+1 query pattern across a
whole recording set. The exact caching shape is an implementation-plan detail, not fixed here.

### 3. Filter integration

`services/map_query.py`'s `filtered_recordings` gains `needs_review: bool | None`. When `True`, a
recording passes if `recording.flagged_for_review` is set **or** `review_reasons(...)` is
non-empty — evaluated post-fetch in Python alongside the existing verdict/taxon filtering, for the
same reason those are Python-side (needs `current_best_identification`, plus here also the
aggregate lookups from §2).

`neighbor_recordings` needs no changes — it already computes prev/next generically over whatever
`filtered_recordings` returns.

### 4. Reviews page

New blueprint, `GET /reviews`, added to `_nav.html`'s sidebar (`Map / Sessions / Species / Sites /
Statistics / Reviews`). Renders:

- The flagged count (`needs_review=True` count from `filtered_recordings`) and a "Start reviewing
  (N)" button/link — disabled or hidden at zero — pointing at the first flagged recording's details
  page with `?needs_review=true` (or however the query-string convention encodes it) so its
  prev/next is scoped to the same set.
- A table below it (same shape as the existing Sessions/Species/Sites list pages) with one row per
  flagged recording: site, time, assigned species, and the matching reasons (manual flag shows as
  its own reason, e.g. "manually flagged"). Each row links directly into that recording's details
  page, same scoped-filter query string.

### 5. Details page

`web/views/recording_detail.py` gains prev/next: parse the incoming filter query string (same
params `map_query.filtered_recordings` accepts, mirroring how `map.py`'s `_render_recording_panel`
already does this for the drawer), call `filtered_recordings` + `neighbor_recordings`, and render
prev/next links carrying the same filter query string forward — exactly the `_recording_panel.html`
pattern, applied to `recording_details.html`. With no filter query string present, no prev/next
renders (§ Non-goals).

At the ends of a `needs_review`-filtered list (no previous/next), the corresponding button is
disabled/absent and a "no more flagged recordings" note appears with a link back to `/reviews`.

**Review-mode banner.** Prev/next alone doesn't tell a reviewer *why* they're seeing navigation
buttons — an ordinary species- or session-filtered browse from the map would look identical. When
the incoming filter query string has `needs_review=true` specifically, the details page shows a
distinct banner (not just the generic prev/next row): "Reviewing flagged recordings" plus a
position indicator (e.g. "3 of 12", computed cheaply from the same `filtered_recordings` result
already fetched for prev/next) and an explicit "Exit review" link back to `/reviews`. This banner
only appears for `needs_review` navigation — a species-filtered or session-filtered prev/next stays
the plain, unbannered row it already would be, since those aren't a "review session," just ordinary
browsing.

The page (and the drawer panel, for parity) shows: a flag badge when `flagged_for_review` is set or
`review_reasons` is non-empty, the list of reasons, and a manual-flag toggle button following the
existing favourite-button pattern — `POST /recordings/<audio_hash>/flag-for-review`, branching on
`panel=detail` the same way `toggle_favourite` already does.

### 6. Testing

- `tests/` unit tests for `review_reasons`/its rule functions: rarity at each threshold boundary,
  misattribution at/below/above the 3-disagreement and >50% thresholds, SPECIES-only scope,
  exclusion when a `MANUAL` claim exists — all against constructed fixtures, no need for `db`-marked
  tests beyond whatever the fixtures already require.
- `test_migrations.py`'s drift check covers the new column automatically once the migration exists.
- View/route tests for `/reviews` and the details page's new prev/next (present with a filter query
  string, absent without one, correct scoped ordering).
- Mutation-check the new filter (`needs_review`) and the migration, per CLAUDE.md's "one that
  cannot fail is worse than no test" rule.
- Any new pure JS logic goes through `node --test`; any DOM-touching change (badge rendering, flag
  toggle wiring, prev/next button state) gets the mandatory headless-Chrome live-verification pass
  before considering the JS task done (CLAUDE.md's JavaScript tooling section is explicit that
  skipping this shipped Critical bugs before).

# Fledermap IUCN range plausibility check — Design

**Status:** implemented
**Date:** 2026-10-04

## Problem

Classifiers sometimes disagree, and the disagreement is often not settleable from the recording
alone: there is rarely a description or measurement of the bat, and habitat is vague. Where the
species can even occur is a cheap, independent signal. A classifier naming a species whose known
range is hundreds of kilometres from the recording is worth flagging; the user then judges.

## Goals

- For each species row of the Identifications matrix, show whether the recording's location lies
  inside, near, or far outside that species' IUCN range, or that there is no range data.
- Display only. The check never changes `current_best`, a verdict, or any review queue.
- Off by default behind a config flag, because of IUCN's terms (see "Licensing").
- Ranges are fetched lazily, only for species that actually occur in this database, and cached.
- The web process never makes an outbound request.

## Non-goals

- Changing identifications, confidence, or review-queue membership from the result (decided
  2026-10-04: "no").
- Showing range polygons anywhere in the browser, on any page (licensing; see below).
- Filtering the map or `/search` by range plausibility.
- Importing IUCN's own spatial download. The fetch sits behind one small loader function so this
  could replace it later, but it is not built now.
- A CLI command to force-refetch. Not needed while the pending-set logic below picks up changed
  codes by itself; add if a real need appears.
- Genus/group-rank rows (e.g. *Myotis*) -- no range exists for them; they show a blank cell.

## Licensing

IUCN Red List Terms and Conditions of Use v3.1 (June 2024), read in full 2026-10-04.

- **§3 No commercial use.** Neither the data nor any derived work may be used "by, on behalf of,
  or to inform or assist the activities of, a commercial entity", or for revenue generation. A
  consultancy running this feature would be in breach. The flag is therefore off by default and its
  documentation says so plainly; enabling it is the operator's own acceptance.
- **§4 No reposting/redistribution**, explicitly including "web services … interactive web maps".
  Consequence: range geometry never leaves the server. There is no GeoJSON endpoint, no overlay,
  and no geometry in any API response. Only a derived verdict (inside/near/far) and a rounded
  distance reach the browser. The data is never committed to the repo or shipped in the wheel.
- **§1 Personal licence.** Rights are personal to the user or household and non-transferable.
  Each installation fetches its own data on its own operator's behalf. Opening one instance to
  other people is a grey area (the verdicts are derived and transformative, but §4 says
  derivatives need a waiver); before doing that, ask redlist@iucn.org.
- **§5** permits conservation, education, scientific analysis and research use without
  requesting permission — a citizen-science bat map fits.
- **§6 Citation incl. version.** The layer is `IUCN_RL_2022_2_Species_Ranges`, i.e. **release
  2022-2**. The UI cites "IUCN Red List 2022-2".
- **§7 Superseded versions.** 2022-2 is superseded. Accepted and documented: the alternative
  (IUCN's own download) requires registration and a per-download statement of intended use, which
  is disproportionate effort for a hobbyist deployment on both sides.
- The hosted service (UNEP-WCMC) carries no licence text of its own; IUCN's terms are assumed to
  apply to the data it serves.

## Decisions

- **Source: the UNEP-WCMC-hosted layer**
  `https://data-gis.unep-wcmc.org/server/rest/services/Hosted/IUCN_Red_List/MapServer/0`, queried
  per species with `f=geojson`. Probed 2026-10-04: reachable, 344 polygons for our 71 species,
  well below the 2,000-record query limit; `presence` values seen 1/3/5/6, `origin` 1/4, `seasonal`
  1/5, `yrcompiled` 2008–2021. (The extinct polygons among them are why presence is filtered.)
- **Display-only, per species row**, in its own matrix column — not a replacement for the
  classifiers' check marks. Range is a property of the species row at the recording's location,
  not something a classifier claims; replacing the marks would only make sense if the result
  changed the classification.
- **Computed at read time**, not stored per recording. The matrix is per recording (a handful of
  species rows), so one indexed distance query per row is cheap, and nothing goes stale when
  ranges or thresholds change.
- **One edge threshold, not several buffered geometries.** Distance from the recording to the
  species' range is computed once; `0` is inside, `≤ edge` is near, `> edge` is far. This
  distinguishes "just outside" from "really implausible" without storing more than one geometry.
  Default `100` km, config-tunable, to be re-tuned against real recordings.
- **Which polygons count:** `presence` in {1 extant, 2 probably extant, 3 possibly extant, 6
  presence uncertain}; extinct (5) and possibly extinct (4) are not fetched. Every `origin` counts
  (introduced populations are real populations — this is a plausibility check, not a
  conservation one). Exception: a point lying only inside a **vagrant** (`origin` 4) polygon is
  **near**, never inside. Code meanings were verified 2026-10-04 against the layer's own `legend`
  field (the service publishes no coded-value domains): presence 1 Extant, 2 Probably Extant, 3
  Possibly Extant, 4 Possibly Extinct, 5 Extinct, 6 Presence Uncertain; origin 1 native, 2
  reintroduced, 3 introduced, 4 vagrant, 5 origin uncertain, 6 assisted colonisation.
- **`seasonal` is ignored.** It is multi-valued (1 resident, 2 breeding, 3 non-breeding, 4 passage,
  5 uncertain), but across all our species the service only ever returns 1 (342 polygons) and 5
  (2 polygons, *Myotis alcathoe* and *Tadarida teniotis*): IUCN files every European and North
  American bat in our lists as resident, migrants like *P. nathusii* included. There is no
  breeding/non-breeding split to compare a recording date against, so neither the filter nor the
  schema carries it. Revisit only if a future species list brings real seasonal polygons.
- **Name matching via an explicit `iucn2022` code** in `taxa_eu.yaml`/`taxa_na.yaml`, on every
  species the service has, with no fallback to `scientific_name`. A fallback would be a silent
  guess; an absent code is "no data", which is the safe failure (same principle as every other
  code source). Three species need a name that differs from `scientific_name`: IUCN 2022-2 still
  files *Cnephaeus serotinus/isabellinus/nilssonii* under *Eptesicus*. Those three are untested
  against the service and must be verified, not assumed.
- **Fetch status is its own record.** A species the service does not know must not be re-queried
  every cycle (same lesson as the classifier NoID marker row).

## Data model

One Alembic migration. Back up with `scripts/db-backup.sh` before applying.

- `species_range(id PK, taxon_id FK→taxon ON DELETE CASCADE, presence smallint, origin smallint,
  geom geography(MULTIPOLYGON, 4326))`, GIST index on `geom`, index on
  `taxon_id`. Geometry is simplified at fetch (server-side `maxAllowableOffset`, then
  `ST_SimplifyPreserveTopology` in PostGIS, ~2 km tolerance — negligible next to the 100 km
  threshold).
- `species_range_fetch(taxon_id PK FK→taxon ON DELETE CASCADE, query_name text, status
  'found'|'not_found', source_version text, fetched_at timestamptz)`. `query_name` records which
  `iucn2022` code was asked, so a changed code makes the taxon pending again.

## Components

- `domain/ranges.py` — pure vocabulary: `RANGE_CODE_SOURCE = "iucn2022"`, the release/citation
  strings, `RangeKind`, `NoDataReason`, and `RangeStatus` (with its tooltip text). Kept out of
  `services/` so `services/current_best.py` can type `MatrixRow.range` without importing the
  module that imports `jobs.tasks`.
- **Existing readers of `taxon_code` must ignore the new source.** Swept 2026-10-04 (`grep
  TaxonCode`): `services/entities.py` takes an arbitrary first code per species for the species
  list and lists every code on the species page; `web/views/recording_detail.py`'s
  `_taxon_search_index` feeds every code to the Classify box's client-side search. An `iucn2022`
  name is a range-lookup key, not a code a user types or a classifier emits, so all three filter
  it out. `services/search.py`'s exact-match species resolution is left alone (a matching
  `iucn2022` row names the same taxon, so it cannot change an answer).
- `services/iucn_ranges.py` (DB + one fetch function):
  - `fetch_range(query_name) -> list[RangePolygon]` — the single place that talks to the service
    (`requests`, explicit timeout, identifying `User-Agent`, one request per species). This is the
    seam a future import loader would replace.
  - `pending_taxa(session)` — species-rank taxa with an `iucn2022` code that appear in any
    `Identification` or `ClassifierDetection` and have no fetch row for the current code.
  - `fetch_pending(session)` — fetch, store polygons, write the fetch row.
  - `range_status(session, taxon_id, recording, edge_km) -> RangeStatus` — `kind` in
    {INSIDE, NEAR, FAR, NO_DATA}, `distance_km`, and for NO_DATA a `reason` in {NO_LOCATION,
    NO_CODE, NOT_FOUND, NOT_FETCHED}.
- `jobs/tasks.py`: `fetch_iucn_ranges_task` on the `geo` queue, enqueued from the ingest cycle only
  when the flag is on **and something is pending** (unlike `resolve_regions_task`, nothing here
  changes without a new species or code, so an idle cycle should not queue a job). Sequential
  requests, one commit per species so a later failure keeps earlier progress.
  A network failure raises and retries via the existing retry policy; nothing is written on failure.
- `services/current_best.py`: `MatrixRow` gains `range: RangeStatus | None`; `IdentificationMatrix`
  gains `show_range: bool`. A separate `attach_ranges(matrix, session, recording, …)` fills them,
  so `identification_matrix` itself stays session-free. Called from the two existing call sites
  (`web/views/map.py`, `web/views/recording_detail.py`).
- `config.py`: `iucn_ranges_enabled` (`FLEDERMAP_IUCN_RANGES_ENABLED`, default `false`) and
  `iucn_range_edge_km` (`FLEDERMAP_IUCN_RANGE_EDGE_KM`, default `100`). Both documented in
  `docs/reference/configuration.md`.

## UI

Placement decided here, per the style guide's "decide element placement once" rule. The matrix is
one shared include (`_identification_matrix.html`) rendered by the drawer panel and the details
page, so drawer/detail parity holds structurally. The sweep found no other element needing a
"range" cell, and no existing red in `app.css`/templates (`marker_colors.js` is a data-series
palette, unrelated).

```
               bd2   bbn   emt.g ║ iucn          ← new, rightmost, shown only when flag on
 Pipistrellus   ✓     ✓     ✓    ║  (ok)        inside      circle-check   --color-success
  pipistrellus
 P. kuhlii      ✓                ║  (!)         near        alert-triangle --color-warning
 Nyctalus                  ✓     ║  (ban)       far         ban            --color-danger
  noctula
 Barbastella               ✓     ║  (?)         no data     help-circle    --color-muted
 Myotis         ✓                ║              not applicable: blank
 ─────────────────────────────────────────────
 Range: IUCN Red List 2022-2, non-commercial use only.
```

- Header label **`iucn`** (lowercase, matching `bd2`/`bbn`). Its `title` carries the full citation.
- Column separated from the classifier columns by a heavier left border so a range icon cannot be
  read as a classifier claim; no check mark is used in it.
- **Icons: `circle-check`, `alert-triangle`, `ban`, `help-circle`.** A map-pin family
  (`map-pin-check/-exclamation/-off/-question`) was rendered at the matrix's real size (≈13.6 px,
  light and dark) and rejected: the pin badges are unreadable and the states differed only by
  colour. The chosen set is distinguishable by shape alone.
- **Colour coding is kept** (it complements, never replaces, the shape): success / warning /
  danger / muted. This needs a new token, **`--color-danger`** (`#c53030` light, `#fc8181` dark),
  added to `:root` and both dark-mode blocks in `app.css` and documented in the style guide's token
  table in the same change.
- **Blank** = not applicable (genus/group rows, unmapped-species rows, NoID/noise rows).
  **`help-circle`** = applicable species row with no data. Tooltip states the reason: no location,
  no IUCN code for this species, service has no range for it, not fetched yet.
- Tooltips: inside → "Inside the IUCN range"; near/far → "N km outside the IUCN range"; the cell
  also carries an `aria-label` with the same text.
- The caption under the matrix states the source, version and non-commercial restriction (§6).
- The current-row highlighting stays driven by the identification only; range never affects it.

## Testing

- Distance tiers and the vagrant rule on synthetic polygons (`db`-marked, PostGIS container; run by
  the controlling session, not a subagent).
- `fetch_range` against a **hand-written, synthetic** GeoJSON fixture shaped like the service's
  response; **never** a live request in the suite. A real response must not be committed: IUCN §4
  forbids redistributing the data "in whole or in part", and a public repo would do exactly that.
  The three *Cnephaeus*→*Eptesicus* `iucn2022` names are verified once against the live service by
  hand when the codes are added, and pinned by an explicit override table in a YAML test.
- Pending-set logic: not-found is not re-queued; a changed `iucn2022` code is.
- Matrix: column absent when the flag is off; cell states for each `RangeStatus`; blank for
  group/NoID rows; both views render it.
- Config: a new `from_env` field needs a test asserting the constructed `Config` attribute, per
  CLAUDE.md. Migration drift test must stay green.
- Seed: every `iucn2022` code in the YAML is unique per `(source, code)`.
- JS: none added. The change is server-rendered, so the headless-Chrome check is limited to
  confirming the column renders legibly in light and dark at drawer and details-page widths.

## Docs to update in the same change

- `docs/style-guide.md`: `--color-danger` token; the range column as a documented matrix
  convention (icon+colour semantics, blank vs `help-circle`).
- `docs/reference/configuration.md`: the two settings, with the non-commercial warning.
- `docs/references.md`: the IUCN service and terms, as the authority for `iucn2022` codes and range
  data, with the 2022-2 release and the *Eptesicus*/*Cnephaeus* naming note.
- `taxa_eu.yaml`/`taxa_na.yaml` header comments: what `iucn2022` codes are.

## Open items for implementation

- Verify the three *Eptesicus* names against the live service when adding the YAML codes.
- Confirm geometry arrives in WGS84 with `f=geojson` and tune `maxAllowableOffset`.
- Revisit the 100 km edge default after seeing real recordings near range borders.

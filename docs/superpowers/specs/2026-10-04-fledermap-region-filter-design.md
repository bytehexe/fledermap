# Fledermap region filter (poiidx regions) — Design

**Status:** implemented
**Date:** 2026-10-04

## Problem

poiidx 0.1.0 can list the Geofabrik regions (e.g. Bremen, Germany, DACH) that intersect a shape and
flags each as `distinct` when it covers a part of the shape no single smaller candidate covers.
Fledermap's map has no way to narrow recordings by where they were made other than panning/zooming
or the per-site filter. A region dropdown lets the user say "only recordings from Austria".

## Goals

- A **Region** dropdown in the map's filter bar, shown only when poiidx is configured
  (`FLEDERMAP_POIIDX_DATABASE_URL` set) and at least one region is stored.
- The dropdown lists only regions that are relevant to the stored recordings **and** `distinct` in
  poiidx's sense, computed over all recorded locations.
- Selecting a region keeps every recording located inside it, including recordings in nested
  sub-regions (Germany includes Bremen).
- Turning poiidx on after recordings already exist backfills automatically, with an explicit CLI
  command for forced recomputation.
- The web process never connects to poiidx; it reads only `bats_db`.

## Non-goals

- Region filter on `/sessions` or `/search` (logged to the backlog; `/search` composes onto
  `filtered_recordings`, so adding a `region` term later is cheap).
- Downloading or importing region data. Only poiidx's region index is read (`list_regions`), never
  `download_region`/`import_region`.
- Region geometry. poiidx returns none, and none is needed: membership is asked per location.

## Decisions

- **Approach A (membership cached in `bats_db`)** over returning shapes from poiidx: no polygon
  storage, no poiidx connection in the web process, no new poiidx work.
- **Store every containing region per recording**, not only the smallest, because regions nest.
- **`region` holds every region seen for a recorded location, with an `is_distinct` flag that is
  recomputed in place on every ingest cycle.** Distinctness depends on which recordings exist, so it
  changes as recordings are added or go missing; it is derived data, refreshed rather than trusted.
  Keeping all regions (not only the distinct ones) lets `recording_region.region_id` be a real
  foreign key and means a region that becomes distinct later needs no re-resolution. Dropping the
  flag and replacing a distinct-only set was considered and rejected: it goes stale in exactly the
  same situations as a recomputed flag, and loses the foreign key.
- **Hidden, not disabled, when poiidx is not configured.** The style guide's "disabled, never
  hidden" targets controls that come and go at runtime. poiidx on/off is fixed per deployment, so a
  permanently greyed-out control would only be noise. Same precedent as the batdetect2/BattyBirdNET
  `Source` options.

## Data model

One Alembic migration. Back up with `scripts/db-backup.sh` before applying.

- `region(id text PK, name text not null, is_distinct bool not null)` — every region returned for a
  recorded location; the dropdown lists `WHERE is_distinct`, ordered by name.
- `location_region(location_key varchar unique, region_ids text[] not null)` — cache of poiidx's
  answer per rounded coordinate, keyed like `SiteNameCache` (3 decimal degrees, ~111 m). An empty
  array means "resolved, in no region", so such a location is never re-asked.
- `recording_region(recording_id FK→recording ON DELETE CASCADE, region_id FK→region, PK(both))` —
  every region containing the recording's point.
- `recording.region_resolved_at timestamptz null` — set when the recording's regions are written,
  including the none case. Without it a recording in no region has no `recording_region` rows and
  would be re-picked forever (same shape of problem as the NoID marker rows).

`tests/test_migrations.py` must pass unchanged in structure; no enum/CHECK is involved, so no
extra blind-spot test is needed. Mutation-test the new columns per CLAUDE.md.

## Resolution job

`services/regions.py` (new) plus `resolve_regions_task` in `jobs/tasks.py`, `geo` queue.

1. Select recordings with a point, `missing_since IS NULL`, and `region_resolved_at IS NULL`.
2. Group by location key. For each key absent from `location_region`, call
   `poiidx.list_regions(Point(lon, lat))` and store every returned region id (candidates, not only
   `used` ones; candidates are all regions containing the point) in `location_region`, and upsert
   each returned `(id, name)` into `region`.
3. Write `recording_region` rows from the cached ids and set `region_resolved_at`.
4. Recompute the flags: `poiidx.list_regions(MultiPoint(all distinct locations of live
   recordings))`, then set `region.is_distinct` true for the ids it flags `distinct` and false for
   every other row, in the same transaction.

Step 4 runs on every ingest cycle that has poiidx configured, even with nothing pending in step 1,
so recordings going missing also refresh the flags. Steps 1-3 are skipped when nothing is pending.

`list_regions` only reads poiidx's region index, so no download/import and no serialization lock
like site naming's is needed; one static queueing lock keeps two runs from overlapping.
The job calls `site_naming.ensure_connected` first, like `name_site_task`. It returns immediately
when `poiidx_database_url` is None (config changed after deferral).

## Backfill

- **Automatic:** `enqueue_region_resolution` is called from the ingest cycle next to
  `enqueue_site_naming`. `fledermap worker` already runs a cycle at startup, so setting
  `FLEDERMAP_POIIDX_DATABASE_URL` and restarting resolves every existing recording (all have
  `region_resolved_at IS NULL`). It is a no-op without the URL and when nothing is pending.
- **Manual:** `fledermap backfill-regions [--force]`. `--force` clears `location_region`, deletes
  `recording_region` and `region`, nulls `region_resolved_at`, then enqueues, for a poiidx region-index change.
  Reports `enqueued N`. Follows `backfill-site-names`.
- **Turning poiidx off** leaves stored rows in place; the UI gate is the config value, so stale
  regions never show.

## Filter

- `filtered_recordings(..., region_id: str | None = None)` adds
  `Recording.id IN (SELECT recording_id FROM recording_region WHERE region_id = :id)` (an `EXISTS`).
  Region ids are opaque strings; nothing to parse beyond empty-is-None, in `web/params.py` next to
  `parse_taxon_filter`.
- The `region` query param is read by `web/views/map.py` and `web/api/geojson.py` (the two callers
  that read the map's filter params) and carried by `filterForm()` in the page's Alpine state,
  exactly like `taxon`.
- Unknown/stale region id: matches nothing (empty map), not an error.

## UI placement

Decided here, not left to implementation. The map's `#filters` bar (`.filter-bar`) already holds
From, To, Taxon (+ "not"), Source, Verdict, and the latest-session checkbox, so a mockup is required.

```
[From ____] [To ____] [Taxon: Any v] [ ] not [Region: Any v] [Source: Any v] [Verdict: Species only v] [ ] Latest session
                                                ^ new: plain <label>Region <select>, directly after the Taxon group
```

- Plain `<label>Region <select name="region" x-model="region">` with an `Any` first option, matching
  the Source/Verdict controls. Not part of `.taxon-group`: it is not a taxon modifier.
- Options are `region.name`, ordered by name, value is `region.id`.
- The whole label is omitted (not disabled) when `regions_enabled` is false. See Decisions.
- The map is the only page affected. Sweep result: `sessions_list.html`'s `#session-filters` and
  `/search` do not get it now (Non-goals).
- No new CSS class: the shared `.filter-bar` select styling applies.

## poiidx dependency

`pyproject.toml`: `poiidx>=0.1.0,<0.2` (was `>=0.0.9,<0.1`). poiidx's diff 0.0.9→0.1.0 does not touch
`models.py`, so its schema hash should be unchanged and `poiidx_bats_db` not rebuilt; verify against
a real `ensure_connected` at implementation time before relying on it. Update the pin's comment in
`pyproject.toml` and `docs/references.md` if it lists the version. Revisit the `buffer=` note in
`services/site_naming.py` (0.1.0 includes "Make buffer work"); out of scope here, backlog item.

## Testing

- Pure logic (no Docker): `parse_region_filter`; distinct-set selection from a faked
  `list_regions` result; empty/none handling; the job's grouping by location key with poiidx faked.
- `db`-marked: migration drift test; `filtered_recordings(region_id=…)` including nested regions and
  a recording with no region; `resolve_regions` end to end against a testcontainer with poiidx
  faked at the `poiidx.list_regions` boundary; `region.is_distinct` flipping when the distinct set
  changes after a recording goes missing; `backfill-regions --force`. The controlling session runs
  these (see CLAUDE.md on subagents and `-m db`).
- Template: the Region label is rendered iff `regions_enabled`; map view test with and without
  poiidx configured.
- JS: none expected (Alpine `x-model` only). If `filterForm()` needs a change, the headless-Chrome
  live check is mandatory.
- Live check against real data once poiidx 0.1.0 is installed: dropdown contents sane, filter
  result counts match a manual count.

## Open items

- `hatch run` currently attempts to reach pypi.org from the sandbox; the env/network situation must
  be resolved before the bump can be installed and tested. Whether 0.1.0 is published on PyPI is
  unverified (only the local checkout's tag was seen).
- The Obsidian backlog gets: region filter for `/sessions` and `/search`; revisit `buffer=` in
  `site_naming.name_site`; ~25 stale hatch env directories for fledermap.

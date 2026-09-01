# Fledermap poiidx Site Naming — Design

**Status:** draft — sections approved individually in chat during brainstorming; awaiting the
user's review of this written spec (see brainstorming skill's user-review gate) before writing an
implementation plan.
**Date:** 2026-08-28

## Problem

`Site.name`/`Site.admin_path` have existed as schema since Phase 2 but nothing has ever populated
them — no phase was assigned the `geo` queue's `name_site` job (Phase 4's P4-1 deviation note).
Every site on the map falls back to a rounded-coordinate label (`fallback_site_label`) instead of
a human-recognisable place name. This closes that gap: wire poiidx (the owner's PostGIS OSM POI
index, `../poiidx`, published on PyPI) into Fledermap as an optional integration that names sites
asynchronously.

## Goals

- Every derived `Site` gets a `name` (nearest recognisable place — quarter, suburb, village, named
  park or wood) and `admin_path` (administrative hierarchy breadcrumb), resolved via poiidx.
- The integration is **optional**: without `FLEDERMAP_POIIDX_DATABASE_URL` configured, behavior is
  unchanged from today — sites keep the coordinate fallback, nothing errors, nothing blocks.
- Naming happens off the request path entirely — a `geo`-queue Procrastinate job, never a web
  handler.
- `derive_sites`'s wholesale rebuild (every site's `name` resets to `NULL` on every rebuild, by
  design — see Non-goals) does not turn into a repeat-work storm: `SiteNameCache` (rounded
  coordinates → name/admin_path) is checked *before* a job is ever enqueued, not just before a
  job calls poiidx.

## Non-goals

- **`derive_sites` itself is not changed.** `Site` is deliberately a projection with no persistent
  identity across a rebuild (its own docstring: "a derived cluster... not an entity"), and the
  wholesale `DELETE`+recreate is a deliberate Phase 2 decision (P2-2: "tuning is free"). Making
  unchanged sites survive a rebuild would mean matching site *identity* across reruns — DBSCAN
  cluster membership is not monotonic, so a single new recording near a cluster boundary can merge
  or split sites, making "did the members change" a real site-identity-matching problem, not a
  simple diff. `SiteNameCache` solves the repeat-work problem this design actually has, at a much
  smaller scope, without touching clustering at all.
- **No change to poiidx itself.** Fledermap depends on it as a normal pinned PyPI package
  (`poiidx>=0.0.9`), not a vendored or forked copy.
- **No web-layer changes beyond what P4-1 already established.** `fallback_site_label` stays as
  the fallback for a site whose name hasn't resolved yet (or poiidx isn't configured at all) — no
  template or view changes.
- **No general-purpose POI browsing.** Fledermap's filter config (below) is curated for
  *toponymy* — naming a coordinate — not for surfacing restaurants/shops/hotels the way poiidx's
  own shipped default config does for its other consumer.

## Design

### 1. Dependency and connection

Add `poiidx>=0.0.9` to `pyproject.toml`'s main dependencies (verified on PyPI: same description,
same dependency list as the local `../poiidx` checkout).

New `Config` field, following the existing env-wins-over-file pattern:

| Setting | Env var | Config file key | Required? | Default |
|---|---|---|---|---|
| poiidx database connection | `FLEDERMAP_POIIDX_DATABASE_URL` | `poiidx_database_url` | no | unset — site naming disabled |

Unlike `database_url` (hard-required), a missing `poiidx_database_url` is not an error: `Config`
carries it as `str | None`, and every consumer downstream (enqueue, job registration) treats
`None` as "feature off."

poiidx's `init()` takes discrete connection kwargs (`host`, `port`, `user`, `password`,
`database`), not a URL, confirmed against its own README/example script. The one call site that
invokes `poiidx.init(...)` parses `poiidx_database_url` with `urllib.parse.urlsplit` and fails
loudly at that point if it's malformed, rather than passing a raw string deep into peewee.

The connection-safety comment from `store/db.py` (never point Fledermap at `poiidx_db`, the
owner's real index, or `bats_db`) gets replicated at this new call site, naming
`poiidx_bats_db` — the database this connection must point at.

### 2. Fledermap's own filter config

poiidx's `init(filter_config, ...)` hashes the filter config together with its schema on every
call; **any drift drops and recreates every table** in `poiidx_bats_db`, discarding all downloaded
regions. The config must therefore be a fixed file, loaded identically on every call, never
constructed ad hoc.

New file `src/fledermap/services/data/poiidx_filter_config.yaml`, packaged the same way
`store/data/taxa_eu.yaml` already is (confirmed: `hatch build -t wheel` includes non-`.py` files
under `src/fledermap/` by default — no extra `pyproject.toml` packaging config needed).

Curated down from poiidx's own shipped default (`poi_filter_config.yaml`), which is tuned for a
POI-browsing consumer (restaurants, shops, hotels, transit) — irrelevant noise for naming a
monitoring site, and it would slow down every region's first-touch OSM parse for no benefit.
Fledermap's config keeps:

- `place: city / town / village / hamlet` (already in poiidx's default)
- `place: suburb / quarter / neighbourhood` (**not** in poiidx's default — added because the
  parent spec explicitly calls for "quarters, suburbs, villages")
- The existing `forest_or_park` group (`landuse`/`leisure`/`natural`: `forest`/`park`/`wood`/
  `nature_reserve`) — "named parks and woods" is already well covered here, kept as-is.
- New `water_body` group (`natural: water`, `waterway: river`) — added during spec review, checked
  against the OSM Map Features wiki: bats forage heavily along water corridors, and a named lake
  or river is exactly the kind of landmark someone would use to describe a detector's placement.
  `natural=water` is the generic tag covering lakes/ponds/reservoirs without needing to enumerate
  every `water=*` sub-value. Deliberately excludes `natural=wetland`/`bay` and the moor/heath/scrub
  group — real habitat in some cases, but rarely carries a proper name in OSM the way a lake or
  river does, and the existing `forest_or_park` group's `leisure=nature_reserve` already catches
  most protected wetland/heath areas that *are* named. Also excludes `waterway=stream` — far more
  numerous than named rivers, adding more visual/matching noise than naming value.

Everything else in poiidx's default (tourism, food & dining, accommodation, shopping, public
transport, entertainment, historic sites, landmarks) is dropped.

### 3. Query module

New `services/site_naming.py`:

```python
def name_site(centroid: Point, radius_m: float) -> tuple[str, str] | None:
    ...
```

1. Round `centroid` to a cache key (same rounding `SiteNameCache`'s existing `geohash`-keyed
   design implies) and check `SiteNameCache` first. A hit returns immediately — no poiidx call.
2. On a miss: call `poiidx.get_nearest_pois(shape, max_distance=N, limit=K)`, where `shape` is a
   buffer around `centroid` (or the site's own extent). Among the results within `N`, pick the
   candidate whose `rank` is **closest to a target rank derived from the site's own radius**
   (corrected 2026-09-01 — see SN-7 below; the original "lowest rank always wins" rule shipped,
   then a hotfix flipped it to "highest rank always wins" to fix the symptom that produced, and
   *that* shipped a different bug: sites named after something too specific to represent their own
   area, sometimes outside it entirely).
3. No POI found within `N` → fall back to `poiidx.get_administrative_hierarchy_string()`, which
   depends only on administrative-boundary containment, not nearby tagged POIs, so it's always
   available.
4. Write the resolved `(name, admin_path)` into `SiteNameCache` before returning, keyed on the
   same rounded coordinate.

`N` (search radius) and `K` (candidate count) are new config constants —
`FLEDERMAP_SITE_NAMING_RADIUS_M` (default `300.0`, a config-file/env pair like `site_eps_m`) and a
fixed `K = 5` (module constant, mirrors poiidx's own example's `limit=5`; not worth exposing as a
setting until real usage shows a reason to).

**Performance note (corrected from an earlier, overstated framing during brainstorming):**
poiidx's "may block for minutes" caution is specifically about the *first touch of a new
geographic region* — downloading and parsing a Geofabrik `.pbf`. Once a region has been scanned
once, later queries against it are ordinary fast Postgres KNN lookups (poiidx uses real spatial
indexes), and that first-touch cost amortizes across every site in the same region, not just one.
The `geo` queue's mutual-exclusion lock (below) exists so two never-before-touched-region
downloads can't race each other, not because every poiidx query is inherently slow.

### 4. Job wiring

New task in `jobs/tasks.py`:

```python
@app.task(queue="geo", pass_context=True, retry=_RETRY)
def name_site_task(context, site_id: int) -> None: ...
```

`lock` is applied at defer time via `.configure(lock=_NAME_SITE_LOCK, ...)`, in
`enqueue_site_naming` — not as a decorator argument; Procrastinate has no `lock=` parameter on
`@app.task` itself.

- `queue="geo"` — the reserved-but-unused queue name from Phase 3.
- `retry=_RETRY` (decorator argument) — reuse the existing shared retry policy (3 attempts,
  exponential backoff), matching the media tasks' handling of transient resource failures.
- `lock="poiidx-name-site"` (applied at defer time, as noted above, not a decorator argument) — a
  single static lock value, so Procrastinate serializes execution of every `name_site` job against
  every other one, regardless of overall worker concurrency. This is the same mechanism
  `_INGEST_CYCLE_LOCK` already uses in this codebase (there for coalescing; here for mutual
  exclusion during a possible first-touch region download).

**Enqueue, cache-first (the mitigation for `derive_sites`'s wholesale-rebuild reset):** wherever
`derive_sites` finishes rebuilding (its caller in `jobs/tasks.py`'s ingest cycle, and the
`fledermap derive` CLI command), a new `enqueue_site_naming(sites, engine)` runs, cache-first, per
site:

1. Round the site's centroid to `SiteNameCache`'s key.
2. `SiteNameCache` hit → copy `name`/`admin_path` onto the `Site` row directly, synchronously, no
   job at all.
3. `SiteNameCache` miss → enqueue `name_site_task(site_id)`.

This keeps queue/job-table churn down to genuinely new locations — a long-stable site never
re-enqueues a job on every derive cycle, it just gets its name copied straight back from cache in
the same transaction that rebuilt it.

If `poiidx_database_url` is unset, `enqueue_site_naming` is a no-op entirely (checked once, at the
top) — sites simply keep `name = NULL` and the existing `fallback_site_label` renders as it does
today. This is the mechanism behind the "optional integration, current behavior preserved" goal.

**Backfill CLI**, mirroring `backfill_media`/`fledermap enqueue-media`: `fledermap
backfill-site-names` — cache-first the same way, for any `Site` still missing a name (covers a
site that existed before this feature shipped, or one whose job failed past its retry budget).

### 5. Docs

- `docs/setup.md`'s settings table gains the `poiidx_database_url` row (see §1) and a short
  paragraph alongside the existing `poiidx_bats_db` warning, pointing at this new connection.
- The parent design spec's Phase 4 P4-1 deviation note gets a closing update once this ships: it
  currently reads "unscheduled — a future task should either build them or retire the plan"; this
  is that future task.
- `docs/references.md`'s existing `../poiidx` entry is accurate as-is (already documents the
  destructive-drop hazard) — no change needed there.

## Testing / Verification

- `Config.from_env` gets a test asserting the constructed `Config.poiidx_database_url` attribute
  (both set and unset), per this project's own documented convention — parsing without asserting
  the final attribute has caused a real bug here before (`port` was silently dropped once).
- URL-parsing (the `urlsplit` → poiidx kwargs step) gets direct unit tests: a well-formed URL, and
  a malformed one raising loudly.
- `services/site_naming.py`'s cache-hit / cache-miss / rank-preference / administrative-hierarchy-
  fallback paths are each testable against a fake/stub poiidx call (no real poiidx or Geofabrik
  network access in the test suite) — matching how the rest of this project's `db`-marked tests
  avoid real external services.
- `enqueue_site_naming`'s cache-first behavior: a `SiteNameCache` hit must produce zero enqueued
  jobs and a directly-updated `Site` row; a miss must produce exactly one enqueued job.
- A `poiidx_database_url`-unset test asserting `enqueue_site_naming` is a true no-op (no jobs, no
  errors) — the "optional integration" goal's actual regression test.
- `jobs/tasks.py`'s existing `_RETRY`/lock-key test conventions extend to `name_site_task`.

## Decisions

| # | Decision |
|---|---|
| SN-1 | poiidx wired in as a normal pinned PyPI dependency (`poiidx>=0.0.9`), not vendored — confirmed published and identical to the local checkout. |
| SN-2 | `FLEDERMAP_POIIDX_DATABASE_URL` is optional; unset means the feature is off and behavior is unchanged from today. |
| SN-3 | Fledermap ships its own curated `poiidx_filter_config.yaml` (toponymy-focused: place hierarchy + suburb/quarter/neighbourhood + forest/park + named water bodies/rivers), not poiidx's general-purpose default. |
| SN-4 | `derive_sites` is not changed to preserve unchanged sites across a rebuild — that's a separate, higher-risk change to clustering identity; `SiteNameCache` solves the actual repeat-work problem at much smaller scope. |
| SN-5 | `enqueue_site_naming` checks `SiteNameCache` synchronously before enqueueing anything, so a stable site's name survives every `derive_sites` rebuild without a job round-trip. |
| SN-6 | `name_site_task` uses a single static Procrastinate `lock`, not a second dedicated worker process, to serialize poiidx access. |
| SN-7 | **Superseded 2026-09-01.** Original: "the lowest-`rank` result wins over the merely-nearest one." Shipped, then hand-patched to the opposite ("highest rank wins") to fix the symptom that rule produced (every site named after its city) — which shipped a different bug (sites named after something too specific for their own area, sometimes outside it). Corrected rule, validated against real field data: among candidates within the search radius (which is now `max(N, site's own radius)`, not `N` alone, so a site bigger than the configured default is never searched smaller than its own footprint), pick the one whose `rank` is closest to a target rank computed from the site's own radius via poiidx's own nominatim-style formula (`services/site_naming.py`'s `_target_rank`) — a small site wants a specific name, a large one wants a broad one. Additionally, any candidate specific enough to matter (`rank > 19`) whose own geometry (poiidx returns real polygon/line geometry for way/relation-sourced POIs, not just a centroid) does not intersect the site's own extent — padded by a 15m tolerance margin for GPS/OSM-digitization noise — is sorted behind every candidate that does, though never discarded outright. `SiteNameCache.geohash` was widened (migration `cef39eb1d63b`) and its cache key now buckets the site's own radius (not the saturating target rank) alongside the coordinate, so two sites of genuinely different scale at the same rounded coordinate resolve — and cache, and enqueue their naming jobs — independently. |
| SN-8 (open) | `_INTERSECTS_RANK_THRESHOLD` (19) and `_INTERSECTS_MARGIN_M` (15m) are hardcoded, tuned against one device's field data at one location (code review finding, 2026-09-01). Deliberately not made configurable yet — no second location's data exists to tune against, and `site_naming_radius_m` is the config surface that's actually needed today. If a different region/device's POI density or GPS accuracy makes these need retuning, that's the trigger to promote them to config (same TOML/env pattern as `site_naming_radius_m`), not before. |
| SN-9 (open, blocked upstream) | **Neither poiidx call ever passes `buffer=`, despite SN-7 above describing the search as `max(N, site's own radius)`.** The widened search radius still drives `max_distance` (candidate filtering) correctly, but the matching `buffer` widening for poiidx's own region-loading (`init_regions_by_shape`) was reverted 2026-09-01, hours after merging: the installed `poiidx==0.0.9` crashes unconditionally on any non-`None` `buffer` (`local_shape.convex_hull().buffer(buffer)` calls a shapely *property* as a method — see `docs/references.md`'s poiidx entry for the full trace) — broke every real `name_site_task` run in production, caught only because the app was actually run against live poiidx, not by any test (all of them mock `poiidx.get_nearest_pois`). So the region-confinement gap SN-7's `buffer` fix was meant to close is open again for large sites near a poiidx region boundary, now deliberately, pending a poiidx release with the fix and a bumped Fledermap pin. |

## Open items

None.

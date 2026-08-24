# Fledermap — Phase 2 (Derivation) — design

**Date:** 2026-08-24
**Status:** design approved, not yet planned
**Parent spec:** `docs/superpowers/specs/2026-08-23-fledermap-design.md` — sections 4, 7, 12,
15, 16 are binding here and not re-litigated; this document pins down the concrete
schema, module, and testing shape those sections leave open.

---

## 1. Scope

Phase 2 ends when `fledermap derive` populates sessions and sites from what
`fledermap ingest` (Phase 1, complete, on `main`) already stored. Still headless — no
web, no job queue. Per parent spec §15: "Phases 1 and 2 are deliberately headless — the
data model is the risky part, and it is far cheaper to get wrong before a UI depends on
its shape."

**In scope:** session partitioning (incremental, gap-based), bridging-session detection
and merge-proposal persistence, site derivation (wholesale DBSCAN rebuild), the schema
both need, and the regression test the parent spec names as this phase's exit criterion.

**Out of scope (see §9):** site naming / poiidx integration, any web or API surface,
transect `kind` assignment.

---

## 2. Schema changes

```
session  (existing table, gains two columns)
  + weather   text | NULL   -- user-set later; parent spec's "durable annotation layer"
  + effort    text | NULL   -- ditto

site  (new table)
  id
  centroid        geometry(Point, 4326)
  radius_m        float
  recording_count int
  first_at, last_at   timestamptz
  name            text | NULL          -- filled by Phase 3
  admin_path      text | NULL          -- filled by Phase 3

site_name_cache  (new table, per parent spec §7 verbatim — schema only, unused until Phase 3)
  geohash, name, admin_path, fetched_at

session_merge_proposal  (new table)
  id
  session_a_id, session_b_id   fk session
  bridging_recording_id        fk recording
  detected_at                  timestamptz
  resolved_at                  timestamptz | NULL
  resolution                   'merged' | 'rejected' | NULL

recording  (existing table, gains one column)
  + site_id   fk site, ON DELETE SET NULL   -- NULL = one-off spot, not an error (§7)
```

`site.name`/`admin_path` and `site_name_cache` are created now, schema-only, rather than
in a later migration — matching parent spec §12's point that the data model should be
settled before anything downstream (Phase 3's naming job) depends on its shape. Nothing
in Phase 2 writes to them.

---

## 3. Module layout

Already fixed by parent spec §4:

```
derive/
  sessions.py    partition_sessions() -- gap-based, incremental, per (make, serial)
  sites.py       derive_sites()       -- wholesale DBSCAN rebuild; also hosts the
                                          ported GeoCluster summariser
util/
  projection.py  LocalProjection, ported from mkmapdiary/util/projection.py
```

`GeoCluster` is a pure per-cluster summary (`mass_point` → centroid, `radius`,
`zoom_level`) over an already-formed point set — parent spec §7: "`GeoCluster` does not
cluster despite its name." It lives with `sites.py`, which is its only caller, not in
`util/` alongside the projection.

**New dependencies** (none currently in `pyproject.toml`): `scikit-learn` (DBSCAN),
`numpy`, `scipy` (`GeoCluster` needs `scipy.stats.zscore` for outlier removal and
`scipy.spatial.ConvexHull`), `shapely`, `pyproj` (`LocalProjection`).

`LocalProjection` and `GeoCluster` are ported (copied and adapted, not
reimplemented) from `../mkmapdiary/src/mkmapdiary/util/projection.py` and
`.../lib/geoCluster.py`. The relicensing from mkmapdiary's PolyForm Noncommercial to
Fledermap's MIT is deliberate and already settled by parent spec §16 — the owner holds
copyright to both, so it is not re-decided here.

Note: mkmapdiary itself does not use DBSCAN anywhere (it clusters via
`sklearn.cluster.AgglomerativeClustering` for an unrelated purpose); DBSCAN is a
Fledermap-specific choice per parent spec §7, not something ported.

---

## 4. `partition_sessions()`

- Query recordings with `session_id IS NULL`, grouped by `(make, serial)` (the model's
  `detector_key`), ordered by `recorded_at`.
- Walk each group in time order: a recording joins the group's most recent session if
  `recorded_at - previous.recorded_at <= session_gap` (config, default 6h), else starts
  a new session. New sessions default `kind='stationary'`, matching the existing model
  column default — nothing sets `'transect'` until the future UI does (parent spec §9:
  `kind` is user-set).
- **Never renumbers or reassigns an already-derived `session_id`** (parent spec §7:
  "incremental, never renumbered") — this only touches rows where `session_id IS NULL`.
- **Bridging detection:** if an out-of-order recording's timestamp falls in the *gap*
  between two existing, already-persisted sessions for the same detector — after A's
  `ended_at`, before B's `started_at`, and within `session_gap` of both — rather than
  simply extending the most recent session, record a `session_merge_proposal` naming
  both candidate sessions and the bridging recording. The recording still needs a
  session to belong to: it joins the earlier of the two candidates. The proposal row is
  what surfaces the ambiguity for a human later; Phase 2 never merges automatically
  (parent spec §7: "never an automatic merge").

---

## 5. `derive_sites()`

- Query all recordings where the owning session's `kind == 'stationary'` and
  `geom IS NOT NULL`.
- Project every coordinate into a shared local CRS via `LocalProjection`, picked once
  from the centroid of the whole point set, so `eps` is in metres (parent spec §7's
  pitfall: raw EPSG:4326 degrees would silently turn a 75 m radius into ~8 km).
- Run `sklearn.cluster.DBSCAN(eps=site_eps_m, min_samples=site_min_points)` on the
  projected coordinates.
- **Wholesale rebuild, transactionally:** `DELETE FROM site` — not `TRUNCATE`, which in
  Postgres does not fire `ON DELETE` foreign-key actions the way `DELETE` does (it either
  errors on a referencing FK or, with `CASCADE`, truncates the *referencing* table too —
  `recording`, which must not happen). A plain `DELETE` cascades `recording.site_id` to
  `NULL` via `ON DELETE SET NULL` as intended. Then insert one `site` row per non-noise
  DBSCAN label
  with `GeoCluster` supplying `centroid = mass_point`, `radius_m = radius`, and
  `recording_count` / `first_at` / `last_at` computed from the member rows, then set
  `site_id` on every member recording. DBSCAN's `-1` (noise) label leaves `site_id
  = NULL` on those recordings — a one-off spot, not an error (parent spec §7).
- `site_name_cache` is read and written by nobody in this phase.

---

## 6. Config additions

Extend the existing `Config` dataclass (`src/fledermap/config.py`) following the same
`from_env` / `ConfigError` pattern already used for `default_timezone`:

- `session_gap_hours: float = 6.0`
- `site_eps_m: float = 75.0` (parent spec §7's own example radius)
- `site_min_points: int = 3` — no parent-spec guidance on this one; a judgment call
  made in this design (floor for "this is a real site, not a fluke of 1-2 passing
  recordings"), confirmed with the user during design review.

---

## 7. CLI

`fledermap derive` (parent spec §4: `cli: fledermap ingest | derive | worker | serve`).
Runs `partition_sessions()` then `derive_sites()` in one invocation, and prints a
summary: new/extended session counts, merge proposals raised, cluster count and the
delta from the previous rebuild. No flags planned beyond what `Config` already supplies
via environment variables.

---

## 8. Testing

- **Pure-function unit tests**, no database: `LocalProjection`, `GeoCluster` (ported —
  largely re-running mkmapdiary's existing math tests, `test_geo_cluster_math.py`,
  against our copy), and `cluster_points()` (Task 8's DBSCAN wrapper — takes and
  returns plain arrays, no ORM involved).
- **DB-backed tests** (`pytest.mark.db`, matching the existing Phase 1 pattern) for
  everything that persists or reads state: `partition_sessions()`, site persistence,
  and merge-proposal detection end to end. `partition_sessions()`'s gap decision
  bisects against real, possibly-just-flushed `Session` rows (a new session created
  mid-run must be visible to the next recording's bisect) — the same reason
  `sweep_missing` (Phase 1) is DB-backed rather than split into a pure core, not an
  oversight to fix later.
- **Phase-exit regression test (parent spec §15's own exit criterion):** "clustering
  regression test passes at both latitudes" — one fixture dataset centred near a
  high-latitude point (e.g. Germany, ~50°N, small UTM distortion) and one near the
  equator, asserting DBSCAN cluster membership is correct in both. This is what actually
  catches a broken UTM-zone selection or a degrees-vs-metres `eps` unit bug — the exact
  pitfall parent spec §7 calls out.

---

## 9. Explicitly out of scope for Phase 2

- Site naming, poiidx integration, the `geo` job queue (Phase 3: "Procrastinate
  running"). `site.name` / `admin_path` and `site_name_cache` stay empty.
- Transect `kind` assignment — user-set, no UI exists yet; every session defaults to
  `'stationary'`.
- Any web or API surface for sites, sessions, or merge proposals (Phase 4/5).

---

## 10. Decisions made in this document

| # | Decision | Why |
|---|---|---|
| P2-1 | DBSCAN runs in Python (scikit-learn) over coordinates pulled from PostGIS, not via `ST_ClusterDBSCAN` in SQL | Keeps the clustering math a pure, easily unit-tested function; PostGIS stays storage/geometry only |
| P2-2 | `fledermap derive` is a separate command from `fledermap ingest`, run manually | Site rebuilding is O(all stationary recordings) and wholesale; coupling it to every ingest run hides an expensive step inside a cheap one |
| P2-3 | Bridging-session merge proposals are persisted now, in a dedicated table, despite no UI existing to act on them until Phase 5 | Parent spec §15: the data model is the risky part to get right before a UI depends on its shape; re-deriving the same detection logic later would be wasted work |
| P2-4 | `site.name`, `site.admin_path`, and `site_name_cache` are created as schema now, populated later (Phase 3) | Avoids a second migration purely to add naming columns once poiidx integration lands |
| P2-5 | `site_min_points` defaults to 3 | No parent-spec guidance; a floor below which a cluster reads as noise rather than a real site |

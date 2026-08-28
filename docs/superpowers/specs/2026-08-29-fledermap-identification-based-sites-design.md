# Fledermap Identification-Based Site Derivation — Design

**Status:** draft — sections approved individually in chat during brainstorming; awaiting the
user's review of this written spec (see brainstorming skill's user-review gate) before writing an
implementation plan.
**Date:** 2026-08-29

## Problem

`derive_sites` (Phase 2, P2-2) clusters GPS-bearing recordings into `Site` rows, but only for
recordings belonging to a `STATIONARY`-classified `Session` — a session-kind heuristic added in
Phase 5b to decide "stationary vs. walked transect" purely so a transect's centroid wouldn't be
mistaken for one meaningful point.

This coupling was a practical shortcut, not an ecological claim, and it has two real costs,
both surfaced by a real field session (`Session_20260828_205144`, a genuine ~3.4 km walked
transect):

1. **A transect that passes through a real hotspot is invisible to site derivation no matter how
   much activity it recorded there.** `derive_sites` never even looks at a `TRANSECT` session's
   recordings.
2. **A site can form from unidentified noise.** Today's clustering has no identification filter
   at all — a dense cluster of `NO_ID`/`NOISE`-verdict recordings becomes a `Site` exactly as
   readily as a dense cluster of confirmed bat calls, which doesn't match "a site is a place where
   we find bats."

## Goals

- A `Site` is defined purely by where identified bat activity clusters — independent of which
  session (or session kind) a recording happens to belong to.
- Only recordings whose current-best identification is `Verdict.SPECIES` contribute to site
  clustering — the same "hide noise" rule the map already applies by default
  (`map_query._passes_verdict_filter`, decision P4-9). One definition of "we found a bat here"
  for the whole app, not two.
- `SessionKind` (`stationary`/`transect`) is removed entirely — site derivation was its only
  consumer, and with that gone nothing in the codebase reads it.
- No new mechanism is needed to recompute sites when an identification changes (see Design §2).

## Non-goals

- **`derive_sites`'s wholesale-rebuild-every-run architecture is unchanged.** `Site` stays a
  derived projection with no persistent identity across a rebuild (P2-2: "tuning is free") — this
  design only changes which recordings feed the rebuild, not how the rebuild itself works.
- **Session *partitioning* (grouping recordings into sessions by detector + time gap) is
  unchanged.** Only the kind-classification layer built on top of it in Phase 5b goes away.
  Sessions themselves, `SessionMergeProposal`, and the merge-review workflow are untouched.
- **No change to poiidx site naming.** `SiteNameCache` is keyed on rounded coordinates, not site
  membership or identity, so it's unaffected by what recordings compose a site.
- **No new "manual re-identification" UI.** Identification changes still only arrive via re-ingest
  (`services/ingest.py`'s `commit_scan`), the same as today.

## Design

### 1. New site-membership query (`services/derive.py`)

Drop the `Session` join and `Session.kind == STATIONARY` filter entirely. Select every
`Recording` with a GPS point, then filter to `current_best_identification(r).verdict ==
Verdict.SPECIES` in Python — the same "SQL prefilter, then Python for identification logic" split
`services/map_query.py` already uses (`current_best_identification` operates on an in-memory
`Recording.identifications`, so pushing the logic into SQL would duplicate it, per that module's
own docstring). A recording with no non-superseded identification at all is excluded, matching
`_passes_verdict_filter`'s treatment of "no best" as equivalent to `NO_ID`.

```python
recordings = [
    r
    for r in db_session.scalars(
        select(Recording).where(Recording.geom.is_not(None)),
    )
    if (best := current_best_identification(r)) is not None
    and best.verdict == Verdict.SPECIES
]
```

The existing `.options(raiseload(Recording.identifications))` guard is removed — it existed
specifically because `derive_sites` never touched identifications; now it does, so the query falls
back to `Recording.identifications`'s model default (`lazy="selectin"`), the same as
`map_query.filtered_recordings` relies on with no special loader option at all.

Everything downstream of the filtered recording list — DBSCAN clustering via `cluster_points`,
`GeoCluster` mass-point/radius, `Site` construction — is unchanged.

### 2. Verdict changes recompute sites for free

`Identification` rows are written in exactly one place in the codebase: `services/ingest.py`'s
`commit_scan`. `commit_scan` only ever runs inside `jobs/tasks.py`'s `run_ingest_cycle`, which
already calls `derive_sites()` unconditionally on every cycle (5-minute cron, or an on-demand
watcher-triggered run), regardless of what changed upstream. Since `derive_sites` already does a
full `DELETE FROM site` + rebuild on every call, a verdict change is picked up automatically the
next time it runs — no new trigger, no per-site invalidation, no cache to bust. Worst case is the
same staleness window every other ingest-cycle-driven effect already has.

This also means the CLI `derive` command needs no changes beyond dropping the now-gone
`transect_distance_m` argument it stopped needing to pass (§3) — it already re-derives from
current DB state on every invocation.

### 3. `SessionKind` removed entirely

Site derivation was `SessionKind`'s only consumer. With it gone, nothing in the codebase reads
`Session.kind` or `Session.kind_locked` — grepped and confirmed before writing this section.

**Schema — new Alembic migration:**
- `op.drop_constraint("sessionkind", "session", type_="check")` (the hand-written CHECK from
  `e9a0c0f92971_phase_2_derivation_schema.py`)
- `op.drop_column("session", "kind")`
- `op.drop_column("session", "kind_locked")`
- `downgrade()` restores all three in reverse order, including re-adding the CHECK and setting a
  sensible default (`'stationary'`) for any row a downgrade would otherwise leave without one.

**Code removed:**
- `SessionKind` enum (`domain/codes.py`).
- `classify_kind` and `reclassify_session` (`derive/sessions.py`), including the GPS-spread
  heuristic and `LocalProjection`/`pdist` usage that existed only to feed them.
- `kind`/`kind_locked` columns (`store/models.py`'s `Session` model).
- `partition_sessions`' `transect_distance_m` parameter and its `kind=SessionKind.STATIONARY`
  default on newly created sessions (`derive/sessions.py`) — partitioning itself (detector + gap
  grouping) is untouched.
- The kind-handling block (`kind_raw` parsing, `session_obj.kind = kind`,
  `session_obj.kind_locked = True`) and `transect_distance_m` threading in the session-detail POST
  handler (`web/views/sessions.py`).
- The `reclassify_session` call and `transect_distance_m` parameter in `resolve_merge_proposal`
  (`services/sessions.py`).
- The "Kind" `<select>` field in `session_detail.html`.
- `transect_distance_m` end to end: `Config.transect_distance_m` and `ENV_TRANSECT_DISTANCE_M`
  (`config.py`, including its `_KNOWN_FILE_KEYS` entry and parsing block), `web/app.py`'s
  `TRANSECT_DISTANCE_M` flask config plumbing, both `partition_sessions` call sites in
  `cli/main.py`, and the one in `jobs/tasks.py`'s `run_ingest_cycle`.
- The `FLEDERMAP_TRANSECT_DISTANCE_M` row and commented example line in `docs/setup.md`.

**Two design specs get a superseded note** (not rewritten — matches this project's existing
dated-deviation-note practice, e.g. `FLEDERMAP_MEDIA_ROOT`'s in `CLAUDE.md`) pointing at this
spec:
- `2026-08-24-fledermap-phase2-derivation-design.md` — where STATIONARY-only site derivation was
  originally decided.
- `2026-08-27-fledermap-phase5b-sessions-design.md` — where the GPS-spread heuristic and
  `kind_locked` were designed and built.

### 4. Test impact

- `tests/test_derive_sites.py`: fixtures rebuilt around identification/verdict instead of session
  kind. New cases: a `SPECIES`-verdict cluster becomes a site regardless of session kind history;
  a cluster of only `NO_ID`/`NOISE`/no-identification recordings does not; a mixed cluster counts
  only the `SPECIES`-verdict members (`recording_count`, `first_at`/`last_at` reflect only those).
- `tests/test_partition_sessions.py`, `tests/test_resolve_merge_proposal.py`,
  `tests/test_sessions_view.py`, `tests/test_config.py`: drop the
  `classify_kind`/`reclassify_session`/kind-form/`transect_distance_m` tests.
- `tests/test_migrations.py`: drop `test_migrated_kind_check_is_enforced` and
  `test_migrated_kind_check_accepts_every_kind` (the constraint no longer exists); strip the now
  by design broken `kind='stationary'` clause from every raw-SQL `INSERT INTO session` elsewhere
  in the file (several unrelated tests only carried it because the column was `NOT NULL`); drop
  the now-unused `SessionKind` import.
- Sweep `tests/test_models.py`, `tests/test_map_view.py`, `tests/test_store_geo.py`,
  `tests/test_cli.py`, `tests/test_jobs_tasks.py` for incidental `SessionKind`/`kind=` fixture
  usage and update or remove as needed.
- `tests/test_setup_docs.py` needs no code change — it validates `docs/setup.md`'s env-var table
  against `Config`'s `_KNOWN_FILE_KEYS`/`ENV_*` constants, so it passes once both sides drop
  `transect_distance_m` together.

## Decisions

| # | Decision |
|---|---|
| SB-1 | Site membership is determined by identification verdict (`SPECIES` only, via `current_best_identification`), independent of session or session kind. |
| SB-2 | `SessionKind` is removed entirely rather than kept as decoupled descriptive metadata — confirmed no other consumer exists. |
| SB-3 | No new recompute trigger for identification changes: the existing unconditional per-cycle `derive_sites()` call already covers it, since `Identification` is only ever written inside that same cycle. |
| SB-4 | Session partitioning (grouping by detector + time gap) is untouched — only the kind-classification layer built on top of it in Phase 5b is removed. |

## Open items

None.

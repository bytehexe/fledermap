# Fledermap — Drop `Identification.superseded_at` — Design

**Status:** design, not yet implemented
**Date:** 2026-09-06

## Problem

`Identification.superseded_at` is a soft-delete column: a claim that's no longer current gets
stamped with a timestamp instead of removed, and every reader (`current_best.py`,
`map_query.py`) filters on `superseded_at IS NULL` to see only "live" claims. It exists to answer
"what did this source used to claim, before it changed its mind" — but nothing actually answers
that question today. `_recording_panel.html` renders a superseded row as `source: label`, struck
through, with no `source_version`, no timestamp, nothing distinguishing what changed or when.
Nothing else in the codebase reads a superseded row for anything beyond that display and a
per-run ingest counter (`IngestReport.identifications_superseded`).

Meanwhile it actively costs correctness elsewhere. Manual classification's `set_manual
_classification` re-submits the full current state on every classifier-box save (add a chip,
remove a chip, toggle a verdict), always superseding-then-reinserting — so a claim edited five
times leaves ~15 dead rows in the Identifications box (2026-09-05 final review finding #2). The
mechanism this soft-delete requires — a partial unique index (`uq_identification_source_claim`,
`WHERE superseded_at IS NULL`, `postgresql_nulls_not_distinct=True`) instead of a plain
`UniqueConstraint` — was itself only needed because a superseded row's key stays "claimed" under
a plain constraint; it was the fix for a real production crash (`f31e2f5`, migration
`300b54c8829a`, 2026-09-05) that a plain constraint couldn't have caused if there were no
superseded rows to collide with in the first place.

There is a plausible future case for per-source history — a configurable-threshold automatic
classifier (the speculative `fledermap.noise` backlog idea) re-scoring every recording each time
its threshold changes — but that classifier doesn't exist, its design isn't settled, and
generalizing retention policy now for it would be guessing at requirements no one has yet. See
"Non-goals."

## Goals

- No `Identification` row is ever soft-deleted. A claim that a source no longer makes is deleted
  outright; a claim whose details changed is updated in place.
- One algorithm handles this for every source — not source-type-specific special casing that
  happens to converge on the same policy by coincidence.
- `uq_identification_source_claim` goes back to being a genuine `UniqueConstraint` — no partial
  index, no `WHERE` clause to keep in sync with anything.
- `current_best.py` and `map_query.py` lose every `superseded_at.is_(None)` filter — dead
  conditions once every row is guaranteed live.
- Existing superseded rows in `bats_db` are actually removed as part of the migration, not left
  behind to silently "come back to life" once the column (and the filters that hid them) is gone.

## Non-goals

- **No per-source retention policy configuration.** If a future classifier genuinely needs
  history (the threshold-churn case above), that is that feature's own design decision, made
  when it exists — not something this change should anticipate or leave a hook for.
- **No change to `Verdict`, `IdSource`, or the precedence order** (`_PRECEDENCE` in
  `current_best.py`). This only touches how a source's *current* claim set is stored and diffed,
  not what a claim means or which source wins.
- **No UI change.** The Identifications box already renders whatever live rows exist; it simply
  never sees a struck-through row again, with no template restructuring needed beyond deleting
  the now-dead `superseded` branch.

## Design

### 1. Schema: plain unique constraint, no `superseded_at`

`Identification` drops the `superseded_at` column entirely. `uq_identification_source_claim`
drops `source_version` and `raw_label` from its key (see §2 for why) and becomes a plain
constraint:

```python
UniqueConstraint(
    "recording_id", "source", "taxon_id",
    name="uq_identification_source_claim",
    postgresql_nulls_not_distinct=True,
)
```

`postgresql_nulls_not_distinct` is still needed: the NO_ID/NOISE sentinel row has `taxon_id IS
NULL`, and without it Postgres would allow unlimited duplicate sentinel rows per `(recording,
source)` (same reason the original partial index needed it).

`source_version` and `raw_label` remain plain columns on `Identification` — they still record
what the current claim's version/label is, they just no longer participate in identifying *which
row this is*.

### 2. Write path: one shared replace-the-set helper

New module, `services/identifications.py` — neither `ingest.py` nor `manual_classification.py`
imports the other today, and putting the shared helper in either would create exactly that
dependency for no reason:

```python
@dataclass(frozen=True)
class ClaimInput:
    taxon_id: int | None
    verdict: Verdict
    raw_label: str | None = None
    source_version: str | None = None

def replace_claims(
    session: OrmSession,
    recording: Recording,
    source: IdSource,
    desired: Sequence[ClaimInput],
    now: datetime,
) -> ReplaceResult:  # counts of added / updated / removed, for callers that report on it
    ...
```

Identity within one `(recording, source)` scope is `taxon_id` (`None` meaning the sentinel
claim). For each call:

- A live row whose `taxon_id` isn't in `desired` any more: **deleted**.
- A live row whose `taxon_id` is still in `desired`: **updated in place** — `verdict`,
  `raw_label`, `source_version` overwritten if changed; `first_seen_at` left alone (it still
  means "when this row was created," not "when it was last confirmed"). `confidence` is a
  long-standing unused column (no `ParsedIdentification` field ever populates it, checked
  against every `Identification(...)` construction site) — out of scope here, untouched.
- A `taxon_id` in `desired` with no existing live row: **inserted**, `first_seen_at=now`.

This is why identity drops to `taxon_id` alone rather than keeping `raw_label`/`source_version`:
a version bump or a re-parsed label for the *same* underlying species is an update to that
species' row, not a new claim. Two genuinely different `raw_label`s that both fail to resolve to
a taxon (`taxon_id=None` for both) do collide under this scheme — but this is exactly today's
existing sentinel behavior (`NO_ID`/`NOISE`/unmapped-species-with-no-taxon all share `taxon_id
IS NULL` already) and single-claim-per-automatic-source is already the documented assumption
(`current_best.py`'s docstring), not a new limitation this change introduces.

**Callers:**

- `_apply_identifications` (`services/ingest.py`): for **every** source in `_EMT_SOURCES`
  (not just ones present in `parsed`), build a `desired` list — `[ClaimInput(...)]` from that
  source's entry in `parsed` if present, `[]` if not — and call `replace_claims`. The empty case
  matters: today, an EMT source whose chunk previously had a claim but no longer does (e.g. the
  operator clears an on-device manual correction, so `EMT_MANUAL` no longer appears in `parsed`
  at all) still gets its existing row superseded, because `_apply_identifications` walks *every*
  existing live key looking for one missing from `incoming`, not just keys `incoming` mentions.
  `replace_claims(..., desired=[])` must reproduce that: delete the row outright, don't skip it
  just because the source has nothing to contribute this scan.
- `set_manual_classification` (`services/manual_classification.py`): builds the full `desired`
  list from the submitted verdict/taxon_ids (one `ClaimInput` per taxon for SPECIES, one
  sentinel `ClaimInput(taxon_id=None, verdict=...)` for NO_ID/NOISE, empty list for "clear") and
  calls `replace_claims(session, recording, IdSource.MANUAL, desired, now)`. Its own validation
  (`verdict=SPECIES` requires `taxon_ids`, etc.) stays — that's about what a valid *save payload*
  looks like, unrelated to how the resulting set gets diffed against the DB.

### 3. Read path simplification

- `current_best.py`: `candidates = [i for i in recording.identifications if i.superseded_at is
  None]` becomes `candidates = list(recording.identifications)`. The non-MANUAL
  dedup-to-one-via-`max(first_seen_at)` step is deleted outright (not just left unreachable):
  update-in-place guarantees at most one live row per non-MANUAL source, so there is never a set
  of size >1 to dedupe.
- `map_query.py`: its three `Identification.superseded_at.is_(None)` filter clauses are removed.
- `identification_status`: the `"superseded"` branch and the `IdentificationStatus` literal's
  `"superseded"` member are deleted — three states remain (`current`/`passive`/`shadowed`).
- `_recording_panel.html`: the `{% if ident.superseded_at %} class="superseded"{% endif %}`
  conditional is deleted (always false, going forward). `.superseded`/`.identification-superseded`
  CSS rules in `app.css` are removed along with it.
- `IngestReport.identifications_superseded` is replaced by two counters,
  `identifications_updated` (an existing row's fields changed in place) and
  `identifications_removed` (a row deleted because its source no longer claims that `taxon_id`)
  — a single rename to `identifications_updated` isn't accurate on its own: the re-ID case that
  changes which `taxon_id` a source claims (e.g. `NoID` → a resolved species) is a delete-plus-add
  under `replace_claims`, not an in-place update, so it needs to show up as `removed`, not
  `updated`. `identifications_added` is unchanged.

### 4. Migration

1. **`scripts/db-backup.sh` first, always** — this deletes data.
2. `DELETE FROM identification WHERE superseded_at IS NOT NULL` — done explicitly, before the
   column drop, so no dead claim survives to be misread as live once nothing can tell the
   difference any more.
3. Drop the `uq_identification_source_claim` partial index.
4. Drop the `superseded_at` column.
5. Add the new plain `UniqueConstraint` from §1.

Written by hand (data-deleting steps and a constraint-shape change aren't autogeneratable), then
confirmed driftless the normal way (`tests/test_migrations.py`'s `compare_metadata`).
`test_migrated_partial_index_where_clause_is_enforced` (the mutation-tested test proving the old
partial index's `WHERE` clause was real) gets replaced with the equivalent proof for the new
plain constraint — insert a duplicate `(recording_id, source, taxon_id)` tuple, confirm Postgres
rejects it — and a second test proving `nulls_not_distinct` still caps sentinel rows at one.

### 5. Test impact

Every test that stamps `superseded_at=...` to set up a scenario is exercising supersession
directly and needs replacing (not just adjusting) with a test of delete/update-in-place instead:
`test_derive_sites.py`, `test_models.py`, `test_map_view.py`, `test_map_query.py`,
`test_current_best.py`, `test_ingest_service.py`. `test_manual_classification.py` gains
coverage for the new shared `replace_claims` behavior (update-in-place for an unchanged
`taxon_id`, delete for a dropped one) in addition to its existing save-shape tests.

## Decisions

- **D1 — identity is `(recording, source, taxon_id)`, not `(source, source_version, raw_label,
  taxon_id)`.** A version bump or re-parsed label for the same taxon updates the existing row
  rather than replacing it. Rationale: §2.
- **D2 — one shared `replace_claims` helper for both ingest and manual classification.**
  Rejected: two independently-maintained delete/update implementations (Approach B from
  brainstorming) — same policy, but nothing stops them drifting apart (e.g. supersession
  quietly reintroduced in only one).
- **D3 — no retention-policy hook for future classifiers.** YAGNI: the one concrete case
  (threshold-churn re-scoring) is speculative and unbuilt; solving it now means guessing at a
  design that doesn't exist yet.

## Open items

None — brainstorming converged; ready for `writing-plans`.

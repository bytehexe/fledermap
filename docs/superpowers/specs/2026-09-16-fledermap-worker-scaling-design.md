# Fledermap — worker ingest-cycle scaling design

Status: proposed. Scope: `run_ingest_cycle` (`jobs/tasks.py`) and the stages it drives every 5
minutes (cron) or on a debounced filesystem event (`jobs/watch.py`). Obsidian backlog item
"Profile the worker" (`[!!bug]` `[!!important]`) is the trigger.

Growth assumption for this design: the archive grows by **two orders of magnitude** (from
~1,300 recordings today to ~130,000). This spec sizes every fix against that target, but splits
work into what's needed at **one** order of magnitude (~13,000 recordings) — done now, see §3 —
versus what only bites at two (§4, written down for later, not implemented by this spec's own
changeset).

## 1. Measured baseline

Live evidence (systemd journal, `fledermap-worker.service`, 2026-09-16): 33 consecutive
`run_ingest_cycle` runs over ~65 minutes, ~88–97s each, back-to-back (each cycle starting as the
previous ends), every one logging `created 0 unchanged 1306` — no new files. 48m15s CPU consumed
in a 55-minute service run (~88% duty cycle).

Root cause, confirmed by profiling `run_ingest_cycle` end-to-end (`cProfile`, real archive/DB,
1306 recordings, nothing changed) — 100.9s total:

| stage | cumulative | share |
|---|---|---|
| `ingest/scan.py` full rescan (`scan_all_roots` → `_scan_one`) | 86.9s | 86% |
| `services/batdetect2.py:enqueue_batdetect2` | ~7–12s | 7–12% |
| `services/classifier_detections.py:materialize_claims` | 4.5s | 4.5% |
| `services/ingest.py:commit_scan` | 1.8s | 1.8% |
| `services/derive.py:derive_sites` | 0.39s | <1% |
| `services/noise.py:classify_noise` | 0.16s | <1% |
| `derive/sessions.py:partition_sessions`, `sweep_missing`, `enqueue_site_naming` | ~0s | — |

Two distinct problems compound each other:

1. **`scan_all_roots` is O(archive bytes), not O(changed bytes).** `ingest/scan.py`'s
   `scan_with_skips` walks every file under every archive root (`root.rglob("*")`) and, for
   every settled file — changed or not — calls `_scan_one`, which does a full PCM decode
   (`media/wav_pcm.py:read_pcm`), a full STFT-based SNR computation (`ingest/snr.py:
   compute_snr_db` — 70% of total cycle time on its own, `numpy.ndarray.partition` inside maad's
   noise-floor estimate alone is 23%), and a SHA-256 hash over the full audio payload
   (`ingest/riff.py:audio_hash`, 22%). None of this is gated on whether the file has actually
   changed since the last successful scan.
2. **`jobs/watch.py`'s `_Handler.on_any_event` re-triggers that same full rescan on *any*
   filesystem event anywhere under an archive root**, filtered only on `is_directory` — not on
   hidden path components. `~/Bat Sessions` is a live Syncthing folder (`.stfolder` present),
   so ordinary sync/housekeeping activity keeps generating real events that debounce into
   back-to-back full rescans, which is what produced the observed ~88% duty cycle.

A third, independent finding from the `EXPLAIN ANALYZE` of `enqueue_batdetect2`'s query: it
resolves "recordings still missing a `classifier_detection` row" via
`Recording.id.not_in(select(...))`, planned as a `Seq Scan` on `recording` with a materialized
`NOT IN` subplan over all of `classifier_detection` (334,025 rows today, filtered only by a
plain `source` index — no composite index backs the anti-join). 12.2s measured for 1306
recordings. `enqueue_battybirdnet` has the identical shape (currently untested live, since
battybirdnet is disabled on this deployment) and will have the same cost once enabled.

## 2. What's provably NOT a problem

Cross-checked so the fix doesn't over-reach:

- `partition_sessions` already scopes its query to `session_id IS NULL` — genuinely O(new
  recordings), not O(n).
- `enqueue_batdetect2`/`enqueue_battybirdnet`'s *dispatch* logic (which recordings need a job)
  is the right shape (an anti-join) — only the anti-join's query plan is the problem, not the
  approach.
- `derive_sites`'s DBSCAN clustering itself is O(n log n) (sklearn's KD-tree backend) — fine at
  any size this project will plausibly reach. Its previous-site reconciliation loop (see §3.4)
  is the part that's actually quadratic, and it's quadratic in **site count** (~47 today), not
  recording count.
- `commit_scan`'s per-file DB resolution cost (1.8s today) is *already* bounded by whatever
  `scan_all_roots` hands it — once §3.1 lands, an unchanged file never reaches `commit_scan` at
  all, so this stage's cost automatically collapses to O(changed files) with no separate fix
  needed.

## 3. Fixes for one order of magnitude — implemented now

### 3.1 Skip re-scanning unchanged files (Medium)

The dominant cost (86% of cycle time) and the one item that turns "gets slower" into "breaks
the 5-minute cadence outright": at 10x (13,060 files), an unconditional full rescan projects to
~14.5 minutes — already longer than the cron interval that's supposed to trigger the next one.

Design: before `_scan_one`'s expensive path (PCM decode, SNR, hash) runs for a given file, check
a cheap fingerprint — `(path, size, mtime)` — against what's already on record for that exact
path. A match means "this is the same file we already fully processed"; skip straight to
UNCHANGED without opening the file for content at all. Any mismatch (new path, or a changed
size/mtime) falls through to the existing full-content path unchanged — correctness never
depends on the fingerprint being reliable, only performance does, so a false "changed" verdict
(e.g. Syncthing touching mtime without content changing) costs one wasted full rescan of that
one file, never a wrong result.

This needs two new columns on `Recording` — `scan_size_bytes: int`, `scan_mtime: float` (epoch
seconds) — populated by `commit_scan` on every CREATED/UPDATED/MOVED/REPLACED resolution
(needs a migration; see CLAUDE.md's "Migrations" section for the drift-test discipline that
applies to any model change).

Keeping `ingest/` pure (D16, no DB access from that package): `scan_with_skips`/`_scan_one`
gain a `known: Mapping[Path, tuple[int, float]]` parameter — a plain dict, not a session —
so `services/ingest.py`'s `scan_all_roots` can issue **one** query for every currently-known
`(path, scan_size_bytes, scan_mtime)` triple up front and pass the dict in, rather than `ingest/`
importing SQLAlchemy. `SkipReason` gains an `UNCHANGED_FINGERPRINT` (or similar) branch so CLI/
log summaries can distinguish "known unchanged, not even opened" from today's "opened, hashed,
and confirmed unchanged" — useful for verifying the fix actually engaged.

### 3.2 Filter watcher events on hidden paths (Cheap)

`jobs/watch.py`'s `_Handler.on_any_event` currently only checks `event.is_directory`. Add a
check mirroring `ingest/scan.py`'s own `SkipReason.EXCLUDED` reasoning (a hidden path component
is permanently irrelevant): skip any event whose path has a component starting with `.`
(catches `.stfolder`, `.stversions`, `.syncthing` temp files, etc.) before it ever reaches the
debouncer. No schema change, no migration — a few lines in `_Handler.on_any_event`.

### 3.3 Fix the classifier-enqueue anti-join query (Medium)

Both `enqueue_batdetect2` and `enqueue_battybirdnet` resolve "recordings missing a detection
row" via `Recording.id.not_in(select(ClassifierDetection.recording_id).where(source == ...))`,
which Postgres plans as a `Seq Scan` + materialized subplan. Rewrite as a `NOT EXISTS` correlated
subquery (or a `LEFT JOIN ... WHERE ClassifierDetection.recording_id IS NULL`) backed by a new
composite index `(source, recording_id)` on `classifier_detection` (needs a migration). This is
already 7–12% of cycle time at today's row count and its cost is governed by the size of
`classifier_detection` (which grows with *every* classifier run, not just new recordings), so it
degrades faster than linear scan cost does — worth fixing at 1 OOM rather than waiting.

### 3.4 `derive_sites`'s O(s²) reconciliation → O(n) (Cheap–Medium)

`best_previous_overlap`/the matched-site loop in `services/derive.py` compares every new cluster
against every previous site (`O(clusters × sites)` set intersections). Cheap today (47 sites),
clean and low-risk to fix while already touching this file's neighborhood: build a
`recording_id → previous_site_id` map once (already have `previous_members`, just inverted), then
for each new cluster's members, count overlaps per candidate previous site in a single pass with
a `Counter` instead of an O(sites) inner loop per cluster. Bundled into this pass because it's
cheap and because a correctness-preserving algorithmic swap is safer to do calmly now than
under future time pressure once site count has actually grown.

### 3.5 Batch `materialize_claims`'s N+1 queries (Medium)

`materialize_claims` issues two queries per recording it has cached detections for (a `select`
of that recording's `classifier_detection` rows, then a `session.get(Recording, ...)`) — 2613
queries measured for 1306 recordings, 4.5s total, 7% of cycle time. Not the dominant cost today,
but the same N+1 shape as §3.3, worth fixing in the same pass rather than leaving a second
"batch this later" item: batch-load both sides (one query keyed by `recording_id IN (...)`,
grouped in Python) instead of per-recording round trips. The design/rationale documented in
`materialize_claims`'s own docstring (deliberately recompute-every-cycle, so a threshold change
takes effect for free) must survive the batching rewrite intact — the fix changes *how* every
recording's state is read and diffed, not *that* every recording is still reconsidered each
cycle.

### 3.6 Confirm the `inotify` watch budget (Cheap)

`jobs/watch.py`'s `Observer` schedules one recursive watch per archive root — one inotify watch
descriptor per directory under it, not per file, but still O(session directories). Check
`fs.inotify.max_user_watches` on the deployment host against a projected 10x session-directory
count; raise it via `/etc/sysctl.d/` if it's not comfortably above that projection. No code
change — an operational check, done once now, documented here so it isn't lost.

## 4. Deferred to two orders of magnitude — not implemented by this spec's changeset

### 4.1 Stream/narrow full-table ORM loads (Medium–Hard)

`classify_noise` and `derive_sites` each load every eligible `Recording` as a full ORM object
every cycle (`select(Recording)...).all()`). No evidence yet that this is a real problem — 0.16s
and 0.39s respectively at today's scale — but at 100x it's 130k live ORM objects materialized
every 5 minutes, real memory/GC pressure. Only worth doing if it's shown to matter in practice at
the next scale checkpoint; no work planned now.

### 4.2 Revisit `inotify` watching strategy if §3.6's sysctl bump isn't enough

If a future host can't raise `fs.inotify.max_user_watches` far enough for a 100x directory count
(e.g. a constrained container), the fallback is non-recursive watching at the archive-root level
only, relying on the (by-then-cheap, thanks to §3.1) periodic cron-driven full rescan to pick up
everything the watcher can't see directly. Not designed further here — revisit only if §3.6's
straightforward sysctl fix turns out insufficient.

## 5. Open question: how often does `materialize_claims` run after the 1 OOM pass?

**Unchanged from today: every `run_ingest_cycle` invocation** — every 5-minute cron tick, and
every debounced filesystem event that survives §3.2's hidden-path filter. §3.5 changes *how*
`materialize_claims` reads and diffs state (batched instead of N+1), not *how often* it runs or
*that* it reconsiders every recording each cycle — the recompute-every-cycle design (so a
threshold change takes effect for free) is preserved.

What *does* change is how often `run_ingest_cycle` itself fires: §3.1 and §3.2 together remove
both causes of the observed back-to-back triggering (an expensive cycle no longer competes with
itself, and spurious Syncthing housekeeping events no longer count as "something changed"), so in
steady state the cycle reverts to firing on the 5-minute cron tick plus genuine new-recording
events — not continuously.

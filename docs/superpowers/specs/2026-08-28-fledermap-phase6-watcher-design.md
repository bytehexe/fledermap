# Fledermap Phase 6 (Watcher) — Design

## 1. Scope

Per the phasing table in `docs/superpowers/specs/2026-08-23-fledermap-design.md` §15: "a night
dropped in by Syncthing appears unattended." Today that requires a human (or cron) to run
`ingest ARCHIVE` and `derive` separately, alongside a permanently-running `worker ARCHIVE`. This
phase makes ingestion continuous and automatic, folds `derive` into the same cycle, and — since
it was raised while scoping this phase, not deferred — generalizes the single archive directory
into a configurable, ordered **list** of archive roots (multiple detectors, or an SD-card dump
directory alongside the Syncthing-synced one).

Also resolves the question §15 explicitly punted to this phase: what to do about `ingest`/`worker`
each taking an independently-typed `archive_root`, with nothing to catch the two disagreeing.

**In scope:** continuous ingest+derive via the existing `worker` process, multiple archive roots.
**Out of scope:** see §9 — notably, a dedicated ingest-activity page, considered and dropped.

## 2. `archive_roots`: from one path to an ordered list

`Config.archive_root: Path` becomes `Config.archive_roots: tuple[Path, ...]`. Order is
meaningful — first-in-list wins when a relative path exists under more than one root (see §4).

- Required, no default — same treatment `database_url` already gets in `Config.from_env`
  (`_lookup`/`_as_str`, raising `ConfigError` if absent). No longer sourced from a CLI positional
  argument: `Config.from_env` drops its `archive_root: Path` parameter entirely.
- Env var `FLEDERMAP_ARCHIVE_ROOTS`, comma-separated (`/mnt/syncthing/bats,/mnt/sdcard-dump`).
  The TOML config file (`docs/setup.md`'s mechanism) holds it as a native array under
  `archive_roots`.
- `ingest ARCHIVE` and `worker ARCHIVE` **drop their positional `ARCHIVE` argument.** Both now
  read `archive_roots` from `Config.from_env()` like every other required setting, exactly the
  same way for both commands — this is what removes the drift risk `Recording.path` resolution
  otherwise builds silently: there is no longer a second, independently-typed place to configure
  the same fact. `derive` was already config-only; unaffected.
- `worker`'s `additional_context` passes `archive_roots` (plural) instead of `archive_root` to
  every task — `jobs/tasks.py`'s three read sites (spectrogram/oscillogram/preview) resolve
  `config.archive_roots[recording.archive_root_index] / recording.path` (§3) instead of a bare
  `archive_root / recording.path`.

## 3. `Recording.archive_root_index`

New column, `Mapped[int]`, NOT NULL. Set at ingest time to the index into `archive_roots` of the
root the file was actually scanned under.

Without this, resolving `recording.path` back to a real file under multiple configured roots
would need a "try each root until one exists" search — and worse, `commit_scan`'s existing
identity logic (keyed on `audio_hash` + `path`, spec D8) would misread a same-relative-path
collision across two *different* roots (plausible with two detectors' independent
auto-numbering, not just the SD-card-dump case) as one file's content having been **replaced**,
when it's really two unrelated files. Recording the source root at ingest time removes both
problems: read-time resolution is exact, and a same-path/different-hash sighting from a
different root index is correctly recognized as two distinct recordings rather than a replace.

- **Migration:** new NOT NULL column, existing rows backfilled to `0`. Every archive that
  predates this phase has exactly one configured root, which becomes index 0 — no behavioral
  change for a single-root deployment.
- **Index drift is an accepted, self-healing risk.** Reordering `archive_roots` in config changes
  what index N means. A recording whose stored index no longer matches its real root just fails
  to resolve on read (`jobs/tasks.py` raises rather than silently rendering the wrong file — an
  `IndexError`-shaped bug would be worse than a clear "not found"). The *next* ingest cycle
  re-scans that root, finds the file again by its actual path, matches it by `audio_hash`
  (identity survives re-ID and, here, survives a wrong stored index too), and corrects
  `archive_root_index` in place. No migration-time renumbering safeguard is needed — this was a
  deliberate call, not an oversight.
- Scanning multiple roots in one cycle can legitimately find the *same* (hash, path) pair twice
  (a file staged in a second root before Syncthing also syncs it into the first). This is exactly
  `IngestReport.duplicates`'s existing case (currently scoped to "within a single `commit_scan`
  call") — scanning every configured root into one `commit_scan` call per cycle (§5) means the
  existing duplicate-counting logic already covers the cross-root case with no new code.

## 4. Scan-time changes

`scan_with_skips` is called once per configured root (in list order), same settle logic
unchanged. Each yielded `ScannedFile` is tagged with which root index produced it before all
roots' results are merged into one `commit_scan` call — `_relative()` in `services/ingest.py`
computes the path relative to *that* root (not by testing every root), and the new
`archive_root_index` is written alongside `path` in the same write `_relative`'s caller already
makes.

## 5. Watch mechanism: folded into `worker`, not a new command

No new `fledermap watch` command. `worker` gains a second job type registered on the same shared
Procrastinate `App` (`jobs/app.py`'s one-App-per-process design, §6 of the phase 3 spec) alongside
the existing spectrogram/oscillogram/preview tasks:

- **`run_ingest_cycle`** — one full pass: scan every configured root → `commit_scan` →
  `sweep_missing` → `partition_sessions` → `derive_sites`. Reuses the pure functions `ingest`/
  `derive`'s CLI bodies already call; the task is a thin wrapper the same way `jobs/tasks.py`'s
  three existing tasks wrap `media/`'s pure renderers.
- Registered `@app.periodic(cron=...)` as the timer backstop — default every 5 minutes, via a new
  `Config.watch_interval_s` (or an internal cron-string constant if a knob turns out unnecessary;
  left to the implementation plan). This guarantees progress even if filesystem events are
  missed, coalesced (a real risk with Syncthing's write pattern), or the process restarted.
- A `watchdog` `Observer` — one per configured archive root — watches for filesystem events.
  Events do NOT defer a run immediately: each event (re)starts a debounce timer, and only once
  the filesystem has gone quiet for the debounce window (no further event arrives) does the
  handler actually `run_ingest_cycle.defer_async()`. Without this, an active Syncthing burst
  (many files arriving over a couple of minutes) would fire a cycle per event, and most of those
  cycles would just get their sweep refused (`IncompleteScanError` — files still arriving, §6) —
  a burst of refusal log lines instead of one clean run once things settle. The debounce window
  reuses `ingest.scan.DEFAULT_SETTLE_SECONDS` (30s) rather than introducing a second, independent
  timing knob that could drift out of sync with the per-file settle check's own timescale.
  `watchdog` is a new dependency: filesystem-event watching (recursive, cross-platform inotify
  wrapping) is exactly the kind of nontrivial domain this project's own conventions say to reach
  for a well-tested library for, not reimplement — the debounce logic on top of it is this
  project's own, small enough to not need a library.
- **Threading note for the implementation plan:** `watchdog`'s `Observer` calls its event handler
  from its own thread, not the asyncio loop `run_worker` runs on. The handler cannot `await
  defer_async()` directly — it must marshal the defer onto the worker's running event loop (e.g.
  `asyncio.run_coroutine_threadsafe`), the same class of bridging problem `worker`'s existing
  `additional_context` plumbing doesn't have to solve because Procrastinate itself owns that
  scheduling. This needs its own test coverage, not just a code comment.
- Procrastinate's existing per-task locking (the same mechanism `jobs/tasks.py` already uses for
  media artifacts, §7 of the phase 3 spec) prevents an event-triggered run from overlapping a
  cycle already in progress. An event arriving mid-cycle results in one more run immediately
  after the in-flight one, not a concurrent second scan.
- `ingest`/`derive` CLI commands are **not removed** — they remain for deliberate manual/
  maintenance use (e.g. a full re-render after a media params change, the case §15 of the parent
  spec called out), now reading `archive_roots` from `Config.from_env()` the same way `worker`
  does, closing the drift risk without removing the manual path.

## 6. Error handling

Two classes, deliberately handled differently:

- **Known, expected refusals** — `MassDisappearanceError` and `IncompleteScanError`, the same
  two conditions `ingest`'s CLI turns into `EXIT_SWEEP_REFUSED` today. `run_ingest_cycle` catches
  these itself, logs via `logger.error(...)` (which reaches stderr through `worker`'s existing
  `logging.basicConfig(level=logging.INFO)` call — already routes to stderr by construction, no
  new plumbing needed) with the structured counts §7 describes, not just the warning message, and
  returns normally. The next cycle (cron or event) retries automatically — there's no process to exit
  non-zero against, so the log/summary row *is* the signal now, replacing the CLI's exit-code
  contract for this path.
- **Everything else** (a DB connection drop, an unanticipated bug) propagates out of the task.
  Procrastinate marks the job failed in its own job history — visible there, subject to its
  normal retry policy (`jobs/tasks.py`'s existing `retry=...`/backoff conventions, §7 of the
  phase 3 spec) — and the exception is still logged to stderr via the task's own exception
  logging. This deliberately keeps "we absorbed an expected operational hiccup" distinct from
  "something is actually broken": a blanket catch-everything would let a real bug silently no-op
  forever with only a log line to notice, no failed-job signal, no backoff.

## 7. Ingest activity visibility (deferred — see §9)

No `IngestCycle` table and no `/activity` page this phase. Visibility is whatever §6 already
gives for free: known refusals log to stderr and (once written) would carry structured counts in
that log line; unexpected failures are visible in Procrastinate's own job history. Revisited in
§9/§10 — cut specifically because the *added* value of a DB-backed table + page over what's
already there is "see it in the app UI" rather than "see it at all," and that wasn't worth the
scope for this phase (a new model + migration + write path + query + route + template + nav link
+ tests, comparable in size to a small version of Phase 5b's sessions-list task).

One consequence worth stating explicitly: `run_ingest_cycle`'s known-refusal log line (§6) should
still carry the same structured counts an `IngestCycle` row would have held (created/updated/
moved/etc., not just the warning message) — cheap to include now, and it's what makes a future
`/activity` page a pure read-side addition (backfilled from log parsing or added alongside)
rather than requiring the write-side plumbing to be revisited too.

## 8. Testing

- `scan_with_skips`/`commit_scan` multi-root behavior: a fixture archive with two root
  directories, asserting `archive_root_index` lands correctly per file and that a same-path/
  different-hash collision across roots produces two rows, not a REPLACED outcome.
- `Recording.archive_root_index` drift self-heal: seed a recording with a wrong index, re-run
  ingest, assert it corrects to the real index via `audio_hash` matching.
- `run_ingest_cycle`: unit-testable the same way `ingest`/`derive`'s CLI bodies presumably factor
  into testable functions today — assert the known-refusal path logs (with the structured counts
  per §7's closing note, not just the warning message) and returns without raising; assert an
  unexpected exception propagates.
- The `watchdog` → `defer_async()` thread-bridging path needs its own focused test (per §5's
  threading note) — not just coverage of the cron path, which alone wouldn't exercise the
  cross-thread marshaling at all. The debounce timer itself needs a test that a second event
  arriving before the window elapses RESETS the timer (no defer yet), distinct from a test that
  one event alone eventually defers once the window elapses uninterrupted.
- Migration test (`tests/test_migrations.py`'s existing drift-detection machinery, per this
  project's established per-column-coverage pattern) for the new NOT NULL `archive_root_index`
  column and its backfill.

## 9. Explicitly out of scope (this phase)

- Removing or restricting `ingest`/`worker` as manual commands — kept for deliberate maintenance
  use (§5), per the option chosen from §15's explicitly offered alternatives.
- A general observability/metrics dashboard.
- **An `IngestCycle` table and `/activity` page** — considered in detail (§7), scoped out
  specifically because stderr logging + Procrastinate's own job history already cover "is this
  working," and a DB-backed table/page's added value is only "see it in the app UI," which wasn't
  worth this phase's scope. Real future work, with a real owner decision needed on when — not a
  silently dropped item.
- Any change to `derive`'s own CLI command (still available standalone; `run_ingest_cycle` calls
  the same underlying functions, not the CLI command itself).

## 10. Decisions

Captured for the record, with the reasoning that led to each (brainstorming session,
2026-08-28):

1. **Hybrid trigger (events wake a debounced run, cron is the backstop)** over pure polling or
   pure events — a lone poll would risk feeling too slow, lone events (fired on every filesystem
   change with no debounce) would risk a refusal-log burst during an active sync window (§5).
   Debouncing was added after the initial design pass, once the "immediate defer per event"
   version's failure mode against a real Syncthing burst was worked through concretely — reuses
   the existing per-file settle window's timescale rather than inventing a second one.
2. **`derive` runs every cycle**, not only when ingest found something new — both
   `partition_sessions`/`derive_sites` are already idempotent and cheap to re-run (CLAUDE.md), and
   "appears unattended" should hold literally, not require a second manual step.
3. **Folded into `worker` via Procrastinate's periodic-task feature, not a new `fledermap watch`
   command** — reconsidered mid-brainstorm once Procrastinate's built-in `@app.periodic` was
   confirmed to exist (Context7-checked, not assumed): it gives scheduling and a persisted job
   history close to free, and avoids a second process's own bespoke poll-loop/threading model.
4. **`archive_roots` as an ordered list, ties broken by list order** — the operational reality
   (Syncthing-synced directory today, likely an SD-card-dump directory or a second detector's own
   directory later) is multiple genuinely-independent sources, not a single directory with a
   backup.
5. **`Recording.archive_root_index` recorded explicitly, not resolved by "first root where the
   file is found" at read time** — the simpler search-based approach was the original framing,
   but it silently misreads a same-path collision from two different detectors as a replace;
   recording the fact once at ingest time is barely more code and removes the ambiguity by
   construction rather than accepting it.
6. **Index drift is accepted as a self-healing risk**, not guarded against — a stale index heals
   on the next ingest cycle via `audio_hash` identity, so the failure mode is "briefly can't
   resolve one file" rather than data loss or silent misattribution.
7. **Known refusals (mass-disappearance, incomplete-scan) log-and-continue; anything else
   propagates and fails the Procrastinate job** — collapsing both into one blanket catch would let
   a genuine bug silently no-op forever with no failure signal.
8. **`IngestCycle` table + `/activity` page dropped from this phase**, after initially being
   designed in and approved (a full table/model/migration/route/template/nav-link design, since
   replaced by §7's shorter deferred note) — reconsidered once the actual build cost was sized
   against what stderr logging + Procrastinate's own job history already provide. The
   structured-counts-in-the-log-line compromise (§7's closing note) keeps a future page a
   read-side-only addition.

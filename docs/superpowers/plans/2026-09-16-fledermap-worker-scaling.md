# Fledermap Worker Scaling (1 OOM pass) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `run_ingest_cycle`'s O(archive) costs so it stays fast at ~13,000 recordings
(1 order of magnitude growth from today's ~1,300), without changing any user-visible behavior.

**Architecture:** Six independent-ish fixes to `jobs/watch.py`, `services/derive.py`,
`ingest/scan.py`/`services/ingest.py` (+ migration), `services/batdetect2.py`/
`services/battybirdnet.py` (+ migration), and `services/classifier_detections.py`. Tasks 4 and 5
each add a migration; `bats_db` was already backed up
(`.db-backups/bats_db-20260916T170949Z.dump`) before this plan was written — do not re-back-up
per task.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x ORM, Alembic, Postgres/PostGIS, pytest (`hatch test`),
watchdog.

**Spec:** `docs/superpowers/specs/2026-09-16-fledermap-worker-scaling-design.md` (§3.1–§3.6 are
this plan's scope; §4 is explicitly out of scope here).

## Global Constraints

- Every model change needs a migration verified by `tests/test_migrations.py`
  (`compare_metadata` drift check) — see CLAUDE.md's "Migrations" section.
- `hatch test -m "not db"` after every task; `hatch test` (full, incl. `db`-marked,
  `dangerouslyDisableSandbox: true`) after Tasks 4, 5, and 6 (the ones touching the DB layer),
  and once more at the end.
- `hatch run types:check` must stay clean after every task.
- Run `git`/migrations with `dangerouslyDisableSandbox: true` (stale `.git/config.lock`
  otherwise); Docker-backed `db` tests need it too.
- No behavior change for any existing test — Tasks 3.4 and 3.5 (derive_sites reconciliation,
  materialize_claims batching) are refactors; their existing test suites are the safety net, no
  new tests required for them beyond what's listed.

---

### Task 1: Filter watcher events on hidden path components (spec §3.2)

**Files:**
- Modify: `src/fledermap/ingest/scan.py` (extract a reusable hidden-path check)
- Modify: `src/fledermap/jobs/watch.py`
- Test: `tests/test_scan.py`, `tests/test_watch.py`

**Interfaces:**
- Produces: `fledermap.ingest.scan.has_hidden_component(path: Path, root: Path) -> bool`
  (public, used by both `ingest/scan.py` internally and `jobs/watch.py`).

- [ ] **Step 1: Extract the hidden-path check in `ingest/scan.py`**

In `src/fledermap/ingest/scan.py`, add a new public function above `_settle_check` and use it
from inside `_settle_check`:

```python
def has_hidden_component(path: Path, root: Path) -> bool:
    """True if `path`, relative to `root`, has any path component starting
    with `.` -- Syncthing's own `.stfolder`, `.stversions`, sync temp files,
    etc. Relative to `root`, not absolute: an archive root that itself lives
    under a dotted directory must not be excluded by its own ancestry."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = (path.name,)
    return any(part.startswith(".") for part in parts)
```

Replace the existing inline block in `_settle_check`:

```python
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = (path.name,)
    if any(part.startswith(".") for part in parts):
        return SkipReason.EXCLUDED
```

with:

```python
    if has_hidden_component(path, root):
        return SkipReason.EXCLUDED
```

- [ ] **Step 2: Run existing scan tests to confirm the extraction is behavior-preserving**

Run: `hatch test tests/test_scan.py`
Expected: PASS, no changes in behavior (this step is a pure extraction).

- [ ] **Step 3: Write the failing watcher test**

Add to `tests/test_watch.py`:

```python
def test_events_under_a_hidden_path_component_are_ignored(tmp_path: Path) -> None:
    calls: list[float] = []
    hidden_dir = tmp_path / ".stfolder"
    hidden_dir.mkdir()

    async def body() -> None:
        loop = asyncio.get_running_loop()
        observer = start_watching(
            [tmp_path],
            loop,
            lambda: _defer_recorder(calls),
            debounce_seconds=0.05,
        )
        try:
            (hidden_dir / "index").write_bytes(b"x")
            await asyncio.sleep(0.2)
        finally:
            observer.stop()
            observer.join()

    asyncio.run(_run(body, timeout=2.0))
    assert calls == []


def test_a_hidden_to_visible_move_is_not_ignored(tmp_path: Path) -> None:
    """A sync tool renaming a hidden temp file to its final visible name is a
    genuine arrival and must still trigger a defer."""
    calls: list[float] = []
    hidden = tmp_path / ".foo.wav.syncthing-tmp"
    hidden.write_bytes(b"x")

    async def body() -> None:
        loop = asyncio.get_running_loop()
        observer = start_watching(
            [tmp_path],
            loop,
            lambda: _defer_recorder(calls),
            debounce_seconds=0.05,
        )
        try:
            hidden.rename(tmp_path / "final.wav")
            await asyncio.sleep(0.2)
        finally:
            observer.stop()
            observer.join()

    asyncio.run(_run(body, timeout=2.0))
    assert len(calls) == 1
```

- [ ] **Step 4: Run to verify both fail**

Run: `hatch test tests/test_watch.py`
Expected: both new tests FAIL (hidden-dir write currently still triggers a defer).

- [ ] **Step 5: Implement the filter in `jobs/watch.py`**

Update imports and `_Handler`/`start_watching` in `src/fledermap/jobs/watch.py`:

```python
from fledermap.ingest.scan import DEFAULT_SETTLE_SECONDS, has_hidden_component
```

```python
def _event_is_hidden(path_str: str, archive_roots: Sequence[Path]) -> bool:
    path = Path(path_str)
    for root in archive_roots:
        if path.is_relative_to(root):
            return has_hidden_component(path, root)
    return False


class _Handler(FileSystemEventHandler):
    def __init__(self, debouncer: _Debouncer, archive_roots: Sequence[Path]) -> None:
        self._debouncer = debouncer
        self._archive_roots = archive_roots

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        dest_path = getattr(event, "dest_path", "") or None
        src_hidden = _event_is_hidden(event.src_path, self._archive_roots)
        dest_hidden = dest_path is None or _event_is_hidden(
            dest_path, self._archive_roots,
        )
        if src_hidden and dest_hidden:
            return
        self._debouncer.notify()
```

And in `start_watching`, pass `archive_roots` through:

```python
    debouncer = _Debouncer(loop, defer, debounce_seconds)
    handler = _Handler(debouncer, archive_roots)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `hatch test tests/test_watch.py tests/test_scan.py`
Expected: PASS.

- [ ] **Step 7: Type-check and commit**

Run: `hatch run types:check`

```bash
git add src/fledermap/ingest/scan.py src/fledermap/jobs/watch.py tests/test_watch.py
git commit -m "fix: ignore filesystem events under hidden path components in the watcher"
```

---

### Task 2: Confirm the inotify watch budget (spec §3.6)

**Files:** none (operational check).

- [ ] **Step 1: Read the current limit**

Run: `cat /proc/sys/fs/inotify/max_user_watches`

- [ ] **Step 2: Estimate projected session-directory count at 1 OOM**

Run: `find ~/"Bat Sessions" -maxdepth 1 -type d | wc -l` (today's session-directory count);
multiply by 10 for the 1 OOM projection. `jobs/watch.py`'s `Observer` uses one inotify watch
descriptor per directory under each archive root (recursive), so this — not file count — is
the number that matters.

- [ ] **Step 3: Compare and, if needed, raise the limit**

If the projected count isn't comfortably below the current limit (aim for at least 10x
headroom), raise it:

```bash
echo 'fs.inotify.max_user_watches=524288' | sudo tee /etc/sysctl.d/60-fledermap-inotify.conf
sudo sysctl --system
```

- [ ] **Step 4: Report the numbers**

State the current limit, the projected 1-OOM directory count, and whether a sysctl change was
needed/applied, so this is on record (this plan's execution log / commit message covers it —
no code change to commit for this task if no sysctl file was needed).

---

### Task 3: `derive_sites` reconciliation, O(sites²) → O(recordings) (spec §3.4)

**Files:**
- Modify: `src/fledermap/services/derive.py`
- Test: `tests/test_derive_sites.py` (existing suite is the safety net — `db`-marked)

**Interfaces:**
- No public signature changes; `derive_sites(db_session, *, eps_m, min_points) ->
  SiteDeriveReport` is unchanged.

- [ ] **Step 1: Run the existing reconciliation-relevant tests first, to have a known-green baseline**

Run: `hatch test tests/test_derive_sites.py -m db` (`dangerouslyDisableSandbox: true`)
Expected: PASS (this is the baseline the refactor must not break).

- [ ] **Step 2: Replace the O(clusters × sites) matching with an inverted-index pass**

In `src/fledermap/services/derive.py`, add `from collections import Counter` to imports, then
replace this block:

```python
    # Greedily match each new cluster to the previous site it overlaps most
    # with, largest overlap first, so a split/merge doesn't let a smaller
    # overlap claim a site before a bigger one gets the chance.
    def best_previous_overlap(members: list[Recording]) -> int:
        member_ids = {r.id for r in members}
        return max(
            (len(member_ids & ids) for ids in previous_members.values()), default=0
        )

    clusters_by_overlap = sorted(
        by_label.values(), key=best_previous_overlap, reverse=True
    )

    matched_site_ids: set[int] = set()
    clusters_with_reuse: list[tuple[list[Recording], int | None]] = []
    for members in clusters_by_overlap:
        member_ids = {r.id for r in members}
        best_site_id: int | None = None
        best_overlap = 0
        for site_id, ids in previous_members.items():
            if site_id in matched_site_ids:
                continue
            overlap = len(member_ids & ids)
            if overlap > best_overlap:
                best_overlap = overlap
                best_site_id = site_id
        if best_site_id is not None:
            matched_site_ids.add(best_site_id)
        clusters_with_reuse.append((members, best_site_id))
```

with:

```python
    # Inverted index built once (O(previous members)), not re-scanned per
    # cluster: `previous_members` is site_id -> its recording ids, this is
    # the reverse, recording_id -> its old site_id. Turns the old
    # O(clusters x sites) matching below into O(recordings): each cluster's
    # overlap with every candidate previous site is read off in one pass
    # over its own members instead of one pass over every previous site.
    recording_to_old_site: dict[int, int] = {
        recording_id: site_id
        for site_id, ids in previous_members.items()
        for recording_id in ids
    }

    def overlap_counts(members: list[Recording]) -> Counter[int]:
        counts: Counter[int] = Counter()
        for r in members:
            old_site_id = recording_to_old_site.get(r.id)
            if old_site_id is not None:
                counts[old_site_id] += 1
        return counts

    # Greedily match each new cluster to the previous site it overlaps most
    # with, largest overlap first, so a split/merge doesn't let a smaller
    # overlap claim a site before a bigger one gets the chance. Overlap
    # counts are computed once per cluster and reused for both the sort key
    # and the matching loop below, rather than recomputed.
    clusters_with_counts = [
        (members, overlap_counts(members)) for members in by_label.values()
    ]
    clusters_by_overlap = sorted(
        clusters_with_counts,
        key=lambda pair: max(pair[1].values(), default=0),
        reverse=True,
    )

    matched_site_ids: set[int] = set()
    clusters_with_reuse: list[tuple[list[Recording], int | None]] = []
    for members, counts in clusters_by_overlap:
        best_site_id: int | None = None
        best_overlap = 0
        for site_id, overlap in counts.items():
            if site_id in matched_site_ids:
                continue
            if overlap > best_overlap:
                best_overlap = overlap
                best_site_id = site_id
        if best_site_id is not None:
            matched_site_ids.add(best_site_id)
        clusters_with_reuse.append((members, best_site_id))
```

- [ ] **Step 3: Run the existing test suite to confirm behavior is preserved**

Run: `hatch test tests/test_derive_sites.py -m db` (`dangerouslyDisableSandbox: true`)
Expected: PASS — in particular `test_a_rebuild_preserves_an_unchanged_sites_id`,
`test_a_site_that_gains_a_recording_keeps_its_id`, and
`test_a_disappearing_site_is_deleted_without_disturbing_others` must still pass unchanged, since
they lock in exactly the reconciliation behavior this refactor must preserve.

- [ ] **Step 4: Type-check and commit**

Run: `hatch run types:check`

```bash
git add src/fledermap/services/derive.py
git commit -m "perf: make derive_sites' previous-site reconciliation O(recordings) not O(sites^2)"
```

---

### Task 4: Skip re-scanning unchanged files (spec §3.1)

**Files:**
- Modify: `src/fledermap/domain/metadata.py` (new `UnchangedFile`, `ScannedFile` gains
  `size_bytes`/`mtime`)
- Modify: `src/fledermap/ingest/scan.py` (`_scan_one`, `scan_with_skips`, `scan` gain a `known`
  param and short-circuit)
- Modify: `src/fledermap/store/models.py` (`Recording` gains `scan_size_bytes`, `scan_mtime`)
- Modify: `src/fledermap/services/ingest.py` (`scan_all_roots` gains a `session` param and
  queries known fingerprints; `commit_scan` writes the two new columns)
- Modify: `src/fledermap/cli/main.py`, `src/fledermap/jobs/tasks.py` (pass `session=session` to
  `scan_all_roots`)
- Create: `src/fledermap/alembic/versions/<rev>_add_recording_scan_fingerprint.py`
- Test: `tests/test_scan.py`, `tests/test_ingest.py`, `tests/test_migrations.py`

**Interfaces:**
- Produces: `fledermap.domain.metadata.UnchangedFile(audio_hash: str)` (frozen dataclass).
- Produces: `ScannedFile.size_bytes: int`, `ScannedFile.mtime: float` (new required fields —
  every existing `ScannedFile(...)` construction in tests needs these two args added).
- `scan_with_skips`/`scan` gain `known: Mapping[Path, tuple[int, float, str]] = {}` (path ->
  `(size_bytes, mtime, audio_hash)`), keyword-only, defaulting to empty so every existing caller
  is unaffected.
- `scan_all_roots(archive_roots, *, timestamp_source, default_timezone, session: OrmSession)` —
  `session` is a new required keyword-only parameter.

- [ ] **Step 1: Add `UnchangedFile` and extend `ScannedFile` in `domain/metadata.py`**

```python
@dataclass(frozen=True)
class UnchangedFile:
    """A file whose (path, size, mtime) matches what was already recorded for
    it -- ingest can trust the already-known audio_hash without re-reading,
    re-hashing, or re-computing SNR for its content."""

    audio_hash: str
```

And add two fields to `ScannedFile`:

```python
@dataclass(frozen=True)
class ScannedFile:
    """One file as found on disk, ready to be committed to the database."""

    audio_hash: str
    path: Path
    size_bytes: int
    mtime: float
    metadata: RecordingMetadata
```

- [ ] **Step 2: Write the failing scan tests**

Add to `tests/test_scan.py` (needs `from fledermap.ingest.scan import scan_with_skips` already
imported; add `from fledermap.domain.metadata import UnchangedFile` if not already present):

```python
def test_a_file_matching_a_known_fingerprint_is_reported_unchanged_without_being_reopened(
    tmp_path: Path,
) -> None:
    wav = tmp_path / "a.wav"
    wav.write_bytes(_valid_wav_bytes())  # use this file's existing WAV-building helper
    stat = wav.stat()
    known = {wav: (stat.st_size, stat.st_mtime, "deadbeef")}

    results = list(scan_with_skips(tmp_path, known=known))

    assert results == [UnchangedFile(audio_hash="deadbeef")]


def test_a_file_whose_mtime_differs_from_the_known_fingerprint_is_rescanned(
    tmp_path: Path,
) -> None:
    wav = tmp_path / "a.wav"
    wav.write_bytes(_valid_wav_bytes())
    stat = wav.stat()
    known = {wav: (stat.st_size, stat.st_mtime - 1, "deadbeef")}

    results = list(scan_with_skips(tmp_path, known=known))

    assert len(results) == 1
    assert not isinstance(results[0], UnchangedFile)
```

(Check `tests/test_scan.py` for its actual existing helper name that builds a minimal valid WAV
file — reuse it verbatim in place of `_valid_wav_bytes()` above; every other test in that file
already needs one, so it exists.)

- [ ] **Step 3: Run to verify both fail**

Run: `hatch test tests/test_scan.py -k known_fingerprint`
Expected: FAIL (`scan_with_skips` doesn't accept `known` yet).

- [ ] **Step 4: Implement the short-circuit in `ingest/scan.py`**

Update imports (`from fledermap.domain.metadata import ScannedFile, UnchangedFile`, and add
`from collections.abc import Mapping`), then update `_scan_one` to compute and return the new
fields, and `scan_with_skips`/`scan` to accept and use `known`:

```python
def _scan_one(
    path: Path,
    timestamp_source: TimestampSource,
    default_timezone: tzinfo,
) -> ScannedFile | SkipReason:
    try:
        stat = path.stat()
        digest = audio_hash(path)
    except OSError:
        return SkipReason.UNREADABLE
    except (NotARiffFileError, MissingAudioChunkError):
        return SkipReason.NOT_A_WAV
    ...
    return ScannedFile(
        audio_hash=digest,
        path=path,
        size_bytes=stat.st_size,
        mtime=stat.st_mtime,
        metadata=metadata,
    )
```

(Keep every line between the two shown unchanged — only the `try` line gains the `stat = ...`
call and the final `return` gains the two new keyword arguments.)

```python
def scan(
    root: Path,
    *,
    timestamp_source: TimestampSource = TimestampSource.FILENAME,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    now: float | None = None,
    default_timezone: tzinfo = UTC,
    known: Mapping[Path, tuple[int, float, str]] = {},
) -> Iterator[ScannedFile]:
    """Yield a ScannedFile for every readable, settled, CHANGED recording
    under `root`. A file matching `known` is settled but unchanged, so it
    never appears here -- see `scan_with_skips` for the full picture
    including `UnchangedFile`."""
    for scanned in scan_with_skips(
        root,
        timestamp_source=timestamp_source,
        settle_seconds=settle_seconds,
        now=now,
        default_timezone=default_timezone,
        known=known,
    ):
        if isinstance(scanned, ScannedFile):
            yield scanned


def scan_with_skips(
    root: Path,
    *,
    timestamp_source: TimestampSource = TimestampSource.FILENAME,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    now: float | None = None,
    default_timezone: tzinfo = UTC,
    known: Mapping[Path, tuple[int, float, str]] = {},
) -> Iterator[ScannedFile | UnchangedFile | tuple[Path, SkipReason]]:
    """As `scan`, but also reports why files were skipped, for CLI summaries,
    and reports an already-known-unchanged file as `UnchangedFile` instead of
    re-reading its content -- `known` maps an absolute path to the
    (size_bytes, mtime, audio_hash) it had the last time it was fully
    scanned; a file whose current stat matches is trusted without being
    reopened at all."""
    clock = time.time() if now is None else now
    for path in sorted(root.rglob("*")):
        try:
            is_file = path.is_file()
        except OSError:
            yield (path, SkipReason.UNREADABLE)
            continue
        if not is_file:
            continue
        reason = _settle_check(path, root, settle_seconds, clock)
        if reason is not None:
            yield (path, reason)
            continue
        fingerprint = known.get(path)
        if fingerprint is not None:
            known_size, known_mtime, known_hash = fingerprint
            try:
                stat = path.stat()
            except OSError:
                yield (path, SkipReason.UNREADABLE)
                continue
            if stat.st_size == known_size and stat.st_mtime == known_mtime:
                yield UnchangedFile(audio_hash=known_hash)
                continue
        result = _scan_one(path, timestamp_source, default_timezone)
        yield result if isinstance(result, ScannedFile) else (path, result)
```

- [ ] **Step 5: Run to verify the new tests pass, then the whole file**

Run: `hatch test tests/test_scan.py`
Expected: PASS. (Existing `ScannedFile(...)` constructions elsewhere in this file that predate
this task will fail to construct until `size_bytes`/`mtime` are added — add
`size_bytes=<n>, mtime=0.0` to each, using a real byte count where the test already has one.)

- [ ] **Step 6: Add the two new `Recording` columns**

In `src/fledermap/store/models.py`, add to `Recording` (near `ingested_at`):

```python
    # Fingerprint of the file's content-relevant disk state the last time it
    # was FULLY scanned (content read, hashed, SNR computed) -- not merely
    # resolved. `services/ingest.py`'s `scan_all_roots` uses these to skip
    # that expensive work entirely for a file whose current (size, mtime)
    # still matches: see docs/superpowers/specs/2026-09-16-fledermap-worker-
    # scaling-design.md §3.1. A stale/mismatching pair only ever costs one
    # extra full rescan of that one file -- correctness never depends on
    # these being right, only performance does.
    scan_size_bytes: Mapped[int | None] = mapped_column(Integer)
    scan_mtime: Mapped[float | None] = mapped_column(Float)
```

- [ ] **Step 7: Generate and inspect the migration**

Run: `dangerouslyDisableSandbox: true` —
`hatch run alembic revision --autogenerate -m "add recording scan fingerprint columns"`

Open the generated file under `src/fledermap/alembic/versions/`; confirm it contains exactly
two `op.add_column("recording", ...)` calls for `scan_size_bytes` (Integer, nullable) and
`scan_mtime` (Float, nullable), and matching `op.drop_column` calls in `downgrade()`. Nothing
else should have changed (if unrelated drift appears, stop and investigate before proceeding —
do not silently accept it).

- [ ] **Step 8: Run the migration test**

Run: `hatch test tests/test_migrations.py`
Expected: PASS.

- [ ] **Step 9: Update `services/ingest.py`'s `scan_all_roots` to query and pass `known`**

In `src/fledermap/services/ingest.py`, add `from sqlalchemy.orm import Session as OrmSession`
(already imported) and update the function:

```python
def scan_all_roots(
    archive_roots: Sequence[Path],
    *,
    timestamp_source: TimestampSource,
    default_timezone: tzinfo,
    session: OrmSession,
) -> tuple[list[tuple[ScannedFile, int]], set[str], int, int]:
    """Scan every configured root in order, pairing each `ScannedFile` with
    the index of the root that produced it (design spec §4). Returns
    `(scanned, seen_hashes, skipped, incomplete_skips)` -- `seen_hashes` and
    the two counts are exactly what `sweep_missing` needs, so a caller
    doesn't have to re-derive them from `scanned`.

    `session` is used ONLY to build `known` up front (one query for every
    already-ingested file's (path, size, mtime, audio_hash)) -- ingest/scan.py
    itself stays pure/DB-free (D16); this is the one place that bridge exists.
    """
    known: dict[Path, tuple[int, float, str]] = {}
    rows = session.execute(
        select(
            Recording.archive_root_index,
            Recording.path,
            Recording.scan_size_bytes,
            Recording.scan_mtime,
            Recording.audio_hash,
        ).where(
            Recording.scan_size_bytes.is_not(None),
            Recording.scan_mtime.is_not(None),
        ),
    )
    for root_index, rel_path, size_bytes, mtime, existing_hash in rows:
        if root_index >= len(archive_roots):
            continue
        known[archive_roots[root_index] / rel_path] = (size_bytes, mtime, existing_hash)

    scanned: list[tuple[ScannedFile, int]] = []
    seen_hashes: set[str] = set()
    skipped = 0
    incomplete_skips = 0
    for root_index, root in enumerate(archive_roots):
        for item in scan_with_skips(
            root,
            timestamp_source=timestamp_source,
            default_timezone=default_timezone,
            known=known,
        ):
            if isinstance(item, ScannedFile):
                scanned.append((item, root_index))
                seen_hashes.add(item.audio_hash)
            elif isinstance(item, UnchangedFile):
                seen_hashes.add(item.audio_hash)
            else:
                _, reason = item
                skipped += 1
                if reason in INCOMPLETE_SCAN_REASONS:
                    incomplete_skips += 1
    return scanned, seen_hashes, skipped, incomplete_skips
```

Add `Recording` and `UnchangedFile` to this file's imports (`from fledermap.domain.metadata
import ParsedIdentification, RecordingMetadata, ScannedFile, UnchangedFile`; `Recording` is
already imported from `fledermap.store.models`).

- [ ] **Step 10: Write recording of the fingerprint in `commit_scan`**

In `commit_scan`'s CREATE branch, add the two fields to the `Recording(...)` construction:

```python
            recording = Recording(
                audio_hash=item.audio_hash,
                path=rel,
                archive_root_index=root_index,
                ingested_at=now,
                scan_size_bytes=item.size_bytes,
                scan_mtime=item.mtime,
            )
```

In the UPDATE branch, right after `existing.missing_since = None`, add:

```python
        if (
            existing.scan_size_bytes != item.size_bytes
            or existing.scan_mtime != item.mtime
        ):
            existing.scan_size_bytes = item.size_bytes
            existing.scan_mtime = item.mtime
```

- [ ] **Step 11: Update `scan_all_roots` call sites**

In `src/fledermap/cli/main.py`'s `ingest` command, change:

```python
        scanned, seen, skipped, incomplete_skips = scan_all_roots(
            config.archive_roots,
            timestamp_source=config.timestamp_source,
            default_timezone=config.default_timezone,
        )
```

to:

```python
        scanned, seen, skipped, incomplete_skips = scan_all_roots(
            config.archive_roots,
            timestamp_source=config.timestamp_source,
            default_timezone=config.default_timezone,
            session=session,
        )
```

Make the identical change in `src/fledermap/jobs/tasks.py`'s `run_ingest_cycle`.

- [ ] **Step 12: Write the failing service-level test**

Add to `tests/test_ingest.py` (find its existing `commit_scan`/`scan_all_roots` test pattern and
match its fixture style for `engine`/`session`/a real temp WAV file):

```python
def test_an_unchanged_file_is_not_reopened_on_a_second_scan_all_roots_call(
    engine: Engine, tmp_path: Path,
) -> None:
    wav = tmp_path / "a.wav"
    wav.write_bytes(_valid_wav_bytes())  # reuse this file's existing helper

    with OrmSession(engine) as session:
        scanned, seen, skipped, incomplete = scan_all_roots(
            [tmp_path],
            timestamp_source=TimestampSource.FILENAME,
            default_timezone=UTC,
            session=session,
        )
        report = commit_scan(session, scanned, archive_roots=[tmp_path])
        session.commit()
        assert report.created == 1

        # Second call: nothing on disk changed. `scanned` must be empty --
        # the file was recognised as unchanged via `known`, never reopened.
        scanned2, seen2, skipped2, incomplete2 = scan_all_roots(
            [tmp_path],
            timestamp_source=TimestampSource.FILENAME,
            default_timezone=UTC,
            session=session,
        )
        assert scanned2 == []
        assert seen2 == seen
```

- [ ] **Step 13: Run to verify it fails, then implement is already done — run to verify it passes**

Run: `hatch test tests/test_ingest.py -k unchanged_file_is_not_reopened`
Expected: FAIL first (before Steps 9-11 are wired up — if run after, it should already PASS;
run it now to confirm).

- [ ] **Step 14: Run the full non-db suite, then the db suite**

Run: `hatch test -m "not db"`
Expected: PASS (after fixing every pre-existing `ScannedFile(...)` construction site flagged in
Step 5).

Run (`dangerouslyDisableSandbox: true`): `hatch test -m db`
Expected: PASS.

- [ ] **Step 15: Type-check and commit**

Run: `hatch run types:check`

```bash
git add src/fledermap/domain/metadata.py src/fledermap/ingest/scan.py \
  src/fledermap/store/models.py src/fledermap/services/ingest.py \
  src/fledermap/cli/main.py src/fledermap/jobs/tasks.py \
  src/fledermap/alembic/versions/ tests/test_scan.py tests/test_ingest.py
git commit -m "perf: skip re-scanning files whose (path, size, mtime) match what's already known"
```

---

### Task 5: Fix the classifier-enqueue anti-join query (spec §3.3)

**Files:**
- Modify: `src/fledermap/store/models.py` (`ClassifierDetection` gains a composite index)
- Modify: `src/fledermap/services/batdetect2.py`, `src/fledermap/services/battybirdnet.py`
- Create: `src/fledermap/alembic/versions/<rev>_add_classifier_detection_source_recording_index.py`
- Test: `tests/test_migrations.py`, existing `tests/test_batdetect2*.py`/
  `tests/test_battybirdnet*.py` (db-marked) as the behavior safety net

**Interfaces:** No public signature changes — `enqueue_batdetect2`/`enqueue_battybirdnet` keep
their existing signatures and return type (`int`).

- [ ] **Step 1: Add the composite index to the model**

In `src/fledermap/store/models.py`, add `Index` to the SQLAlchemy import line, then add
`__table_args__` to `ClassifierDetection`:

```python
    __tablename__ = "classifier_detection"
    __table_args__ = (
        # Backs the NOT EXISTS anti-join in services/batdetect2.py's
        # enqueue_batdetect2 and services/battybirdnet.py's
        # enqueue_battybirdnet -- both filter by (source, recording_id)
        # every ingest cycle to find recordings still missing a detection
        # row. Without this, Postgres falls back to a full Seq Scan of this
        # table (confirmed via EXPLAIN ANALYZE: 12.2s at 334k rows) even
        # though `source` and `recording_id` each have their OWN index --
        # neither alone can drive an index-only anti-join on the pair.
        Index(
            "ix_classifier_detection_source_recording_id", "source", "recording_id",
        ),
    )
```

- [ ] **Step 2: Generate and inspect the migration**

Run (`dangerouslyDisableSandbox: true`):
`hatch run alembic revision --autogenerate -m "add classifier_detection source+recording_id index"`

Confirm the generated file contains exactly one `op.create_index(...)` for
`ix_classifier_detection_source_recording_id` on `("source", "recording_id")` and a matching
`op.drop_index(...)` in `downgrade()`. No other drift.

- [ ] **Step 3: Run the migration test**

Run: `hatch test tests/test_migrations.py`
Expected: PASS.

- [ ] **Step 4: Rewrite `enqueue_batdetect2`'s anti-join**

In `src/fledermap/services/batdetect2.py`, replace:

```python
    already_processed = select(ClassifierDetection.recording_id).where(
        ClassifierDetection.source == IdSource.BATDETECT2,
    )
    hashes = session.scalars(
        select(Recording.audio_hash).where(
            Recording.missing_since.is_(None),
            Recording.id.not_in(already_processed),
        ),
    ).all()
```

with:

```python
    already_processed = (
        select(ClassifierDetection.id)
        .where(
            ClassifierDetection.recording_id == Recording.id,
            ClassifierDetection.source == IdSource.BATDETECT2,
        )
        .exists()
    )
    hashes = session.scalars(
        select(Recording.audio_hash).where(
            Recording.missing_since.is_(None),
            ~already_processed,
        ),
    ).all()
```

- [ ] **Step 5: Make the identical change in `enqueue_battybirdnet`**

In `src/fledermap/services/battybirdnet.py`, apply the same replacement (swap
`IdSource.BATDETECT2` for `IdSource.BATTYBIRDNET`).

- [ ] **Step 6: Run the existing db-marked tests for both**

Run (`dangerouslyDisableSandbox: true`): `hatch test tests/test_batdetect2.py
tests/test_battybirdnet.py -m db` (adjust filenames to whatever `grep -rl enqueue_batdetect2
tests/` / `grep -rl enqueue_battybirdnet tests/` actually shows — use those exact paths)
Expected: PASS, unchanged behavior (same recordings returned/enqueued as before the query
rewrite).

- [ ] **Step 7: Confirm the query plan actually changed**

Run (`dangerouslyDisableSandbox: true`), against the real `bats_db`:

```bash
hatch run python - <<'EOF'
from fledermap.config import Config
from fledermap.store.db import make_engine
from sqlalchemy import text

engine = make_engine(Config.from_env().database_url)
with engine.connect() as conn:
    for row in conn.execute(text("""
        EXPLAIN ANALYZE
        SELECT audio_hash FROM recording r
        WHERE missing_since IS NULL
        AND NOT EXISTS (
            SELECT 1 FROM classifier_detection cd
            WHERE cd.recording_id = r.id AND cd.source = 'batdetect2'
        )
    """)):
        print(row[0])
EOF
```

Expected: no top-level `Seq Scan on classifier_detection`; the plan should use the new
`ix_classifier_detection_source_recording_id` index, and total execution time should be well
under the 12.2s baseline measured before this task.

- [ ] **Step 8: Type-check and commit**

Run: `hatch run types:check`

```bash
git add src/fledermap/store/models.py src/fledermap/services/batdetect2.py \
  src/fledermap/services/battybirdnet.py src/fledermap/alembic/versions/
git commit -m "perf: rewrite classifier-enqueue anti-join as NOT EXISTS with a covering index"
```

---

### Task 6: Batch `materialize_claims`'s per-recording queries (spec §3.5)

**Files:**
- Modify: `src/fledermap/services/classifier_detections.py`
- Test: existing db-marked test suite for `materialize_claims` is the safety net (find it via
  `grep -rl materialize_claims tests/`)

**Interfaces:** No public signature changes — `materialize_claims(session, source, threshold) ->
MaterializeReport` unchanged.

- [ ] **Step 1: Run the existing tests first, as a known-green baseline**

Run (`dangerouslyDisableSandbox: true`): `hatch test <the test file found above> -m db`
Expected: PASS.

- [ ] **Step 2: Batch-load recordings and detections**

In `src/fledermap/services/classifier_detections.py`, add `from collections import defaultdict`
to imports, then replace:

```python
    recording_ids = session.scalars(
        select(ClassifierDetection.recording_id)
        .where(ClassifierDetection.source == source)
        .distinct(),
    ).all()

    now = datetime.now(UTC)
    updated = 0
    for recording_id in recording_ids:
        recording = session.get(Recording, recording_id)
        assert recording is not None  # FK guarantees this
        detections = session.scalars(
            select(ClassifierDetection).where(
                ClassifierDetection.recording_id == recording_id,
                ClassifierDetection.source == source,
            ),
        ).all()
        passing = [d for d in detections if d.confidence >= threshold]
```

with:

```python
    recording_ids = session.scalars(
        select(ClassifierDetection.recording_id)
        .where(ClassifierDetection.source == source)
        .distinct(),
    ).all()

    if not recording_ids:
        return MaterializeReport(recordings_updated=0)

    # Batched instead of one SELECT + one GET per recording (was 2 queries x
    # every recording this source has ever touched, every cycle -- see
    # docs/superpowers/specs/2026-09-16-fledermap-worker-scaling-design.md
    # §3.5). The recompute-every-cycle behavior below is unchanged, only how
    # the starting state is READ.
    recordings_by_id = {
        recording.id: recording
        for recording in session.scalars(
            select(Recording).where(Recording.id.in_(recording_ids)),
        )
    }
    detections_by_recording: dict[int, list[ClassifierDetection]] = defaultdict(list)
    for detection in session.scalars(
        select(ClassifierDetection).where(
            ClassifierDetection.source == source,
            ClassifierDetection.recording_id.in_(recording_ids),
        ),
    ):
        detections_by_recording[detection.recording_id].append(detection)

    now = datetime.now(UTC)
    updated = 0
    for recording_id in recording_ids:
        recording = recordings_by_id[recording_id]  # FK guarantees this exists
        detections = detections_by_recording.get(recording_id, [])
        passing = [d for d in detections if d.confidence >= threshold]
```

(Everything from `best_by_taxon: dict[...] = {}` through the end of the loop body is unchanged —
only the lines shown above move from per-recording queries to batched, pre-fetched lookups.)

- [ ] **Step 3: Run the existing tests to confirm behavior is preserved**

Run (`dangerouslyDisableSandbox: true`): same command as Step 1.
Expected: PASS, identical results.

- [ ] **Step 4: Confirm the query count actually dropped**

Run (`dangerouslyDisableSandbox: true`), against the real `bats_db`:

```bash
hatch run python - <<'EOF'
import logging
logging.basicConfig()
logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)

from fledermap.config import Config
from fledermap.store.db import make_engine
from fledermap.domain.codes import IdSource
from fledermap.services.classifier_detections import materialize_claims
from sqlalchemy.orm import Session as OrmSession

config = Config.from_env()
engine = make_engine(config.database_url)
with OrmSession(engine) as session:
    materialize_claims(session, IdSource.BATDETECT2, config.batdetect2_threshold)
    session.rollback()  # inspection only, don't actually commit here
EOF
```

Expected: a handful of `SELECT` statements logged (roughly: 1 for `recording_ids`, 1 for
`recordings_by_id`, 1 for `detections_by_recording`, plus whatever `replace_claims` itself
issues per recording — that part is unchanged and already cheap), not one-select-plus-one-get
per recording as before.

- [ ] **Step 5: Type-check and commit**

Run: `hatch run types:check`

```bash
git add src/fledermap/services/classifier_detections.py
git commit -m "perf: batch-load recordings and detections in materialize_claims instead of N+1"
```

---

### Final verification

- [ ] Run `hatch test` (full suite, `dangerouslyDisableSandbox: true`) once more, all tasks
  combined.
- [ ] Run `hatch fmt` and confirm no diffs beyond what's already committed.
- [ ] Re-run the end-to-end profile from the spec's baseline (`cProfile` over
  `run_ingest_cycle.func` against the real archive/DB, as used to produce §1's numbers) and
  confirm total cycle time on the unchanged 1306-recording archive drops from ~101s to a few
  seconds — this is the concrete before/after number for the Obsidian item.
- [ ] Restart `fledermap-worker.service` (`systemctl --user restart fledermap.target`) and watch
  `journalctl --user -u fledermap-worker.service -f` for a few cycles to confirm real-world
  cycle time and duty cycle.

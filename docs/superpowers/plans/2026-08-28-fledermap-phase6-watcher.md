# Fledermap Phase 6 (Watcher) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Continuous, unattended ingest+derive via the existing `worker` process (cron backstop +
debounced filesystem events), and a configurable, ordered list of archive roots instead of one
fixed directory.

**Architecture:** Four tasks. Task 1 adds `Recording.archive_root_index` — purely additive,
nothing reads it yet. Task 2 is the necessarily-large rename: `Config.archive_root: Path` becomes
`Config.archive_roots: tuple[Path, ...]`, `commit_scan` threads a root index through every scanned
item, both CLI commands drop their positional `ARCHIVE` argument, and the three existing media
tasks in `jobs/tasks.py` switch from `archive_root` to `archive_roots[recording.archive_root_index]`
— verified via grep to be exactly these readers, nothing else. This one has to land as a single
task: renaming the field breaks every reader simultaneously (confirmed empirically while writing
this plan — there is no smaller slice that leaves `hatch test` green), the same shape of ripple
`docs/superpowers/plans/2026-08-25-fledermap-phase3-media-jobs.md`'s Task 3 already went through
for `media_root`. Task 3 adds `run_ingest_cycle`, a Procrastinate task registered BOTH as
`@app.periodic(cron=...)` (the timer backstop) and manually deferred (Task 4's event trigger),
sharing one `lock`/`queueing_lock` pair so at most one cycle ever runs at a time with at most one
more queued behind it. Task 4 adds a `watchdog`-based filesystem watcher with its own debounce
timer, bridged into the worker's asyncio loop.

**Tech Stack:** `procrastinate` (already a dependency — `@app.periodic`, confirmed against the
installed package's own source, not just its docs) for scheduling; `watchdog` (new dependency)
for filesystem events; `croniter` (already pulled in transitively by `procrastinate`) for the cron
backstop's own scheduling, never imported directly by this project's code.

**Spec:** `docs/superpowers/specs/2026-08-28-fledermap-phase6-watcher-design.md`

## Global Constraints

- **`hatch` only.** Never `pip`, never bare `python`/`python3`, never `PYTHONPATH`. `hatch test`,
  `hatch fmt --check`, `hatch run types:check`. (`pyproject.toml`'s `[tool.hatch.envs.hatch-static-analysis]`
  sets `config-path = "none"`, so `hatch fmt` no longer injects hatch's own bundled ruff config —
  the older `hatch run ruff:ruff check .` workaround some earlier plans in this repo used is no
  longer necessary; confirmed directly against `pyproject.toml` while writing this plan.)
- **Test output must be pristine** — a warning is a defect, fix the cause, never `filterwarnings`.
- **`hatch run types:check` covers `tests/` too** — test code must type-check for real.
- **New third-party imports mypy can't resolve go in `[tool.hatch.envs.types]`'s
  `extra-dependencies`**, checked per dependency added rather than guessed in advance — never a
  global `ignore_missing_imports`.
- **`db`-marked tests need Docker, which the command sandbox blocks** — run with
  `dangerouslyDisableSandbox: true`.
- **Run `git` unsandboxed too** — sandboxed `git` config writes leave a stale `.git/config.lock`.
- **`Config.from_env()` now takes NO arguments** (Task 2) — `archive_roots` moves from a
  per-invocation CLI argument to a required, no-default `Config` field, resolved through the same
  `_lookup`/`_as_str` machinery `database_url` already uses. This is a deliberate behavior change:
  EVERY command that builds a `Config` now requires `FLEDERMAP_ARCHIVE_ROOTS` to be set, including
  `derive`/`serve`/`enqueue-media`, which never needed it before — matching how `media_root` is
  already required for all of them regardless of which command literally touches media. Every
  `Config.from_env(...)` call site (5 in `src/`, ~61 in `tests/test_config.py`, ~30 in
  `tests/test_cli.py` via CLI invocations) needs updating; Task 2 covers all of them.
- **`commit_scan`'s `scanned` parameter changes shape**: from `Iterable[ScannedFile]` to
  `Iterable[tuple[ScannedFile, int]]` (item, root index) — `Recording.archive_root_index` needs to
  know which configured root produced each item, and the caller (which scans one root at a time)
  is the only place that fact is known without re-deriving it by path-prefix guessing.
- **Procrastinate's `queueing_lock` only blocks a PENDING (`todo`) duplicate, not a running
  (`doing`) one** — confirmed against the installed package's own schema SQL
  (`procrastinate_jobs_queueing_lock_idx_v1` is a partial unique index `WHERE status = 'todo'`),
  not assumed from the docs. Concurrency itself is prevented by the separate `lock` field, which
  Procrastinate's job-fetch query uses to skip a job whose lock is already held by a `doing` job.
  `run_ingest_cycle` (Task 3) sets both to the same key for exactly this reason.
- **A periodic task's Python signature must accept `timestamp: int` as its first parameter after
  `context`** — confirmed against `procrastinate/periodic.py`: `PeriodicDeferrer.defer_jobs`
  unconditionally injects `task_kwargs["timestamp"] = timestamp` before deferring. A manual
  `defer_async()` call (Task 4's event trigger) must supply it too.
- **`_start_side_tasks` in Procrastinate's `Worker` always starts the periodic deferrer**,
  regardless of `wait`/`queues` — confirmed by reading `procrastinate/worker.py` directly. Once
  `run_ingest_cycle` is registered periodic on the shared `jobs_app`, EVERY existing test that
  calls `run_worker`/`_run_worker` on that app (all of `tests/test_jobs_tasks.py`) will have the
  periodic deferrer attempt to defer a `run_ingest_cycle` job too. `run_ingest_cycle` is put on its
  own `queue="ingest"` (existing media tasks are `queue="media"`) specifically so those existing
  tests can pass `queues=["media"]` and never fetch/execute it — the deferred row sits `todo` and
  harmless. Task 3 updates every existing `_run_worker`/`run_worker` call in that file with this
  scoping; skipping it risks a stray `run_ingest_cycle` job executing mid-test with whatever
  `additional_context` that unrelated test happened to supply.
- **`jobs/tasks.py` cannot import `services.media.enqueue_media` at module level** —
  `services/media.py` itself imports task objects (`render_spectrogram_task`, etc.) from
  `jobs/tasks.py` at ITS module level (confirmed by grep), so a top-level import the other
  direction is a circular import that fails at process startup. `run_ingest_cycle` (Task 3) imports
  it locally, inside the function body, which is safe because by the time the function actually
  runs, `jobs/tasks.py` has finished defining every task object `services/media.py` needs.
- **The debounce window (Task 4) reuses `ingest.scan.DEFAULT_SETTLE_SECONDS`**, not a new,
  independent constant — see the design spec's revised §5/§10 (committed after the initial design
  pass, once the "defer per event with no debounce" version's failure mode against a real
  Syncthing burst was worked through: most of those cycles would just get their sweep refused).

---

## Task 1: `Recording.archive_root_index`

**Files:**
- Modify: `src/fledermap/store/models.py`
- Create: `alembic/versions/<generated>_phase_6_archive_root_index.py`
- Test: `tests/test_migrations.py` (extend)

**Interfaces:**
- Consumes: nothing from other Phase 6 tasks.
- Produces: `Recording.archive_root_index: int` (NOT NULL, ORM default `0`), consumed by Task 2
  (`commit_scan`, the media tasks) and Task 3 (`run_ingest_cycle`, transitively via `commit_scan`).

Purely additive — nothing reads this column yet, so every existing test stays green untouched.

- [ ] **Step 1: Add the column to the model**

In `src/fledermap/store/models.py`, add to `Recording` (after `path`, before `recorded_at`):

```python
    # Which configured `Config.archive_roots[i]` this file was scanned under
    # (design spec §3). Read-time media resolution (`jobs/tasks.py`) is
    # `archive_roots[archive_root_index] / path` -- exact, no search. Default
    # 0 (ORM-level, not just the migration's server_default) because several
    # test helpers across the suite construct `Recording(...)` directly
    # without setting it (e.g. `tests/test_jobs_tasks.py`'s `_make_recording`)
    # -- matches `Session.kind_locked`'s existing `default=False` pattern.
    archive_root_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
```

- [ ] **Step 2: Generate and fill in the migration**

Run (unsandboxed — Docker):
```bash
hatch run alembic revision -m "phase 6 archive root index"
```

This creates a new file under `alembic/versions/`. Edit it to match
`alembic/versions/4d15c22c4f33_phase_5b_session_schema.py`'s shape exactly (revision/down_revision
already filled in by the generator — leave those; `down_revision` must equal the current head,
`4d15c22c4f33`):

```python
def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "recording",
        sa.Column(
            "archive_root_index",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("recording", "archive_root_index")
```

- [ ] **Step 3: Write the failing migration tests**

Add to `tests/test_migrations.py` (mirroring the file's existing per-column-coverage pattern —
read the existing `test_migration_idsource_literal_matches_the_model`-style tests first for the
exact fixture names in scope, e.g. `migrated_engine`):

```python
def test_archive_root_index_column_is_not_null(migrated_engine: Engine) -> None:
    """The new column must actually be NOT NULL at the DB level, not just in
    the ORM's own type annotation -- an INSERT omitting it must fail."""
    with migrated_engine.connect() as conn:
        with pytest.raises(IntegrityError):
            with conn.begin():
                conn.execute(
                    text(
                        "INSERT INTO recording (audio_hash, path, recorded_at, "
                        "archive_root_index) VALUES ('x', 'y', now(), NULL)",
                    ),
                )


def test_archive_root_index_defaults_to_zero_server_side(
    migrated_engine: Engine,
) -> None:
    """The migration's server_default matters independently of the ORM
    default: a raw INSERT that omits the column (e.g. a future non-ORM tool,
    or a backfill against pre-Phase-6 data) must still land on 0, not NULL."""
    with migrated_engine.connect() as conn:
        with conn.begin():
            conn.execute(
                text(
                    "INSERT INTO recording (audio_hash, path, recorded_at) "
                    "VALUES ('x', 'y', now())",
                ),
            )
        value = conn.execute(
            text("SELECT archive_root_index FROM recording WHERE audio_hash = 'x'"),
        ).scalar()
    assert value == 0
```

Add `from sqlalchemy.exc import IntegrityError` to the imports if not already present.

- [ ] **Step 4: Run tests to verify they fail**

Run: `hatch test tests/test_migrations.py -v` (Docker, unsandboxed)
Expected: FAIL — the migration doesn't exist against `migrated_engine`'s fixture yet if Step 2's
file wasn't picked up, or the column genuinely isn't there yet if Step 1/2 aren't done. If Steps
1-2 are already complete, these should already PASS — in that case this step confirms nothing
regressed rather than proving red-to-green; note that explicitly rather than skipping the run.

- [ ] **Step 5: Run the full suite**

Run: `hatch test` (Docker, unsandboxed) — expect all passing, no warnings. `compare_metadata`
(the model-vs-migration drift check) must see the new column identically on both sides — a plain
`Integer` column with no CHECK constraint needs no `_comparable` exclusion (unlike the enum
columns `CLAUDE.md`'s Migrations section discusses).

Run: `hatch run types:check`, `hatch fmt --check` — expect clean.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/store/models.py alembic/versions/ tests/test_migrations.py
git commit -m "feat: add Recording.archive_root_index (additive, unused yet)"
```

---

## Task 2: `Config.archive_roots` end to end

**Files:**
- Modify: `src/fledermap/config.py`
- Modify: `src/fledermap/services/ingest.py`
- Modify: `src/fledermap/cli/main.py`
- Modify: `src/fledermap/jobs/tasks.py`
- Test: `tests/test_config.py` (extend + mechanically fix ~61 existing call sites)
- Test: `tests/test_ingest_service.py` (extend + mechanically fix ~33 existing `commit_scan` calls)
- Test: `tests/test_cli.py` (mechanically fix ~30 call sites)
- Test: `tests/test_jobs_tasks.py` (mechanically fix 3 media-task `additional_context` dicts)

**Interfaces:**
- Consumes: `Recording.archive_root_index` (Task 1).
- Produces: `Config.archive_roots: tuple[Path, ...]`, `ENV_ARCHIVE_ROOTS = "FLEDERMAP_ARCHIVE_ROOTS"`,
  `commit_scan(session, scanned: Iterable[tuple[ScannedFile, int]], *, archive_roots: Sequence[Path]) -> IngestReport`,
  all consumed by Task 3 (`run_ingest_cycle` reuses `commit_scan` and the same scan-orchestration
  shape `ingest`'s CLI body now has).

- [ ] **Step 1: `Config.archive_roots` — failing tests**

Add to `tests/test_config.py`, alongside the existing per-field test groups (add `ENV_ARCHIVE_ROOTS`
to the import block):

```python
def test_missing_archive_roots_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.delenv(ENV_ARCHIVE_ROOTS, raising=False)
    with pytest.raises(ConfigError, match=ENV_ARCHIVE_ROOTS):
        Config.from_env()


def test_single_archive_root_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    root = tmp_path / "archive"
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(root))
    config = Config.from_env()
    assert config.archive_roots == (root.resolve(),)


def test_multiple_archive_roots_are_comma_separated_and_order_preserved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    first = tmp_path / "syncthing"
    second = tmp_path / "sdcard-dump"
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, f"{first},{second}")
    config = Config.from_env()
    assert config.archive_roots == (first.resolve(), second.resolve())


def test_archive_roots_strips_whitespace_and_drops_empty_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A trailing comma (a common hand-edited env var mistake) must not
    produce a phantom empty root, and " b" must resolve the same as "b"."""
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    a = tmp_path / "a"
    b = tmp_path / "b"
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, f"{a}, {b},")
    config = Config.from_env()
    assert config.archive_roots == (a.resolve(), b.resolve())


def test_archive_roots_expands_tilde_per_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(ENV_DATABASE_URL, "postgresql://x/y")
    monkeypatch.setenv(ENV_MEDIA_ROOT, str(tmp_path / "media"))
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, "~/archive")
    config = Config.from_env()
    assert config.archive_roots == ((tmp_path / "archive").resolve(),)


def test_config_file_supplies_archive_roots_as_a_toml_array(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    path = _write_config_file(
        tmp_path,
        f'database_url = "postgresql://x/y"\n'
        f'media_root = "{tmp_path / "media"}"\n'
        f'archive_roots = ["{first}", "{second}"]\n',
    )
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    monkeypatch.delenv(ENV_ARCHIVE_ROOTS, raising=False)
    config = Config.from_env()
    assert config.archive_roots == (first.resolve(), second.resolve())


def test_env_archive_roots_overrides_config_file_archive_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = _write_config_file(
        tmp_path,
        f'database_url = "postgresql://x/y"\n'
        f'media_root = "{tmp_path / "media"}"\n'
        f'archive_roots = ["{tmp_path / "from-file"}"]\n',
    )
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    from_env_root = tmp_path / "from-env"
    monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(from_env_root))
    config = Config.from_env()
    assert config.archive_roots == (from_env_root.resolve(),)


def test_non_list_non_string_archive_roots_in_config_file_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = _write_config_file(
        tmp_path,
        f'database_url = "postgresql://x/y"\narchive_roots = 5\n',
    )
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    with pytest.raises(ConfigError, match="archive_roots"):
        Config.from_env()
```

Every OTHER existing test in this file that builds a `Config` now also needs
`monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))` and its
`Config.from_env(tmp_path)` call changed to `Config.from_env()` — covered in Step 5 below, not
repeated per-test here (~55 remaining call sites; see that step for the exact mechanical pattern).

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `hatch test tests/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'ENV_ARCHIVE_ROOTS'` (and everything else in the
file also failing once that import fails, which is expected and resolved by Step 5).

- [ ] **Step 3: Implement `Config.archive_roots`**

In `src/fledermap/config.py`:

Add the constant near the other `ENV_*` constants:
```python
ENV_ARCHIVE_ROOTS = "FLEDERMAP_ARCHIVE_ROOTS"
```

Add `"archive_roots"` to `_KNOWN_FILE_KEYS`, and rewrite the comment above it (it currently claims
`archive_root` is deliberately absent because it's "supplied per-invocation" — no longer true):
```python
# Every key the config file is allowed to set -- one entry per `Config` field
# that has a `FLEDERMAP_*` env var above. Checked in `_load_config_file` so a
# typo'd key (`sesion_gap_hours`) fails loudly instead of being silently
# ignored forever.
_KNOWN_FILE_KEYS = frozenset(
    {
        "database_url",
        "archive_roots",
        "timestamp_source",
        "default_timezone",
        "session_gap_hours",
        "site_eps_m",
        "site_min_points",
        "transect_distance_m",
        "media_root",
        "static_root",
        "port",
        "host",
    },
)
```

Add a parsing helper, near `_as_str`:
```python
def _parse_archive_roots(value: Any, label: str) -> tuple[Path, ...]:
    """Env var: comma-separated. Config file: a native TOML array, OR a
    single comma-separated string (accepted the same way, for consistency
    with the env var format rather than forcing a different shape per
    source). Order is preserved -- it's meaningful (design spec §2): the
    first configured root wins any relative-path tie.
    """
    if isinstance(value, str):
        raw_parts = value.split(",")
    elif isinstance(value, list):
        raw_parts = [_as_str(v, label) for v in value]
    else:
        msg = f"{label}={value!r} must be a comma-separated string or an array of paths."
        raise ConfigError(msg)
    parts = [p.strip() for p in raw_parts if p.strip()]
    if not parts:
        msg = f"{label} must name at least one directory."
        raise ConfigError(msg)
    return tuple(Path(p).expanduser().resolve() for p in parts)
```

Change the `Config` dataclass field (replacing `archive_root: Path`):
```python
    archive_roots: tuple[Path, ...]
```
(keep its position as the second field, right after `database_url` — both are required-no-default
and must stay ahead of every defaulted field.)

Change `from_env`'s signature (drop the parameter entirely):
```python
    @classmethod
    def from_env(cls) -> Config:
```

Add the resolution block, right after the `database_url` block (before `timestamp_source_raw = ...`):
```python
        archive_roots_raw = _lookup(ENV_ARCHIVE_ROOTS, "archive_roots", file_values)
        if not archive_roots_raw:
            msg = (
                f"{ENV_ARCHIVE_ROOTS} is not set, and no 'archive_roots' entry "
                f"exists in {config_path}. Point it at one or more directories "
                "holding recordings -- comma-separated for the env var, or a "
                "TOML array in the config file."
            )
            raise ConfigError(msg)
        archive_roots = _parse_archive_roots(
            archive_roots_raw,
            _source_label(ENV_ARCHIVE_ROOTS, "archive_roots", config_path),
        )
```

In the final `return cls(...)`, replace `archive_root=archive_root.resolve(),` with
`archive_roots=archive_roots,`.

- [ ] **Step 4: `commit_scan` multi-root threading**

In `src/fledermap/services/ingest.py`:

Add `Sequence` to the `collections.abc` import (alongside the existing `Iterable`).

Add the field to `Recording`'s write path — first, the `REPLACED`-detection query must be scoped
per-root too, not just per-path (design spec §3: a same-relative-path collision across two
DIFFERENT roots must never be read as one replacing the other):

Replace the whole `commit_scan` function body with:
```python
def commit_scan(
    session: OrmSession,
    scanned: Iterable[tuple[ScannedFile, int]],
    *,
    archive_roots: Sequence[Path],
) -> IngestReport:
    """Write scanned files to the database, resolving each by `audio_hash`.

    `scanned` pairs each item with the index (into `archive_roots`) of the
    root it was scanned under (design spec §3/§4) -- the caller already knows
    this (it scans one root at a time), so it's threaded through rather than
    re-derived here by testing path prefixes.

    Implements the four-row resolution table in spec section 6:
    unknown hash -> CREATED; known hash + same path -> UNCHANGED/UPDATED;
    known hash + new path -> MOVED; same path (AND SAME ROOT INDEX) + new
    hash -> REPLACED (the old row is never deleted -- spec is explicit that
    deleting it would destroy manually entered identifications). A same-path
    collision across DIFFERENT root indices is a different fact entirely --
    two distinct detectors' independent auto-numbering colliding on filename
    text -- and must not be read as a replace; scoping the REPLACED query to
    `archive_root_index == root_index` is what keeps that distinct (design
    spec §3).
    """
    report = IngestReport()
    now = datetime.now(tz=UTC)
    seen_hashes: set[str] = set()

    for item, root_index in scanned:
        rel = _relative(item.path, archive_roots[root_index])

        if item.audio_hash in seen_hashes:
            report.duplicates += 1
            continue
        seen_hashes.add(item.audio_hash)

        existing = session.scalars(
            select(Recording).where(Recording.audio_hash == item.audio_hash),
        ).one_or_none()

        if existing is None:
            replaced = session.scalars(
                select(Recording)
                .where(
                    Recording.path == rel,
                    Recording.archive_root_index == root_index,
                    Recording.missing_since.is_(None),
                )
                .order_by(Recording.id.desc())
                .limit(1),
            ).first()
            if replaced is not None:
                replaced.missing_since = now

            recording = Recording(
                audio_hash=item.audio_hash,
                path=rel,
                archive_root_index=root_index,
                ingested_at=now,
            )
            _apply_metadata(recording, item)
            session.add(recording)
            session.flush()
            _apply_identifications(
                session,
                recording,
                item.metadata.identifications,
                report,
                now,
            )
            report.record(
                IngestOutcome.REPLACED
                if replaced is not None
                else IngestOutcome.CREATED,
                audio_hash=item.audio_hash,
            )
            continue

        moved = existing.path != rel
        existing.path = rel
        # Corrects a stale index in place, the same way `missing_since` below
        # is unconditionally cleared on reappearance -- not reported as its
        # own outcome (design spec §3, index drift is accepted as
        # self-healing: config reordering makes a stored index stale, and the
        # next scan that finds this hash again silently corrects it).
        existing.archive_root_index = root_index
        existing.missing_since = None
        metadata_changed = _apply_metadata(existing, item)
        ids_changed = _apply_identifications(
            session,
            existing,
            item.metadata.identifications,
            report,
            now,
        )

        if moved:
            report.record(IngestOutcome.MOVED, audio_hash=item.audio_hash)
        elif metadata_changed or ids_changed:
            report.record(IngestOutcome.UPDATED, audio_hash=item.audio_hash)
        else:
            report.record(IngestOutcome.UNCHANGED, audio_hash=item.audio_hash)

    return report
```

(`_relative`, `_apply_metadata`, `_apply_identifications`, `sweep_missing`, and everything else in
this file are unchanged.)

- [ ] **Step 5: Fix every existing `commit_scan`/`Config.from_env` call site**

This is mechanical but touches many places — the same shape of ripple
`docs/superpowers/plans/2026-08-25-fledermap-phase3-media-jobs.md`'s Task 3 went through for
`media_root`, at larger scale because `archive_roots` is consumed more widely.

**`tests/test_ingest_service.py`** (~33 call sites, e.g. `commit_scan(session, [a], archive_root=ROOT)`):
grep for `commit_scan(` in this file. Every call passing a bare list of `ScannedFile` (e.g. `[a]`,
`[a, b]`) becomes a list of `(item, 0)` tuples (single-root fixtures — `ROOT` stays the one
configured root, index 0), and `archive_root=ROOT` becomes `archive_roots=(ROOT,)`. Example:
```python
# before
report = commit_scan(session, [a, b], archive_root=ROOT)
# after
report = commit_scan(session, [(a, 0), (b, 0)], archive_roots=(ROOT,))
```
Add at least one NEW test exercising `root_index != 0` and the collision fix directly
(`ScannedFile` is confirmed `@dataclass(frozen=True)` with `path: Path` a direct field —
`dataclasses.replace(b, path=...)` below is real, not a guess):
```python
def test_same_relative_path_from_different_roots_is_not_a_replace(
    engine: Engine,
) -> None:
    """Design spec §3: two detectors' independent auto-numbering colliding on
    filename text must produce two distinct rows, not a REPLACED outcome."""
    other_root = Path("/other-archive")
    a = _scanned(digest="a" * 64)
    b = _scanned(digest="b" * 64)  # same relative path as `a` (default name),
                                    # different content -- would collide if
                                    # archive_root_index weren't in the query
    b = replace(b, path=other_root / "Session_20130401_053030" / b.path.name)

    with OrmSession(engine) as session:
        seed_taxonomy(session)
        report = commit_scan(
            session,
            [(a, 0), (b, 1)],
            archive_roots=(ROOT, other_root),
        )

    assert report.created == 2
    assert report.replaced == 0
```
(Add `from dataclasses import replace` to the test file's imports.)

```python
def test_archive_root_index_self_heals_on_next_scan(engine: Engine) -> None:
    """Design spec §3/§10 decision 6: a stale index (e.g. from reordering
    configured roots) corrects itself the next time the same hash is scanned
    from its real root -- no data loss, no manual fix needed."""
    a = _scanned(digest="a" * 64)

    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [(a, 1)], archive_roots=(Path("/wrong"), ROOT))
        session.commit()
        commit_scan(session, [(a, 0)], archive_roots=(ROOT, Path("/other")))
        session.commit()
        recording = session.scalars(
            select(Recording).where(Recording.audio_hash == a.audio_hash),
        ).one()

    assert recording.archive_root_index == 0
```

**`src/fledermap/cli/main.py`** (5 `Config.from_env(...)` call sites): change all five to
`Config.from_env()`. For `ingest` and `worker`, ALSO remove the `@click.argument("archive", ...)`
decorator and the `archive: Path` parameter entirely (both commands are config-only from here on).

Rewrite `ingest`'s body (the scan loop) to iterate every configured root:
```python
@cli.command()
@click.option(
    "--sweep/--no-sweep",
    default=True,
    help="Flag recordings whose source file was not found.",
)
@click.pass_context
def ingest(ctx: click.Context, sweep: bool) -> None:
    """Scan every configured archive root and write recordings to the
    database. Read-only on every root (D16).

    Exit codes: 0 on success. 1 if configuration is invalid (nothing was
    written). 3 if the ingest itself succeeded and was committed, but the
    missing-file sweep was refused -- check the warning on stderr for which.
    """
    try:
        config = Config.from_env()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)
    ensure_schema(jobs_app, engine)

    seen: set[str] = set()
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        scanned: list[tuple[ScannedFile, int]] = []
        skipped = 0
        incomplete_skips = 0
        for root_index, root in enumerate(config.archive_roots):
            for item in scan_with_skips(
                root,
                timestamp_source=config.timestamp_source,
                default_timezone=config.default_timezone,
            ):
                if isinstance(item, ScannedFile):
                    scanned.append((item, root_index))
                    seen.add(item.audio_hash)
                else:
                    _, reason = item
                    skipped += 1
                    if reason in INCOMPLETE_SCAN_REASONS:
                        incomplete_skips += 1

        report = commit_scan(session, scanned, archive_roots=config.archive_roots)
        session.commit()

        enqueue_media(report.created_hashes, engine)

        click.echo(
            f"created {report.created}  unchanged {report.unchanged}  "
            f"updated {report.updated}  moved {report.moved}  "
            f"replaced {report.replaced}  duplicates {report.duplicates}  "
            f"skipped {skipped}",
        )
        click.echo(
            f"identifications added {report.identifications_added}  "
            f"superseded {report.identifications_superseded}",
        )
        if report.unmapped_labels:
            labels = ", ".join(sorted(report.unmapped_labels))
            click.echo(f"unmapped labels needing review: {labels}")

        if sweep:
            try:
                flagged = sweep_missing(session, seen, skipped=incomplete_skips)
                session.commit()
                if flagged:
                    click.echo(f"flagged {flagged} recording(s) as missing")
            except MassDisappearanceError as exc:
                click.echo(f"WARNING: {exc}", err=True)
                ctx.exit(EXIT_SWEEP_REFUSED)
            except IncompleteScanError as exc:
                click.echo(f"WARNING: {exc}", err=True)
                ctx.exit(EXIT_SWEEP_REFUSED)
```

For `worker`, drop the `archive` argument/decorator and change `Config.from_env(archive)` to
`Config.from_env()`; leave the rest of its body as-is for now (Task 4 rewrites it further for
watchdog — don't add async here, just fix the signature and the `additional_context` key, see
below).

For `derive`, `serve`, `enqueue_media_command`: change `Config.from_env(Path.cwd())` to
`Config.from_env()` (three call sites; `Path.cwd()` was always a throwaway value these commands
never used).

**`src/fledermap/jobs/tasks.py`** (3 media tasks): each of `render_spectrogram_task`,
`render_oscillogram_task`, `make_preview_task` currently does:
```python
    archive_root: Path = context.additional_context["archive_root"]
    ...
    wav_path = archive_root / recording.path
```
Change to:
```python
    archive_roots: tuple[Path, ...] = context.additional_context["archive_roots"]
    ...
    wav_path = archive_roots[recording.archive_root_index] / recording.path
```
in all three functions.

In `worker`'s body (`cli/main.py`), change the `additional_context` dict's `"archive_root":
config.archive_root` to `"archive_roots": config.archive_roots`.

**`tests/test_config.py`**: for every remaining test that calls `Config.from_env(tmp_path)`
(everything not touched by Step 1's new tests — grep for `Config.from_env(` to enumerate them),
change the call to `Config.from_env()` and add
`monkeypatch.setenv(ENV_ARCHIVE_ROOTS, str(tmp_path / "archive"))` next to the existing
`monkeypatch.setenv(ENV_DATABASE_URL, ...)` line in that test. Tests that already `delenv`/assert
around `ENV_DATABASE_URL` specifically (e.g. `test_missing_database_url_raises`) still need
`ENV_ARCHIVE_ROOTS` set so they fail for the reason they're actually testing, not a different one.

**`tests/test_cli.py`**: grep for `env = {` and inline `env={` dicts (~30). Add
`"FLEDERMAP_ARCHIVE_ROOTS": str(archive)` (reusing whatever `archive`/`tmp_path / "archive"`
variable that test already has in scope) to every one. Separately, grep for
`["ingest", str(archive)]` / `["worker", str(archive), ...]` (or their `--no-sweep`-flagged
variants) in `runner.invoke(cli, [...], env=env)` calls, and remove the `str(archive)` positional
element from the args list (it's still needed as a plain local variable to build the archive
fixture and populate the env dict — only the CLI invocation's positional argument goes away).

**`tests/test_jobs_tasks.py`**: `_write_wav`/`_make_recording` and the three
`test_..._task_writes_a_file` tests each build an `additional_context` dict with
`"archive_root": archive_root`. Change the key to `"archive_roots": (archive_root,)` (a one-tuple —
these tests don't exercise multi-root, `Recording.archive_root_index` defaults to `0` per Task 1,
which correctly indexes into a one-element tuple).

- [ ] **Step 6: Run tests to verify they pass**

Run: `hatch test tests/test_config.py tests/test_ingest_service.py -v` (Docker, unsandboxed)
Expected: PASS, including every new test from Step 1/Step 5's additions.

Run: `hatch test tests/test_cli.py tests/test_jobs_tasks.py -v` (Docker, unsandboxed)
Expected: PASS.

- [ ] **Step 7: Full verification**

Run: `hatch test` (Docker, unsandboxed) — full suite, expect all passing, no warnings. This is the
step most likely to surface a missed call site (an `AttributeError: 'Config' object has no
attribute 'archive_root'` or a `ConfigError` about `FLEDERMAP_ARCHIVE_ROOTS` from a file this task
description didn't enumerate) — if so, fix it the same way as the enumerated sites above, it isn't
a sign something else is wrong.

Run: `hatch run types:check`, `hatch fmt --check` — expect clean.

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/config.py src/fledermap/services/ingest.py src/fledermap/cli/main.py \
        src/fledermap/jobs/tasks.py tests/test_config.py tests/test_ingest_service.py \
        tests/test_cli.py tests/test_jobs_tasks.py
git commit -m "feat: Config.archive_roots -- ordered list, config-only (no more positional ARCHIVE)"
```

---

## Task 3: `run_ingest_cycle` — the Procrastinate task

**Files:**
- Modify: `src/fledermap/jobs/tasks.py`
- Modify: `src/fledermap/cli/main.py` (one line — see Step 4)
- Test: `tests/test_jobs_tasks.py` (extend + scope every existing `run_worker`/`_run_worker` call)

**Interfaces:**
- Consumes: `Config` (Task 2, whole object passed via `additional_context["config"]`), `commit_scan`
  (Task 2), `scan_with_skips`/`INCOMPLETE_SCAN_REASONS` (existing, `ingest/scan.py`), `sweep_missing`/
  `MassDisappearanceError`/`IncompleteScanError` (existing, `services/ingest.py`), `partition_sessions`
  (existing, `derive/sessions.py`), `derive_sites` (existing, `services/derive.py`), `enqueue_media`
  (existing, `services/media.py`, imported locally to avoid a circular import — see Global
  Constraints).
- Produces: `run_ingest_cycle` (a `procrastinate.Task`), `_INGEST_CYCLE_LOCK` (the shared lock/
  queueing_lock key), consumed by Task 4 (the watchdog event handler defers it manually).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_jobs_tasks.py`:

```python
from fledermap.config import Config
from fledermap.jobs.tasks import _INGEST_CYCLE_LOCK, run_ingest_cycle
from fledermap.store.models import Session as SessionModel  # avoid clashing with OrmSession


def _make_config(tmp_path: Path, *, archive_roots: tuple[Path, ...] | None = None) -> Config:
    roots = archive_roots if archive_roots is not None else (tmp_path / "archive",)
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
    return Config(
        database_url="postgresql://unused/unused",  # never read by run_ingest_cycle itself
        archive_roots=roots,
        media_root=tmp_path / "media",
    )


def test_run_ingest_cycle_creates_a_recording_and_derives(
    engine: Engine, tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    _write_wav(archive_root, "EPTSER_20150610_215446.wav")
    old = time.time() - 3600
    os.utime(archive_root / "EPTSER_20150610_215446.wav", (old, old))
    config = _make_config(tmp_path, archive_roots=(archive_root,))

    run_ingest_cycle.configure(
        lock=_INGEST_CYCLE_LOCK, queueing_lock=_INGEST_CYCLE_LOCK,
    ).defer(timestamp=int(time.time()))
    _run_worker(
        engine,
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        queues=["ingest"],
        additional_context={"config": config, "engine": engine},
    )

    with OrmSession(engine) as session:
        count = session.scalar(select(func.count()).select_from(Recording))
    assert count == 1


def test_run_ingest_cycle_logs_and_continues_on_incomplete_scan(
    engine: Engine, tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A file too young to have settled makes the sweep refuse
    (IncompleteScanError) -- the cycle must log it and return normally, not
    raise (design spec §6): the job must still show 'succeeded', not
    'failed', in procrastinate_jobs."""
    archive_root = tmp_path / "archive"
    _write_wav(archive_root, "fresh.wav")  # NOT backdated -- still "unsettled"
    config = _make_config(tmp_path, archive_roots=(archive_root,))

    job_id = run_ingest_cycle.configure(
        lock=_INGEST_CYCLE_LOCK, queueing_lock=_INGEST_CYCLE_LOCK,
    ).defer(timestamp=int(time.time()))
    with caplog.at_level(logging.ERROR):
        _run_worker(
            engine,
            wait=False,
            install_signal_handlers=False,
            listen_notify=False,
            queues=["ingest"],
            additional_context={"config": config, "engine": engine},
        )

    assert "refusing to sweep" in caplog.text
    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM procrastinate_jobs WHERE id = :id"),
            {"id": job_id},
        ).scalar()
    assert status == "succeeded"


def test_run_ingest_cycle_fails_the_job_on_an_unexpected_error(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinct from the known-refusal path above: an exception that is
    NEITHER MassDisappearanceError NOR IncompleteScanError must propagate so
    Procrastinate marks the job failed (design spec §6), not get swallowed
    the same way the two known refusal types are. `Path.rglob()` on a
    nonexistent directory does NOT raise (confirmed directly -- it just
    yields nothing), so a bad `archive_roots` entry can't be used to trigger
    this path; monkeypatching `seed_taxonomy` (the first thing the task body
    calls) to raise is deterministic and portable, unlike a permission-based
    approach that would behave differently under a root test runner."""
    import fledermap.jobs.tasks as tasks_module

    def _boom(*_args: object, **_kwargs: object) -> None:
        msg = "synthetic failure for test coverage"
        raise RuntimeError(msg)

    monkeypatch.setattr(tasks_module, "seed_taxonomy", _boom)
    config = _make_config(tmp_path)

    job_id = run_ingest_cycle.configure(
        lock=_INGEST_CYCLE_LOCK, queueing_lock=_INGEST_CYCLE_LOCK,
    ).defer(timestamp=int(time.time()))
    _run_worker(
        engine,
        wait=False,
        install_signal_handlers=False,
        listen_notify=False,
        queues=["ingest"],
        additional_context={"config": config, "engine": engine},
    )

    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM procrastinate_jobs WHERE id = :id"),
            {"id": job_id},
        ).scalar()
    assert status == "failed"
```

Add `import logging`, `import os`, `import time`, `from sqlalchemy import func` to the test file's
imports if not already present (check first — `func`/`select` are likely already imported given
existing tests use `select`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_jobs_tasks.py -v` (Docker, unsandboxed)
Expected: FAIL with `ImportError: cannot import name 'run_ingest_cycle'`.

- [ ] **Step 3: Implement `run_ingest_cycle`**

In `src/fledermap/jobs/tasks.py`, add to the imports:
```python
import logging
from datetime import timedelta

from fledermap.config import Config
from fledermap.derive.sessions import partition_sessions
from fledermap.domain.metadata import ScannedFile
from fledermap.ingest.scan import INCOMPLETE_SCAN_REASONS, scan_with_skips
from fledermap.services.derive import derive_sites
from fledermap.services.ingest import (
    IncompleteScanError,
    MassDisappearanceError,
    commit_scan,
    sweep_missing,
)
from fledermap.store.seed import seed_taxonomy
```

Add near the top, after `app = make_job_app()`:
```python
logger = logging.getLogger(__name__)

# Shared by both scheduling paths onto the SAME job -- the periodic
# registration below, and Task 4's event-triggered `defer_async()` -- so
# `queueing_lock` coalesces a burst of either kind into at most one pending
# run, and `lock` keeps that run from ever overlapping one already executing
# (design spec §5, Global Constraints above).
_INGEST_CYCLE_LOCK = "ingest_cycle"
_INGEST_CYCLE_CRON = "*/5 * * * *"
```

Add the task itself:
```python
@app.periodic(
    cron=_INGEST_CYCLE_CRON,
    lock=_INGEST_CYCLE_LOCK,
    queueing_lock=_INGEST_CYCLE_LOCK,
)
@app.task(queue="ingest", pass_context=True)
def run_ingest_cycle(context: procrastinate.JobContext, timestamp: int) -> None:
    """One full ingest+derive pass across every configured archive root.

    `timestamp` is unused directly -- Procrastinate's periodic-task machinery
    requires it as the first parameter (confirmed against
    `procrastinate/periodic.py`: `PeriodicDeferrer.defer_jobs` always injects
    it), and Task 4's manual `defer_async()` call supplies it too so both
    scheduling paths share one task signature.

    `MassDisappearanceError`/`IncompleteScanError` (the same two conditions
    `ingest`'s CLI turns into `EXIT_SWEEP_REFUSED`) are caught and logged --
    the job still "succeeds" from Procrastinate's point of view, so the next
    cycle (cron or event) retries automatically; there's no process to exit
    non-zero against any more (design spec §6). Anything else propagates:
    Procrastinate marks the job failed, subject to its own retry policy.
    """
    config: Config = context.additional_context["config"]
    engine = context.additional_context["engine"]

    # Local import: `services.media` imports task objects FROM this module at
    # ITS top level, so a top-level import here would be circular (Global
    # Constraints above). Safe here because by the time this function
    # actually runs, module import has long finished.
    from fledermap.services.media import enqueue_media

    seen: set[str] = set()
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        session.commit()

        scanned: list[tuple[ScannedFile, int]] = []
        skipped = 0
        incomplete_skips = 0
        for root_index, root in enumerate(config.archive_roots):
            for item in scan_with_skips(
                root,
                timestamp_source=config.timestamp_source,
                default_timezone=config.default_timezone,
            ):
                if isinstance(item, ScannedFile):
                    scanned.append((item, root_index))
                    seen.add(item.audio_hash)
                else:
                    _, reason = item
                    skipped += 1
                    if reason in INCOMPLETE_SCAN_REASONS:
                        incomplete_skips += 1

        report = commit_scan(session, scanned, archive_roots=config.archive_roots)
        session.commit()
        enqueue_media(report.created_hashes, engine)

        ingest_summary = (
            f"ingest cycle: created {report.created} unchanged {report.unchanged} "
            f"updated {report.updated} moved {report.moved} "
            f"replaced {report.replaced} duplicates {report.duplicates} "
            f"skipped {skipped} identifications added "
            f"{report.identifications_added} superseded "
            f"{report.identifications_superseded}"
        )

        try:
            flagged = sweep_missing(session, seen, skipped=incomplete_skips)
            session.commit()
        except (MassDisappearanceError, IncompleteScanError) as exc:
            logger.error("%s -- %s", ingest_summary, exc)
            return

        session_report = partition_sessions(
            session,
            session_gap=timedelta(hours=config.session_gap_hours),
            transect_distance_m=config.transect_distance_m,
        )
        session.commit()
        site_report = derive_sites(
            session,
            eps_m=config.site_eps_m,
            min_points=config.site_min_points,
        )
        session.commit()

        logger.info(
            "%s flagged_missing %d -- sessions created %d extended %d "
            "merge_proposals %d -- sites %d unclustered %d",
            ingest_summary,
            flagged,
            session_report.created,
            session_report.extended,
            session_report.merge_proposals,
            site_report.site_count,
            site_report.unclustered,
        )
```

(`SessionPartitionReport.created`/`.extended`/`.merge_proposals` and `SiteDeriveReport.site_count`/
`.unclustered` confirmed directly against `src/fledermap/derive/sessions.py` and
`src/fledermap/services/derive.py` while writing this plan — the field names above are exact, not
a transcription of `cli/main.py`'s usage.)

- [ ] **Step 4: Wire `"config"` into the real `worker` CLI command's `additional_context`**

Registering `run_ingest_cycle` as periodic means the shared `jobs_app`'s periodic deferrer will
attempt to defer (and, once picked up, run) it on EVERY `worker` invocation from now on — including
the real production command, not just this task's own isolated tests. Without this step, the
actual `worker` CLI (still synchronous until Task 4 rewrites it) would defer the job successfully
via cron, then crash it with `KeyError: 'config'` the moment a worker actually executes it —
silently, every ~5 minutes, from the commit that ends this task until Task 4 lands. This one-line
addition keeps the real CLI working end-to-end (via the cron path) as its own complete
deliverable; Task 4 only adds the watchdog/debounce event path on top.

In `src/fledermap/cli/main.py`'s `worker` command body, add `"config": config` to the
`additional_context` dict already passed to `worker_app.run_worker(...)`:
```python
        worker_app.run_worker(
            wait=wait,
            install_signal_handlers=wait,
            listen_notify=wait,
            additional_context={
                "archive_roots": config.archive_roots,
                "media_root": config.media_root,
                "config": config,
                "engine": engine,
            },
        )
```
(Task 4's Step 6 replaces this whole command body with an async version that carries the same
dict forward — this addition is not wasted work, just an intermediate state that keeps the CLI
correct in the meantime.)

- [ ] **Step 5: Scope every existing `_run_worker` call in `test_jobs_tasks.py` to `queues=["media"]`**

Per Global Constraints: the periodic deferrer now runs (and attempts to defer `run_ingest_cycle`)
on EVERY worker run against `jobs_app`, regardless of `wait`. Grep `tests/test_jobs_tasks.py` for
`_run_worker(` and add `queues=["media"]` to every call's kwargs that doesn't already have a
`queues` argument (this task's own new tests above already pass `queues=["ingest"]` deliberately —
leave those as-is). Also add a comment at `_run_worker`'s own definition noting why this matters
now, mirroring the Global Constraints' wording, so a future test added without `queues=` doesn't
silently reintroduce the gap.

`tests/test_cli.py`'s `test_worker_no_wait_processes_queued_jobs_and_writes_media` invokes the
REAL `worker` CLI command, which (unlike `_run_worker` above) deliberately does NOT scope
`queues=` — production `worker` must handle both queues in one process. Checked directly against
that test's actual assertions (confirmed while writing this plan, not assumed): it only asserts
`result.exit_code == 0` and file counts under `media_root`, never anything about `Session`/`Site`
rows. A periodic `run_ingest_cycle` also firing during that test's `--no-wait` pass is genuinely
harmless to it — the archive it re-scans is already fully ingested (idempotent, no new writes,
`enqueue_media([], ...)` is a no-op), and any `Session`/`Site` rows it creates for the first time
(since plain `ingest` never called `derive`) are not something this test looks at. No change
needed to that test.

- [ ] **Step 6: Run tests to verify they pass**

Run: `hatch test tests/test_jobs_tasks.py -v` (Docker, unsandboxed)
Expected: PASS, including all three new tests. Existing tests must ALSO still pass — this is
exactly what Step 5's `queues=["media"]` scoping protects.

- [ ] **Step 7: Full verification**

Run: `hatch test` (Docker, unsandboxed) — full suite, expect all passing, no warnings. Pay
particular attention to `tests/test_cli.py`'s worker/ingest tests, given Step 5's analysis above —
confirm they're genuinely still green, not just theoretically unaffected.
Run: `hatch run types:check`, `hatch fmt --check` — expect clean.

- [ ] **Step 8: Commit**

```bash
git add src/fledermap/jobs/tasks.py src/fledermap/cli/main.py tests/test_jobs_tasks.py
git commit -m "feat: run_ingest_cycle -- periodic ingest+derive task, cron backstop"
```

---

## Task 4: Watchdog integration — debounced event trigger

**Files:**
- Create: `src/fledermap/jobs/watch.py`
- Modify: `src/fledermap/cli/main.py` (rewrite `worker`'s body to async)
- Modify: `pyproject.toml` — add `watchdog` to `dependencies`
- Test: `tests/test_watch.py`
- Test: `tests/test_cli.py` (extend — `worker` now starts/stops an Observer)

**Interfaces:**
- Consumes: `run_ingest_cycle`, `_INGEST_CYCLE_LOCK` (Task 3).
- Produces: `start_watching(archive_roots, loop, defer, *, debounce_seconds=DEFAULT_SETTLE_SECONDS) -> BaseObserver`,
  used only by `cli/main.py`'s `worker` command.

- [ ] **Step 1: Add the dependency**

Add `"watchdog"` to `pyproject.toml`'s `[project] dependencies`, appended after `"psycopg[binary,pool]"`.

Run: `hatch run types:check`. `watchdog` ships its own inline types (`py.typed`); if mypy still
can't resolve it, add it to `[tool.hatch.envs.types]`'s `extra-dependencies` first, and only fall
back to a `[[tool.mypy.overrides]]` entry (matching the `sklearn.*` precedent) if that doesn't
resolve it either.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_watch.py
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from fledermap.jobs.watch import start_watching


async def _defer_recorder(calls: list[float]) -> None:
    calls.append(time.monotonic())


async def _run(coro_factory, timeout: float) -> None:
    """Run the given async body with a hard timeout so a bug that never
    fires the debounce can't hang the test suite."""
    await asyncio.wait_for(coro_factory(), timeout=timeout)


def test_a_single_event_defers_once_after_the_debounce_window(
    tmp_path: Path,
) -> None:
    calls: list[float] = []

    async def body() -> None:
        loop = asyncio.get_running_loop()
        observer = start_watching(
            [tmp_path], loop, lambda: _defer_recorder(calls), debounce_seconds=0.05,
        )
        try:
            (tmp_path / "new.wav").write_bytes(b"x")
            await asyncio.sleep(0.2)
        finally:
            observer.stop()
            observer.join()

    asyncio.run(_run(body, timeout=2.0))
    assert len(calls) == 1


def test_a_second_event_before_the_window_elapses_resets_the_timer(
    tmp_path: Path,
) -> None:
    """Two events 0.03s apart, debounce window 0.05s: if the timer were NOT
    reset, the first event's timer would fire at ~0.05s regardless -- the
    only way this test can see exactly one call at ~0.08s (not ~0.05s) is if
    the second event genuinely restarted the countdown."""
    calls: list[float] = []
    start = time.monotonic()

    async def body() -> None:
        loop = asyncio.get_running_loop()
        observer = start_watching(
            [tmp_path], loop, lambda: _defer_recorder(calls), debounce_seconds=0.05,
        )
        try:
            (tmp_path / "a.wav").write_bytes(b"x")
            await asyncio.sleep(0.03)
            (tmp_path / "b.wav").write_bytes(b"x")
            await asyncio.sleep(0.2)
        finally:
            observer.stop()
            observer.join()

    asyncio.run(_run(body, timeout=2.0))
    assert len(calls) == 1
    assert calls[0] - start >= 0.08 - 0.01  # small tolerance for scheduler jitter


def test_events_across_multiple_roots_are_all_watched(tmp_path: Path) -> None:
    calls: list[float] = []
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()

    async def body() -> None:
        loop = asyncio.get_running_loop()
        observer = start_watching(
            [root_a, root_b], loop, lambda: _defer_recorder(calls), debounce_seconds=0.05,
        )
        try:
            (root_b / "new.wav").write_bytes(b"x")
            await asyncio.sleep(0.2)
        finally:
            observer.stop()
            observer.join()

    asyncio.run(_run(body, timeout=2.0))
    assert len(calls) == 1
```

Timing-based tests are inherently a little tolerant-of-jitter rather than exact — that's accepted
here (the debounce window itself is a real wall-clock timer, not something to fake a clock for
without rewriting `start_watching` to accept an injectable clock/timer, which is more machinery
than this deserves). If these prove flaky in practice, widen the sleeps/tolerances rather than
deleting the assertions.

- [ ] **Step 3: Run tests to verify they fail**

Run: `hatch test tests/test_watch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fledermap.jobs.watch'`.

- [ ] **Step 4: Implement `jobs/watch.py`**

```python
"""Filesystem watching that triggers `run_ingest_cycle` (jobs/tasks.py)
between cron ticks. See design spec §5.

Debounced, not immediate: watchdog's Observer runs its own thread and fires
an event per filesystem change, not per "burst" -- an active Syncthing sync
can raise dozens of events over a couple of minutes. Deferring a cycle per
event would mean most of those cycles just get their sweep refused
(IncompleteScanError -- files still arriving, jobs/tasks.py). Instead each
event (re)starts a debounce timer; only once `debounce_seconds` pass with no
further event does the handler actually call `defer`.

Deliberately generic (`defer: Callable[[], Awaitable[None]]`, no import of
`run_ingest_cycle` or anything Procrastinate-specific): keeps this module
testable in isolation and mirrors this project's `media/`-stays-pure
separation. `cli/main.py` supplies the actual `run_ingest_cycle.defer_async`
closure.

Threading note: watchdog calls its event handler from its OWN thread, never
the asyncio loop the caller passes in. `_Debouncer.notify` is the one method
safe to call from that thread (`loop.call_soon_threadsafe`); every other
method runs ON `loop`, so no further synchronization is needed anywhere else
in this module.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from fledermap.ingest.scan import DEFAULT_SETTLE_SECONDS

if TYPE_CHECKING:
    from watchdog.observers.api import BaseObserver

_Defer = Callable[[], Awaitable[None]]


class _Debouncer:
    """Coalesces a burst of filesystem events into one `defer()` call, fired
    only after `debounce_seconds` of quiet."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        defer: _Defer,
        debounce_seconds: float,
    ) -> None:
        self._loop = loop
        self._defer = defer
        self._debounce_seconds = debounce_seconds
        self._handle: asyncio.TimerHandle | None = None

    def notify(self) -> None:
        """Call from ANY thread -- schedules the actual reset onto `_loop`."""
        self._loop.call_soon_threadsafe(self._reset)

    def _reset(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
        self._handle = self._loop.call_later(self._debounce_seconds, self._fire)

    def _fire(self) -> None:
        self._handle = None
        self._loop.create_task(self._defer())


class _Handler(FileSystemEventHandler):
    def __init__(self, debouncer: _Debouncer) -> None:
        self._debouncer = debouncer

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._debouncer.notify()


def start_watching(
    archive_roots: Sequence[Path],
    loop: asyncio.AbstractEventLoop,
    defer: _Defer,
    *,
    debounce_seconds: float = DEFAULT_SETTLE_SECONDS,
) -> BaseObserver:
    """Start one Observer watching every configured root, debounced onto
    `defer`. Caller owns the returned Observer's lifecycle: `.stop()` then
    `.join()` it on shutdown."""
    debouncer = _Debouncer(loop, defer, debounce_seconds)
    handler = _Handler(debouncer)
    observer = Observer()
    for root in archive_roots:
        observer.schedule(handler, str(root), recursive=True)
    observer.start()
    return observer
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `hatch test tests/test_watch.py -v`
Expected: PASS (3/3). If the debounce-reset test is flaky, widen its sleeps rather than removing
the assertion (Step 2's note).

- [ ] **Step 6: Rewrite `worker`'s CLI body to async, wiring the watcher in**

In `src/fledermap/cli/main.py`, add to the imports:
```python
import asyncio
import time

import procrastinate
from sqlalchemy.engine import Engine

from fledermap.jobs.tasks import _INGEST_CYCLE_LOCK, run_ingest_cycle
from fledermap.jobs.watch import start_watching
```
(`Engine` is not already imported in this file — only `Session as OrmSession` is, confirmed while
writing this plan — so this is a genuinely new import, not a duplicate of something already there.)

Add, above the `worker` command:
```python
async def _defer_ingest_cycle() -> None:
    try:
        await run_ingest_cycle.configure(
            lock=_INGEST_CYCLE_LOCK,
            queueing_lock=_INGEST_CYCLE_LOCK,
        ).defer_async(timestamp=int(time.time()))
    except procrastinate.exceptions.AlreadyEnqueued:
        pass  # a cycle is already queued behind the one currently running


async def _run_worker_async(config: Config, engine: Engine, *, wait: bool) -> None:
    async_connector = make_worker_connector(config.database_url)
    with jobs_app.replace_connector(async_connector) as worker_app:
        loop = asyncio.get_running_loop()
        observer = start_watching(config.archive_roots, loop, _defer_ingest_cycle)
        try:
            await worker_app.run_worker_async(
                wait=wait,
                install_signal_handlers=wait,
                listen_notify=wait,
                additional_context={
                    "archive_roots": config.archive_roots,
                    "media_root": config.media_root,
                    "config": config,
                    "engine": engine,
                },
            )
        finally:
            observer.stop()
            observer.join()
```

Replace the `worker` command's whole body:
```python
@cli.command()
@click.option(
    "--wait/--no-wait",
    default=True,
    help="Keep running until stopped (default), or process the current "
    "queue once and exit.",
)
def worker(wait: bool) -> None:
    """Run the media job worker AND the continuous ingest+derive watcher
    (design spec 2026-08-28-fledermap-phase6-watcher-design.md): a cron
    backstop plus a debounced filesystem watch, both deferring the same
    `run_ingest_cycle` task. Reads FLEDERMAP_ARCHIVE_ROOTS to resolve
    `Recording.path` AND to know which directories to watch.
    """
    logging.basicConfig(level=logging.INFO)

    try:
        config = Config.from_env()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    engine = make_engine(config.database_url)
    _run_migrations(config.database_url)
    ensure_schema(jobs_app, engine)

    asyncio.run(_run_worker_async(config, engine, wait=wait))
```

- [ ] **Step 7: Fix `tests/test_cli.py`'s worker test for the async rewrite**

The existing `test_worker_no_wait_processes_queued_jobs_and_writes_media` test invokes
`["worker", "--no-wait"]` via `CliRunner` — this should keep working unchanged from the test's own
point of view (Click still drives a synchronous `worker()` function; `asyncio.run(...)` is an
implementation detail inside it), EXCEPT that `--no-wait` now also starts a watchdog Observer for
the duration of that one pass and stops it again before returning. Run the existing test as-is
first; if it hangs or fails, the likely cause is `observer.join()` blocking on a platform/CI
quirk — investigate via `systematic-debugging` rather than guessing a fix, since this is exactly
the kind of concurrency edge case worth reproducing precisely before changing anything.

Add one new test confirming the watcher actually reacts to a file dropped in while `--wait` (no
`--no-wait`) runs, using a short-lived background thread to run the CLI invocation and stop it
after observing a DB change — this is more involved than the file's existing patterns, so design
it deliberately:
```python
def test_worker_wait_mode_picks_up_a_file_dropped_in_after_startup(
    clean_database_url: str, tmp_path: Path,
) -> None:
    """End-to-end: `worker` (no --no-wait) is already running, a WAV appears
    in the watched archive, and it gets ingested without a second `ingest`
    invocation -- the actual behavior this whole phase exists to add."""
    import threading

    archive = _archive_with_n_files(tmp_path, 0)  # empty, settled archive dir
    env = {
        "FLEDERMAP_DATABASE_URL": clean_database_url,
        "FLEDERMAP_MEDIA_ROOT": str(tmp_path / "media"),
        "FLEDERMAP_ARCHIVE_ROOTS": str(archive),
    }
    runner = CliRunner()
    result_holder: list[object] = []

    def _run() -> None:
        result_holder.append(runner.invoke(cli, ["worker"], env=env))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    time.sleep(0.5)  # let the worker/observer actually start

    path = archive / "EPTSER_20150610_215446.wav"
    path.write_bytes(
        build_wav([(b"fmt ", fmt_payload()), (b"data", b"\x01\x02" * 32)]),
    )
    old = time.time() - 3600
    os.utime(path, (old, old))

    deadline = time.time() + 10
    engine = make_engine(clean_database_url)
    found = False
    while time.time() < deadline:
        with OrmSession(engine) as session:
            if session.scalar(select(func.count()).select_from(Recording)):
                found = True
                break
        time.sleep(0.2)

    # No clean way to stop a CliRunner-invoked `--wait` worker from here --
    # this test process exiting ends the daemon thread. Not attempting a
    # graceful shutdown call; document that limitation rather than papering
    # over it with a fragile signal-based workaround.
    assert found, "recording was not ingested within the timeout"
```

(`_archive_with_n_files(tmp_path, n)` confirmed directly against `tests/test_cli.py` while writing
this plan: `n=0` creates `tmp_path/archive/Session_20130401_053030/` — empty, and old enough to
never be "unsettled" since nothing is written into it — and returns `tmp_path/archive`, exactly
the empty archive root this test needs.)

- [ ] **Step 8: Run tests to verify they pass**

Run: `hatch test tests/test_cli.py -v` (Docker, unsandboxed)
Expected: PASS, including the new watcher end-to-end test. This test is slower than the rest of
the suite (real sleeps) — that's an accepted cost for genuine end-to-end coverage of the feature
this whole phase exists to add; do not delete it for being slow.

- [ ] **Step 9: Full verification**

Run: `hatch test` (Docker, unsandboxed) — full suite, expect all passing, no warnings.
Run: `hatch run types:check`, `hatch fmt --check` — expect clean.

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml src/fledermap/jobs/watch.py src/fledermap/cli/main.py \
        tests/test_watch.py tests/test_cli.py
git commit -m "feat: watchdog-based debounced trigger for run_ingest_cycle"
```

---

## Final Verification

After Task 4:

```bash
hatch fmt --check
hatch run types:check
hatch test
```

All green, no warnings. Then a manual smoke check (this phase's core behavior has no automated
end-to-end test beyond Task 4's Step 7 addition, which is necessarily narrow): run `fledermap
worker` against a real or synthetic archive with `FLEDERMAP_ARCHIVE_ROOTS` pointing at two
directories, drop a file into each, and confirm both get ingested without a manual `ingest`
invocation, and that `fledermap ingest`/`fledermap worker` still work standalone for manual/
maintenance use (design spec §5, §9). Then hand off to `superpowers:finishing-a-development-branch`
as usual — this plan's branch was created via `superpowers:using-git-worktrees`/SDD setup at
execution time, per that skill's process.

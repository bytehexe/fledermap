# Fledermap Phase 3 (Media + jobs) — design

Parent spec: `docs/superpowers/specs/2026-08-23-fledermap-design.md` (§4 architecture,
§7 derivation pitfalls for precedent, §8 media, §12 constraints, §13 testing, §14 v1
excludes, §15 phasing — "3 · Media + jobs: Procrastinate running; spectrograms and
÷10 previews generated on ingest").

## 1. Scope

Given a `Recording` row (Phase 1) with a real, readable source file, generate and
persist two derived media artifacts per recording: a spectrogram (WebP) and a
time-expanded ÷10 preview (Opus). Wire this through a Postgres-backed job queue
(Procrastinate) so generation happens asynchronously, off the ingest critical path.

Still no web surface — this phase is headless, like Phases 1–2. The `fledermap
worker` process and a small CLI backfill command are the only new user-facing
surfaces.

## 2. Deviations from the parent spec, decided during brainstorming

- **Spectrogram rendering is written fresh, not ported from batogram.** The parent
  spec (§2) names batogram as "build on, not reimplement," matching how
  `mkmapdiary`'s `LocalProjection`/`GeoCluster` were ported in Phase 2. Checked:
  unlike `mkmapdiary`, there is no local batogram checkout, and batogram itself
  (`jmears63/batogram`, MIT, on PyPI) is a Tkinter GUI application the author
  describes as "in fairly rapid flux" with no stable, separable library API — not
  a clean porting target the way GeoCluster/LocalProjection were. A spectrogram
  (STFT via `scipy.signal`, log-magnitude, a small colormap) is small and
  well-understood enough to write directly against `scipy`/`numpy`/`Pillow`
  without meaningfully more risk than porting unstable code would carry.
- **Opus encoding shells out to `ffmpeg`**, not a Python libopus binding. Chosen
  over `pyogg`/`opuslib` for maturity: ffmpeg is one well-known binary dependency
  (already the kind of external service this project depends on, alongside
  Postgres/PostGIS), where the Python bindings are comparatively unmaintained and
  would still need manual container muxing.
- **Procrastinate's own schema is applied via its own API, not vendored into
  Alembic.** Procrastinate ships its schema as versioned raw SQL, not SQLAlchemy
  metadata Alembic can autogenerate against. Hand-vendoring it into an Alembic
  migration would need manual re-sync on every Procrastinate upgrade, with no
  automated drift check — a worse version of exactly the blind spot
  `tests/test_migrations.py`'s `_comparable` filter already exists to manage
  narrowly. Keeping Procrastinate's schema on its own lifecycle, applied
  programmatically at the same point Alembic's migration runs, keeps the
  "run one command, your DB is ready" property for the end user without taking on
  that maintenance burden.
  **Checked against Procrastinate's own source (`schema.py`/`cli.py`):**
  `SchemaManager.apply_schema_async()` is the real underlying call (`procrastinate
  schema --apply` just wraps it) — but the CLI's own docstring warns "This won't
  work if the schema has already been applied," i.e. it is **not** safe to call
  unconditionally on every startup the way `alembic upgrade head` is. The
  implementing task must therefore check whether Procrastinate's schema already
  exists first (e.g. `procrastinate_jobs` present in `information_schema.tables`)
  and only call the apply method when it is absent — restoring the same
  idempotent "safe to run every time" property Alembic already has, rather than
  assuming Procrastinate gives it for free.
  **Resolved empirically against a real Postgres 16 container, not left as an
  open question:** `SchemaManager.apply_schema()` is a real, directly-callable
  sync method — but calling it exactly as documented
  (`app.schema_manager.apply_schema()`) FAILS against a real database with
  `psycopg2.errors.SyntaxError: too many parameters specified for RAISE`.
  Root cause, confirmed by reading the actual generated SQL: `apply_schema()`
  unconditionally runs `schema_sql.replace("%", "%%")` before executing it —
  presumably correct for whichever DBAPI call path Procrastinate's other
  connectors normally use (which apparently always pass a params argument,
  even an empty one, making a bare `%` significant to psycopg2) — but this
  quietly breaks the schema's own, unrelated `RAISE '...(job id: %)', job_id`
  statements (PL/pgSQL's own use of `%` as a format placeholder), turning a
  single real placeholder into an escaped literal (`%%`, zero placeholders)
  while still passing `job_id` as an argument — hence "too many parameters."
  **Confirmed working fix, verified end-to-end:** fetch the UNESCAPED SQL via
  `schema_manager.get_schema()` and execute it through a raw psycopg2 cursor
  obtained from `engine.raw_connection()`, calling `cursor.execute(sql)` with
  **no params argument at all** — not even an empty one. Going through
  SQLAlchemy's own `exec_driver_sql`/`connection.execute` still implicitly
  supplies an empty params structure that re-triggers the same problem
  (confirmed by testing that path too and watching it fail differently:
  `TypeError: ...immutabledict is not a sequence`). `ensure_schema` (plan
  Task 4) uses this confirmed recipe directly, not Procrastinate's own
  `apply_schema()`.
- **The `fledermap worker` process needs its own, separate async-capable
  connector — it cannot reuse the sync `SQLAlchemyPsycopg2Connector` used for
  deferring jobs.** This corrects the original assumption in this document's
  first draft (`§6` originally proposed one shared connector for everything).
  Confirmed both from Procrastinate's own docs ("An asynchronous connector is
  always required for running the worker or the Procrastinate CLI...
  Synchronous connectors are restricted to deferring jobs") and empirically:
  calling `app.run_worker(...)` on an App opened with
  `SQLAlchemyPsycopg2Connector` raises `SyncConnectorConfigurationError`
  outright. **Confirmed working shape, verified end-to-end against a real
  Postgres container:** keep ONE `App` instance for task registration
  (`@app.task` decorations, shared by both defer-side code and the worker —
  Procrastinate's tasks are bound to the App they're declared against, not to
  a specific connector), and swap in an async connector only for the
  duration of running the worker, via `App.replace_connector` — a documented,
  fully generic context manager (not specific to any one connector type):
  `with app.replace_connector(PsycopgConnector(conninfo=database_url)) as
  worker_app: worker_app.run_worker(...)`. This needs `psycopg[binary,pool]`
  (psycopg **3**, distinct from the `psycopg2-binary` this project already
  depends on for its own SQLAlchemy engine) as a new dependency, used ONLY by
  the `worker` command — `enqueue_media`/`backfill_media`/`ingest`'s defer
  calls never touch it.

## 3. Module layout

```
fledermap/
  media/
    __init__.py
    spectrogram.py   render_spectrogram()
    preview.py       make_preview()
  jobs/
    __init__.py
    app.py           Procrastinate App, SQLAlchemyPsycopg2Connector
    tasks.py         render_spectrogram_task, make_preview_task
  services/
    ingest.py        (modified) IngestReport.created_hashes; defer call sites
    media.py         (new) enqueue_media(), backfill_media()
  cli/
    main.py          (modified) `worker` command, `enqueue-media` command
```

`media/` stays pure — no DB session, no Procrastinate import, no queue awareness.
It takes file paths in, writes file paths out. `jobs/tasks.py` is the only place
that bridges `Recording` rows to `media/`'s functions.

## 4. `media/spectrogram.py`

```python
@dataclass(frozen=True)
class SpectrogramParams:
    window_ms: float = 3.0
    overlap: float = 0.5
    max_freq_hz: float = 128_000.0
    width_px: int = 1024
    height_px: int = 512

    @property
    def params_hash(self) -> str:
        """Short, stable hash of every field — the on-disk filename's
        `<params>` component. Changing any field invalidates existing
        renders without touching `audio_hash` (spec §8)."""


def render_spectrogram(
    wav_path: Path,
    out_path: Path,
    *,
    params: SpectrogramParams = SpectrogramParams(),
) -> None:
    """Read PCM via stdlib `wave`, STFT via `scipy.signal.spectrogram`,
    log-magnitude normalised to [0, 1], mapped through a small hand-written
    numpy colormap LUT (no matplotlib dependency solely for colour tables),
    written as WebP via Pillow. Writes to a temp file in `out_path`'s parent
    directory, then `os.replace()`s onto `out_path` — atomic on the same
    filesystem, so a concurrent reader never sees a partial file and two
    concurrent writers (however that happened) never interleave."""
```

Bat calls span roughly 9 kHz–212 kHz across the EU species this project targets
(spec's own species list, `docs/references.md`); `max_freq_hz` defaults to
128 kHz to cover the practical range without wasting resolution on near-silent
bins above it. This happens to equal the Nyquist frequency of the bundled
EMT sample rate (256 kHz ÷ 2) — coincidence worth naming so nobody mistakes it
for the reason. **`render_spectrogram` clamps its actual upper bound to
`min(params.max_freq_hz, source_sample_rate / 2)` at render time**, read from
the WAV header, not the fixed constant unconditionally — a recording at a
different sample rate (a different or future detector, a different EMT
setting) must never be asked to render frequency bins above its own Nyquist
limit, which don't exist in the data. `window_ms`/`overlap` are STFT tuning,
not correctness — a short window favours time resolution over frequency
resolution, appropriate for short, fast bat calls. All defaults are
revisitable without a schema change (`params_hash` exists precisely so a
settings change invalidates old renders without needing a migration).

## 5. `media/preview.py`

```python
def make_preview(wav_path: Path, out_path: Path) -> None:
    """Read the WAV header via stdlib `wave`, rewrite ONLY the declared frame
    rate to one tenth (no resampling — the PCM samples are untouched, per
    spec §8's "nearly free": 256 kHz becomes 25.6 kHz, so a 45 kHz
    Pipistrellus lands at 4.5 kHz, audible). Write the relabelled WAV to a
    temp file, then invoke `ffmpeg -y -i <temp.wav> -c:a libopus <out_path>`
    via `subprocess.run(..., check=True)`. Same atomic temp-file-then-replace
    pattern as `render_spectrogram` for the final `.opus` output; the
    intermediate relabelled WAV is a private tempfile, cleaned up in a
    `finally`."""
```

No `SpectrogramParams`-style params object: the ÷10 ratio is fixed by the parent
spec and not exposed as a setting in v1. `preview-<params>.opus`'s `<params>`
component is therefore a fixed literal (e.g. `preview-v1.opus`), not a computed
hash — still versioned by the filename so a future ratio change gets a new
filename without touching `audio_hash`.

## 6. `jobs/app.py`

```python
from procrastinate import App
from procrastinate.contrib.sqlalchemy import SQLAlchemyPsycopg2Connector

def make_job_app(engine: Engine) -> App:
    """One Procrastinate App shared by defer-side code (enqueue_media,
    backfill_media, ingest's own defer call) via SQLAlchemyPsycopg2Connector
    -- sharing this project's own SQLAlchemy engine/connection pool, no
    second connection pool, no asyncio for THIS side. This App is also where
    every @app.task lives (jobs/tasks.py) -- tasks are bound to the App
    object they're declared against, not to a specific connector, so the
    SAME app (and its already-registered tasks) is reused by the worker
    process too, just with its connector swapped for the duration of the
    run (see below) -- there is exactly one App per process, never two."""


def ensure_schema(app: App) -> None:
    """Procrastinate's own schema-apply is NOT idempotent (it errors if
    already applied) AND its own apply_schema()/apply_schema_async() methods
    are broken against a real Postgres server (see the deviations section
    above for the confirmed root cause and fix). Checks information_schema
    for procrastinate_jobs first; if absent, executes
    app.schema_manager.get_schema() via a raw psycopg2 cursor with no params
    argument, matching the confirmed working recipe exactly."""
```

Queues, matching parent spec §4's table exactly: `media` (this phase's two
tasks), `geo` and `classify` declared as known queue names but with no task
registered against them yet — explicitly out of scope for this phase (site
naming via poiidx, and any classifier integration, are later work; the spec
itself calls the classify queue "queue slot exists, no integration").

**Running the worker needs a second, async-capable connector — confirmed
empirically, not assumed** (see the deviations section above for the full
finding): `SQLAlchemyPsycopg2Connector` can defer jobs but cannot run a
worker (`app.run_worker(...)` raises `SyncConnectorConfigurationError`
outright). The `worker` CLI command (§9) builds a second connector,
`procrastinate.PsycopgConnector(conninfo=config.database_url)` (psycopg 3,
genuinely async — a new dependency, `psycopg[binary,pool]`, used only by this
one command), and runs the worker via `with app.replace_connector(async_connector)
as worker_app: worker_app.run_worker(...)` — the SAME App object (and its
already-`@app.task`-registered tasks from `jobs/tasks.py`), connector swapped
only for the duration of that call. `enqueue_media`/`backfill_media`/`ingest`
never touch this second connector at all.

## 7. `jobs/tasks.py` — locking

```python
@app.task(queue="media", pass_context=True)
def render_spectrogram_task(context: procrastinate.JobContext, audio_hash: str) -> None: ...

@app.task(queue="media", pass_context=True)
def make_preview_task(context: procrastinate.JobContext, audio_hash: str) -> None: ...
```

A task function is a plain module-level function — it only ever receives its
`.defer()`-time keyword arguments (`audio_hash`), nothing else automatically.
`archive_root`/`media_root`/the DB session factory are shared resources every
task needs but none of them own, so they ride in via Procrastinate's own
documented mechanism for exactly this case: `pass_context=True` on the task,
reading `context.additional_context["archive_root"]` /
`context.additional_context["media_root"]`, supplied once at
`app.run_worker(additional_context={...})` — not a bare module-level global,
and not re-read from `Config.from_env()` inside every task call. The engine
rides the same way (`context.additional_context["engine"]`) — each task opens
its own `with OrmSession(engine) as session:` for the duration of the call,
matching every other short-lived session in this codebase (`cli/main.py`'s
existing commands all do the same), rather than holding one session open for
the worker's whole lifetime.

Each resolves `audio_hash` → `Recording` → real path (`archive_root / recording.path`).
If the file is missing (`recording.missing_since is not None`, or the read
itself fails) the task raises — Procrastinate's own retry policy is configured
with a small fixed retry count (e.g. 3, exponential backoff) then permanent
failure, visible later via the job-status strip (Phase 5). No infinite retry
loop against a file that is never coming back (D16: deletion is permanent).

**Duplicate-enqueue protection (the race the human partner asked about
directly):** Procrastinate's own atomic `fetch_job` (select-and-mark-as-doing
in one DB operation) already guarantees a single job row is only ever picked
up by one worker — nothing to add there. What Procrastinate does **not** do
automatically is prevent the *same real-world unit of work* from being
deferred as two separate job rows (e.g. `commit_scan`'s own defer racing
`enqueue-media`'s backfill sweep seeing the same not-yet-enqueued recording).
Both tasks are deferred with a lock key computed by the *caller*
(`enqueue_media`, §8) — `params_hash` is never a task argument, since v1's
spectrogram/preview parameters are fixed code constants, not per-recording or
end-user-configurable (§12):

```python
spectrogram_params_hash = SpectrogramParams().params_hash  # fixed for this build
render_spectrogram_task.configure(
    lock=f"spectrogram:{audio_hash}:{spectrogram_params_hash}",
    queueing_lock=f"spectrogram:{audio_hash}:{spectrogram_params_hash}",
).defer(audio_hash=audio_hash)

make_preview_task.configure(
    lock=f"preview:{audio_hash}:v1",
    queueing_lock=f"preview:{audio_hash}:v1",
).defer(audio_hash=audio_hash)
```

`queueing_lock` refuses a second "todo" row with the same key
(`AlreadyEnqueued`, caught and ignored at the call site); `lock` additionally
serialises execution for that key even past the point where the first job has
already moved to "doing" — so two jobs for the same (recording, params) never
run concurrently regardless of timing. Keyed per task type (`spectrogram:`/
`preview:` prefix) so the two real tasks for one recording still run in
parallel; only genuine duplicates serialise. Combined with both functions'
own atomic temp-file-then-`os.replace()` writes (§4–5), this closes the race
at both the queue level and the filesystem level.

## 8. `services/ingest.py` and `services/media.py`

`IngestReport` (existing dataclass, `src/fledermap/services/ingest.py`) gains:

```python
created_hashes: list[str] = field(default_factory=list)
```

populated alongside the existing `self.created += 1` in `IngestReport.record()`
— additive, no existing field removed or renamed.

New `services/media.py`:

```python
def enqueue_media(created_hashes: list[str]) -> None:
    """Defer both tasks (locked/queueing-locked as above) for each hash. Called
    from `cli/main.py`'s `ingest` command AFTER `session.commit()` succeeds —
    not from inside `commit_scan` itself, which does not commit — so nothing
    can be picked up by a worker for a row that isn't durably committed yet."""

def backfill_media(db_session: OrmSession, media_root: Path) -> int:
    """For every Recording, check whether its current-params media files
    already exist on disk under `media_root`; if not, enqueue_media([hash]).
    Disk existence, not a Procrastinate job-history query: the job table isn't
    a reliable 'was this ever rendered' record (Procrastinate can be
    configured to delete completed jobs), and disk state is what actually
    determines whether a recording needs work. Returns the count enqueued."""
```

## 9. CLI (`cli/main.py`)

```
fledermap worker ARCHIVE     # app.run_worker() — blocking, listens on `media`
fledermap enqueue-media      # backfill_media() — one-shot, prints count enqueued
```

**`worker` takes the same `ARCHIVE` positional argument `ingest` does** —
checked the existing `derive` command (Phase 2) for precedent first: it calls
`Config.from_env(Path.cwd())`, passing the *current directory* as a throwaway
`archive_root` value, which works there only because `derive` never actually
reads `config.archive_root` for anything. `jobs/tasks.py`'s task bodies
genuinely need the real archive root — to turn a `Recording.path` (stored
relative, spec §6) into a real filesystem path to read — so `worker` cannot
borrow that placeholder trick; it needs the operator's real archive path, the
same one already passed to `ingest`. `enqueue-media` never opens a source
file (only checks `media_root` and defers jobs, §8), so it follows `derive`'s
existing `Config.from_env(Path.cwd())` precedent instead — consistent with
how a command that doesn't touch files already behaves in this codebase.

`worker` runs until a stop signal (`ctrl+c`/SIGTERM), matching Procrastinate's
own default (`wait=True`). `enqueue-media` is a one-shot command an operator
runs once after upgrading past this phase, or any time media params change and
a full re-render is wanted.

**`worker` gets a `--wait/--no-wait` flag** (default `--wait`), matching the
existing `--sweep/--no-sweep` pattern on `ingest`. A blocking command can't be
driven through `CliRunner().invoke()` the way `ingest`/`derive`'s tests do —
`--no-wait` exists so tests can exercise the real CLI command end-to-end
(defer a job via `ingest`, then run `worker ARCHIVE --no-wait` and assert the
file landed) rather than only testing `app.run_worker()` directly. `--no-wait`
implies Procrastinate's own documented testing recipe — `wait=False,
install_signal_handlers=False, listen_notify=False` — since a real, waiting
worker legitimately wants signal handling and NOTIFY-based low latency, but a
one-shot catch-up run (test or manual) needs neither.

**`worker` builds the second, async-capable connector described in §6 and
swaps it in for the run:**

```python
async_connector = procrastinate.PsycopgConnector(conninfo=config.database_url)
with jobs_app.replace_connector(async_connector) as worker_app:
    worker_app.run_worker(
        wait=wait,
        install_signal_handlers=wait,
        listen_notify=wait,
        additional_context={
            "archive_root": config.archive_root,
            "media_root": config.media_root,
            "engine": engine,
        },
    )
```

`ingest`/`enqueue-media` never construct a `PsycopgConnector` at all — only
`worker` does, since only running the worker requires one (§6).

## 10. Config additions

```python
media_root: Path   # required, own FLEDERMAP_MEDIA_ROOT env var, no default
```

**Naming collision to be aware of, not a design problem in itself:** the parent
spec uses "media" for two unrelated things — `fledermap/media/` (§4, the
*Python source package* added in §3 above, living under `src/`) and
`media/<hash[:2]>/<hash>/...` (§8, the *runtime storage tree* an operator
points `FLEDERMAP_MEDIA_ROOT` at). They never collide on disk (one is under
this repo's `src/`, the other is wherever the operator configures — never
defaulted to a path under the repo), but are worth naming explicitly here so
an implementer or reviewer doesn't conflate "the media module" with "the
media directory."

Required rather than defaulted (matching `database_url`'s treatment, not
`session_gap_hours`'s): where derived media lives is a real deployment decision
(disk space, backup policy), and it must be a location distinct from
`archive_root` — writing into the archive would violate D16's read-only
invariant on the source tree.

**Not `platformdirs`.** That library solves "guess a sensible per-user data
directory on the machine this process happens to run on" — the right tool for
a desktop app, wrong fit for a self-hosted *server* process. Inside a
container, "the user's data directory" is an arbitrary, meaningless path (some
service account's home), and what actually matters operationally is a
predictable, explicit path the operator names once and mounts a volume at —
exactly what an explicit required config value already gives, with no
platform-detection layer in between. This also keeps `media_root` consistent
with `archive_root` and `database_url`, both already explicit, required,
operator-supplied paths/URLs with no auto-detected default. Whatever the
eventual deployment story turns out to be (this design doesn't need to commit
to it), an explicit path is what makes `FLEDERMAP_MEDIA_ROOT=/data/media` a
one-line Docker volume mount — auto-detected platform dirs would work against
that, not for it.

`ffmpeg` is resolved from `PATH`, not independently configurable in v1 (YAGNI —
add an env var later only if a real deployment needs a nonstandard binary
location).

## 11. Testing

- `media/`'s two functions get direct unit tests against tiny synthesized WAV
  fixtures (matching Phase 1's guano-py convention) — no DB, no queue,
  no Procrastinate import. Assert real output: a spectrogram's WebP decodes to
  the expected pixel dimensions; a preview's Opus file, probed via `ffprobe`,
  reports a sample rate of the original ÷10 and a nonzero duration.
- Job execution is tested against the real testcontainers Postgres — **not**
  Procrastinate's `InMemoryConnector`, to stay consistent with this project's
  established "tests verify real behavior" convention (matching every DB test
  in Phases 1–2). Pattern: defer a task, then
  `app.run_worker(wait=False, install_signal_handlers=False, listen_notify=False)`,
  then assert the expected file exists (or, for the missing-file case, that the
  job is marked failed after its retry budget).
- Duplicate-enqueue regression test: defer the same task twice with the same
  lock/queueing_lock keys before running a worker; assert only one job row
  ends up in "todo" (the second `defer()` either raises `AlreadyEnqueued` and
  is caught, or is asserted to raise — whichever the implementation chooses,
  the test pins the actual contract).
- `ffmpeg` must be present wherever tests run; noted as a new test-environment
  requirement (Docker test image / dev container gets one `apt-get install
  ffmpeg` line — this plan does not touch CI/Docker files if none exist yet,
  only documents the requirement).

## 12. Explicitly out of scope for Phase 3

Any web surface (Phases 4–5) · the `geo` queue / poiidx site naming (queue name
reserved, no task) · the `classify` queue / any classifier integration (v2,
per parent spec §14) · configurable spectrogram/preview parameters exposed to
an end user (params exist as code constants with a hash, not a settings UI) ·
job-status reporting beyond what `hatch test`/manual `procrastinate` CLI
inspection already gives (the job-status strip is Phase 5).

## 13. Decisions made in this document

| # | Decision | Rationale |
|---|---|---|
| P3-1 | Spectrogram rendering written fresh (scipy/numpy/Pillow), not ported from batogram | No local checkout, batogram is GUI-shaped and explicitly unstable, not a clean porting target |
| P3-2 | Preview's Opus encoding shells out to ffmpeg | Mature, well-tested, one binary dependency vs. fragile Python libopus bindings |
| P3-3 | Procrastinate's schema applied via its own API, kept out of the Alembic chain | Its schema is raw-SQL-versioned by its own releases; vendoring would need manual re-sync with no drift check |
| P3-4 | Backfill command included in this phase (`fledermap enqueue-media`) | Recordings ingested in Phases 1–2, before this phase existed, would otherwise never get media |
| P3-5 | Both tasks deferred with combined `lock` + `queueing_lock`, keyed per task type per (audio_hash, params_hash) | `queueing_lock` alone doesn't cover a job already in "doing"; combining both closes the duplicate-enqueue race Procrastinate doesn't handle automatically |
| P3-6 | `backfill_media` checks disk state, not Procrastinate's job table, to decide what needs enqueueing | Job history isn't a reliable durable record (jobs can be configured to auto-delete); disk state is the actual source of truth for "has this been rendered" |
| P3-7 | `media_root` is a required config value, distinct from `archive_root` | Writing into the archive would violate D16's read-only invariant; where media lives is a real deployment decision, not safe to default silently |
| P3-8 | `ensure_schema` executes Procrastinate's schema SQL via a raw psycopg2 cursor with no params argument, not `app.schema_manager.apply_schema()` | `apply_schema()`'s own `%`→`%%` escaping breaks a legitimate single `%` inside the schema's own `RAISE '...%', arg` statements — confirmed by reproducing the exact Postgres error against a live container and tracing it to that escaping step |
| P3-9 | `fledermap worker` builds a second, async `PsycopgConnector` (psycopg 3) and runs via `app.replace_connector(...)`, rather than reusing the sync `SQLAlchemyPsycopg2Connector` everything else uses | Confirmed both from Procrastinate's own docs and empirically that `run_worker()` requires an async connector and raises `SyncConnectorConfigurationError` on a sync one — the App and its registered tasks are still shared (one process, one App), only the connector is swapped for the worker's run |

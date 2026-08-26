# Fledermap — project notes

Design authority: `docs/superpowers/specs/2026-08-23-fledermap-design.md` (decisions D1–D18;
R1–R3 all closed 2026-08-26 against real field recordings — see the spec's R1–R3 section for
scope and a new device-specific `wamd` longitude-sign bug found in the process). Phase 1 plan
is in `docs/superpowers/plans/`; its execution ledger is
`.superpowers/sdd/*/progress.md` (gitignored, but it carries every ruling made during the build).

Authoritative domain sources — species codes, names, file formats — live in
@docs/references.md. Check there before hand-entering a code or a chunk layout, and add
to it when you find a new source.

## Environment gotchas

- **Docker is blocked by the command sandbox.** Any `db`-marked test (testcontainers + PostGIS)
  must run with `dangerouslyDisableSandbox: true`. The failure does not look like a sandbox
  problem — it surfaces as `requests.exceptions.ConnectionError: PermissionError(1, 'Operation
  not permitted')` out of `docker.from_env()`, which reads like a network fault.
- **`$TMPDIR` is only set in *sandboxed* Bash calls.** Unsandboxed it expands to empty, so
  `cp x "$TMPDIR/y"` silently targets `/y` and fails on permissions — and any restore step that
  depends on it never runs. Use the scratchpad path literally when running unsandboxed.
- **`hatch run` brace-substitutes its arguments.** `hatch run python -c "...{x.name}..."` dies
  with `Unknown context field`. Put throwaway code in a file and run the file.
- Run `git` unsandboxed; sandboxed git config writes leave a stale `.git/config.lock`.
- **`ffmpeg` and `ffprobe` must be installed and on `PATH`.** `media/preview.py` shells out to
  `ffmpeg` for Opus encoding and the preview tests read back with `ffprobe`. Missing, they fail
  loudly with `FileNotFoundError` — deliberately not skipped, so the gap can't hide.
- **`FLEDERMAP_MEDIA_ROOT` is optional as of 2026-08-26**, and is a *separate* directory from
  the archive root. The archive is read-only (D16); derived media is written only under the
  media root. It falls back to a `platformdirs` *data* directory (not a cache directory — see
  the next bullet) when unset, reversing Phase 3's original required-with-no-default decision
  (dated deviation note in `docs/superpowers/specs/2026-08-25-fledermap-phase3-media-jobs-design.md`
  §10). **Set it explicitly for any real deployment anyway**: the fallback path is still wrong
  for a container, where "the user's data directory" is some arbitrary service-account home
  inside the container's own ephemeral filesystem — not backed up, and gone on the next
  `docker run` — exactly the failure mode the original required-with-no-default decision existed
  to force an operator to notice at startup instead of silently losing derived media later.
- **`FLEDERMAP_STATIC_ROOT` is optional (Phase 4)**, and unlike `FLEDERMAP_MEDIA_ROOT` above,
  a silent default is actually *fine* for it — it defaults to a `platformdirs` cache directory,
  since fetched vendor JS/CSS is small and regenerable rather than an operator's deliberate
  data-placement decision. `fledermap serve`
  fetches whatever's missing from it automatically on startup (`services/vendor_assets.py`'s
  `ensure_vendor_assets`, needs real network access on a cold cache — never part of the test
  suite's own execution path) — `fledermap fetch-assets` pre-warms it deliberately instead,
  for an offline/air-gapped install or to force a full verified re-fetch. This logic lives in
  the installed package, not `scripts/`: `scripts/` is dev-only git-hook tooling that is never
  part of a built wheel, so code `serve` needs at runtime cannot live there (it used to, and
  broke every real install — caught only because nothing had tried installing the package
  itself yet).
- **Every `FLEDERMAP_*` setting can also live in a TOML config file** (see `docs/setup.md`), at
  a `platformdirs` config directory by default, or wherever `FLEDERMAP_CONFIG_FILE` names.
  **Env var wins when both are set** — deliberate, since a future Docker deployment configures
  purely through env and the file exists for standalone/local use, not to override the
  container path. Naming `FLEDERMAP_CONFIG_FILE` explicitly and having it be absent is an
  error; the default location being absent is not (same "optional, regenerable" shape as
  `FLEDERMAP_STATIC_ROOT` above).
- **`scripts/` never ships in the built package** — only `src/fledermap/` does (hatchling's
  default src-layout packaging). `scripts/` is for dev-only tooling invoked from a checkout
  (git hooks: `check_commit_msg.py`, `check_yaml.py`). Before treating "run this script
  manually, it's documented" as merely a UX rough edge, check whether the script would even
  exist for someone who installed the package rather than cloned the repo — `fetch_vendor_assets.py`
  lived there and silently couldn't run for any real install until this was caught. Verify with
  `hatch build -t wheel` + `python3 -m zipfile -l dist/*.whl`, not by inspecting the source tree.

## Tooling

- `hatch run types:check` runs mypy over **`tests/` as well as `src/`**. Test code must
  type-check: bind an `X | None`, assert it is not None, then dereference. Not `# type: ignore`.
- **A new `Config.from_env` field needs a test asserting the constructed `Config`'s attribute,
  not just that parsing didn't raise.** mypy cannot catch a fully-validated local variable that
  never reaches the final `cls(...)` call — `port` was parsed, range-checked, and then silently
  dropped on the floor once, caught only because `test_port_is_configurable_via_env` asserted
  `config.port == 8080` rather than merely that `Config.from_env` didn't error.
- When mypy cannot resolve a third-party import, **add the package to the `types` env** in
  `pyproject.toml`. A global `ignore_missing_imports` has been rejected twice here — it
  blind-spots every future dependency.
- Ruff treats top-level `alembic` as **first-party** (there is an `alembic/` directory at the
  repo root), so `from alembic import command` sorts with the `fledermap` imports while
  `from alembic.autogenerate import ...` stays in the third-party block. Let `--fix` do it.
- **Test output must be pristine** — a warning is a defect. Fix the cause, never add a
  `filterwarnings` ignore. One such ignore was removed after it turned out never to have worked:
  module-scoped filters match where a warning is *raised*, not where the deprecated module is
  imported, so it suppressed nothing and would only have masked future deprecations.

## Database

- **`bats_db` is never poiidx's database.** poiidx DROPS AND RECREATES all its tables on any
  schema/filter-config mismatch. Full warning in `src/fledermap/store/db.py`.
- **Postgres treats NULLs as distinct**, so a `UniqueConstraint` over a nullable column does not
  fire at all. `uq_identification_source_claim` needs `postgresql_nulls_not_distinct=True`
  precisely because `source_version` is NULL for the sources that most need it — filename IDs
  and manual annotations.
- **A NUL byte is not a NULL value, and Postgres rejects it outright.** psycopg2 raises
  `ValueError: A string literal cannot contain NUL (0x00) characters.` client-side, before the
  query ever reaches the server, for any `text` value containing `\x00`. That is why
  `derive.sessions._detector_key` joins `(make, serial)` with `\x1f` (ASCII Unit Separator) —
  a separator that cannot occur in either field and round-trips through Postgres text fine.
  `\x00` would have crashed on every session ever created.
- **`DELETE FROM site`, never `TRUNCATE`, for a wholesale rebuild.** `TRUNCATE` does not fire
  `ON DELETE SET NULL` the way `DELETE` does: it errors on the referencing `recording.site_id`
  FK, or with `CASCADE` truncates `recording` too — which must never happen.
- **`np.float64` has no psycopg2 adapter.** Bound as a query parameter it renders via `repr()`
  as the literal text `np.float64(...)`, which Postgres reads as a schema-qualified name and
  rejects with `InvalidSchemaName`. Cast to a plain `float()` before binding. Every numpy scalar
  reaching the DB layer needs this — `GeoCluster.radius` is only the first one found.
- **SQLAlchemy's `Enum` persists the member *name*, not `.value`.** The `IdSource` / `Verdict`
  values (`emt.wamd`, not `EMT_WAMD`) are the canonical on-disk vocabulary, so both columns pass
  `values_callable`. Dropping it silently rewrites stored representation.
- `verdict` carries a CHECK constraint (closed vocabulary); `source` deliberately does not
  (`create_constraint=False`) — further classifiers are planned and a CHECK would force a
  migration per classifier.
- A `mapped_column(String(...))` annotated `Mapped[SomeEnum]` type-checks but does **not**
  round-trip: reads come back as plain `str` and `.value` raises `AttributeError`.
- **Procrastinate's schema has no upgrade path here.** `jobs/app.py`'s `ensure_schema` only ever
  *applies* the schema — it returns immediately if `procrastinate_jobs` exists and never runs
  Procrastinate's own versioned migrations. Bumping the `procrastinate` dependency (pinned
  `>=3.9,<4` for this reason) therefore means running those migrations against every existing
  database **by hand**; Alembic does not cover them.

## Migrations

`tests/test_migrations.py` runs `alembic upgrade head` against an empty schema and asserts
`compare_metadata` finds no drift. If it fails after a model change, the migration is stale —
that is the test working.

**Do not "simplify" the `_comparable` filter it contains.** Alembic's
`sqla_compat.all_table_check_constraints` drops `_type_bound` constraints from the metadata side
of the comparison, while reflection has no notion of type-bound. A non-native `Enum` with
`create_constraint=True` is therefore reported as `remove_constraint` against a **perfectly
faithful** migration. `_comparable` excludes exactly those by `(table, name)` derived from the
enum columns themselves; excluding check constraints wholesale would make the test blind to real
constraint drift.

Two tests cover what the exclusion drops, and both halves are needed:
`test_migrated_verdict_check_is_enforced` proves the CHECK *exists* (a bogus value is rejected),
and `test_migrated_verdict_check_accepts_every_verdict` proves it still *matches `Verdict`* — a
member added to the enum without a migration passes the first and the drift comparison alike.

**This coverage is per-column, not automatic — `source` needed its own test.** `Identification.source`
uses `create_constraint=False` (spec D9: further classifiers are coming, and a CHECK would force a
migration per one), so it has no CHECK for `_comparable` to exclude and `compare_metadata` sees
identical `VARCHAR(32)` DDL on both sides no matter how the two enum lists disagree. This is exactly
how `emt.manual` (added to `IdSource` in a later fix round) shipped missing from the migration's
literal list for a while, undetected — a real instance of the drift this section warns about, caught
by a whole-branch review rather than any single task's. `test_migration_idsource_literal_matches_the_model`
closes it with a static `ast`-based check of the migration's literal list against `IdSource`, needing
no database.

**The general rule:** a schema-drift test's blind spots are exactly the parts of the schema
`compare_metadata` cannot see (non-native enums without a CHECK, anything else erased to a
featureless column type). Each one needs its own explicit test; none of it is covered by default.

**Adding a CHECK to an EXISTING enum column must be hand-written.** Autogenerate compares
column *types*, and a type-bound constraint appearing on a column whose declared type did not
change is invisible to it — the migration comes out silently missing the constraint. Write
`op.create_check_constraint()` (and its `drop` in `downgrade`) by hand, then let
`tests/test_migrations.py` confirm the result. Another instance of the same blind spot as above,
reached from the other direction: there the CHECK was absent from the metadata side, here from
the migration.

Mutation-test any `compare_metadata` or excluded-constraint assertion (add a column, add an enum
member, confirm it fails). One that cannot fail is worse than no test.

## Sample data — not representative

The two bundled sample recordings are **iPhone Simulator** output: no `guan` chunk at all (only
`wamd`), and a `+02:00` metadata offset against New York coordinates. Their filename and metadata
timestamps differ by 12 h minus a few seconds, which reads as an AM/PM fault in the simulator's
writer. Do not generalise timestamp, timezone, or metadata behaviour from them.

**Real field recordings exist as of 2026-08-26** (`~/Bat Sessions/Session_20260826_173533`,
Echo Meter Touch 2, Android — 10 files, none containing an identified bat). Spec R1, R2, and R3
are all now **closed** for this device (see the spec's R1–R3 section for exact scope — one
device/OS, one export pathway). R2 needed one on-device manual reclassification to get a
before/after audio-hash pair; species content was never the blocker for any of the three. Two
real bugs
surfaced and were fixed: `ingest/riff.py`'s `iter_chunks` assumed every odd-sized chunk was
followed by the RIFF spec's pad byte, which this device's odd-sized `guan` chunk doesn't get,
desyncing everything parsed after it; and `ingest/merge.py` treated GUANO/wamd's own "No ID"/
"Noise" sentinel strings (`Species Auto ID: No ID`) as an unmapped species code instead of
recognising them the way the filename convention already did, which is why 9 of these 10 files
showed up as "unidentified species" on the map instead of "No ID". Full writeup, including a new
latent `wamd`-longitude-sign bug on this device (harmless today because GUANO's position is
already preferred), is in the design spec's R1–R3 section — see there before touching
`ingest/riff.py`, `ingest/merge.py`, or `domain/codes.py`'s `sentinel_verdict`.

## Species codes

Never hand-enter a species code. The Wildlife Acoustics list (see
@docs/references.md) is **species-level only** — no genus or group codes exist.
`MYOSPP` was invented by the plan, survived a review as "plausible", and had to be
removed. There is no official name for these code systems — do not call them
"alpha codes", which is a bird standard built differently. NABat's six-letter codes
currently coincide with the WA ones; keep them as separate sources anyway (spec
D10). A taxon may hold several codes per source: `uq_taxon_code` is
`(source, code)`, not `(source, taxon_id)`.

An unmapped label is not a failure: it resolves to `None` and lands in the review
queue by design (spec section 5). A *wrong* mapping is far worse than a missing
one, because it resolves confidently to something the detector never emits.

`taxa_eu.yaml` covers 10 of the 31 European species — a deliberate deferral Janna
owns and will pick up as its own task.

## Ingest invariants

- **Ingest is strictly read-only on the archive** (D16). No code may move, rename, write, or
  delete a source recording.
- Identity is `audio_hash` over the `fmt` ‖ `data` chunks only (D8), so re-ID — which renames
  files and rewrites metadata chunks — preserves it. Verified against real recordings.
- `recorded_at` precedence is **provisional** (D17). `filename_at`, `metadata_at`, and
  `timestamp_disagreement_s` must all survive; removing any as "unused" is a defect.

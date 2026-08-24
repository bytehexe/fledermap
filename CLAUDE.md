# Fledermap — project notes

Design authority: `docs/superpowers/specs/2026-08-23-fledermap-design.md` (decisions D1–D18,
open risks R1–R3). Phase 1 plan is in `docs/superpowers/plans/`; its execution ledger is
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

## Tooling

- `hatch run types:check` runs mypy over **`tests/` as well as `src/`**. Test code must
  type-check: bind an `X | None`, assert it is not None, then dereference. Not `# type: ignore`.
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
- **SQLAlchemy's `Enum` persists the member *name*, not `.value`.** The `IdSource` / `Verdict`
  values (`emt.wamd`, not `EMT_WAMD`) are the canonical on-disk vocabulary, so both columns pass
  `values_callable`. Dropping it silently rewrites stored representation.
- `verdict` carries a CHECK constraint (closed vocabulary); `source` deliberately does not
  (`create_constraint=False`) — further classifiers are planned and a CHECK would force a
  migration per classifier.
- A `mapped_column(String(...))` annotated `Mapped[SomeEnum]` type-checks but does **not**
  round-trip: reads come back as plain `str` and `.value` raises `AttributeError`.

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

Mutation-test any `compare_metadata` or excluded-constraint assertion (add a column, add an enum
member, confirm it fails). One that cannot fail is worse than no test.

## Sample data — not representative

The two bundled sample recordings are **iPhone Simulator** output: no `guan` chunk at all (only
`wamd`), and a `+02:00` metadata offset against New York coordinates. Their filename and metadata
timestamps differ by 12 h minus a few seconds, which reads as an AM/PM fault in the simulator's
writer. Do not generalise timestamp, timezone, or metadata behaviour from them — spec R1–R3 and
the timezone half of D17 stay open until real field recordings exist.

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

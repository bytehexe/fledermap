# Drop Identification.superseded_at Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `Identification.superseded_at` soft-delete mechanism with a shared
delete/update-in-place algorithm used by both ingest and manual classification, and simplify
`uq_identification_source_claim` back to a plain unique constraint.

**Architecture:** A new `services/identifications.py` module owns one function,
`replace_claims`, that diffs a source's desired live claim set against what's actually stored for
one `(recording, source)` and deletes/updates/inserts accordingly — no row is ever soft-deleted.
`services/ingest.py`'s `_apply_identifications` and `services/manual_classification.py`'s
`set_manual_classification` both become thin callers that build a `desired` list and hand it to
`replace_claims`. Every reader that filtered on `superseded_at IS NULL` (`current_best.py`,
`map_query.py`) drops that filter once the column is gone. The schema change (drop the column,
replace the partial index with a plain `UniqueConstraint`) lands last, after every caller and
reader has already stopped depending on soft-delete semantics.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x ORM, Alembic, pytest (`hatch test`), Postgres via
testcontainers for `db`-marked tests.

**Spec:** `docs/superpowers/specs/2026-09-06-fledermap-drop-identification-supersede-design.md`

## Global Constraints

- **Never soft-delete.** No code path may set anything resembling `superseded_at` — a claim that
  should stop being live is deleted (`session`/relationship removal), a claim whose details
  changed is updated in place. This applies to every task, not just the schema task.
- **Identity within one `(recording, source)` is `taxon_id`** (`None` = the sentinel
  NO_ID/NOISE claim) — this is `replace_claims`'s contract from Task 1 onward; every later task
  relies on it.
- **`_EMT_SOURCES` (`services/ingest.py`) never includes `IdSource.MANUAL`** — unchanged from
  today, still the reason ingest and manual classification stay separate call sites into the
  same shared helper rather than one combined code path.
- **Run `hatch test -m "not db"` after every step that touches non-DB code**, and the full
  `hatch test` (with `dangerouslyDisableSandbox: true`, Docker needed) before each task's commit
  — this project's Docker-backed `db` tests can't run sandboxed (see CLAUDE.md's "Environment
  gotchas").
- **Run `git` unsandboxed** (`dangerouslyDisableSandbox: true`) — sandboxed git config writes
  leave a stale `.git/config.lock` in this repo.
- **`hatch run types:check` must pass after every task** — this project's mypy run covers
  `tests/` too, not just `src/`.

---

### Task 1: `replace_claims` — the shared delete/update-in-place helper

**Files:**
- Create: `src/fledermap/services/identifications.py`
- Test: `tests/test_identifications_service.py`

**Interfaces:**
- Produces: `ClaimInput` (frozen dataclass: `taxon_id: int | None`, `verdict: Verdict`,
  `raw_label: str | None = None`, `source_version: str | None = None`), `ReplaceResult` (frozen
  dataclass: `added: int`, `updated: int`, `removed: int`), `replace_claims(session: OrmSession,
  recording: Recording, source: IdSource, desired: Sequence[ClaimInput], now: datetime) ->
  ReplaceResult`. Tasks 2 and 3 both import and call this.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_identifications_service.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.identifications import ClaimInput, ReplaceResult, replace_claims
from fledermap.store.models import Identification, Recording

pytestmark = pytest.mark.db


def _recording(session: OrmSession, audio_hash: str) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    session.add(r)
    session.commit()
    return r


def _claims(session: OrmSession, recording_id: int, source: IdSource) -> list[Identification]:
    return list(
        session.scalars(
            select(Identification).where(
                Identification.recording_id == recording_id,
                Identification.source == source,
            ),
        ).all(),
    )


def test_inserting_into_an_empty_set_adds_a_row(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "a" * 64)
        now = datetime(2026, 9, 6, tzinfo=UTC)

        result = replace_claims(
            session,
            recording,
            IdSource.EMT_GUANO,
            [ClaimInput(taxon_id=1, verdict=Verdict.SPECIES, raw_label="EPTSER")],
            now,
        )
        session.commit()

        assert result.added == 1
        assert result.updated == 0
        assert result.removed == 0
        claims = _claims(session, recording.id, IdSource.EMT_GUANO)
        assert len(claims) == 1
        assert claims[0].taxon_id == 1
        assert claims[0].raw_label == "EPTSER"
        assert claims[0].first_seen_at == now


def test_same_taxon_id_with_changed_fields_updates_in_place(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "b" * 64)
        first_seen = datetime(2026, 9, 1, tzinfo=UTC)
        replace_claims(
            session,
            recording,
            IdSource.EMT_GUANO,
            [ClaimInput(taxon_id=1, verdict=Verdict.SPECIES, raw_label="EPTSER", source_version="1.0")],
            first_seen,
        )
        session.commit()
        original_id = _claims(session, recording.id, IdSource.EMT_GUANO)[0].id

        result = replace_claims(
            session,
            recording,
            IdSource.EMT_GUANO,
            [ClaimInput(taxon_id=1, verdict=Verdict.SPECIES, raw_label="EPTSER", source_version="2.0")],
            datetime(2026, 9, 6, tzinfo=UTC),
        )
        session.commit()

        assert result.added == 0
        assert result.updated == 1
        assert result.removed == 0
        claims = _claims(session, recording.id, IdSource.EMT_GUANO)
        assert len(claims) == 1
        assert claims[0].id == original_id  # same row, not delete+reinsert
        assert claims[0].source_version == "2.0"
        assert claims[0].first_seen_at == first_seen  # untouched by the update


def test_identical_resubmission_is_a_true_no_op(engine: Engine) -> None:
    """No field differs -- must not even count as `updated`, matching this
    project's ingest idempotency principle (services/ingest.py's module
    docstring)."""
    with OrmSession(engine) as session:
        recording = _recording(session, "c" * 64)
        claim = ClaimInput(taxon_id=1, verdict=Verdict.SPECIES, raw_label="EPTSER")
        replace_claims(session, recording, IdSource.EMT_GUANO, [claim], datetime(2026, 9, 1, tzinfo=UTC))
        session.commit()

        result = replace_claims(session, recording, IdSource.EMT_GUANO, [claim], datetime(2026, 9, 6, tzinfo=UTC))
        session.commit()

        assert result == ReplaceResult(added=0, updated=0, removed=0)


def test_a_taxon_id_missing_from_desired_is_deleted_outright(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "d" * 64)
        replace_claims(
            session,
            recording,
            IdSource.EMT_GUANO,
            [ClaimInput(taxon_id=1, verdict=Verdict.SPECIES, raw_label="EPTSER")],
            datetime(2026, 9, 1, tzinfo=UTC),
        )
        session.commit()

        result = replace_claims(session, recording, IdSource.EMT_GUANO, [], datetime(2026, 9, 6, tzinfo=UTC))
        session.commit()

        assert result.added == 0
        assert result.updated == 0
        assert result.removed == 1
        assert _claims(session, recording.id, IdSource.EMT_GUANO) == []
        # The row is gone from the table entirely -- not soft-marked.
        assert session.scalars(select(Identification)).all() == []


def test_a_different_taxon_id_deletes_the_old_and_adds_the_new(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = _recording(session, "e" * 64)
        replace_claims(
            session,
            recording,
            IdSource.EMT_GUANO,
            [ClaimInput(taxon_id=1, verdict=Verdict.SPECIES, raw_label="MYODAU")],
            datetime(2026, 9, 1, tzinfo=UTC),
        )
        session.commit()

        result = replace_claims(
            session,
            recording,
            IdSource.EMT_GUANO,
            [ClaimInput(taxon_id=2, verdict=Verdict.SPECIES, raw_label="EPTSER")],
            datetime(2026, 9, 6, tzinfo=UTC),
        )
        session.commit()

        assert result.added == 1
        assert result.updated == 0
        assert result.removed == 1
        claims = _claims(session, recording.id, IdSource.EMT_GUANO)
        assert len(claims) == 1
        assert claims[0].taxon_id == 2
        assert claims[0].raw_label == "EPTSER"


def test_multiple_taxon_ids_in_desired_all_get_inserted(engine: Engine) -> None:
    """The MANUAL multi-species case: a source can claim more than one
    taxon_id at once."""
    with OrmSession(engine) as session:
        recording = _recording(session, "f" * 64)

        result = replace_claims(
            session,
            recording,
            IdSource.MANUAL,
            [
                ClaimInput(taxon_id=1, verdict=Verdict.SPECIES),
                ClaimInput(taxon_id=2, verdict=Verdict.SPECIES),
            ],
            datetime(2026, 9, 6, tzinfo=UTC),
        )
        session.commit()

        assert result.added == 2
        claims = _claims(session, recording.id, IdSource.MANUAL)
        assert {c.taxon_id for c in claims} == {1, 2}


def test_only_the_named_source_is_touched(engine: Engine) -> None:
    """A claim from a different source on the same recording must survive
    untouched."""
    with OrmSession(engine) as session:
        recording = _recording(session, "g" * 64)
        replace_claims(
            session,
            recording,
            IdSource.EMT_FILENAME,
            [ClaimInput(taxon_id=1, verdict=Verdict.SPECIES, raw_label="EPTSER")],
            datetime(2026, 9, 1, tzinfo=UTC),
        )
        session.commit()

        replace_claims(session, recording, IdSource.EMT_GUANO, [], datetime(2026, 9, 6, tzinfo=UTC))
        session.commit()

        assert len(_claims(session, recording.id, IdSource.EMT_FILENAME)) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch test tests/test_identifications_service.py -v` (`dangerouslyDisableSandbox: true` —
`db`-marked, needs Docker)
Expected: FAIL/ERROR — `fledermap.services.identifications` does not exist yet.

- [ ] **Step 3: Write the implementation**

```python
# src/fledermap/services/identifications.py
"""One shared algorithm for keeping a recording's claim set from one source in
sync with what that source currently asserts -- used by both
services/ingest.py's _apply_identifications (automatic/EMT sources) and
services/manual_classification.py's set_manual_classification (IdSource.MANUAL).

No row is ever soft-deleted. A claim identity within one (recording, source)
is its taxon_id (None meaning the NO_ID/NOISE sentinel claim); replace_claims
deletes a live row whose taxon_id the caller no longer wants, updates a live
row's other fields in place when its taxon_id is still wanted but something
about it changed, and inserts a brand new row for a taxon_id with no existing
live claim. See docs/superpowers/specs/2026-09-06-fledermap-drop-
identification-supersede-design.md, Design section 2, for why identity is
taxon_id rather than the old (source, source_version, raw_label, taxon_id)
tuple."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording


@dataclass(frozen=True)
class ClaimInput:
    """What one desired live claim looks like, before it's compared against
    what's actually stored."""

    taxon_id: int | None
    verdict: Verdict
    raw_label: str | None = None
    source_version: str | None = None


@dataclass(frozen=True)
class ReplaceResult:
    added: int
    updated: int
    removed: int


def replace_claims(
    session: OrmSession,
    recording: Recording,
    source: IdSource,
    desired: Sequence[ClaimInput],
    now: datetime,
) -> ReplaceResult:
    """Bring `recording`'s live claims from `source` in line with `desired`."""
    existing_by_taxon = {
        i.taxon_id: i for i in recording.identifications if i.source == source
    }
    desired_by_taxon = {c.taxon_id: c for c in desired}

    added = updated = removed = 0

    for taxon_id, ident in list(existing_by_taxon.items()):
        if taxon_id not in desired_by_taxon:
            recording.identifications.remove(ident)  # cascade="all, delete-orphan"
            removed += 1

    for taxon_id, claim in desired_by_taxon.items():
        ident = existing_by_taxon.get(taxon_id)
        if ident is None:
            recording.identifications.append(
                Identification(
                    source=source,
                    verdict=claim.verdict,
                    taxon_id=claim.taxon_id,
                    raw_label=claim.raw_label,
                    source_version=claim.source_version,
                    first_seen_at=now,
                ),
            )
            added += 1
            continue
        if (
            ident.verdict != claim.verdict
            or ident.raw_label != claim.raw_label
            or ident.source_version != claim.source_version
        ):
            ident.verdict = claim.verdict
            ident.raw_label = claim.raw_label
            ident.source_version = claim.source_version
            updated += 1

    return ReplaceResult(added=added, updated=updated, removed=removed)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch test tests/test_identifications_service.py -v` (`dangerouslyDisableSandbox: true`)
Expected: PASS, all 7 tests.

- [ ] **Step 5: Type-check**

Run: `hatch run types:check`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/identifications.py tests/test_identifications_service.py
git commit -m "feat: add replace_claims, the shared delete/update-in-place claim helper

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FgqEf89xFgRsPq8wQrH1Eh"
```

---

### Task 2: Switch `_apply_identifications` to `replace_claims`

**Files:**
- Modify: `src/fledermap/services/ingest.py` (the `IngestReport` dataclass, and
  `_apply_identifications`)
- Modify: `src/fledermap/cli/main.py` (the `identifications added/superseded` report line)
- Modify: `tests/test_ingest_service.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `ClaimInput`, `replace_claims` from Task 1 (`fledermap.services.identifications`).
- Produces: `IngestReport.identifications_updated` and `IngestReport.identifications_removed`
  (replacing `identifications_superseded`) — later tasks and any other `IngestReport` reader use
  these names.

- [ ] **Step 1: Update `IngestReport` and rewrite `_apply_identifications`**

In `src/fledermap/services/ingest.py`, replace the `identifications_superseded` field:

```python
    identifications_added: int = 0
    identifications_updated: int = 0
    identifications_removed: int = 0
```

Add the import (near the existing `from fledermap.store.seed import resolve_code` line):

```python
from fledermap.services.identifications import ClaimInput, replace_claims
```

Replace the whole `_apply_identifications` function body with:

```python
def _apply_identifications(
    session: OrmSession,
    recording: Recording,
    parsed: tuple[ParsedIdentification, ...],
    report: IngestReport,
    now: datetime,
) -> bool:
    """Bring every EMT source's claim set (0 or 1 claims each, per source) in
    line with what this scan parsed, via replace_claims.

    Iterates ALL of `_EMT_SOURCES`, not just sources present in `parsed`: a
    source that previously had a claim but has nothing in `parsed` this scan
    (e.g. an on-device manual correction cleared, so EMT_MANUAL no longer
    appears at all) must still have its row deleted, not left behind.

    Resolution (`resolve_code`) only runs when a source's `raw_label` text
    actually changed from what's on file (or there's no existing row yet) --
    matching `reresolve_unmapped_identifications`'s documented contract that
    `_apply_identifications` resolves a claim once, not on every scan. An
    unchanged `raw_label` keeps whatever `taxon_id` the existing row already
    has (resolved or still `None`); only `reresolve_unmapped_identifications`
    retries resolution for an unchanged label.
    """
    parsed_by_source = {p.source: p for p in parsed}
    existing_by_source = {
        i.source: i for i in recording.identifications if i.source in _EMT_SOURCES
    }
    changed = False

    for source in _EMT_SOURCES:
        p = parsed_by_source.get(source)
        existing = existing_by_source.get(source)
        desired: list[ClaimInput] = []
        if p is not None:
            if existing is not None and existing.raw_label == p.raw_label:
                taxon_id = existing.taxon_id
            else:
                taxon = None
                if p.raw_label:
                    taxon = resolve_code(session, _code_source(p.source), p.raw_label)
                    if taxon is None:
                        report.unmapped_labels.add(p.raw_label)
                taxon_id = taxon.id if taxon else None
            desired.append(
                ClaimInput(
                    taxon_id=taxon_id,
                    verdict=p.verdict,
                    raw_label=p.raw_label,
                    source_version=p.source_version,
                ),
            )
        result = replace_claims(session, recording, source, desired, now)
        report.identifications_added += result.added
        report.identifications_updated += result.updated
        report.identifications_removed += result.removed
        if result.added or result.updated or result.removed:
            changed = True

    return changed
```

- [ ] **Step 2: Update the CLI's report line**

In `src/fledermap/cli/main.py`, replace:

```python
        click.echo(
            f"identifications added {report.identifications_added}  "
            f"superseded {report.identifications_superseded}",
        )
```

with:

```python
        click.echo(
            f"identifications added {report.identifications_added}  "
            f"updated {report.identifications_updated}  "
            f"removed {report.identifications_removed}",
        )
```

- [ ] **Step 3: Update `tests/test_cli.py`**

In `test_ingest_reports_created_recordings`, replace:

```python
    assert "identifications added 4" in result.output
    assert "superseded 0" in result.output
```

with:

```python
    assert "identifications added 4" in result.output
    assert "updated 0" in result.output
    assert "removed 0" in result.output
```

- [ ] **Step 4: Rewrite the affected tests in `tests/test_ingest_service.py`**

Replace `test_moved_and_reidentified_reports_as_moved` (this test does not call
`seed_taxonomy`, so both "NoID" and "EPTSER" stay unmapped — same `taxon_id=None` identity,
so this becomes an in-place update, not a delete+insert):

```python
def test_moved_and_reidentified_reports_as_moved(engine: Engine) -> None:
    """Deliberate: a file that both moved AND changed its identification (the
    re-ID case) is reported as MOVED, not UPDATED — spec section 6 defines the
    outcome by (hash, path) status ('known hash, new path'), not by whether
    metadata happens to also differ. See task-11 report, judgement call on
    'MOVED masks UPDATED'. The underlying claim is updated in place: neither
    "NoID" nor "EPTSER" resolves to a taxon here (no seed_taxonomy call), so
    both share taxon_id=None -- same identity, same row."""
    with OrmSession(engine) as session:
        commit_scan(
            session,
            [(_scanned(name="NoID_20150610_215446.wav", label="NoID"), 0)],
            archive_roots=(ROOT,),
        )
        session.commit()

        report = commit_scan(
            session,
            [(_scanned(name="EPTSER_20150610_215446.wav", label="EPTSER"), 0)],
            archive_roots=(ROOT,),
        )
        session.commit()

        assert report.moved == 1
        assert report.updated == 0
        # The orthogonal counters (task-11 fix round 1, priority 5) are what
        # give this exact case visibility: MOVED alone tells the operator
        # nothing about the identification change happening underneath it.
        assert report.identifications_added == 0
        assert report.identifications_updated == 1
        assert report.identifications_removed == 0
        ids = session.scalars(select(Identification)).all()
        assert len(ids) == 1
        assert ids[0].raw_label == "EPTSER"
```

Replace `test_changed_identification_supersedes_the_old_one` (this one DOES call
`seed_taxonomy`, so "MYODAU" and "EPTSER" resolve to two different real taxa — genuinely
different identity, so this is a delete+insert):

```python
def test_changed_identification_to_a_different_taxon_replaces_the_old_row(
    engine: Engine,
) -> None:
    """The EMT changing its mind to a different species deletes the old row
    and inserts a new one -- no history kept."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [(_scanned(label="MYODAU"), 0)], archive_roots=(ROOT,))
        session.commit()

        report = commit_scan(session, [(_scanned(label="EPTSER"), 0)], archive_roots=(ROOT,))
        session.commit()

        assert report.identifications_added == 1
        assert report.identifications_removed == 1
        ids = session.scalars(select(Identification)).all()
        assert len(ids) == 1
        assert ids[0].raw_label == "EPTSER"
```

Replace `test_emt_manual_identification_is_superseded_on_rescan` (also seeds taxonomy, also a
genuine delete+insert):

```python
def test_emt_manual_identification_changing_replaces_the_old_row(engine: Engine) -> None:
    """The operator changing the on-device manual ID must replace the old
    claim outright, not leave two contradictory active manual identifications
    (task-11 fix round 1, priority 4). Goes through the real `merge_metadata`,
    not a hand-built `ParsedIdentification`, so it exercises the actual source
    this defect was about.

    Before the original fix: `IdSource.MANUAL` (excluded from `_EMT_SOURCES`)
    meant the second scan added EPTSER without ever touching MYODAU — two
    active claims. `IdSource.EMT_MANUAL` is in `_EMT_SOURCES`, so the rescan
    replaces it correctly.
    """

    def _scanned_with_manual_id(manual_id: str) -> ScannedFile:
        metadata = merge_metadata(
            guano=None,
            wamd=parse_wamd(wamd_payload(auto_id=None, manual_id=manual_id)),
            filename=None,
        )
        return ScannedFile(audio_hash="e" * 64, path=ROOT / "a.wav", metadata=metadata)

    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(
            session, [(_scanned_with_manual_id("MYODAU"), 0)], archive_roots=(ROOT,)
        )
        session.commit()

        commit_scan(
            session, [(_scanned_with_manual_id("EPTSER"), 0)], archive_roots=(ROOT,)
        )
        session.commit()

        ids = session.scalars(select(Identification)).all()
        assert len(ids) == 1
        assert ids[0].raw_label == "EPTSER"
        assert ids[0].source is IdSource.EMT_MANUAL
```

- [ ] **Step 5: Run the tests to verify they fail, then pass**

Run: `hatch test tests/test_ingest_service.py tests/test_cli.py -v`
(`dangerouslyDisableSandbox: true`)
Expected: first run (before Step 1's implementation edit) shows the old behavior's assertions
failing against the new code; after Steps 1-4 are all in place, run again and expect PASS across
every test in both files, including the three rewritten ones and the unrelated pre-existing ones
(`test_unmapped_label_is_stored_and_reported`, `test_emt_sources_stays_exactly_the_emt_prefixed_id_sources`,
etc. — these must be unaffected).

- [ ] **Step 6: Type-check**

Run: `hatch run types:check`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/fledermap/services/ingest.py src/fledermap/cli/main.py \
        tests/test_ingest_service.py tests/test_cli.py
git commit -m "refactor: ingest uses replace_claims instead of soft-superseding

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FgqEf89xFgRsPq8wQrH1Eh"
```

---

### Task 3: Switch `set_manual_classification` to `replace_claims`

**Files:**
- Modify: `src/fledermap/services/manual_classification.py`
- Modify: `tests/test_manual_classification.py`

**Interfaces:**
- Consumes: `ClaimInput`, `replace_claims` from Task 1.

- [ ] **Step 1: Rewrite `set_manual_classification` and `current_manual_state`**

Replace the whole body of `src/fledermap/services/manual_classification.py` from the imports
down:

```python
"""Writes IdSource.MANUAL identification rows from the recording-details
page's classifier box (design spec 2026-09-05-fledermap-manual-classification-
design.md, §4). The only place in the codebase that writes IdSource.MANUAL --
every other source is written by services/ingest.py's commit_scan during a
scan, never here."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.identifications import ClaimInput, replace_claims
from fledermap.store.models import Recording


def set_manual_classification(
    session: OrmSession,
    recording: Recording,
    *,
    verdict: Verdict | None,
    taxon_ids: Sequence[int] = (),
) -> None:
    """Replaces every standing MANUAL claim with exactly what the new state
    calls for -- the classifier box always submits its full current state
    rather than a diff, and replace_claims (services/identifications.py) is
    the safe way to apply that: no history kept, so an edited-many-times
    claim never accumulates dead rows.

    `verdict=None` means "clear to no manual opinion" -- a real, explicit
    input, not an implicit consequence of SPECIES with an empty taxon_ids
    (that combination raises instead, so a client bug sending an empty list
    by accident fails loudly rather than silently clearing a classification).
    """
    if verdict == Verdict.SPECIES and not taxon_ids:
        msg = "verdict=SPECIES requires at least one taxon_id"
        raise ValueError(msg)
    if verdict != Verdict.SPECIES and taxon_ids:
        msg = "taxon_ids is only meaningful for verdict=SPECIES"
        raise ValueError(msg)

    now = datetime.now(UTC)
    desired: list[ClaimInput] = []
    if verdict in (Verdict.NO_ID, Verdict.NOISE):
        desired.append(ClaimInput(taxon_id=None, verdict=verdict))
    elif verdict == Verdict.SPECIES:
        desired.extend(
            ClaimInput(taxon_id=taxon_id, verdict=Verdict.SPECIES) for taxon_id in taxon_ids
        )
    # verdict is None: "clear" -- desired stays empty, replace_claims removes
    # every standing MANUAL row outright.

    replace_claims(session, recording, IdSource.MANUAL, desired, now)
    session.commit()


def current_manual_state(recording: Recording) -> tuple[Verdict | None, frozenset[int]]:
    """The classifier box's own displayed/edited state -- deliberately
    independent of `current_best_identification`'s cross-source precedence
    walk (Task 5 review finding, 2026-09-05). The box must reflect and edit
    ONLY the recording's own standing MANUAL claims:

    - If it read `best` instead, an automatic classifier's winning SPECIES
      claim would render as editable manual chips -- adding one more chip
      would then resubmit the full chip set as new MANUAL claims, silently
      converting an automatic identification into a manual one the user
      never asked to make, and "Clear" would look broken (the chips just
      come back from the still-there automatic claim).
    - It would also let an always-active AUTOMATIC NOISE/NO_ID claim
      disable the search input with no way to enter a correction --
      defeating the core use case of overriding a wrong automatic call.

    Returns `(None, frozenset())` when there is no standing manual claim at
    all -- the same "no manual opinion" state `set_manual_classification`
    produces for `verdict=None`.
    """
    manual_claims = [i for i in recording.identifications if i.source == IdSource.MANUAL]
    manual_verdict = manual_claims[0].verdict if manual_claims else None
    manual_taxon_ids = frozenset(
        i.taxon_id for i in manual_claims if i.taxon_id is not None
    )
    return manual_verdict, manual_taxon_ids
```

- [ ] **Step 2: Update `tests/test_manual_classification.py`**

Drop the `superseded_at` filter from the `_manual_claims` helper:

```python
def _manual_claims(session: OrmSession, recording_id: int) -> list[Identification]:
    return list(
        session.scalars(
            select(Identification).where(
                Identification.recording_id == recording_id,
                Identification.source == IdSource.MANUAL,
            ),
        ).all(),
    )
```

Rename three tests for accuracy (bodies unchanged — the observable behavior, exact row counts
included, is identical either way; only what actually happens under the hood changed):
`test_setting_no_id_supersedes_a_prior_species_claim` → `test_setting_no_id_replaces_a_prior_species_claim`;
`test_setting_a_species_claim_supersedes_a_prior_no_id` → `test_setting_a_species_claim_replaces_a_prior_no_id`;
`test_clearing_supersedes_every_standing_manual_claim` → `test_clearing_removes_every_standing_manual_claim`.

Update `test_two_manual_species_rows_insert_cleanly_under_the_unique_constraint`'s docstring
(the constraint shape it describes changes in Task 7 — this docstring should describe the
current, accurate shape once Task 7 lands; for now, past Task 3 but before Task 7, just drop
the now-stale claim that it's specifically about superseded-row reuse):

```python
def test_two_manual_species_rows_insert_cleanly_under_the_unique_constraint(
    engine: Engine,
) -> None:
    """Two MANUAL SPECIES rows differing only in taxon_id must coexist (a
    genuine multi-species file) -- taxon_id is part of
    uq_identification_source_claim precisely so this doesn't collide."""
    with OrmSession(engine) as session:
        taxon_a = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        taxon_b = Taxon(rank="genus", scientific_name="Myotis")
        session.add_all([taxon_a, taxon_b])
        session.flush()
        recording = _recording(session, "i" * 64)

        set_manual_classification(
            session,
            recording,
            verdict=Verdict.SPECIES,
            taxon_ids=[taxon_a.id, taxon_b.id],
        )
        session.commit()  # would raise IntegrityError if the constraint collided

        assert len(_manual_claims(session, recording.id)) == 2
```

- [ ] **Step 3: Run the tests**

Run: `hatch test tests/test_manual_classification.py -v` (`dangerouslyDisableSandbox: true`)
Expected: PASS, every test (renamed ones included).

- [ ] **Step 4: Type-check**

Run: `hatch run types:check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/services/manual_classification.py tests/test_manual_classification.py
git commit -m "refactor: manual classification uses replace_claims instead of supersede

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FgqEf89xFgRsPq8wQrH1Eh"
```

---

### Task 4: Simplify `current_best.py` — drop the `superseded_at` filter and the dead dedup branch

**Files:**
- Modify: `src/fledermap/services/current_best.py`
- Modify: `tests/test_current_best.py`
- Modify: `tests/test_derive_sites.py`

**Interfaces:**
- Produces: `IdentificationStatus = Literal["current", "passive", "shadowed"]` (three members,
  down from four) — any other reader of `identification_status`'s return value must not expect
  `"superseded"` any more (checked: only `_identifications_box.html` reads it, generically, via
  `class="identification-{{ status }}"` — no template change needed).

- [ ] **Step 1: Edit `current_best_identification` and `identification_status`**

In `src/fledermap/services/current_best.py`, replace:

```python
    candidates = [i for i in recording.identifications if i.superseded_at is None]
    for source in _PRECEDENCE:
        matches = [i for i in candidates if i.source == source]
        if not matches:
            continue
        if source != IdSource.MANUAL and all(
            m.verdict == Verdict.NO_ID for m in matches
        ):
            continue
        if source != IdSource.MANUAL:
            matches = [max(matches, key=lambda i: i.first_seen_at or _EPOCH)]
        return CurrentIdentification.from_matches(matches)
    return None
```

with:

```python
    for source in _PRECEDENCE:
        matches = [i for i in recording.identifications if i.source == source]
        if not matches:
            continue
        if source != IdSource.MANUAL and all(
            m.verdict == Verdict.NO_ID for m in matches
        ):
            continue
        return CurrentIdentification.from_matches(matches)
    return None
```

(The `if source != IdSource.MANUAL: matches = [max(...)]` dedup step is deleted outright, not
merely left dead: `replace_claims` guarantees at most one live row per non-MANUAL source, so
`matches` can never have more than one entry there any more. `_EPOCH` becomes unused by this
function — check whether anything else in the file still uses it before removing the constant;
if nothing does, delete it too.)

Update the function's docstring to drop the now-inaccurate "A non-MANUAL source having two
non-superseded claims at once is rare" paragraph — replace with:

```python
    """Walk sources in precedence order. A source's claims are skipped
    (fall through to the next source) only when they are ALL automatic
    NO_ID -- any SPECIES, NOISE, or MANUAL claim of any verdict wins
    outright and stops the walk.

    Only MANUAL may surface more than one claim (a genuine multi-species
    file) -- every other source has at most one live row per recording by
    construction (services/identifications.py's replace_claims keeps it that
    way), so there is nothing to dedupe here any more."""
```

In `identification_status`, delete the `superseded` branch and its check:

```python
    if best is not None and ident in best.claims:
        return "current"
    if ident.source != IdSource.MANUAL and ident.verdict == Verdict.NO_ID:
        return "passive"
    return "shadowed"
```

Update its docstring: delete the `"superseded": ...` bullet from the `IdentificationStatus`
enumeration in the docstring, and change:

```python
IdentificationStatus = Literal["current", "passive", "shadowed", "superseded"]
```

to:

```python
IdentificationStatus = Literal["current", "passive", "shadowed"]
```

- [ ] **Step 2: Update `tests/test_current_best.py`**

Delete `test_superseded_identifications_are_ignored`, `test_all_superseded_returns_none`,
`test_two_non_superseded_claims_from_the_same_source_break_on_recency`, and
`test_identification_status_superseded_regardless_of_precedence` entirely — each tests a
scenario (a superseded row existing, or two live claims from one non-MANUAL source) that can no
longer occur.

In the `_ident` helper, delete the `superseded` parameter and the `superseded_at=...` line:

```python
def _ident(
    source: IdSource,
    *,
    taxon_id: int | None = 1,
    verdict: Verdict = Verdict.SPECIES,
    first_seen_at: datetime = datetime(2026, 8, 25, tzinfo=UTC),
) -> Identification:
    return Identification(
        source=source,
        verdict=verdict,
        taxon_id=taxon_id,
        first_seen_at=first_seen_at,
    )
```

- [ ] **Step 3: Delete the dead test in `tests/test_derive_sites.py`**

Delete `test_superseded_species_identification_does_not_count` entirely — a superseded claim
can no longer exist to construct.

- [ ] **Step 4: Run the tests**

Run: `hatch test tests/test_current_best.py tests/test_derive_sites.py -v`
(`dangerouslyDisableSandbox: true`)
Expected: PASS, every remaining test.

- [ ] **Step 5: Type-check**

Run: `hatch run types:check`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fledermap/services/current_best.py tests/test_current_best.py tests/test_derive_sites.py
git commit -m "refactor: current_best.py drops the superseded_at filter and dead dedup branch

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FgqEf89xFgRsPq8wQrH1Eh"
```

---

### Task 5: Drop the three `superseded_at` filters in `map_query.py`

**Files:**
- Modify: `src/fledermap/services/map_query.py`
- Modify: `tests/test_map_query.py`

- [ ] **Step 1: Edit `filtered_recordings`, `list_taxa`, `has_unmapped_species`**

In `filtered_recordings` (`src/fledermap/services/map_query.py`), replace:

```python
    if source is not None:
        stmt = stmt.where(
            Recording.identifications.any(
                (Identification.source == source)
                & (Identification.superseded_at.is_(None)),
            ),
        )
```

with:

```python
    if source is not None:
        stmt = stmt.where(
            Recording.identifications.any(Identification.source == source),
        )
```

In `list_taxa`, replace:

```python
            Taxon.id.in_(
                select(Identification.taxon_id).where(
                    Identification.taxon_id.is_not(None),
                    Identification.superseded_at.is_(None),
                ),
            ),
```

with:

```python
            Taxon.id.in_(
                select(Identification.taxon_id).where(
                    Identification.taxon_id.is_not(None),
                ),
            ),
```

Also update `list_taxa`'s docstring: drop "Restricted to taxa referenced by at least one
non-superseded Identification" → "Restricted to taxa referenced by at least one Identification".

In `has_unmapped_species`, replace:

```python
    stmt = select(Identification.id).where(
        Identification.taxon_id.is_(None),
        Identification.verdict == Verdict.SPECIES,
        Identification.superseded_at.is_(None),
    )
```

with:

```python
    stmt = select(Identification.id).where(
        Identification.taxon_id.is_(None),
        Identification.verdict == Verdict.SPECIES,
    )
```

- [ ] **Step 2: Update `tests/test_map_query.py`**

Rename `test_source_filters_by_a_non_superseded_identification_from_that_source` →
`test_source_filters_recordings_by_that_source_identification` (body unchanged — it never
constructed a superseded row, only its name referenced the concept).

Delete `test_list_taxa_excludes_a_taxon_whose_only_identification_is_superseded` and
`test_has_unmapped_species_ignores_a_superseded_claim` entirely — each constructs an
`Identification(..., superseded_at=...)`, which no longer exists as a field; the scenario they
tested (a superseded claim being excluded) can't occur any more since a superseded claim can't
exist.

- [ ] **Step 3: Run the tests**

Run: `hatch test tests/test_map_query.py -v` (`dangerouslyDisableSandbox: true`)
Expected: PASS, every remaining test.

- [ ] **Step 4: Type-check**

Run: `hatch run types:check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/services/map_query.py tests/test_map_query.py
git commit -m "refactor: map_query.py drops its three superseded_at filters

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FgqEf89xFgRsPq8wQrH1Eh"
```

---

### Task 6: Remove the dead `superseded` template branch and CSS

**Files:**
- Modify: `src/fledermap/web/templates/_recording_panel.html`
- Modify: `src/fledermap/web/static/app.css`
- Modify: `tests/test_map_view.py`

- [ ] **Step 1: Edit the template**

In `src/fledermap/web/templates/_recording_panel.html`, replace:

```html
      <li{% if ident.superseded_at %} class="superseded"{% endif %}>
```

with:

```html
      <li>
```

- [ ] **Step 2: Remove the dead CSS rule**

In `src/fledermap/web/static/app.css`, delete the rule (and its now-inaccurate comment) that
currently reads:

```css
.superseded, .identification-superseded { text-decoration: line-through; opacity: 0.6; }
```

(Check the comment immediately above it — it currently explains why the two selectors are
combined; delete that comment along with the rule, don't leave it describing a rule that's
gone.)

- [ ] **Step 3: Rewrite `test_recording_panel_renders_identification_list_content`**

In `tests/test_map_view.py`, replace the test body (drop the second, superseded `Identification`
entirely, and assert the dead CSS class never appears):

```python
def test_recording_panel_renders_identification_list_content(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="e" * 64,
            path="e.wav",
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
        )
        session.add(recording)
        session.flush()
        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.EMT_GUANO,
                source_version=None,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                raw_label="EPTSER",
                first_seen_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'e' * 64}/panel")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "emt.guano" in html
    assert "EPTSER" in html
    assert 'class="superseded"' not in html
```

- [ ] **Step 4: Run the tests**

Run: `hatch test tests/test_map_view.py -v` (`dangerouslyDisableSandbox: true`)
Expected: PASS, every test in the file.

- [ ] **Step 5: Commit**

```bash
git add src/fledermap/web/templates/_recording_panel.html src/fledermap/web/static/app.css \
        tests/test_map_view.py
git commit -m "refactor: remove the dead superseded template branch and CSS rule

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FgqEf89xFgRsPq8wQrH1Eh"
```

---

### Task 7: Schema — drop `superseded_at`, plain `UniqueConstraint`, migration

**Files:**
- Modify: `src/fledermap/store/models.py` (`Identification`)
- Create: `src/fledermap/alembic/versions/<alembic-generated>_drop_identification_superseded_at.py`
- Modify: `tests/test_models.py`
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Produces: `Identification` with no `superseded_at` column and a plain
  `UniqueConstraint("recording_id", "source", "taxon_id", name="uq_identification_source_claim",
  postgresql_nulls_not_distinct=True)`.

- [ ] **Step 1: Edit `Identification` in `src/fledermap/store/models.py`**

Replace the `__table_args__` tuple's `Index(...)` entry and its long explanatory comment with:

```python
    __tablename__ = "identification"
    __table_args__ = (
        # Plain constraint, not a partial index: with replace_claims
        # (services/identifications.py) never soft-deleting, there is no
        # "superseded, still occupying the key" row left to collide with --
        # the collision this constraint's predecessor (a partial unique
        # INDEX scoped to WHERE superseded_at IS NULL, see migration
        # 300b54c8829a) existed to work around cannot happen any more. See
        # docs/superpowers/specs/2026-09-06-fledermap-drop-identification-
        # supersede-design.md.
        UniqueConstraint(
            "recording_id",
            "source",
            "taxon_id",
            name="uq_identification_source_claim",
            # Postgres treats NULLs as distinct by default -- without this,
            # the NO_ID/NOISE sentinel claim (taxon_id IS NULL) could have
            # unlimited duplicate live rows per (recording, source).
            postgresql_nulls_not_distinct=True,
        ),
    )
```

Delete the `superseded_at` column:

```python
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

Update the class docstring (`"""One source's claim. Sources coexist; superseded_at records
changes of mind."""`) to:

```python
    """One source's claim. Sources coexist; a claim a source no longer makes
    is deleted, not kept around (services/identifications.py's
    replace_claims)."""
```

Remove `Index` and `text` from the `sqlalchemy` import line at the top of `models.py` — confirmed
(grep the file) that the old `Index(...)`/`text("superseded_at IS NULL")` construct just deleted
was their only use anywhere in this file. `UniqueConstraint` must already be imported there too
(other tables' own constraints already use it) — don't add a duplicate.

- [ ] **Step 2: Rewrite `tests/test_models.py`**

Delete `test_reinserting_a_superseded_claims_exact_key_tuple_succeeds` entirely — its scenario
(a soft-superseded row blocking reinsertion) can't happen once nothing soft-supersedes.

`test_two_live_claims_with_the_same_key_tuple_still_collide` and
`test_duplicate_manual_no_id_claims_are_rejected` need no code changes — neither references
`superseded_at`, and both already exercise exactly what the new plain constraint must still
enforce (two rows sharing `(recording_id, source, taxon_id)` collide). Leave them as-is.

- [ ] **Step 3: Run `tests/test_models.py`**

Run: `hatch test tests/test_models.py -v` (`dangerouslyDisableSandbox: true`)
Expected: PASS. This runs against `Base.metadata.create_all`, not the migration, so it proves
the *model's* constraint is correct independent of the migration written next.

- [ ] **Step 4: Find the current alembic head**

Run: `grep -L "" $(grep -rl "down_revision" src/fledermap/alembic/versions/*.py)` is overkill —
simpler: `for f in src/fledermap/alembic/versions/*.py; do rev=$(grep -oP 'revision: str = "\K[a-f0-9]+' "$f"); grep -rq "down_revision.*$rev" src/fledermap/alembic/versions/*.py || echo "$f is head ($rev)"; done`
Expected output: exactly one file, naming its own revision id as head. As of this plan's
writing that's `300b54c8829a` (`src/fledermap/alembic/versions/300b54c8829a_replace_uq_
identification_source_claim_.py`) — verify it's still true, don't assume, in case another
migration landed on `main` since.

- [ ] **Step 5: Generate and write the migration**

Run: `hatch run alembic revision -m "drop identification superseded_at, plain unique constraint"`
(real command — produces a real revision ID and timestamp; do not hand-type them).

Fill in the generated file's `upgrade`/`downgrade`:

```python
"""drop identification superseded_at, plain unique constraint

Revision ID: <alembic-generated>
Revises: <actual current head from Step 4>
Create Date: <alembic-generated>

Replaces the partial unique index from migration 300b54c8829a with a plain
UniqueConstraint now that services/identifications.py's replace_claims never
soft-deletes -- see docs/superpowers/specs/2026-09-06-fledermap-drop-
identification-supersede-design.md.

Deletes every row where superseded_at IS NOT NULL before dropping the column
-- left in place, those rows would become indistinguishable from live claims
the instant nothing can filter on superseded_at any more.

BACKUP FIRST: run scripts/db-backup.sh against the real database before
applying this migration there (`alembic upgrade head`) -- this migration
deletes data. This is a deploy-time step, not part of this project's
automated test suite (which only ever runs migrations against a disposable
testcontainer).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "<alembic-generated>"
down_revision: str | Sequence[str] | None = "<actual current head from Step 4>"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("DELETE FROM identification WHERE superseded_at IS NOT NULL")
    op.drop_index("uq_identification_source_claim", table_name="identification")
    op.drop_column("identification", "superseded_at")
    op.create_unique_constraint(
        "uq_identification_source_claim",
        "identification",
        ["recording_id", "source", "taxon_id"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    """Downgrade schema.

    Cannot restore deleted rows or which claims were superseded before this
    migration ran -- downgrading recreates the column and the old partial
    index shape, but every row comes back with superseded_at=NULL (i.e.
    "live"), which is data loss relative to pre-upgrade state. Acceptable for
    a dev-only downgrade path; never run this against a database whose
    upgrade already deleted real superseded rows and expect them back.
    """
    op.drop_constraint(
        "uq_identification_source_claim", "identification", type_="unique"
    )
    op.add_column(
        "identification",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_identification_source_claim",
        "identification",
        ["recording_id", "source", "source_version", "raw_label", "taxon_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
        postgresql_nulls_not_distinct=True,
    )
```

Confirm the generated file's `down_revision` matches Step 4's finding before moving on.

- [ ] **Step 6: Rewrite the two `test_migrations.py` tests that reference the old shape**

In `test_migrated_verdict_check_accepts_every_verdict`, the loop currently relies on `raw_label`
differing per row to avoid colliding under the *old* 5-column partial index; under the new
3-column constraint, `taxon_id` is what must differ (all three rows would otherwise share
`(recording_id, source='manual', taxon_id=NULL)`). Replace the loop body:

```python
        for i, verdict in enumerate(Verdict):
            # taxon_id must differ per row now: uq_identification_source_claim
            # is (recording_id, source, taxon_id) with nulls_not_distinct, and
            # `source`/`verdict` alone no longer distinguish these rows the way
            # the old (source, source_version, raw_label, taxon_id) key did.
            conn.execute(
                text(
                    "INSERT INTO taxon (rank, scientific_name)"
                    " VALUES ('species', :name)"
                ),
                {"name": f"Test taxon {i}"},
            )
            conn.execute(
                text(
                    "INSERT INTO identification"
                    " (recording_id, source, verdict, raw_label, taxon_id)"
                    " SELECT r.id, 'manual', :verdict, :label,"
                    " (SELECT id FROM taxon WHERE scientific_name = :name)"
                    " FROM recording r"
                ),
                {"verdict": verdict.value, "label": verdict.value, "name": f"Test taxon {i}"},
            )
```

(Check the surrounding function for whatever `taxon` table columns are actually required
NOT NULL — `rank`/`scientific_name` are the two seen elsewhere in this file's fixtures; add any
other required column this specific `taxon` table enforces if the insert fails on a missing
NOT NULL.)

Replace `test_migrated_partial_index_where_clause_is_enforced` entirely — its subject (a
partial index's WHERE clause) no longer exists — with a test proving the new plain constraint is
actually enforced by the migrated schema (not just by the model, which Task 7 Step 3 already
covers separately):

```python
def test_migrated_unique_constraint_rejects_a_duplicate_claim(
    migrated_engine: Engine,
) -> None:
    """The new plain uq_identification_source_claim (recording_id, source,
    taxon_id) must actually be enforced by the migrated schema, not just by
    the model under Base.metadata.create_all (test_models.py's job) --
    proves the migration's op.create_unique_constraint call actually landed."""
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO recording (audio_hash, path, recorded_at, guano_raw)"
                " VALUES ('u' || repeat('0', 63), 'u.wav', now(), '{}'::jsonb)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO identification (recording_id, source, verdict)"
                " SELECT id, 'manual', 'no_id' FROM recording"
                " WHERE audio_hash = 'u' || repeat('0', 63)"
            )
        )
    with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO identification (recording_id, source, verdict)"
                " SELECT id, 'manual', 'no_id' FROM recording"
                " WHERE audio_hash = 'u' || repeat('0', 63)"
            )
        )
```

(Add `from sqlalchemy.exc import IntegrityError` and `import pytest` to the file's imports if
not already present — check first.)

- [ ] **Step 7: Run and mutation-test `tests/test_migrations.py`**

Run: `hatch test tests/test_migrations.py -v` (`dangerouslyDisableSandbox: true`)
Expected: PASS, every test.

**Mutation-test whether `compare_metadata` sees `postgresql_nulls_not_distinct`** — per
CLAUDE.md's Migrations section, a drift test that cannot fail is worse than no test, and this
flag is new enough (Postgres 15+, plain-constraint form) that it's not established whether
Alembic's autogenerate comparison detects it the way it detects an added/removed column.
Temporarily remove `postgresql_nulls_not_distinct=True` from the migration's
`op.create_unique_constraint` call (leave the model unchanged) and re-run
`hatch test tests/test_migrations.py`:

- If `test_migration_matches_the_models` now FAILS: `compare_metadata` does see the flag — no
  further test needed, revert the temporary removal and move on.
- If it still PASSES: `compare_metadata` is blind to this flag, matching the established pattern
  for other invisible schema properties in this file. Revert the temporary removal, then add a
  dedicated test asserting it via Postgres's own catalog (mirroring
  `test_migrated_partial_index_where_clause_is_enforced`'s now-superseded approach):

  ```python
  def test_migrated_unique_constraint_nulls_not_distinct_is_enforced(
      migrated_engine: Engine,
  ) -> None:
      """compare_metadata cannot see NULLS NOT DISTINCT -- mutation-tested
      2026-09-06 by temporarily dropping postgresql_nulls_not_distinct from
      the migration's op.create_unique_constraint call:
      test_migration_matches_the_models still PASSED. Assert the sentinel
      (taxon_id IS NULL) singleton rule directly instead."""
      with migrated_engine.begin() as conn:
          conn.execute(
              text(
                  "INSERT INTO recording (audio_hash, path, recorded_at, guano_raw)"
                  " VALUES ('v' || repeat('0', 63), 'v.wav', now(), '{}'::jsonb)"
              )
          )
          conn.execute(
              text(
                  "INSERT INTO identification (recording_id, source, verdict)"
                  " SELECT id, 'manual', 'no_id' FROM recording"
                  " WHERE audio_hash = 'v' || repeat('0', 63)"
              )
          )
      with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
          conn.execute(
              text(
                  "INSERT INTO identification (recording_id, source, verdict)"
                  " SELECT id, 'manual', 'no_id' FROM recording"
                  " WHERE audio_hash = 'v' || repeat('0', 63)"
              )
          )
  ```

  (This looks identical to `test_migrated_unique_constraint_rejects_a_duplicate_claim` from
  Step 6 because both rows have `taxon_id IS NULL` — that overlap is fine; Step 6's test proves
  the constraint columns are right, this one specifically proves the NULL-handling flag is set,
  and the mutation testing above is what tells you whether this second test is actually needed
  or redundant with drift detection.)

- [ ] **Step 8: Full test suite**

Run: `hatch test` (`dangerouslyDisableSandbox: true` — includes `db`-marked tests)
Expected: PASS, the entire suite (not just the files this plan touched — a schema change like
this is exactly the kind of change that can break something elsewhere unnoticed).

- [ ] **Step 9: Type-check**

Run: `hatch run types:check`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/fledermap/store/models.py src/fledermap/alembic/versions/*.py \
        tests/test_models.py tests/test_migrations.py
git commit -m "feat: drop Identification.superseded_at, plain unique constraint

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FgqEf89xFgRsPq8wQrH1Eh"
```

**Do not run this migration against the real `bats_db` as part of this task.** Applying it there
is a separate deploy step: `scripts/db-backup.sh` first (non-negotiable, this migration deletes
data), then `hatch run fledermap` ... actually via whatever this project's real deploy path runs
`alembic upgrade head` (check `docs/setup.md`/CLAUDE.md's systemd-install memory for how `bats_db`
gets migrated in practice), then the usual `pipx install --force .` + `fledermap.target` restart
deploy sequence. Ask for explicit confirmation before doing any of that.

---

### Task 8: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Fix the Architecture section's `services/` bullet**

Replace:

```
  `manual_classification.py` is the only place that writes `IdSource.MANUAL` identification
  rows, via a supersede-then-insert function (`set_manual_classification`) matching
  `services/ingest.py`'s own key-based approach.
```

with:

```
  `manual_classification.py` is the only place that writes `IdSource.MANUAL` identification
  rows, via `set_manual_classification`, which (like `services/ingest.py`'s own
  `_apply_identifications`) delegates to `services/identifications.py`'s `replace_claims` —
  the one shared delete/update-in-place algorithm both use.
```

- [ ] **Step 2: Replace the two `uq_identification_source_claim`/superseded-history bullets in
  the Database section**

Delete both bullets currently reading (the `postgresql_nulls_not_distinct` one and the "is a
partial unique `Index`, not a `UniqueConstraint`" one — the second one entirely, the first one
needs a smaller edit since `nulls_not_distinct` is still real, just for a different reason now):

Replace:

```
- **Postgres treats NULLs as distinct**, so a `UniqueConstraint` over a nullable column does not
  fire at all. `uq_identification_source_claim` needs `postgresql_nulls_not_distinct=True`
  precisely because `source_version` is NULL for the sources that most need it — filename IDs
  and manual annotations.
- **`uq_identification_source_claim` is a partial unique `Index`, not a `UniqueConstraint`** —
  and that's load-bearing, not a style choice. A plain constraint has no notion of "superseded,
  no longer live": `set_manual_classification`'s (and `services/ingest.py`'s
  `_apply_identifications`') supersede-then-insert pattern re-adds a `taxon_id` that a row this
  same call just superseded, and that collides with the now-superseded row's still-enforced key
  tuple under a plain constraint — a real `UniqueViolation` crashed the classifier box's own
  primary multi-species workflow live, 2026-09-05. Postgres has no partial unique CONSTRAINT
  syntax, so a partial unique INDEX scoped to `WHERE superseded_at IS NULL` is the fix — only
  currently-live claims participate in the uniqueness check, so a superseded row's key tuple
  becomes free to reuse the moment it's superseded. See migration `300b54c8829a`.
```

with:

```
- **`Identification` has no soft-delete.** `services/identifications.py`'s `replace_claims` is
  the only writer of `Identification` rows from either `services/ingest.py` or
  `services/manual_classification.py`: a claim a source no longer makes is deleted outright, a
  claim whose details changed is updated in place. This replaced an earlier `superseded_at`
  soft-delete column and the partial unique index it required (migration `300b54c8829a`,
  2026-09-05) — see `docs/superpowers/specs/2026-09-06-fledermap-drop-identification-supersede-
  design.md` for why the partial index turned out to be unnecessary complexity rather than
  load-bearing: nothing ever read a superseded row for anything beyond a struck-through display
  with no version/timestamp shown, and no config for a hypothetical future retention need existed
  either.
- **Postgres treats NULLs as distinct**, so a `UniqueConstraint` over a nullable column does not
  fire at all. `uq_identification_source_claim` needs `postgresql_nulls_not_distinct=True`
  because the NO_ID/NOISE sentinel claim has `taxon_id IS NULL` — without it, a source could
  insert unlimited duplicate sentinel rows.
```

- [ ] **Step 3: Fix the Migrations section's partial-index paragraph**

Replace:

```
**A partial (filtered) unique index's `WHERE`/predicate clause is also invisible to
`compare_metadata`.** `uq_identification_source_claim` (Task 7's fix for the constraint/partial-
index collision above) is a unique `Index` scoped to `WHERE superseded_at IS NULL` — mutation-
tested 2026-09-05 by temporarily stripping the migration's `postgresql_where` clause (making it a
plain, non-partial unique index) and re-running `hatch test tests/test_migrations.py`, which still
PASSED. Same fix pattern as the other blind spots here: a dedicated test asserting against
Postgres's own catalog directly. `test_migrated_partial_index_where_clause_is_enforced` does this
by string-matching the predicate's text representation in `pg_indexes.indexdef` — **not**
`pg_indexes.indpred`, which doesn't exist; `indpred` lives on `pg_index`, a different catalog
view.
```

with (whichever branch Task 7 Step 7's mutation test actually found — write this once that's
known):

```
**`postgresql_nulls_not_distinct` on a plain `UniqueConstraint`'s visibility to
`compare_metadata` was mutation-tested 2026-09-06** when `uq_identification_source_claim` went
back to being a plain constraint (dropping `Identification.superseded_at` and the partial index
it required — see `docs/superpowers/specs/2026-09-06-fledermap-drop-identification-supersede-
design.md`). [Fill in with whichever of the two Task 7 Step 7 outcomes actually happened: either
"`compare_metadata` DOES detect it — no dedicated catalog test was needed" or "`compare_metadata`
does NOT detect it — `test_migrated_unique_constraint_nulls_not_distinct_is_enforced` closes the
gap the same way the old partial-index test did, asserting against Postgres's own catalog rather
than the drift comparison."]
```

- [ ] **Step 4: Verify prose**

Read the three edited sections back in full (`Architecture`, `Database`, `Migrations`) to confirm
nothing else in their surrounding text still assumes `superseded_at` exists — earlier searches in
this plan's own preparation found exactly these spots, but re-read after editing rather than
trusting that search was exhaustive.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for the superseded_at removal

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01FgqEf89xFgRsPq8wQrH1Eh"
```

---

## Self-Review Notes

- **Spec coverage:** Design §1 (schema) → Task 7. §2 (shared helper + callers) → Tasks 1-3. §3
  (read-path simplification) → Tasks 4-6. §4 (migration, backup-first) → Task 7. §5 (test
  fallout) → every task's own test edits, itemized per file. Decisions D1-D3 are all embodied in
  Task 1's `replace_claims` design and Task 3/8's "no retention hook" scope.
- **Placeholder scan:** `<alembic-generated>` markers in Task 7 are real values a tool produces
  at execution time (same convention as the prior manual-classification plan's Task 7), not
  content gaps — the surrounding text says exactly what command produces them and how to verify
  `down_revision`. Task 8 Step 3's bracketed instruction is resolved by Task 7's own mutation
  test, which runs earlier in the plan — by the time Task 8 executes, which branch happened is
  known, not still an open question.
- **Type consistency:** `ClaimInput`/`ReplaceResult`/`replace_claims` signatures introduced in
  Task 1 are used identically (same parameter names and order) in Tasks 2 and 3. `IngestReport`'s
  `identifications_updated`/`identifications_removed` names introduced in Task 2 match what Task
  7's spec-alignment section and CLAUDE.md's Task 8 rewrite refer to.

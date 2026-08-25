from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from geoalchemy2 import Geometry
from sqlalchemy import Engine, cast, event, func, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.domain.metadata import (
    ParsedIdentification,
    RecordingMetadata,
    ScannedFile,
)
from fledermap.ingest.merge import merge_metadata
from fledermap.ingest.wamd import parse_wamd
from fledermap.services.ingest import _EMT_SOURCES, commit_scan
from fledermap.store.models import Identification, Recording
from fledermap.store.seed import seed_taxonomy
from tests.fixtures import wamd_payload

pytestmark = pytest.mark.db

ROOT = Path("/archive")
CET = timezone(timedelta(hours=2))


def _scanned(
    digest: str = "a" * 64,
    name: str = "EPTSER_20150610_215446.wav",
    label: str = "EPTSER",
    device: str | None = None,
    note: str | None = None,
    duration_s: float | None = None,
    identifications: tuple[ParsedIdentification, ...] | None = None,
) -> ScannedFile:
    return ScannedFile(
        audio_hash=digest,
        path=ROOT / "Session_20130401_053030" / name,
        metadata=RecordingMetadata(
            recorded_at=datetime(2015, 6, 10, 21, 54, 46, tzinfo=UTC),
            # Populated, not left at None (task-11 fix round 1, priority 1):
            # a no-UPDATE regression test can't see a defect in how a field is
            # guarded on the second scan if that field is None on both scans,
            # since `None != None` is always False. `filename_at`/
            # `metadata_at` are aware, matching their `DateTime(timezone=True)`
            # columns — the specific shape of the defect this covers.
            filename_at=datetime(2015, 6, 10, 21, 54, 46, tzinfo=CET),
            metadata_at=datetime(2015, 6, 10, 9, 54, 54, tzinfo=CET),
            timestamp_disagreement_s=43200.0,
            elevation_m=350.0,
            loc_accuracy_m=5.0,
            samplerate_hz=256000,
            te_factor=10,
            make="Wildlife Acoustics",
            model="Echo Meter Touch 2",
            serial="EMT2-0001",
            latitude=42.346973,
            longitude=-76.48760,
            device=device,
            note=note,
            duration_s=duration_s,
            identifications=(
                identifications
                if identifications is not None
                else (
                    ParsedIdentification(
                        source=IdSource.EMT_WAMD,
                        source_version="App 3.1.10",
                        verdict=Verdict.SPECIES,
                        raw_label=label,
                    ),
                )
            ),
        ),
    )


def test_new_file_is_created(engine: Engine) -> None:
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        report = commit_scan(session, [_scanned()], archive_root=ROOT)
        session.commit()

        assert report.created == 1
        assert session.scalar(select(func.count()).select_from(Recording)) == 1


def test_created_hashes_records_every_newly_created_audio_hash(
    engine: Engine,
) -> None:
    a = _scanned(digest="a" * 64, name="EPTSER_20150610_215446.wav")
    b = _scanned(digest="b" * 64, name="EPTSER_20150610_215447.wav")

    with OrmSession(engine) as session:
        report = commit_scan(session, [a, b], archive_root=ROOT)

    assert sorted(report.created_hashes) == sorted([a.audio_hash, b.audio_hash])


def test_created_hashes_excludes_unchanged_recordings(engine: Engine) -> None:
    a = _scanned(digest="a" * 64)

    with OrmSession(engine) as session:
        commit_scan(session, [a], archive_root=ROOT)
        session.commit()
        second_report = commit_scan(session, [a], archive_root=ROOT)

    assert second_report.created_hashes == []


def test_ingest_is_idempotent(engine: Engine) -> None:
    """Run twice, nothing changes. The defining property of spec section 6."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [_scanned()], archive_root=ROOT)
        session.commit()

        report = commit_scan(session, [_scanned()], archive_root=ROOT)
        session.commit()

        assert report.created == 0
        assert report.unchanged == 1
        assert session.scalar(select(func.count()).select_from(Recording)) == 1


def test_second_run_emits_no_update_statements(engine: Engine) -> None:
    """Circularity guard: report counters are computed by the code under test,
    so `test_ingest_is_idempotent` alone can't prove nothing was written. Capture
    the actual SQL sent to Postgres on the second run and assert no UPDATE is
    among it (task-11 amendments, 'Verify idempotency properly')."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [_scanned()], archive_root=ROOT)
        session.commit()

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        with OrmSession(engine) as session:
            # NOT `assert not session.dirty` here: SQLAlchemy can list an
            # object in `session.dirty` (a candidate for the flush's change
            # evaluation) even when the eventual flush emits no SQL for it at
            # all — confirmed empirically for `guano_raw` while building this
            # task. Only the SQL actually sent to Postgres is conclusive.
            commit_scan(session, [_scanned()], archive_root=ROOT)
            session.commit()
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    # A listener that failed to attach would leave `statements` empty, which
    # would make the UPDATE-filter assertion below pass vacuously — assert
    # something was actually captured first (task-11 fix round 1, priority 6).
    assert statements != []
    updates = [s for s in statements if s.strip().upper().startswith("UPDATE")]
    assert updates == []


def test_rename_updates_path_without_duplicating(engine: Engine) -> None:
    """The re-ID case: same audio, new filename. This is why identity is the hash."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(
            session, [_scanned(name="NoID_20150610_215446.wav")], archive_root=ROOT
        )
        session.commit()

        report = commit_scan(
            session,
            [_scanned(name="EPTSER_20150610_215446.wav")],
            archive_root=ROOT,
        )
        session.commit()

        assert report.moved == 1
        assert session.scalar(select(func.count()).select_from(Recording)) == 1
        assert (
            session.scalars(select(Recording))
            .one()
            .path.endswith(
                "EPTSER_20150610_215446.wav",
            )
        )


def test_moved_and_reidentified_reports_as_moved(engine: Engine) -> None:
    """Deliberate: a file that both moved AND changed its identification (the
    re-ID case) is reported as MOVED, not UPDATED — spec section 6 defines the
    outcome by (hash, path) status ('known hash, new path'), not by whether
    metadata happens to also differ. See task-11 report, judgement call on
    'MOVED masks UPDATED'. The underlying data still records the change either
    way: the old identification is superseded and a new one is added."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(
            session,
            [_scanned(name="NoID_20150610_215446.wav", label="NoID")],
            archive_root=ROOT,
        )
        session.commit()

        report = commit_scan(
            session,
            [_scanned(name="EPTSER_20150610_215446.wav", label="EPTSER")],
            archive_root=ROOT,
        )
        session.commit()

        assert report.moved == 1
        assert report.updated == 0
        # The orthogonal counters (task-11 fix round 1, priority 5) are what
        # give this exact case visibility: MOVED alone tells the operator
        # nothing about the identification change happening underneath it.
        assert report.identifications_added == 1
        assert report.identifications_superseded == 1
        ids = session.scalars(select(Identification)).all()
        assert len(ids) == 2
        assert {i.raw_label for i in ids} == {"NoID", "EPTSER"}


def test_changed_identification_supersedes_the_old_one(engine: Engine) -> None:
    """The EMT changing its mind is recorded, not overwritten."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [_scanned(label="MYODAU")], archive_root=ROOT)
        session.commit()

        commit_scan(session, [_scanned(label="EPTSER")], archive_root=ROOT)
        session.commit()

        ids = session.scalars(select(Identification)).all()
        assert len(ids) == 2
        superseded = [i for i in ids if i.superseded_at is not None]
        assert len(superseded) == 1
        assert superseded[0].raw_label == "MYODAU"


def test_note_change_without_move_is_reported_as_updated(engine: Engine) -> None:
    """metadata_changed must cover more than the original 3 fields (task-11
    amendments, defect 4) — `note` and `duration_s` are not in that set."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [_scanned()], archive_root=ROOT)
        session.commit()

        report = commit_scan(
            session,
            [_scanned(note="heard a dog bark", duration_s=2.5)],
            archive_root=ROOT,
        )
        session.commit()

        assert report.updated == 1
        assert report.unchanged == 0
        rec = session.scalars(select(Recording)).one()
        assert rec.note == "heard a dog bark"
        assert rec.duration_s == 2.5


def test_device_field_round_trips(engine: Engine) -> None:
    """`device` (the host phone, from wamd) must not be silently dropped —
    task-11 amendments, defect 3."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [_scanned(device="iPhone 12")], archive_root=ROOT)
        session.commit()

        assert session.scalars(select(Recording)).one().device == "iPhone 12"


def test_paths_are_stored_relative_to_archive_root(engine: Engine) -> None:
    """So the archive can move without rewriting every row."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [_scanned()], archive_root=ROOT)
        session.commit()

        path = session.scalars(select(Recording)).one().path
        assert not path.startswith("/")
        assert path.startswith("Session_20130401_053030/")


def test_path_outside_archive_root_raises(engine: Engine) -> None:
    """A scanned path that isn't under `archive_root` is a programming/config
    error, not a tolerable fallback — silently storing an absolute path would
    violate the invariant `test_paths_are_stored_relative_to_archive_root`
    asserts (task-11 amendments, judgement call on `_relative`)."""
    outside = ScannedFile(
        audio_hash="c" * 64,
        path=Path("/elsewhere/foo.wav"),
        metadata=RecordingMetadata(
            recorded_at=datetime(2015, 6, 10, 21, 54, 46, tzinfo=UTC),
        ),
    )
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        with pytest.raises(ValueError, match="archive_root"):
            commit_scan(session, [outside], archive_root=ROOT)


def test_known_label_resolves_to_taxon(engine: Engine) -> None:
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [_scanned(label="EPTSER")], archive_root=ROOT)
        session.commit()

        ident = session.scalars(select(Identification)).one()
        assert ident.taxon_id is not None


def test_manual_identification_resolves_to_taxon(engine: Engine) -> None:
    """Manual IDs on the EMT use the same Wildlife Acoustics vocabulary as the
    auto IDs (task-11 amendments, defect 5). Source is `EMT_MANUAL`, not the
    generic `MANUAL` (task-11 fix round 1, priority 4) — this simulates what
    `merge.py` emits for a GUANO/wamd `manual_id`."""
    scanned = _scanned(
        identifications=(
            ParsedIdentification(
                source=IdSource.EMT_MANUAL,
                source_version=None,
                verdict=Verdict.SPECIES,
                raw_label="EPTSER",
            ),
        ),
    )
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        report = commit_scan(session, [scanned], archive_root=ROOT)
        session.commit()

        ident = session.scalars(select(Identification)).one()
        assert ident.source is IdSource.EMT_MANUAL
        assert ident.taxon_id is not None
        assert "EPTSER" not in report.unmapped_labels


def test_duplicate_manual_identifications_collapse_to_one_row(engine: Engine) -> None:
    """GUANO and wamd both carrying the same `manual_id` is the normal case
    (they're the same on-device correction, read from two chunks of the same
    file) and must not raise IntegrityError (task-11 amendments, defect 2)."""
    dup_claim = ParsedIdentification(
        source=IdSource.EMT_MANUAL,
        source_version=None,
        verdict=Verdict.SPECIES,
        raw_label="EPTSER",
    )
    scanned = _scanned(identifications=(dup_claim, dup_claim))

    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [scanned], archive_root=ROOT)
        session.commit()  # would raise IntegrityError before the fix

        ids = session.scalars(select(Identification)).all()
        assert len(ids) == 1
        assert ids[0].source is IdSource.EMT_MANUAL
        assert ids[0].raw_label == "EPTSER"


def test_emt_manual_identification_is_superseded_on_rescan(engine: Engine) -> None:
    """The operator changing the on-device manual ID must supersede the old
    claim, not leave two contradictory active manual identifications (task-11
    fix round 1, priority 4). Goes through the real `merge_metadata`, not a
    hand-built `ParsedIdentification`, so it exercises the actual source this
    defect was about.

    Before the fix: `IdSource.MANUAL` (excluded from `_EMT_SOURCES`) means the
    second scan adds EPTSER without ever superseding MYODAU — two active
    claims. After the fix: `IdSource.EMT_MANUAL` is in `_EMT_SOURCES`, so the
    rescan supersedes it correctly.
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
        commit_scan(session, [_scanned_with_manual_id("MYODAU")], archive_root=ROOT)
        session.commit()

        commit_scan(session, [_scanned_with_manual_id("EPTSER")], archive_root=ROOT)
        session.commit()

        ids = session.scalars(select(Identification)).all()
        active = [i for i in ids if i.superseded_at is None]
        assert len(active) == 1
        assert active[0].raw_label == "EPTSER"
        assert active[0].source is IdSource.EMT_MANUAL
        superseded = [i for i in ids if i.superseded_at is not None]
        assert len(superseded) == 1
        assert superseded[0].raw_label == "MYODAU"


def test_unmapped_label_is_stored_and_reported(engine: Engine) -> None:
    """Ingest must not fail on an unknown code; it becomes a review item."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        report = commit_scan(session, [_scanned(label="ZZZZZZ")], archive_root=ROOT)
        session.commit()

        ident = session.scalars(select(Identification)).one()
        assert ident.taxon_id is None
        assert ident.raw_label == "ZZZZZZ"
        assert "ZZZZZZ" in report.unmapped_labels


def test_geometry_is_written_when_position_present(engine: Engine) -> None:
    """Re-query in a fresh session and check the actual stored coordinates —
    asserting `.geom is not None` on the in-session object proves nothing, since
    that's just the value that was assigned a moment earlier (task-11
    amendments, defect 6)."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [_scanned()], archive_root=ROOT)
        session.commit()

    with OrmSession(engine) as session:
        lon, lat = session.execute(
            select(
                func.ST_X(cast(Recording.geom, Geometry)),
                func.ST_Y(cast(Recording.geom, Geometry)),
            ),
        ).one()
        assert lon == pytest.approx(-76.48760)
        assert lat == pytest.approx(42.346973)


def test_replaced_file_marks_old_row_missing_and_creates_new_row(
    engine: Engine,
) -> None:
    """Same path, new hash: the file was replaced. A new recording is created;
    the old row's path is marked missing, never deleted (task-11 amendments,
    defect 1 — spec section 6, row 4)."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [_scanned(digest="a" * 64)], archive_root=ROOT)
        session.commit()

        report = commit_scan(session, [_scanned(digest="b" * 64)], archive_root=ROOT)
        session.commit()

        assert report.replaced == 1
        assert report.created == 0
        recs = {r.audio_hash: r for r in session.scalars(select(Recording)).all()}
        assert len(recs) == 2
        assert recs["a" * 64].missing_since is not None
        assert recs["b" * 64].missing_since is None
        assert recs["a" * 64].path == recs["b" * 64].path


def test_created_hashes_includes_replaced_recordings(engine: Engine) -> None:
    """REPLACED (same path, new hash) inserts a brand-new row via the exact
    same code path as CREATED — the new hash has never had media rendered for
    it either, so it belongs in `created_hashes` too, not just CREATED's."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        commit_scan(session, [_scanned(digest="a" * 64)], archive_root=ROOT)
        session.commit()

        report = commit_scan(session, [_scanned(digest="b" * 64)], archive_root=ROOT)
        session.commit()

        assert report.replaced == 1
        assert report.created_hashes == ["b" * 64]


def test_second_replacement_at_the_same_path_does_not_crash(engine: Engine) -> None:
    """Before the fix: `Recording.path` is indexed but not unique, and a
    REPLACED row keeps its old `path`, so after one replacement two rows share
    a path. `.one_or_none()` against that raises `MultipleResultsFound` on the
    NEXT replacement, aborting the whole scan (task-11 fix round 1, priority
    2 — reproduced by the controller with exactly this three-hash sequence
    before dispatching this fix round). Filtering to `missing_since IS NULL`
    restores the real invariant: at most one *live* row occupies a path."""
    with OrmSession(engine) as session:
        seed_taxonomy(session)
        for digest in ("a" * 64, "b" * 64, "c" * 64):
            report = commit_scan(session, [_scanned(digest=digest)], archive_root=ROOT)
            session.commit()

        assert report.replaced == 1
        assert report.created == 0
        recs = {r.audio_hash: r for r in session.scalars(select(Recording)).all()}
        assert len(recs) == 3
        assert recs["a" * 64].missing_since is not None
        assert recs["b" * 64].missing_since is not None
        assert recs["c" * 64].missing_since is None
        # Only the LIVE row (c) is at the path; a and b's rows still record it
        # too (never rewritten), which is why the lookup must filter on
        # missing_since rather than assume uniqueness.
        assert recs["c" * 64].path == recs["a" * 64].path == recs["b" * 64].path


def test_duplicate_file_in_the_archive_does_not_ping_pong(engine: Engine) -> None:
    """Two copies of one recording at different paths (a backup folder, a
    re-filed session) hash identically. Before the fix, the second copy was
    resolved against the row the first just created, saw a different path,
    and was reported+written as MOVED — which then flipped back on every
    subsequent scan as the two paths alternated being 'current' (task-11 fix
    round 1, priority 3 — reproduced by the controller: `moved == 2` forever,
    one row, before dispatching this fix round). The first path sighted in a
    call wins; a later sighting of the same hash is a duplicate, not a move."""
    same_hash = "d" * 64
    copy_a = _scanned(digest=same_hash, name="copy_a.wav")
    copy_b = _scanned(digest=same_hash, name="copy_b.wav")

    with OrmSession(engine) as session:
        seed_taxonomy(session)
        first = commit_scan(session, [copy_a, copy_b], archive_root=ROOT)
        session.commit()

        assert first.created == 1
        assert first.duplicates == 1
        # A duplicate sighting isn't one of the five (hash, path) outcomes
        # spec section 6 defines, so `total` must not count it (task-11 fix
        # round 1, priority 6).
        assert first.total == 1
        first_path = session.scalars(select(Recording)).one().path

        second = commit_scan(session, [copy_a, copy_b], archive_root=ROOT)
        session.commit()

        assert second.moved == 0
        assert second.duplicates == 1
        rows = session.scalars(select(Recording)).all()
        assert len(rows) == 1
        assert rows[0].path == first_path


def test_emt_sources_membership_uses_stringenum_semantics() -> None:
    """`IdSource` is a `StrEnum`, so `IdSource.EMT_WAMD in {"emt.wamd"}` is True
    and `identification.source` (which round-trips as an `IdSource`, not a
    plain `str`, per Task 9) still matches a set of `IdSource` members.
    Confirmed rather than assumed (task-11 amendments, judgement calls)."""
    assert IdSource.EMT_WAMD in _EMT_SOURCES
    assert "emt.wamd" in {s.value for s in _EMT_SOURCES}


def test_emt_sources_stays_exactly_the_emt_prefixed_id_sources() -> None:
    """`_EMT_SOURCES` happens to equal exactly the `emt.`-prefixed `IdSource`
    members today, but nothing enforced that stays true — and the branch
    already shipped a bug of exactly this shape once (`EMT_MANUAL` initially
    wasn't in this set, so it was never superseded). This is the guard
    against a future `IdSource` member repeating the same mismatch
    (whole-branch review, Minor D)."""
    assert _EMT_SOURCES == {s for s in IdSource if s.value.startswith("emt.")}

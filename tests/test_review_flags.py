from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.review_flags import (
    ReviewContext,
    _misattribution_rates,
    _misattribution_reason,
    _rarity_reason,
    _taxon_counts,
    build_review_snapshot,
    parse_review_snapshot,
    resolve_review_snapshot,
    review_reasons,
)
from fledermap.store.models import Identification, Recording, Site, Taxon


def test_rarity_reason_none_when_common() -> None:
    dataset_counts = {1: 50}
    site_counts = {(10, 1): 20}
    assert _rarity_reason(dataset_counts, site_counts, 10, 1) is None


def test_rarity_reason_fires_at_site_threshold() -> None:
    dataset_counts = {1: 50}
    site_counts = {(10, 1): 2}
    reason = _rarity_reason(dataset_counts, site_counts, 10, 1)
    assert reason is not None
    # Deliberately doesn't name the species -- every caller already shows
    # it right next to this reason (recording-detail title, Reviews table's
    # Species column).
    assert "2" in reason and "site" in reason


def test_rarity_reason_fires_at_dataset_threshold() -> None:
    dataset_counts = {1: 5}
    site_counts = {(10, 1): 30}
    reason = _rarity_reason(dataset_counts, site_counts, 10, 1)
    assert reason is not None
    assert "5" in reason and "dataset" in reason


def test_rarity_reason_handles_no_site() -> None:
    # recording.site_id is None -- must not raise, and must still fall
    # back to the dataset-wide check.
    dataset_counts = {1: 3}
    site_counts: dict[tuple[int, int], int] = {}
    reason = _rarity_reason(dataset_counts, site_counts, None, 1)
    assert reason is not None


def test_misattribution_reason_none_below_minimum_count() -> None:
    rates = {(IdSource.EMT_GUANO, 1): (2, 2)}  # 2/2 wrong, but under the min. of 3
    assert _misattribution_reason(rates, IdSource.EMT_GUANO, 1) is None


def test_misattribution_reason_none_below_majority() -> None:
    rates = {(IdSource.EMT_GUANO, 1): (3, 7)}  # 3/7 wrong -- not a majority
    assert _misattribution_reason(rates, IdSource.EMT_GUANO, 1) is None


def test_misattribution_reason_fires_at_majority_and_minimum() -> None:
    rates = {(IdSource.EMT_GUANO, 1): (3, 5)}  # 3/5 wrong, >=3 disagreements
    reason = _misattribution_reason(rates, IdSource.EMT_GUANO, 1)
    assert reason is not None
    # Deliberately doesn't name the species -- see the rarity-reason test above.
    assert IdSource.EMT_GUANO.value in reason


def test_misattribution_reason_absent_pair_is_none() -> None:
    assert _misattribution_reason({}, IdSource.EMT_GUANO, 1) is None


# DB-marked tests follow


def _site(session: OrmSession, *, lon: float = 10.0, lat: float = 50.0) -> Site:
    from datetime import UTC, datetime

    site = Site(
        centroid=WKTElement(f"POINT({lon} {lat})", srid=4326),
        radius_m=50.0,
        recording_count=0,
        first_at=datetime(2026, 8, 1, tzinfo=UTC),
        last_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    session.add(site)
    session.flush()
    return site


@pytest.mark.db
def test_taxon_counts_tallies_by_site_and_dataset(engine: Engine) -> None:
    from datetime import UTC, datetime

    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        site = _site(session)
        for i in range(3):
            r = Recording(
                audio_hash=f"{i:064x}",
                path=f"{i}.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                site_id=site.id,
            )
            session.add(r)
            session.flush()
            session.add(
                Identification(
                    recording_id=r.id,
                    source="emt.guano",
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon.id,
                    first_seen_at=r.recorded_at,
                ),
            )
        session.commit()

        recordings = session.scalars(select(Recording)).all()
        dataset_counts, site_counts = _taxon_counts(recordings)

        # Capture IDs while session is still open
        taxon_id = taxon.id
        site_id = site.id

    assert dataset_counts[taxon_id] == 3
    assert site_counts[(site_id, taxon_id)] == 3


def _recording_with_claims(
    session: OrmSession,
    *,
    audio_hash: str,
    classifier_taxon_id: int,
    manual_verdict: Verdict | None,
    manual_taxon_ids: tuple[int, ...] = (),
) -> Recording:
    from datetime import UTC, datetime

    r = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    session.add(r)
    session.flush()
    session.add(
        Identification(
            recording_id=r.id,
            source=IdSource.EMT_GUANO,
            verdict=Verdict.SPECIES,
            taxon_id=classifier_taxon_id,
            first_seen_at=r.recorded_at,
        ),
    )
    if manual_verdict == Verdict.SPECIES:
        for taxon_id in manual_taxon_ids:
            session.add(
                Identification(
                    recording_id=r.id,
                    source=IdSource.MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon_id,
                    first_seen_at=r.recorded_at,
                ),
            )
    elif manual_verdict is not None:
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.MANUAL,
                verdict=manual_verdict,
                taxon_id=None,
                first_seen_at=r.recorded_at,
            ),
        )
    session.flush()
    return r


@pytest.mark.db
def test_misattribution_rates_matches_the_spec_table(engine: Engine) -> None:

    with OrmSession(engine) as session:
        x = Taxon(rank="species", scientific_name="X species")
        y = Taxon(rank="species", scientific_name="Y species")
        session.add_all([x, y])
        session.flush()

        # C: X, H: X -> correct
        _recording_with_claims(
            session,
            audio_hash="a" * 64,
            classifier_taxon_id=x.id,
            manual_verdict=Verdict.SPECIES,
            manual_taxon_ids=(x.id,),
        )
        # C: X, H: Y -> misattribution
        _recording_with_claims(
            session,
            audio_hash="b" * 64,
            classifier_taxon_id=x.id,
            manual_verdict=Verdict.SPECIES,
            manual_taxon_ids=(y.id,),
        )
        # C: X, H: {X, Y} -> correct
        _recording_with_claims(
            session,
            audio_hash="c" * 64,
            classifier_taxon_id=x.id,
            manual_verdict=Verdict.SPECIES,
            manual_taxon_ids=(x.id, y.id),
        )
        # C: X, H: {Y, Z} -> misattribution (using just Y here, two-taxon
        # case already covered above)
        z = Taxon(rank="species", scientific_name="Z species")
        session.add(z)
        session.flush()
        _recording_with_claims(
            session,
            audio_hash="d" * 64,
            classifier_taxon_id=x.id,
            manual_verdict=Verdict.SPECIES,
            manual_taxon_ids=(y.id, z.id),
        )
        # C: X, H: Noise -> misattribution
        _recording_with_claims(
            session,
            audio_hash="e" * 64,
            classifier_taxon_id=x.id,
            manual_verdict=Verdict.NOISE,
        )
        # C: X, H: NoID -> ignored entirely
        _recording_with_claims(
            session,
            audio_hash="f" * 64,
            classifier_taxon_id=x.id,
            manual_verdict=Verdict.NO_ID,
        )
        session.commit()

        recordings = session.scalars(select(Recording)).all()
        rates = _misattribution_rates(recordings)

        # Capture ID while session is still open
        x_id = x.id

    # 3 misattributions (b, d, e) out of 5 counted recordings (a, b, c, d, e)
    # -- f is excluded entirely, matching the "NO_ID ignored" rule.
    assert rates[(IdSource.EMT_GUANO, x_id)] == (3, 5)


@pytest.mark.db
def test_review_context_build_fetches_recordings_exactly_once(engine: Engine) -> None:
    """`_taxon_counts` and `_misattribution_rates` used to each run their own
    independent `select(Recording).where(missing_since.is_(None))` query --
    `ReviewContext.build` must instead fetch once and share the result, so
    exactly one such SELECT is sent to Postgres per `build` call."""
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="c" * 64,
                path="c.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        with OrmSession(engine) as session:
            ReviewContext.build(session)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    recording_selects = [
        s
        for s in statements
        if s.strip().upper().startswith("SELECT") and "from recording " in s.lower()
    ]
    assert len(recording_selects) == 1, recording_selects


@pytest.mark.db
def test_review_reasons_empty_when_not_species_verdict(engine: Engine) -> None:
    with OrmSession(engine) as session:
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.NOISE,
                taxon_id=None,
                first_seen_at=r.recorded_at,
            ),
        )
        session.commit()
        context = ReviewContext.build(session)

        assert review_reasons(r, context) == []


@pytest.mark.db
def test_review_reasons_empty_when_manually_classified(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        session.add_all(
            [
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_GUANO,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon.id,
                    first_seen_at=r.recorded_at,
                ),
                Identification(
                    recording_id=r.id,
                    source=IdSource.MANUAL,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon.id,
                    first_seen_at=r.recorded_at,
                ),
            ]
        )
        session.commit()
        context = ReviewContext.build(session)

        # Already reviewed by a human -- a rare-species match must not fire.
        assert review_reasons(r, context) == []


@pytest.mark.db
def test_review_reasons_includes_rarity_match(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.flush()
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=r.recorded_at,
            ),
        )
        session.commit()
        context = ReviewContext.build(session)

        reasons = review_reasons(r, context)

    assert len(reasons) == 1
    assert "Rare species" in reasons[0]


class _FakeRecording:
    """Stands in for a real `Recording` in the two `build_review_snapshot`
    tests below -- only `.id` is read, so a full ORM row (with its
    NOT NULL columns) would be pure setup noise. `cast` at each call site
    tells mypy this satisfies the `Sequence[Recording]` parameter without
    an `# type: ignore`."""

    def __init__(self, id_: int) -> None:
        self.id = id_


def test_build_review_snapshot_preserves_order() -> None:
    recordings = cast(
        "list[Recording]", [_FakeRecording(3), _FakeRecording(1), _FakeRecording(2)]
    )
    assert build_review_snapshot(recordings) == [3, 1, 2]


def test_build_review_snapshot_caps_at_the_maximum() -> None:
    recordings = cast("list[Recording]", [_FakeRecording(i) for i in range(600)])
    snapshot = build_review_snapshot(recordings)
    assert len(snapshot) == 500
    assert snapshot == list(range(500))


def test_parse_review_snapshot_round_trips_a_comma_list() -> None:
    assert parse_review_snapshot("14,52,109") == [14, 52, 109]


def test_parse_review_snapshot_skips_malformed_entries() -> None:
    assert parse_review_snapshot("14,not-a-number,109,") == [14, 109]


def test_parse_review_snapshot_empty_or_none_is_empty_list() -> None:
    assert parse_review_snapshot(None) == []
    assert parse_review_snapshot("") == []


@pytest.mark.db
def test_resolve_review_snapshot_preserves_snapshot_order(engine: Engine) -> None:
    with OrmSession(engine) as session:
        ids = []
        for audio_hash in ("a" * 64, "b" * 64, "c" * 64):
            r = Recording(
                audio_hash=audio_hash,
                path=f"{audio_hash}.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            )
            session.add(r)
            session.flush()
            ids.append(r.id)
        session.commit()
        # Snapshot order is reversed relative to insertion/id order --
        # resolve_review_snapshot must preserve THIS order, not re-sort.
        snapshot_ids = [ids[2], ids[0], ids[1]]

        resolved = resolve_review_snapshot(session, snapshot_ids)

    assert [r.id for r in resolved] == snapshot_ids


@pytest.mark.db
def test_resolve_review_snapshot_drops_unresolvable_ids(engine: Engine) -> None:
    with OrmSession(engine) as session:
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(r)
        session.commit()
        real_id = r.id

        resolved = resolve_review_snapshot(session, [999999, real_id])

    assert [r.id for r in resolved] == [real_id]

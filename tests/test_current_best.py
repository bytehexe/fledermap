from __future__ import annotations

from datetime import UTC, datetime

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.current_best import current_best_identification
from fledermap.store.models import Identification, Recording


def _recording(*identifications: Identification) -> Recording:
    r = Recording(
        audio_hash="a" * 64,
        path="x.wav",
        recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    r.identifications = list(identifications)
    return r


def _ident(
    source: IdSource,
    *,
    taxon_id: int | None = 1,
    verdict: Verdict = Verdict.SPECIES,
    superseded: bool = False,
    first_seen_at: datetime = datetime(2026, 8, 25, tzinfo=UTC),
) -> Identification:
    return Identification(
        source=source,
        verdict=verdict,
        taxon_id=taxon_id,
        first_seen_at=first_seen_at,
        superseded_at=datetime(2026, 8, 26, tzinfo=UTC) if superseded else None,
    )


def test_manual_wins_over_every_other_source() -> None:
    r = _recording(
        _ident(IdSource.EMT_GUANO),
        _ident(IdSource.MANUAL, taxon_id=2),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.source == IdSource.MANUAL
    assert best.taxon_id == 2


def test_emt_guano_beats_emt_wamd_beats_emt_filename() -> None:
    r = _recording(
        _ident(IdSource.EMT_FILENAME, taxon_id=1),
        _ident(IdSource.EMT_WAMD, taxon_id=2),
        _ident(IdSource.EMT_GUANO, taxon_id=3),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.source == IdSource.EMT_GUANO
    assert best.taxon_id == 3


def test_superseded_identifications_are_ignored() -> None:
    r = _recording(
        _ident(IdSource.MANUAL, superseded=True),
        _ident(IdSource.EMT_GUANO, taxon_id=5),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.source == IdSource.EMT_GUANO
    assert best.taxon_id == 5


def test_no_identifications_returns_none() -> None:
    r = _recording()

    assert current_best_identification(r) is None


def test_all_superseded_returns_none() -> None:
    r = _recording(_ident(IdSource.EMT_GUANO, superseded=True))

    assert current_best_identification(r) is None


def test_two_non_superseded_claims_from_the_same_source_break_on_recency() -> None:
    """Possible but rare: a source re-reports under a different
    source_version/raw_label before the earlier claim is superseded. Not
    arbitrary dict-iteration-order behaviour -- the most recently first-seen
    claim wins."""
    r = _recording(
        _ident(
            IdSource.EMT_GUANO,
            taxon_id=1,
            first_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        _ident(
            IdSource.EMT_GUANO,
            taxon_id=2,
            first_seen_at=datetime(2026, 6, 1, tzinfo=UTC),
        ),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.taxon_id == 2

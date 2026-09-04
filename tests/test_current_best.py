from __future__ import annotations

from datetime import UTC, datetime

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.current_best import (
    current_best_identification,
    recording_headline,
)
from fledermap.store.models import Identification, Recording, Taxon


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


def test_recording_headline_prefers_the_resolved_taxon() -> None:
    taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
    best = _ident(IdSource.EMT_GUANO, taxon_id=1)

    assert recording_headline(taxon, best) == "Pipistrellus pipistrellus"


def test_recording_headline_is_unidentified_with_no_best() -> None:
    assert recording_headline(None, None) == "unidentified"


def test_recording_headline_shows_the_raw_code_for_an_unmapped_species() -> None:
    """Regression test: a real SPECIES verdict whose code never mapped to a Taxon used to
    fall through to the literal, useless string "species" (`best.verdict.value`) -- the
    actual detector code was sitting right there in `raw_label`, unused."""
    best = _ident(IdSource.EMT_FILENAME, taxon_id=None, verdict=Verdict.SPECIES)
    best.raw_label = "EPTNIL"

    assert recording_headline(None, best) == "EPTNIL (unmapped species)"


def test_recording_headline_falls_back_to_the_verdict_value_with_no_raw_label() -> None:
    """Defensive fallback: a SPECIES verdict with neither a taxon nor a raw_label shouldn't
    happen in practice (every SPECIES claim carries the code it was resolved -- or failed
    to resolve -- from), but must not crash or show an empty/None-ish string if it did."""
    best = _ident(IdSource.EMT_FILENAME, taxon_id=None, verdict=Verdict.SPECIES)

    assert recording_headline(None, best) == "species"


def test_recording_headline_shows_no_id_and_noise_verdicts_directly() -> None:
    no_id = _ident(IdSource.EMT_FILENAME, taxon_id=None, verdict=Verdict.NO_ID)
    noise = _ident(IdSource.EMT_FILENAME, taxon_id=None, verdict=Verdict.NOISE)

    assert recording_headline(None, no_id) == "no_id"
    assert recording_headline(None, noise) == "noise"

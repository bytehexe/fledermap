from __future__ import annotations

from datetime import UTC, datetime

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.current_best import (
    CurrentIdentification,
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
    assert best.primary.source == IdSource.MANUAL
    assert best.primary.taxon_id == 2


def test_emt_guano_beats_emt_wamd_beats_emt_filename() -> None:
    r = _recording(
        _ident(IdSource.EMT_FILENAME, taxon_id=1),
        _ident(IdSource.EMT_WAMD, taxon_id=2),
        _ident(IdSource.EMT_GUANO, taxon_id=3),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.EMT_GUANO
    assert best.primary.taxon_id == 3


def test_superseded_identifications_are_ignored() -> None:
    r = _recording(
        _ident(IdSource.MANUAL, superseded=True),
        _ident(IdSource.EMT_GUANO, taxon_id=5),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.EMT_GUANO
    assert best.primary.taxon_id == 5


def test_no_identifications_returns_none() -> None:
    r = _recording()

    assert current_best_identification(r) is None


def test_all_superseded_returns_none() -> None:
    r = _recording(_ident(IdSource.EMT_GUANO, superseded=True))

    assert current_best_identification(r) is None


def test_two_non_superseded_claims_from_the_same_source_break_on_recency() -> None:
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
    assert best.primary.taxon_id == 2


def test_automatic_no_id_is_passive_and_a_lower_precedence_species_wins() -> None:
    """The ^a549ce fix: a high-precedence automatic NO_ID no longer shadows a
    real SPECIES answer further down the precedence order."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, verdict=Verdict.NO_ID, taxon_id=None),
        _ident(IdSource.EMT_WAMD, taxon_id=7),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.EMT_WAMD
    assert best.primary.taxon_id == 7


def test_automatic_no_id_with_nothing_below_it_returns_none() -> None:
    """Every automatic source drew a blank -- current_best_identification
    returns None (displayed as "unidentified"), not a NO_ID result."""
    r = _recording(_ident(IdSource.EMT_GUANO, verdict=Verdict.NO_ID, taxon_id=None))

    assert current_best_identification(r) is None


def test_manual_no_id_is_active_and_shadows_a_lower_precedence_species() -> None:
    """Unlike an automatic NO_ID, a MANUAL NO_ID is a deliberate human judgment
    and shadows everything below it -- it does not fall through."""
    r = _recording(
        _ident(IdSource.MANUAL, verdict=Verdict.NO_ID, taxon_id=None),
        _ident(IdSource.EMT_GUANO, taxon_id=9),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.MANUAL
    assert best.verdict == Verdict.NO_ID


def test_noise_is_always_active_even_for_automatic_sources() -> None:
    r = _recording(
        _ident(IdSource.EMT_GUANO, verdict=Verdict.NOISE, taxon_id=None),
        _ident(IdSource.EMT_WAMD, taxon_id=3),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.EMT_GUANO
    assert best.verdict == Verdict.NOISE


def test_multiple_manual_species_claims_all_surface() -> None:
    r = _recording(
        _ident(IdSource.MANUAL, taxon_id=1),
        _ident(IdSource.MANUAL, taxon_id=2),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.is_multi
    assert best.taxon_ids == frozenset({1, 2})


def test_single_claim_is_not_multi() -> None:
    r = _recording(_ident(IdSource.EMT_GUANO, taxon_id=1))

    best = current_best_identification(r)

    assert best is not None
    assert not best.is_multi
    assert best.taxon_ids == frozenset({1})


def test_primary_of_multiple_manual_claims_is_the_first_added() -> None:
    r = _recording(
        _ident(
            IdSource.MANUAL,
            taxon_id=1,
            first_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        _ident(
            IdSource.MANUAL,
            taxon_id=2,
            first_seen_at=datetime(2026, 6, 1, tzinfo=UTC),
        ),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.taxon_id == 1


def test_current_identification_from_matches_wraps_a_list() -> None:
    ident = _ident(IdSource.EMT_GUANO, taxon_id=1)

    wrapped = CurrentIdentification.from_matches([ident])

    assert wrapped.claims == (ident,)
    assert wrapped.primary is ident


def test_recording_headline_prefers_the_resolved_taxon() -> None:
    taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
    best = CurrentIdentification.from_matches([_ident(IdSource.EMT_GUANO, taxon_id=1)])

    assert recording_headline(taxon, best) == "Pipistrellus pipistrellus"


def test_recording_headline_is_unidentified_with_no_best() -> None:
    assert recording_headline(None, None) == "unidentified"


def test_recording_headline_shows_the_raw_code_for_an_unmapped_species() -> None:
    ident = _ident(IdSource.EMT_FILENAME, taxon_id=None, verdict=Verdict.SPECIES)
    ident.raw_label = "EPTNIL"
    best = CurrentIdentification.from_matches([ident])

    assert recording_headline(None, best) == "EPTNIL (unmapped species)"


def test_recording_headline_falls_back_to_the_verdict_value_with_no_raw_label() -> None:
    ident = _ident(IdSource.EMT_FILENAME, taxon_id=None, verdict=Verdict.SPECIES)
    best = CurrentIdentification.from_matches([ident])

    assert recording_headline(None, best) == "species"


def test_recording_headline_shows_no_id_and_noise_verdicts_directly() -> None:
    no_id = CurrentIdentification.from_matches(
        [_ident(IdSource.MANUAL, taxon_id=None, verdict=Verdict.NO_ID)],
    )
    noise = CurrentIdentification.from_matches(
        [_ident(IdSource.EMT_FILENAME, taxon_id=None, verdict=Verdict.NOISE)],
    )

    assert recording_headline(None, no_id) == "no_id"
    assert recording_headline(None, noise) == "noise"


def test_recording_headline_shows_multiple_species_for_a_multi_claim_result() -> None:
    best = CurrentIdentification.from_matches(
        [
            _ident(IdSource.MANUAL, taxon_id=1),
            _ident(IdSource.MANUAL, taxon_id=2),
        ],
    )

    assert recording_headline(None, best) == "Multiple Species"

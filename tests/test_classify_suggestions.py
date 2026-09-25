from __future__ import annotations

from datetime import UTC, datetime

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.classify_suggestions import SuggestedTaxon, classify_suggestions
from fledermap.store.models import Identification, Recording, Taxon


def _recording(
    *identifications: Identification, snr_db: float | None = None
) -> Recording:
    r = Recording(
        audio_hash="a" * 64,
        path="x.wav",
        recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
        snr_db=snr_db,
    )
    r.identifications = list(identifications)
    return r


def _taxon(taxon_id: int, name: str) -> Taxon:
    t = Taxon(rank="species", scientific_name=name)
    t.id = taxon_id
    return t


def _ident(
    source: IdSource,
    *,
    taxon: Taxon | None = None,
    verdict: Verdict = Verdict.SPECIES,
) -> Identification:
    ident = Identification(
        source=source,
        verdict=verdict,
        taxon_id=taxon.id if taxon is not None else None,
        first_seen_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    ident.taxon = taxon
    return ident


def test_no_claims_and_no_snr_suggests_noise_only() -> None:
    r = _recording()

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0)

    assert len(suggestions) == 1
    assert suggestions[0].is_noise
    assert suggestions[0].taxa == ()


def test_automatic_species_claim_suggests_agree_with_classifiers() -> None:
    pip = _taxon(1, "Pipistrellus pipistrellus")
    r = _recording(_ident(IdSource.EMT_GUANO, taxon=pip))

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0)

    assert len(suggestions) == 1
    assert suggestions[0].is_noise is False
    assert suggestions[0].taxa == (
        SuggestedTaxon(id=1, scientific_name="Pipistrellus pipistrellus"),
    )
    assert "Pipistrellus pipistrellus" in suggestions[0].label


def test_low_snr_and_a_species_claim_both_suggest_at_once() -> None:
    pip = _taxon(1, "Pipistrellus pipistrellus")
    r = _recording(_ident(IdSource.EMT_GUANO, taxon=pip), snr_db=1.0)

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0)

    assert len(suggestions) == 2
    assert suggestions[0].is_noise is False  # Agree with classifiers sorts first
    assert suggestions[1].is_noise is True


def test_snr_exactly_at_threshold_is_not_low() -> None:
    r = _recording(snr_db=2.0)

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0)

    # Still suggested, but ONLY via the "nothing found" branch, not the SNR branch --
    # this test exists to pin the boundary; test_no_claims_and_no_snr_suggests_noise_only
    # already covers the "nothing found" branch producing the same single Noise result, so
    # a passing result here (rather than two Noise-shaped entries or a crash) confirms the
    # `<` comparison, not `<=`.
    assert len(suggestions) == 1
    assert suggestions[0].is_noise


def test_unmapped_automatic_species_claim_suggests_nothing() -> None:
    """An unresolved code has no taxon to turn into a chip -- and since automatic_best's
    verdict is SPECIES (not None/NOISE), the Noise heuristic doesn't fire either. No
    suggestion is better than a broken one here."""
    r = _recording(_ident(IdSource.EMT_GUANO, taxon=None))

    assert classify_suggestions(r, noise_snr_threshold_db=2.0) == []


def test_manual_claim_does_not_suppress_the_automatic_suggestion() -> None:
    """classify_suggestions never looks at MANUAL state -- that filtering happens entirely
    client-side against whatever is currently staged."""
    pip = _taxon(1, "Pipistrellus pipistrellus")
    other = _taxon(2, "Myotis mystacinus")
    r = _recording(
        _ident(IdSource.MANUAL, taxon=other),
        _ident(IdSource.EMT_GUANO, taxon=pip),
    )

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0)

    assert len(suggestions) == 1
    assert suggestions[0].taxa[0].id == 1


def test_multi_species_claim_produces_one_suggestion_with_both_taxa_sorted_by_name() -> (
    None
):
    a = _taxon(1, "Pipistrellus pipistrellus")
    b = _taxon(2, "Myotis mystacinus")
    r = _recording(
        _ident(IdSource.BATDETECT2, taxon=a),
        _ident(IdSource.BATDETECT2, taxon=b),
    )

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0)

    assert len(suggestions) == 1
    assert [t.scientific_name for t in suggestions[0].taxa] == [
        "Myotis mystacinus",
        "Pipistrellus pipistrellus",
    ]


def test_pool_cap_truncates_to_the_highest_priority_candidates() -> None:
    pip = _taxon(1, "Pipistrellus pipistrellus")
    r = _recording(_ident(IdSource.EMT_GUANO, taxon=pip), snr_db=1.0)

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0, pool_cap=1)

    assert len(suggestions) == 1
    assert suggestions[0].is_noise is False

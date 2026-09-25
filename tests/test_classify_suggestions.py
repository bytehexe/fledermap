from __future__ import annotations

from datetime import UTC, datetime

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.classify_suggestions import (
    ClassifySuggestion,
    SuggestedTaxon,
    classify_suggestions,
)
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
    assert suggestions[0].label == "Noise"


def test_automatic_species_claim_suggests_agree_with_classifiers() -> None:
    pip = _taxon(1, "Pipistrellus pipistrellus")
    r = _recording(_ident(IdSource.EMT_GUANO, taxon=pip))

    # pool_cap=1 isolates heuristic A on its own -- with room to spare, the pool would
    # otherwise also pick up the unconditional Noise-if-room fallback (spec part (b) §7),
    # which is covered separately by test_noise_is_added_when_room_left_and_not_already_present.
    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0, pool_cap=1)

    assert len(suggestions) == 1
    assert suggestions[0].is_noise is False
    assert suggestions[0].taxa == (
        SuggestedTaxon(id=1, scientific_name="Pipistrellus pipistrellus"),
    )
    # A single species stays full -- only a multi-species label gets abbreviated.
    assert suggestions[0].label == "Pipistrellus pipistrellus"


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


def test_unmapped_automatic_species_claim_falls_back_to_noise_only() -> None:
    """An unresolved code has no taxon to turn into a chip -- and since automatic_best's
    verdict is SPECIES (not None/NOISE), part (a)'s evidence-driven Noise heuristic doesn't
    fire either. The pool is otherwise empty with room to spare, so the unconditional
    Noise-if-room fallback (spec part (b) §7) fires and the pool ends up holding just Noise,
    not nothing."""
    r = _recording(_ident(IdSource.EMT_GUANO, taxon=None))

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0)

    assert len(suggestions) == 1
    assert suggestions[0].is_noise


def test_manual_claim_does_not_suppress_the_automatic_suggestion() -> None:
    """classify_suggestions never looks at MANUAL state -- that filtering happens entirely
    client-side against whatever is currently staged."""
    pip = _taxon(1, "Pipistrellus pipistrellus")
    other = _taxon(2, "Myotis mystacinus")
    r = _recording(
        _ident(IdSource.MANUAL, taxon=other),
        _ident(IdSource.EMT_GUANO, taxon=pip),
    )

    # pool_cap=1 isolates heuristic A -- see the comment in
    # test_automatic_species_claim_suggests_agree_with_classifiers.
    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0, pool_cap=1)

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

    # pool_cap=1 isolates heuristic A -- see the comment in
    # test_automatic_species_claim_suggests_agree_with_classifiers.
    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0, pool_cap=1)

    assert len(suggestions) == 1
    assert [t.scientific_name for t in suggestions[0].taxa] == [
        "Myotis mystacinus",
        "Pipistrellus pipistrellus",
    ]
    # 2+ taxa -> genus-abbreviated label, same convention as annotation_geometry.js's
    # abbreviateScientificName (the Obsidian sketch's genus-abbreviation convention).
    assert suggestions[0].label == "M. mystacinus, P. pipistrellus"


def test_multi_species_label_leaves_a_one_word_group_taxon_name_unabbreviated() -> None:
    """A genus/group taxon (e.g. "Myotis" alone, no species epithet) has no second word to
    abbreviate against -- must pass through unchanged, same as the JS helper it mirrors."""
    group = _taxon(1, "Myotis")
    pip = _taxon(2, "Pipistrellus pipistrellus")
    r = _recording(
        _ident(IdSource.BATDETECT2, taxon=group),
        _ident(IdSource.BATDETECT2, taxon=pip),
    )

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0)

    assert suggestions[0].label == "Myotis, P. pipistrellus"


def test_pool_cap_truncates_to_the_highest_priority_candidates() -> None:
    pip = _taxon(1, "Pipistrellus pipistrellus")
    r = _recording(_ident(IdSource.EMT_GUANO, taxon=pip), snr_db=1.0)

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0, pool_cap=1)

    assert len(suggestions) == 1
    assert suggestions[0].is_noise is False


def test_mined_candidates_are_merged_in_and_sorted_by_their_own_priority() -> None:
    r = _recording()
    mined = [
        ClassifySuggestion(
            taxa=(SuggestedTaxon(id=1, scientific_name="Myotis mystacinus"),),
            is_noise=False,
            label="Myotis mystacinus",
            priority=0.4,
        ),
        ClassifySuggestion(
            taxa=(SuggestedTaxon(id=2, scientific_name="Nyctalus noctula"),),
            is_noise=False,
            label="Nyctalus noctula",
            priority=0.2,
        ),
    ]

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0, mined=mined)

    # Noise (part a's evidence-driven heuristic, priority 1.0) sorts first, then the two
    # mined candidates by their own priority.
    assert [s.label for s in suggestions] == [
        "Noise",
        "Myotis mystacinus",
        "Nyctalus noctula",
    ]


def test_mined_candidates_never_outrank_part_a_heuristics() -> None:
    pip = _taxon(1, "Pipistrellus pipistrellus")
    r = _recording(_ident(IdSource.EMT_GUANO, taxon=pip))
    # A mined candidate with an implausibly high raw confidence still can't outrank "Agree
    # with classifiers" (priority 2.0): the *0.5 scale caps it at 0.5.
    mined = [
        ClassifySuggestion(
            taxa=(SuggestedTaxon(id=9, scientific_name="Species Z"),),
            is_noise=False,
            label="Species Z",
            priority=0.5,
        ),
    ]

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0, mined=mined)

    assert suggestions[0].label == "Pipistrellus pipistrellus"


def test_noise_is_added_when_room_left_and_not_already_present() -> None:
    """Spec §7: after part (a)'s heuristics and mined candidates fill fewer than pool_cap
    slots, and none of them is already Noise, append one Noise candidate unconditionally."""
    pip = _taxon(1, "Pipistrellus pipistrellus")
    r = _recording(_ident(IdSource.EMT_GUANO, taxon=pip))  # no low SNR, something found
    mined = [
        ClassifySuggestion(
            taxa=(SuggestedTaxon(id=2, scientific_name="Myotis mystacinus"),),
            is_noise=False,
            label="Myotis mystacinus",
            priority=0.3,
        ),
    ]

    suggestions = classify_suggestions(
        r, noise_snr_threshold_db=2.0, mined=mined, pool_cap=8
    )

    assert any(s.is_noise for s in suggestions)
    assert suggestions[-1].is_noise  # lowest priority -> sorts last


def test_noise_fallback_never_duplicates_an_existing_noise_entry() -> None:
    r = _recording(
        snr_db=1.0
    )  # low SNR -- part (a)'s own Noise heuristic already fires

    suggestions = classify_suggestions(r, noise_snr_threshold_db=2.0, pool_cap=8)

    assert sum(1 for s in suggestions if s.is_noise) == 1


def test_noise_fallback_does_not_fire_when_pool_is_already_at_cap() -> None:
    pip = _taxon(1, "Pipistrellus pipistrellus")
    r = _recording(_ident(IdSource.EMT_GUANO, taxon=pip))
    mined = [
        ClassifySuggestion(
            taxa=(SuggestedTaxon(id=i, scientific_name=f"Species {i}"),),
            is_noise=False,
            label=f"Species {i}",
            priority=0.1,
        )
        for i in range(2, 3)
    ]

    suggestions = classify_suggestions(
        r, noise_snr_threshold_db=2.0, mined=mined, pool_cap=1
    )

    assert len(suggestions) == 1
    assert not suggestions[0].is_noise

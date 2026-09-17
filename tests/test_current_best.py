from __future__ import annotations

from datetime import UTC, datetime

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.current_best import (
    CurrentIdentification,
    current_best_identification,
    identification_label,
    identification_status,
    recording_headline,
    sort_identifications_by_precedence,
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
    first_seen_at: datetime = datetime(2026, 8, 25, tzinfo=UTC),
) -> Identification:
    return Identification(
        source=source,
        verdict=verdict,
        taxon_id=taxon_id,
        first_seen_at=first_seen_at,
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


def test_no_identifications_returns_none() -> None:
    r = _recording()

    assert current_best_identification(r) is None


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


def test_fledermap_noise_wins_when_nothing_else_identified_the_recording() -> None:
    r = _recording(
        _ident(IdSource.FLEDERMAP_NOISE, verdict=Verdict.NOISE, taxon_id=None),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.FLEDERMAP_NOISE
    assert best.verdict == Verdict.NOISE


def test_fledermap_noise_loses_to_a_real_species_claim_from_any_other_source() -> None:
    """Lowest precedence: even EMT_FILENAME, itself the lowest of the EMT
    sources, still beats fledermap.noise."""
    r = _recording(
        _ident(IdSource.FLEDERMAP_NOISE, verdict=Verdict.NOISE, taxon_id=None),
        _ident(IdSource.EMT_FILENAME, taxon_id=5),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.EMT_FILENAME


def test_fledermap_noise_no_id_is_passive_like_any_other_automatic_no_id() -> None:
    r = _recording(
        _ident(IdSource.FLEDERMAP_NOISE, verdict=Verdict.NO_ID, taxon_id=None),
    )

    assert current_best_identification(r) is None


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


def test_identification_label_shows_code_and_scientific_name_when_both_exist() -> None:
    taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
    ident = _ident(IdSource.EMT_GUANO, taxon_id=1)
    ident.raw_label = "PIPPIP"

    assert identification_label(ident, taxon) == "PIPPIP (Pipistrellus pipistrellus)"


def test_identification_label_shows_only_the_scientific_name_with_no_raw_label() -> (
    None
):
    # The MANUAL case this was added to fix: a manual classification has a
    # resolved taxon but no raw_label of its own.
    taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
    ident = _ident(IdSource.MANUAL, taxon_id=1)

    assert identification_label(ident, taxon) == "Pipistrellus pipistrellus"


def test_identification_label_falls_back_to_the_raw_label_without_a_taxon() -> None:
    ident = _ident(IdSource.EMT_FILENAME, taxon_id=None, verdict=Verdict.SPECIES)
    ident.raw_label = "EPTNIL"

    assert identification_label(ident, None) == "EPTNIL"


def test_identification_label_falls_back_to_the_verdict_value_with_neither() -> None:
    ident = _ident(IdSource.EMT_FILENAME, taxon_id=None, verdict=Verdict.SPECIES)

    assert identification_label(ident, None) == "species"


def test_identification_label_shows_no_id_and_noise_verdicts_directly() -> None:
    no_id = _ident(IdSource.MANUAL, taxon_id=None, verdict=Verdict.NO_ID)
    noise = _ident(IdSource.EMT_FILENAME, taxon_id=None, verdict=Verdict.NOISE)

    assert identification_label(no_id, None) == "no_id"
    assert identification_label(noise, None) == "noise"


def test_identification_status_current_for_the_winning_claim() -> None:
    ident = _ident(IdSource.EMT_GUANO, taxon_id=1)
    r = _recording(ident)
    best = current_best_identification(r)

    assert identification_status(ident, best) == "current"


def test_identification_status_passive_for_skipped_automatic_no_id() -> None:
    passive = _ident(IdSource.EMT_GUANO, verdict=Verdict.NO_ID, taxon_id=None)
    winner = _ident(IdSource.EMT_WAMD, taxon_id=1)
    r = _recording(passive, winner)
    best = current_best_identification(r)

    assert identification_status(passive, best) == "passive"
    assert identification_status(winner, best) == "current"


def test_identification_status_passive_even_when_nothing_else_wins() -> None:
    """A passive automatic NO_ID is "passive" regardless of the overall
    outcome -- describes the row's own status, not the final result."""
    only = _ident(IdSource.EMT_GUANO, verdict=Verdict.NO_ID, taxon_id=None)
    r = _recording(only)
    best = current_best_identification(r)  # None

    assert identification_status(only, best) == "passive"


def test_identification_status_shadowed_for_a_lower_precedence_real_claim() -> None:
    winner = _ident(IdSource.MANUAL, taxon_id=1)
    loser = _ident(IdSource.EMT_GUANO, taxon_id=2)
    r = _recording(winner, loser)
    best = current_best_identification(r)

    assert identification_status(loser, best) == "shadowed"


def test_identification_status_every_claim_of_a_multi_species_winner_is_current() -> (
    None
):
    a = _ident(IdSource.MANUAL, taxon_id=1)
    b = _ident(IdSource.MANUAL, taxon_id=2)
    r = _recording(a, b)
    best = current_best_identification(r)

    assert identification_status(a, best) == "current"
    assert identification_status(b, best) == "current"


def test_sort_identifications_by_precedence_puts_the_highest_precedence_source_first() -> (
    None
):
    """emt.guano outranks emt.wamd in `_PRECEDENCE` regardless of which one
    actually wins the precedence walk (identification_status decides that
    separately) -- display order is purely about source rank."""
    wamd = _ident(IdSource.EMT_WAMD)
    guano = _ident(IdSource.EMT_GUANO)

    assert sort_identifications_by_precedence([wamd, guano]) == [guano, wamd]


def test_sort_identifications_by_precedence_is_stable_for_the_same_source() -> None:
    """A multi-species MANUAL result has more than one claim from the same
    source -- their relative order must survive the sort untouched."""
    first = _ident(IdSource.MANUAL, taxon_id=1)
    second = _ident(IdSource.MANUAL, taxon_id=2)

    assert sort_identifications_by_precedence([first, second]) == [first, second]


def test_sort_identifications_by_precedence_puts_an_unranked_source_last() -> None:
    """A source defined in `IdSource` but not yet wired into `_PRECEDENCE`
    (e.g. KALEIDOSCOPE, which nothing writes yet) must not crash the sort."""
    manual = _ident(IdSource.MANUAL)
    future = _ident(IdSource.KALEIDOSCOPE)

    assert sort_identifications_by_precedence([future, manual]) == [manual, future]


def test_batdetect2_outranks_fledermap_noise_but_not_emt_sources() -> None:
    manual = _ident(IdSource.MANUAL)
    emt_filename = _ident(IdSource.EMT_FILENAME)
    batdetect2 = _ident(IdSource.BATDETECT2)
    noise = _ident(IdSource.FLEDERMAP_NOISE)

    assert sort_identifications_by_precedence(
        [noise, batdetect2, emt_filename, manual],
    ) == [manual, emt_filename, batdetect2, noise]


def test_batdetect2_outranks_battybirdnet_which_outranks_fledermap_noise() -> None:
    manual = _ident(IdSource.MANUAL)
    emt_filename = _ident(IdSource.EMT_FILENAME)
    batdetect2 = _ident(IdSource.BATDETECT2)
    battybirdnet = _ident(IdSource.BATTYBIRDNET)
    noise = _ident(IdSource.FLEDERMAP_NOISE)

    assert sort_identifications_by_precedence(
        [noise, battybirdnet, batdetect2, emt_filename, manual],
    ) == [manual, emt_filename, batdetect2, battybirdnet, noise]


def test_emt_manual_no_id_is_active_and_shadows_a_lower_precedence_species() -> None:
    """Unlike before this change, EMT_MANUAL's own NO_ID now wins outright (strong NoID),
    matching MANUAL -- see spec 2026-09-17-fledermap-classifier-interaction-design.md §2/§3.1."""
    r = _recording(
        _ident(IdSource.EMT_MANUAL, verdict=Verdict.NO_ID, taxon_id=None),
        _ident(IdSource.EMT_GUANO, taxon_id=9),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.EMT_MANUAL
    assert best.verdict == Verdict.NO_ID


def test_multi_support_override_when_higher_precedence_species_is_contained() -> None:
    """EMT_GUANO (plain, higher precedence) calls PIPPIP; BATDETECT2 (multi_support, lower
    precedence) calls {PIPPIP, MYODAU}. Since PIPPIP is in BATDETECT2's set, the richer
    multi-species result wins instead of narrowing to EMT_GUANO's single claim."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=2),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.is_multi
    assert best.taxon_ids == frozenset({1, 2})
    assert all(c.source == IdSource.BATDETECT2 for c in best.claims)


def test_no_override_when_higher_precedence_species_is_not_contained() -> None:
    """EMT_GUANO calls a taxon BATDETECT2's set doesn't contain -- a real disagreement, so
    EMT_GUANO's claim stands untouched (the review-flag rewrite in Task 3 is what surfaces this
    as "Classifiers disagree")."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, taxon_id=3),
        _ident(IdSource.BATDETECT2, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=2),
    )

    best = current_best_identification(r)

    assert best is not None
    assert not best.is_multi
    assert best.primary.source == IdSource.EMT_GUANO
    assert best.primary.taxon_id == 3


def test_multi_support_override_skips_over_a_weak_no_id_source_in_between() -> None:
    """EMT_WAMD sits between EMT_GUANO and BATDETECT2 in precedence and has only an automatic
    NO_ID claim (skipped, "weak") -- the peek must not stop there and must still reach
    BATDETECT2 below it."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, taxon_id=1),
        _ident(IdSource.EMT_WAMD, verdict=Verdict.NO_ID, taxon_id=None),
        _ident(IdSource.BATDETECT2, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=2),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.is_multi
    assert best.taxon_ids == frozenset({1, 2})


def test_no_peek_when_the_winner_is_manual() -> None:
    """A human's call is never second-guessed by a multi_support classifier below it, even when
    that classifier's set would have contained the human's taxon."""
    r = _recording(
        _ident(IdSource.EMT_MANUAL, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=2),
    )

    best = current_best_identification(r)

    assert best is not None
    assert not best.is_multi
    assert best.primary.source == IdSource.EMT_MANUAL
    assert best.primary.taxon_id == 1


def test_no_peek_when_the_winner_verdict_is_noise() -> None:
    """A multi_support source's set is always species-only or {NO_ID} (spec §5), so a NOISE
    winner can never be contained in one -- the peek is skipped as pure overhead, and the NOISE
    verdict stands."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, verdict=Verdict.NOISE, taxon_id=None),
        _ident(IdSource.BATDETECT2, taxon_id=1),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.primary.source == IdSource.EMT_GUANO
    assert best.verdict == Verdict.NOISE


def test_multi_support_winner_returned_directly_without_peeking_further() -> None:
    """When the FIRST source with live claims is itself multi_support, it wins outright --
    nothing below it (even another multi_support source) is ever consulted."""
    r = _recording(
        _ident(IdSource.BATDETECT2, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=2),
        _ident(IdSource.BATTYBIRDNET, taxon_id=3),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.is_multi
    assert best.taxon_ids == frozenset({1, 2})


def test_multi_support_override_also_works_with_battybirdnet() -> None:
    """The containment override is not hardcoded to BATDETECT2 -- BATTYBIRDNET (the other
    multi_support automatic classifier) triggers it the same way."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, taxon_id=1),
        _ident(IdSource.BATTYBIRDNET, taxon_id=1),
        _ident(IdSource.BATTYBIRDNET, taxon_id=2),
    )

    best = current_best_identification(r)

    assert best is not None
    assert best.is_multi
    assert best.taxon_ids == frozenset({1, 2})
    assert all(c.source == IdSource.BATTYBIRDNET for c in best.claims)


def test_identification_status_shadowed_for_a_species_overridden_by_containment() -> (
    None
):
    """EMT_GUANO's own claim loses to the multi-support override -- it must show as
    "shadowed" (a real claim that lost), not "current" or "passive"."""
    guano = _ident(IdSource.EMT_GUANO, taxon_id=1)
    batdetect2_a = _ident(IdSource.BATDETECT2, taxon_id=1)
    batdetect2_b = _ident(IdSource.BATDETECT2, taxon_id=2)
    r = _recording(guano, batdetect2_a, batdetect2_b)
    best = current_best_identification(r)

    assert identification_status(guano, best) == "shadowed"
    assert identification_status(batdetect2_a, best) == "current"
    assert identification_status(batdetect2_b, best) == "current"


def test_multi_support_peek_stops_at_nearest_source_even_if_it_does_not_contain() -> (
    None
):
    """The peek must stop at the NEAREST multi_support source with live claims, even when it
    doesn't contain the winner's taxon -- it must NOT keep scanning past it to a further
    multi_support source that would have contained it. A real disagreement with the nearest
    one wins; the farther one is never consulted."""
    r = _recording(
        _ident(IdSource.EMT_GUANO, taxon_id=3),
        _ident(IdSource.BATDETECT2, taxon_id=1),
        _ident(IdSource.BATDETECT2, taxon_id=2),
        _ident(IdSource.BATTYBIRDNET, taxon_id=3),
    )

    best = current_best_identification(r)

    assert best is not None
    assert not best.is_multi
    assert best.primary.source == IdSource.EMT_GUANO
    assert best.primary.taxon_id == 3

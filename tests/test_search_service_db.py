from __future__ import annotations

from datetime import UTC, datetime

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import AnnotationSource, IdSource, Verdict
from fledermap.services.search import (
    MAX_SEARCH_RESULTS,
    ParsedQuery,
    _resolve_species_value,
    search_recordings,
)
from fledermap.store.models import (
    Annotation,
    Identification,
    Recording,
    Site,
    Taxon,
    TaxonCode,
)

pytestmark = pytest.mark.db


def _taxon(session: OrmSession, *, scientific_name: str) -> Taxon:
    taxon = Taxon(rank="species", scientific_name=scientific_name)
    session.add(taxon)
    session.flush()
    return taxon


def test_resolves_by_code(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session, scientific_name="Pipistrellus pipistrellus")
        session.add(TaxonCode(source="wa", code="PIPPIP", taxon_id=taxon.id))
        session.commit()

        assert _resolve_species_value(session, "PIPPIP") == taxon.id


def test_resolves_by_full_scientific_name_case_insensitive(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session, scientific_name="Pipistrellus pipistrellus")
        session.commit()

        assert _resolve_species_value(session, "pipistrellus PIPISTRELLUS") == taxon.id


def test_resolves_by_abbreviated_genus_form(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = _taxon(session, scientific_name="Pipistrellus pipistrellus")
        session.commit()

        assert _resolve_species_value(session, "P. pipistrellus") == taxon.id
        assert _resolve_species_value(session, "p. PIPISTRELLUS") == taxon.id


def test_ambiguous_abbreviated_form_resolves_to_none(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _taxon(session, scientific_name="Pipistrellus pygmaeus")
        _taxon(session, scientific_name="Plecotus pygmaeus")
        session.commit()

        assert _resolve_species_value(session, "P. pygmaeus") is None


def test_unrecognized_value_resolves_to_none(engine: Engine) -> None:
    with OrmSession(engine) as session:
        session.commit()
        assert _resolve_species_value(session, "not a real species") is None


def test_abbreviated_form_wildcard_characters_in_epithet_are_escaped(
    engine: Engine,
) -> None:
    """A `%`/`_` in the epithet portion of an abbreviated-form query must be matched literally,
    never as a SQL LIKE wildcard -- otherwise a query could resolve to a taxon it has no business
    matching, violating "a wrong match is worse than a missing one"."""
    with OrmSession(engine) as session:
        # If `_` in the query below were treated as a wildcard instead of a literal underscore,
        # it would match this taxon's differing letter and resolve incorrectly.
        _taxon(session, scientific_name="Pipistrellus pipXstrellus")
        session.commit()

        assert _resolve_species_value(session, "P. pip_strellus") is None


def test_code_shared_by_two_taxa_across_sources_resolves_to_none(
    engine: Engine,
) -> None:
    """Review Focus item: a code spelled identically under two different code sources, pointing
    at two different taxa, must not silently pick one."""
    with OrmSession(engine) as session:
        a = _taxon(session, scientific_name="Pipistrellus pipistrellus")
        b = _taxon(session, scientific_name="Plecotus auritus")
        session.add(TaxonCode(source="wa", code="AMBIG", taxon_id=a.id))
        session.add(TaxonCode(source="nabat", code="AMBIG", taxon_id=b.id))
        session.commit()

        assert _resolve_species_value(session, "AMBIG") is None


def _recording(
    session: OrmSession,
    *,
    audio_hash: str,
    path: str,
    site_id: int | None = None,
    favourite: bool = False,
    flagged: bool = False,
    note: str | None = None,
) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=path,
        recorded_at=datetime(2026, 9, 25, tzinfo=UTC),
        site_id=site_id,
        favourite=favourite,
        flagged_for_review=flagged,
        note=note,
    )
    session.add(r)
    session.flush()
    return r


def test_free_text_matches_filename(engine: Engine) -> None:
    with OrmSession(engine) as session:
        target = _recording(
            session, audio_hash="a" * 64, path="PIPPIP_20260914_220508.wav"
        )
        _recording(session, audio_hash="b" * 64, path="EPTSER_20260914_220508.wav")
        session.commit()

        page = search_recordings(session, ParsedQuery(free_text_terms=["PIPPIP"]))

    assert [r.id for r in page.rows] == [target.id]


def test_free_text_matches_note(engine: Engine) -> None:
    with OrmSession(engine) as session:
        target = _recording(
            session,
            audio_hash="a" * 64,
            path="a.wav",
            note="possible roost nearby",
        )
        _recording(session, audio_hash="b" * 64, path="b.wav")
        session.commit()

        page = search_recordings(session, ParsedQuery(free_text_terms=["roost"]))

    assert [r.id for r in page.rows] == [target.id]


def test_hash_tag_filters_to_the_exact_tag(engine: Engine) -> None:
    with OrmSession(engine) as session:
        target = _recording(
            session, audio_hash="a" * 64, path="a.wav", note="#buzz here"
        )
        _recording(session, audio_hash="b" * 64, path="b.wav", note="no tags here")
        session.commit()

        page = search_recordings(session, ParsedQuery(tags=["buzz"]))

    assert [r.id for r in page.rows] == [target.id]


def test_hash_tag_does_not_match_a_longer_tag_it_is_a_prefix_of(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _recording(session, audio_hash="a" * 64, path="a.wav", note="#device:emt2")
        session.commit()

        page = search_recordings(session, ParsedQuery(tags=["device"]))

    assert page.rows == []


def test_free_text_matches_label_annotation(engine: Engine) -> None:
    with OrmSession(engine) as session:
        target = _recording(session, audio_hash="a" * 64, path="a.wav")
        _recording(session, audio_hash="b" * 64, path="b.wav")
        session.add(
            Annotation(
                recording_id=target.id,
                source=AnnotationSource.LABEL,
                time_start_s=0.0,
                literal="feeding buzz here",
                created_at=datetime(2026, 9, 25, tzinfo=UTC),
            ),
        )
        session.commit()

        page = search_recordings(session, ParsedQuery(free_text_terms=["buzz"]))

    assert [r.id for r in page.rows] == [target.id]


def test_free_text_matches_site_name(engine: Engine) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=10.0,
            recording_count=1,
            first_at=datetime(2026, 9, 25, tzinfo=UTC),
            last_at=datetime(2026, 9, 25, tzinfo=UTC),
            name="Kleiner Wald",
        )
        session.add(site)
        session.flush()
        target = _recording(session, audio_hash="a" * 64, path="a.wav", site_id=site.id)
        _recording(session, audio_hash="b" * 64, path="b.wav")
        session.commit()

        page = search_recordings(session, ParsedQuery(free_text_terms=["Kleiner"]))

    assert [r.id for r in page.rows] == [target.id]


def test_free_text_matches_species_name(engine: Engine) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        target = _recording(session, audio_hash="a" * 64, path="a.wav")
        _recording(session, audio_hash="b" * 64, path="b.wav")
        session.add(
            Identification(
                recording_id=target.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=datetime(2026, 9, 25, tzinfo=UTC),
            ),
        )
        session.commit()

        page = search_recordings(session, ParsedQuery(free_text_terms=["pipistrellus"]))

    assert [r.id for r in page.rows] == [target.id]


def test_ilike_special_characters_are_escaped(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _recording(session, audio_hash="a" * 64, path="fifty%off.wav")
        no_match = _recording(session, audio_hash="b" * 64, path="fiftyXoff.wav")
        session.commit()

        page = search_recordings(session, ParsedQuery(free_text_terms=["fifty%off"]))

    assert [r.id for r in page.rows] != [no_match.id]
    assert len(page.rows) == 1


def test_is_labeled_filters_to_recordings_with_a_label(engine: Engine) -> None:
    with OrmSession(engine) as session:
        labeled = _recording(session, audio_hash="a" * 64, path="a.wav")
        _recording(session, audio_hash="b" * 64, path="b.wav")
        session.add(
            Annotation(
                recording_id=labeled.id,
                source=AnnotationSource.LABEL,
                time_start_s=0.0,
                literal="note",
                created_at=datetime(2026, 9, 25, tzinfo=UTC),
            ),
        )
        session.commit()

        page = search_recordings(session, ParsedQuery(is_labeled=True))

    assert [r.id for r in page.rows] == [labeled.id]


def test_is_flagged_reuses_filtered_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        flagged = _recording(session, audio_hash="a" * 64, path="a.wav", flagged=True)
        _recording(session, audio_hash="b" * 64, path="b.wav")
        session.commit()

        page = search_recordings(session, ParsedQuery(is_flagged=True))

    assert [r.id for r in page.rows] == [flagged.id]


def test_annotation_field_reuses_filtered_recordings_source(engine: Engine) -> None:
    with OrmSession(engine) as session:
        target = _recording(session, audio_hash="a" * 64, path="a.wav")
        _recording(session, audio_hash="b" * 64, path="b.wav")
        session.add(
            Identification(
                recording_id=target.id,
                source=IdSource.BATDETECT2,
                verdict=Verdict.SPECIES,
                taxon_id=None,
                raw_label="PIPPIP",
                first_seen_at=datetime(2026, 9, 25, tzinfo=UTC),
            ),
        )
        session.commit()

        page = search_recordings(
            session,
            ParsedQuery(annotation_source=IdSource.BATDETECT2),
        )

    assert [r.id for r in page.rows] == [target.id]


def test_species_field_resolves_to_taxon_and_narrows_results(engine: Engine) -> None:
    """The `species:` field's happy path at the `search_recordings` level -- a value that
    `_resolve_species_value` resolves to a real taxon (via the code pass-through) must actually
    narrow the result set to recordings identified as that taxon, not just avoid the
    falls-back-to-free-text warning."""
    with OrmSession(engine) as session:
        taxon = _taxon(session, scientific_name="Pipistrellus pipistrellus")
        session.add(TaxonCode(source="wa", code="PIPPIP", taxon_id=taxon.id))
        target = _recording(session, audio_hash="a" * 64, path="a.wav")
        other = _recording(session, audio_hash="b" * 64, path="b.wav")
        session.add(
            Identification(
                recording_id=target.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=datetime(2026, 9, 25, tzinfo=UTC),
            ),
        )
        session.commit()

        page = search_recordings(session, ParsedQuery(species_value="PIPPIP"))

    assert [r.id for r in page.rows] == [target.id]
    assert page.rows[0].id != other.id
    assert page.warnings == []


def test_site_field_filters_to_matching_site_name(engine: Engine) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=10.0,
            recording_count=1,
            first_at=datetime(2026, 9, 25, tzinfo=UTC),
            last_at=datetime(2026, 9, 25, tzinfo=UTC),
            name="Kleiner Wald",
        )
        other_site = Site(
            centroid=WKTElement("POINT(11 51)", srid=4326),
            radius_m=10.0,
            recording_count=1,
            first_at=datetime(2026, 9, 25, tzinfo=UTC),
            last_at=datetime(2026, 9, 25, tzinfo=UTC),
            name="Grosser Wald",
        )
        session.add_all([site, other_site])
        session.flush()
        target = _recording(session, audio_hash="a" * 64, path="a.wav", site_id=site.id)
        _recording(session, audio_hash="b" * 64, path="b.wav", site_id=other_site.id)
        session.commit()

        page = search_recordings(session, ParsedQuery(site_name="Kleiner Wald"))

    assert [r.id for r in page.rows] == [target.id]


def test_explicit_verdict_narrows_results(engine: Engine) -> None:
    with OrmSession(engine) as session:
        species_rec = _recording(session, audio_hash="a" * 64, path="a.wav")
        noise_rec = _recording(session, audio_hash="b" * 64, path="b.wav")
        taxon = _taxon(session, scientific_name="Pipistrellus pipistrellus")
        session.add(
            Identification(
                recording_id=species_rec.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=datetime(2026, 9, 25, tzinfo=UTC),
            ),
        )
        session.add(
            Identification(
                recording_id=noise_rec.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.NOISE,
                taxon_id=None,
                first_seen_at=datetime(2026, 9, 25, tzinfo=UTC),
            ),
        )
        session.commit()

        page = search_recordings(session, ParsedQuery(verdict=Verdict.NOISE))

    assert [r.id for r in page.rows] == [noise_rec.id]


def test_is_favourite_filters_to_favourited_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        favourited = _recording(
            session, audio_hash="a" * 64, path="a.wav", favourite=True
        )
        _recording(session, audio_hash="b" * 64, path="b.wav")
        session.commit()

        page = search_recordings(session, ParsedQuery(is_favourite=True))

    assert [r.id for r in page.rows] == [favourited.id]


def test_two_conditions_and_combine_excluding_a_partial_match(engine: Engine) -> None:
    """Genuine AND-combination across two distinct conditions (a free-text term and a field) --
    a recording matching only one of the two must be excluded from the results."""
    with OrmSession(engine) as session:
        both = _recording(session, audio_hash="a" * 64, path="a.wav", flagged=True)
        session.add(
            Annotation(
                recording_id=both.id,
                source=AnnotationSource.LABEL,
                time_start_s=0.0,
                literal="feeding buzz here",
                created_at=datetime(2026, 9, 25, tzinfo=UTC),
            ),
        )
        # Matches the free-text term but is not flagged.
        text_only = _recording(session, audio_hash="b" * 64, path="b.wav")
        session.add(
            Annotation(
                recording_id=text_only.id,
                source=AnnotationSource.LABEL,
                time_start_s=0.0,
                literal="feeding buzz here too",
                created_at=datetime(2026, 9, 25, tzinfo=UTC),
            ),
        )
        # Flagged, but its comment doesn't match the free-text term.
        _recording(session, audio_hash="c" * 64, path="c.wav", flagged=True)
        session.commit()

        page = search_recordings(
            session,
            ParsedQuery(free_text_terms=["buzz"], is_flagged=True),
        )

    assert [r.id for r in page.rows] == [both.id]


def test_unresolved_species_value_falls_back_to_free_text_with_warning(
    engine: Engine,
) -> None:
    with OrmSession(engine) as session:
        # A recording whose *filename* happens to contain the literal unresolved species text --
        # proves the value became a free-text term rather than being dropped or forcing zero rows.
        target = _recording(
            session, audio_hash="a" * 64, path="totallymadeupspecies.wav"
        )
        session.commit()

        page = search_recordings(
            session,
            ParsedQuery(species_value="totallymadeupspecies"),
        )

    assert [r.id for r in page.rows] == [target.id]
    assert page.warnings == [
        "No unique species match for 'totallymadeupspecies' — searched as text instead.",
    ]


def test_no_query_returns_everything_filtered_recordings_would(engine: Engine) -> None:
    """Review Focus item: an empty ParsedQuery (no free text, no fields) must not crash on an
    empty terms loop or empty token list, and must return the same set filtered_recordings()
    alone would."""
    with OrmSession(engine) as session:
        a = _recording(session, audio_hash="a" * 64, path="a.wav")
        b = _recording(session, audio_hash="b" * 64, path="b.wav")
        session.commit()

        page = search_recordings(session, ParsedQuery())

    assert {r.id for r in page.rows} == {a.id, b.id}
    assert page.warnings == []


def test_offset_past_the_end_returns_no_rows_and_no_more(engine: Engine) -> None:
    """Review Focus item: a stale pagination link pointing past the current last page must not
    500 or return a negative/garbage state -- Python list slicing already makes this safe by
    construction, but that needs a test to be a guarantee rather than an accident."""
    with OrmSession(engine) as session:
        _recording(session, audio_hash="a" * 64, path="a.wav")
        session.commit()

        page = search_recordings(session, ParsedQuery(), offset=1000)

    assert page.rows == []
    assert page.has_more is False


def test_multiple_free_text_terms_and_with_multiple_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        for i in range(MAX_SEARCH_RESULTS + 5):
            _recording(session, audio_hash=f"{i:064x}", path=f"rec{i}.wav")
        session.commit()

        page = search_recordings(session, ParsedQuery(free_text_terms=["rec"]))

    assert len(page.rows) == MAX_SEARCH_RESULTS
    assert page.has_more is True

    with OrmSession(engine) as session:
        page2 = search_recordings(
            session,
            ParsedQuery(free_text_terms=["rec"]),
            offset=MAX_SEARCH_RESULTS,
        )
    assert len(page2.rows) == 5
    assert page2.has_more is False

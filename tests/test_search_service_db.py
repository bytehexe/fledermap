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
) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=path,
        recorded_at=datetime(2026, 9, 25, tzinfo=UTC),
        site_id=site_id,
        favourite=favourite,
        flagged_for_review=flagged,
    )
    session.add(r)
    session.flush()
    return r


def _empty_query(**overrides: object) -> ParsedQuery:
    return ParsedQuery(**overrides)  # type: ignore[arg-type]


def test_free_text_matches_filename(engine: Engine) -> None:
    with OrmSession(engine) as session:
        target = _recording(
            session, audio_hash="a" * 64, path="PIPPIP_20260914_220508.wav"
        )
        _recording(session, audio_hash="b" * 64, path="EPTSER_20260914_220508.wav")
        session.commit()

        page = search_recordings(session, _empty_query(free_text_terms=["PIPPIP"]))

    assert [r.id for r in page.rows] == [target.id]


def test_free_text_matches_comment_annotation(engine: Engine) -> None:
    with OrmSession(engine) as session:
        target = _recording(session, audio_hash="a" * 64, path="a.wav")
        _recording(session, audio_hash="b" * 64, path="b.wav")
        session.add(
            Annotation(
                recording_id=target.id,
                source=AnnotationSource.COMMENT,
                time_start_s=0.0,
                literal="feeding buzz here",
                created_at=datetime(2026, 9, 25, tzinfo=UTC),
            ),
        )
        session.commit()

        page = search_recordings(session, _empty_query(free_text_terms=["buzz"]))

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

        page = search_recordings(session, _empty_query(free_text_terms=["Kleiner"]))

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

        page = search_recordings(
            session, _empty_query(free_text_terms=["pipistrellus"])
        )

    assert [r.id for r in page.rows] == [target.id]


def test_ilike_special_characters_are_escaped(engine: Engine) -> None:
    with OrmSession(engine) as session:
        _recording(session, audio_hash="a" * 64, path="fifty%off.wav")
        no_match = _recording(session, audio_hash="b" * 64, path="fiftyXoff.wav")
        session.commit()

        page = search_recordings(session, _empty_query(free_text_terms=["fifty%off"]))

    assert [r.id for r in page.rows] != [no_match.id]
    assert len(page.rows) == 1


def test_is_commented_filters_to_recordings_with_a_comment(engine: Engine) -> None:
    with OrmSession(engine) as session:
        commented = _recording(session, audio_hash="a" * 64, path="a.wav")
        _recording(session, audio_hash="b" * 64, path="b.wav")
        session.add(
            Annotation(
                recording_id=commented.id,
                source=AnnotationSource.COMMENT,
                time_start_s=0.0,
                literal="note",
                created_at=datetime(2026, 9, 25, tzinfo=UTC),
            ),
        )
        session.commit()

        page = search_recordings(session, _empty_query(is_commented=True))

    assert [r.id for r in page.rows] == [commented.id]


def test_is_flagged_reuses_filtered_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        flagged = _recording(session, audio_hash="a" * 64, path="a.wav", flagged=True)
        _recording(session, audio_hash="b" * 64, path="b.wav")
        session.commit()

        page = search_recordings(session, _empty_query(is_flagged=True))

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
            _empty_query(annotation_source=IdSource.BATDETECT2),
        )

    assert [r.id for r in page.rows] == [target.id]


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

        page = search_recordings(session, _empty_query())

    assert {r.id for r in page.rows} == {a.id, b.id}
    assert page.warnings == []


def test_offset_past_the_end_returns_no_rows_and_no_more(engine: Engine) -> None:
    """Review Focus item: a stale pagination link pointing past the current last page must not
    500 or return a negative/garbage state -- Python list slicing already makes this safe by
    construction, but that needs a test to be a guarantee rather than an accident."""
    with OrmSession(engine) as session:
        _recording(session, audio_hash="a" * 64, path="a.wav")
        session.commit()

        page = search_recordings(session, _empty_query(), offset=1000)

    assert page.rows == []
    assert page.has_more is False


def test_multiple_free_text_terms_and_with_multiple_recordings(engine: Engine) -> None:
    with OrmSession(engine) as session:
        for i in range(MAX_SEARCH_RESULTS + 5):
            _recording(session, audio_hash=f"{i:064x}", path=f"rec{i}.wav")
        session.commit()

        page = search_recordings(session, _empty_query(free_text_terms=["rec"]))

    assert len(page.rows) == MAX_SEARCH_RESULTS
    assert page.has_more is True

    with OrmSession(engine) as session:
        page2 = search_recordings(
            session,
            _empty_query(free_text_terms=["rec"]),
            offset=MAX_SEARCH_RESULTS,
        )
    assert len(page2.rows) == 5
    assert page2.has_more is False

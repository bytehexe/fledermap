from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from flask.testing import FlaskClient
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording, Site, Taxon
from fledermap.web.app import create_app

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("_vendor_icons")]


def app_client(engine: Engine, tmp_path: Path) -> FlaskClient:
    return create_app(engine, tmp_path / "static", tmp_path / "media").test_client()


def test_reviews_page_lists_flagged_recordings(engine: Engine, tmp_path: Path) -> None:
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
                first_seen_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()
        recording_id = r.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get("/reviews")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Pipistrellus pipistrellus" in html
    assert "a" * 64 in html
    assert "1 recording flagged for review" in html
    assert f"review={recording_id}" in html


def test_reviews_page_recording_column_is_first(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            flagged_for_review=True,
        )
        session.add(r)
        session.commit()

    html = app_client(engine, tmp_path).get("/reviews").get_data(as_text=True)

    assert (
        "<tr><th>Recording</th><th>Species</th><th>Site</th><th>Reasons</th></tr>"
        in html
    )
    row_start = html.index("<tbody>")
    row = html[row_start : html.index("</tr>", row_start)]
    assert row.index(f"/recordings/{'a' * 64}") < row.index("<td>unidentified</td>")


def test_reviews_page_species_column_links_to_the_species_page(
    engine: Engine,
    tmp_path: Path,
) -> None:
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
                first_seen_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()
        taxon_id = taxon.id

    html = app_client(engine, tmp_path).get("/reviews").get_data(as_text=True)

    assert f'href="/species/{taxon_id}"' in html


def test_reviews_page_recording_column_links_to_the_recording(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            flagged_for_review=True,
        )
        session.add(r)
        session.commit()
        recording_id = r.id

    html = app_client(engine, tmp_path).get("/reviews").get_data(as_text=True)

    # The date text itself must be inside the anchor -- not just the href
    # existing anywhere on the page (e.g. on a differently-purposed Species
    # column link).
    assert f'<a href="/recordings/{"a" * 64}?review={recording_id}">2026-08-25' in html


def test_reviews_page_site_column_links_to_the_site_page(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
            name="Behind the barn",
        )
        session.add(site)
        session.flush()
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            site_id=site.id,
            flagged_for_review=True,
        )
        session.add(r)
        session.commit()
        site_id = site.id

    html = app_client(engine, tmp_path).get("/reviews").get_data(as_text=True)

    assert f'href="/sites/{site_id}?return_to=/reviews"' in html


def test_reviews_page_unidentified_species_cell_has_no_link(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        r = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            flagged_for_review=True,
        )
        session.add(r)
        session.commit()

    html = app_client(engine, tmp_path).get("/reviews").get_data(as_text=True)

    assert "<td>unidentified</td>" in html


def test_reviews_page_zero_flagged(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get("/reviews")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "0 recordings flagged for review" in html
    assert "Start reviewing" not in html


def test_reviews_page_start_reviewing_label_reflects_the_capped_snapshot_size(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """A flagged count over MAX_REVIEW_SNAPSHOT (500) must not make the
    "Start reviewing (N)" button lie about how many recordings the review=
    snapshot actually reaches -- it should read the same 500 the truncation
    banner reports, not the untruncated total (this was the Important
    finding from code review: the button previously showed `count`, not
    `len(snapshot_ids)`)."""
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        for i in range(501):
            r = Recording(
                audio_hash=f"{i:064x}",
                path=f"{i}.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                flagged_for_review=True,
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

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get("/reviews")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "501 recordings flagged for review" in html
    assert "Start reviewing (500)" in html
    assert "Start reviewing (501)" not in html
    assert "Showing the first 500" in html


def test_reviews_page_includes_unidentified_flagged_recording(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """A recording flagged for review with no identification (unidentified)
    must appear on the reviews page, not be filtered out by the verdict
    filter. The manual flag is independent of species classification."""
    with OrmSession(engine) as session:
        r = Recording(
            audio_hash="b" * 64,
            path="b.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            flagged_for_review=True,
        )
        session.add(r)
        session.commit()
        recording_id = r.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get("/reviews")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "1 recording flagged for review" in html
    assert "b" * 64 in html
    assert f"review={recording_id}" in html
    # The species column must use recording_headline's real "unidentified"
    # label for a recording with no identification at all -- not the view's
    # old hand-rolled ternary, which wrongly fell back to "unmapped species"
    # for every non-simple case (unidentified, multi-species, NOISE, NO_ID).
    assert ">unidentified<" in html
    assert "unmapped species" not in html

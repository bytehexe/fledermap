from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording, Site, Taxon, TaxonCode
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_species_list_renders_a_species_row(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(
            rank="species",
            scientific_name="Eptesicus serotinus",
            common_name_en="Serotine bat",
            common_name_de="Breitflügelfledermaus",
        )
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

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/species")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Eptesicus serotinus" in html


def test_species_detail_renders_common_names_and_404s_for_unknown_taxon(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(
            rank="species",
            scientific_name="Eptesicus serotinus",
            common_name_en="Serotine bat",
            common_name_de="Breitflügelfledermaus",
        )
        session.add(taxon)
        session.flush()
        session.add(TaxonCode(source="wa", code="EPTSER", taxon_id=taxon.id))
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
        taxon_id = taxon.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/species/{taxon_id}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Serotine bat" in html
    assert "Breitflügelfledermaus" in html
    assert "EPTSER" in html
    assert "<table" in html  # sites + recent recordings render as tables, not lists
    assert (
        "panel-columns" not in html
    )  # side-by-side column boxes dropped on this full page

    missing_response = client.get("/species/999999")
    assert missing_response.status_code == 404
    assert "showing the most recent" not in html  # only 1 recording -- not capped


def test_species_detail_notes_the_recent_recordings_cap_when_it_is_hit(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """The "Recent recordings" table is a bounded preview
    (`RECENT_RECORDINGS_LIMIT`), not a full paginated list -- it must say so
    when the cap is actually hit, or a reader can't tell "these are all the
    recordings" from "there might be more we're not showing"."""
    from fledermap.services.entities import RECENT_RECORDINGS_LIMIT

    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.flush()
        for i in range(RECENT_RECORDINGS_LIMIT):
            r = Recording(
                audio_hash=f"{i:02d}" * 32,
                path=f"{i}.wav",
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
        taxon_id = taxon.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/species/{taxon_id}")

    html = response.get_data(as_text=True)
    assert f"showing the most recent {RECENT_RECORDINGS_LIMIT}" in html


def test_sites_list_renders_a_site_row(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        session.add(
            Site(
                centroid=WKTElement("POINT(10 50)", srid=4326),
                radius_m=50.0,
                recording_count=3,
                first_at=datetime(2026, 8, 25, tzinfo=UTC),
                last_at=datetime(2026, 8, 26, tzinfo=UTC),
                name="Old Barn",
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/sites")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Old Barn" in html


def test_site_detail_page_renders_and_404s_for_unknown_site(
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
            name="Old Barn",
        )
        session.add(site)
        session.commit()
        site_id = site.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/sites/{site_id}")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Old Barn" in html
    assert "<table" in html  # species breakdown + sessions render as tables, not lists
    assert (
        "panel-columns" not in html
    )  # side-by-side column boxes dropped on this full page

    missing_response = client.get("/sites/999999")
    assert missing_response.status_code == 404


def test_site_detail_page_renders_a_minimap_with_the_sites_own_geometry(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """Obsidian backlog: "Site's details page also needs a minimap!" --
    unlike the map drawer's site panel (which sits right on top of the main
    map already, so a redundant minimap there is the documented
    drawer/detail-page parity exception for "clearly map-related"), the
    standalone details page has no map context at all."""
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=75.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
            name="Old Barn",
        )
        session.add(site)
        session.commit()
        site_id = site.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/sites/{site_id}").get_data(as_text=True)

    assert 'id="site-mini-map"' in html
    assert f'data-site-id="{site_id}"' in html
    assert 'data-lat="50.0"' in html
    assert 'data-lng="10.0"' in html
    assert 'data-radius-m="75.0"' in html
    assert "site_map.js" in html


def test_species_detail_page_links_to_its_statistics_subpage(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.commit()
        taxon_id = taxon.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/species/{taxon_id}").get_data(as_text=True)

    assert f'href="/statistics/species/{taxon_id}"' in html


def test_site_detail_page_links_to_its_statistics_subpage(
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
        )
        session.add(site)
        session.commit()
        site_id = site.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/sites/{site_id}").get_data(as_text=True)

    assert f'href="/statistics/sites/{site_id}"' in html


def test_species_detail_page_back_link_honours_return_to(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """docs/style-guide.md's "Back-links (return_to)" section -- a recognised
    map origin gets a "Back to map" label; the default with no return_to is
    the species list."""
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.commit()
        taxon_id = taxon.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    default_html = client.get(f"/species/{taxon_id}").get_data(as_text=True)
    assert "← All species" in default_html
    assert 'href="/species"' in default_html

    returned_html = client.get(
        f"/species/{taxon_id}?return_to=/%3Fsite%3D3",
    ).get_data(as_text=True)
    assert "← Back to map" in returned_html
    assert 'href="/?site=3"' in returned_html


def test_site_detail_page_back_link_honours_return_to(
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
        )
        session.add(site)
        session.commit()
        site_id = site.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    default_html = client.get(f"/sites/{site_id}").get_data(as_text=True)
    assert "← All sites" in default_html

    unsafe_html = client.get(
        f"/sites/{site_id}?return_to=//evil.example",
    ).get_data(as_text=True)
    assert "← All sites" in unsafe_html  # unsafe return_to rejected, falls back

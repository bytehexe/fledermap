from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.media.paths import oscillogram_path, preview_path, spectrogram_path
from fledermap.store.models import Identification, Recording, Site, Taxon
from fledermap.store.models import Session as AnnotationSession
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def test_map_page_renders_the_leaflet_shell(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<div id="map">' in html
    assert "vendor/leaflet.js" in html
    assert "vendor/leaflet.markercluster.js" in html
    assert "vendor/htmx.min.js" in html
    assert "vendor/alpine.min.js" in html


def test_map_page_includes_the_filter_form(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/")

    html = response.get_data(as_text=True)
    assert 'name="verdict"' in html
    assert 'name="taxon"' in html
    assert 'name="taxon_exclude"' in html
    assert 'name="from"' in html
    assert 'name="to"' in html
    assert 'name="session"' in html
    assert 'name="source"' in html
    assert 'name="favourite_only"' in html
    # finding 7: emt.manual has real, produced data today and must be
    # selectable, unlike `manual`, which nothing produces yet.
    assert 'value="emt.manual"' in html
    assert 'value="manual"' not in html


def test_taxon_filter_is_a_dropdown_of_real_taxa(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """A numeric ID input is not something a person can use -- feedback on
    the first UI pass. Taxon has a small, fixed set of real names, so it
    becomes a <select>, unlike Session below."""
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.commit()
        taxon_id = taxon.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/")

    html = response.get_data(as_text=True)
    assert '<select name="taxon"' in html
    assert f'<option value="{taxon_id}">Pipistrellus pipistrellus</option>' in html


def test_taxon_option_includes_common_name_when_present(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(
            rank="species",
            scientific_name="Eptesicus serotinus",
            common_name_en="Serotine bat",
        )
        session.add(taxon)
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    html = client.get("/").get_data(as_text=True)
    assert "Eptesicus serotinus — Serotine bat" in html


def test_session_filter_is_a_dropdown_labelled_by_date_range_and_detector(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        annotation_session = AnnotationSession(
            started_at=datetime(2026, 8, 1, 22, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 1, 23, 15, tzinfo=UTC),
            detector_key="ABC123",
        )
        session.add(annotation_session)
        session.commit()
        session_id = annotation_session.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    html = client.get("/").get_data(as_text=True)
    assert '<select name="session"' in html
    assert (
        f'<option value="{session_id}">2026-08-01 22:00–23:15 (ABC123)</option>' in html
    )


def test_session_option_falls_back_when_detector_key_is_missing(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        annotation_session = AnnotationSession(
            started_at=datetime(2026, 8, 1, 22, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 1, 23, 15, tzinfo=UTC),
        )
        session.add(annotation_session)
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    html = client.get("/").get_data(as_text=True)
    assert "2026-08-01 22:00–23:15 (unknown detector)" in html


def test_recording_panel_renders_identification_and_metadata(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
        )
        session.add(recording)
        session.flush()
        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.EMT_GUANO,
                source_version=None,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                raw_label="EPTSER",
                first_seen_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'a' * 64}/panel")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Eptesicus serotinus" in html
    trigger = json.loads(response.headers["HX-Trigger"])
    assert trigger["recording-selected"]["hash"] == "a" * 64


def test_recording_panel_renders_identification_list_content(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="e" * 64,
            path="e.wav",
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
        )
        session.add(recording)
        session.flush()
        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.EMT_GUANO,
                source_version=None,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                raw_label="EPTSER",
                first_seen_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            ),
        )
        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.EMT_WAMD,
                source_version=None,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                raw_label="EPTSER",
                first_seen_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
                superseded_at=datetime(2026, 8, 25, 21, 5, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'e' * 64}/panel")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "emt.guano" in html
    assert "EPTSER" in html
    assert 'class="superseded"' in html


def test_recording_panel_not_found_renders_gracefully(
    engine: Engine,
    tmp_path: Path,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'z' * 64}/panel")

    assert response.status_code == 200
    assert "not found" in response.get_data(as_text=True).lower()
    assert "HX-Trigger" not in response.headers


def test_recording_panel_degrades_when_media_not_rendered(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="b" * 64,
                path="b.wav",
                recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    # verdict=all: without an identification, this recording's effective
    # verdict is NO_ID, which the default (species-only, P4-9 "hide noise by
    # default") filter would exclude entirely -- neighbor_recordings then
    # reports it as not-found rather than reaching this test's actual target,
    # the media-placeholder degradation path.
    html = (
        app.test_client()
        .get(
            f"/recordings/{'b' * 64}/panel?verdict=all",
        )
        .get_data(as_text=True)
    )

    assert "not processed yet" in html.lower()


def test_recording_panel_renders_media_when_already_processed(
    engine: Engine,
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "media"
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="f" * 64,
                path="f.wav",
                recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
                duration_s=0.5,
                samplerate_hz=256_000,
            ),
        )
        session.commit()

    spectrogram_path(media_root, "f" * 64).parent.mkdir(parents=True)
    spectrogram_path(media_root, "f" * 64).write_bytes(b"fake-webp-bytes")
    oscillogram_path(media_root, "f" * 64).parent.mkdir(parents=True, exist_ok=True)
    oscillogram_path(media_root, "f" * 64).write_bytes(b"fake-webp-bytes")
    preview_path(media_root, "f" * 64).parent.mkdir(parents=True, exist_ok=True)
    preview_path(media_root, "f" * 64).write_bytes(b"fake-opus-bytes")

    app = create_app(engine, tmp_path / "static", media_root)
    html = (
        app.test_client()
        .get(f"/recordings/{'f' * 64}/panel?verdict=all")
        .get_data(as_text=True)
    )

    assert '<img class="spectrogram"' in html
    assert '<img class="oscillogram"' in html
    assert 'class="audio-controls"' in html
    audio_controls_match = re.search(
        r'<div\s+class="audio-controls"[^>]*>',
        html,
        re.DOTALL,
    )
    assert audio_controls_match is not None
    assert 'data-time-expansion-factor="10"' in audio_controls_match.group(0)
    assert '<div class="playback-cursor" hidden></div>' in html
    # Frequency axis: 128kHz default ceiling clamped to 256kHz/2 Nyquist.
    assert "128 kHz" in html
    # Time axis: full 0.5s duration.
    assert "0.50s" in html
    # Crosshair readout needs these on the spectrogram img itself -- the
    # axis labels alone aren't enough precision for JS to compute from.
    assert 'data-duration-s="0.5"' in html
    assert 'data-max-freq-khz="128.0"' in html


def test_recording_panel_oscillogram_degrades_when_not_yet_rendered(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="k" * 64,
                path="k.wav",
                recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = (
        app.test_client()
        .get(f"/recordings/{'k' * 64}/panel?verdict=all")
        .get_data(as_text=True)
    )

    assert "waveform not processed yet" in html.lower()


def test_recording_panel_shows_prev_next_within_filters(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        early = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
        )
        middle = Recording(
            audio_hash="b" * 64,
            path="b.wav",
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
        )
        late = Recording(
            audio_hash="c" * 64,
            path="c.wav",
            recorded_at=datetime(2026, 8, 25, 22, 0, tzinfo=UTC),
        )
        session.add_all([early, middle, late])
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = (
        app.test_client()
        .get(
            f"/recordings/{'b' * 64}/panel?verdict=all",
        )
        .get_data(as_text=True)
    )

    assert f"/recordings/{'a' * 64}/panel" in html
    assert f"/recordings/{'c' * 64}/panel" in html


def test_recording_panel_shows_the_favourite_toggle_unstarred_by_default(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="a" * 64,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = (
        app.test_client()
        .get(f"/recordings/{'a' * 64}/panel?verdict=all")
        .get_data(as_text=True)
    )

    assert '<button\n    type="button"\n    class="favourite-toggle"' in html
    assert 'aria-pressed="false"' in html
    assert "☆" in html
    assert "★" not in html


def test_recording_panel_shows_the_favourite_toggle_starred(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="a" * 64,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
                favourite=True,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = (
        app.test_client()
        .get(f"/recordings/{'a' * 64}/panel?verdict=all")
        .get_data(as_text=True)
    )

    assert 'aria-pressed="true"' in html
    assert "★" in html


def test_toggle_favourite_flips_it_on_then_off(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="a" * 64,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.post(f"/recordings/{'a' * 64}/favourite?verdict=all")
    assert response.status_code == 200
    with OrmSession(engine) as session:
        recording = session.get(Recording, 1)
        assert recording is not None
        assert recording.favourite is True

    response = client.post(f"/recordings/{'a' * 64}/favourite?verdict=all")
    assert response.status_code == 200
    with OrmSession(engine) as session:
        recording = session.get(Recording, 1)
        assert recording is not None
        assert recording.favourite is False


def test_toggle_favourite_unknown_hash_returns_404(
    engine: Engine,
    tmp_path: Path,
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")

    response = app.test_client().post(f"/recordings/{'z' * 64}/favourite")

    assert response.status_code == 404
    assert "Recording not found" in response.get_data(as_text=True)


def test_toggle_favourite_does_not_set_hx_trigger(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="a" * 64,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
                geom=WKTElement("POINT(10 50)", srid=4326),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().post(f"/recordings/{'a' * 64}/favourite?verdict=all")

    assert response.status_code == 200
    assert "HX-Trigger" not in response.headers


def test_favourite_only_filters_the_recording_panel_set(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        starred = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
            favourite=True,
        )
        plain = Recording(
            audio_hash="b" * 64,
            path="b.wav",
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            favourite=False,
        )
        session.add_all([starred, plain])
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = (
        app.test_client()
        .get(f"/recordings/{'a' * 64}/panel?verdict=all&favourite_only=1")
        .get_data(as_text=True)
    )

    assert "not found" not in html.lower()
    assert f"/recordings/{'b' * 64}/panel" not in html


def test_recording_panel_taxon_exclude_omits_neighbors_of_the_named_taxon(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        excluded = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(excluded)
        session.flush()
        excluded_id = excluded.id
        early = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
        )
        middle = Recording(
            audio_hash="b" * 64,
            path="b.wav",
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
        )
        late = Recording(
            audio_hash="c" * 64,
            path="c.wav",
            recorded_at=datetime(2026, 8, 25, 22, 0, tzinfo=UTC),
        )
        session.add_all([early, middle, late])
        session.flush()
        session.add(
            Identification(
                recording_id=early.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=excluded.id,
                first_seen_at=early.recorded_at,
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = (
        app.test_client()
        .get(
            f"/recordings/{'b' * 64}/panel"
            f"?verdict=all&taxon={excluded_id}&taxon_exclude=1",
        )
        .get_data(as_text=True)
    )

    assert f"/recordings/{'a' * 64}/panel" not in html
    assert f"/recordings/{'c' * 64}/panel" in html


def test_site_panel_renders_species_breakdown_and_sessions(
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
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add_all([site, taxon])
        session.flush()
        recording = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            site_id=site.id,
        )
        session.add(recording)
        session.flush()
        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.EMT_GUANO,
                source_version=None,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                raw_label="EPTSER",
                first_seen_at=datetime(2026, 8, 25, 21, 0, tzinfo=UTC),
            ),
        )
        session.commit()
        site_id = site.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/sites/{site_id}/panel").get_data(as_text=True)

    assert "Behind the barn" in html
    assert "Eptesicus serotinus" in html


def test_site_panel_triggers_site_selected_with_centroid_and_radius(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=75.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(site)
        session.commit()
        site_id = site.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/sites/{site_id}/panel")

    assert response.status_code == 200
    trigger = json.loads(response.headers["HX-Trigger"])
    assert trigger["site-selected"] == {
        "id": site_id,
        "latitude": 50.0,
        "longitude": 10.0,
        "radius_m": 75.0,
    }


def test_site_panel_not_found_renders_gracefully(
    engine: Engine, tmp_path: Path
) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get("/sites/999999/panel")

    assert response.status_code == 200
    assert "not found" in response.get_data(as_text=True).lower()
    assert "HX-Trigger" not in response.headers


def test_site_panel_has_show_only_this_site_button(
    engine: Engine, tmp_path: Path
) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=0,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(site)
        session.commit()
        site_id = site.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get(f"/sites/{site_id}/panel").get_data(as_text=True)

    assert f"fledermapFilterBySite({site_id})" in html


def test_map_page_includes_the_drawer_shell(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    html = app.test_client().get("/").get_data(as_text=True)

    assert 'id="drawer"' in html
    assert 'id="drawer-body"' in html
    assert "$store.drawer.open" in html
    assert "$store.drawer.collapsed" in html


def test_map_page_includes_the_sidebar(engine: Engine, tmp_path: Path) -> None:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get("/")

    html = response.get_data(as_text=True)
    assert 'id="sidebar"' in html
    assert 'href="/sessions"' in html


def test_site_panel_links_sessions_to_session_detail(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        annotation_session = AnnotationSession(
            started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
            detector_key="EMT\x1f1",
        )
        session.add(annotation_session)
        session.flush()
        site = Site(
            centroid=WKTElement("POINT(10.0 51.0)", srid=4326),
            radius_m=10.0,
            recording_count=1,
            first_at=datetime(2026, 8, 21, tzinfo=UTC),
            last_at=datetime(2026, 8, 21, tzinfo=UTC),
        )
        session.add(site)
        session.flush()
        session.add(
            Recording(
                audio_hash="a".rjust(64, "0"),
                path="a.wav",
                recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
                session_id=annotation_session.id,
                site_id=site.id,
            ),
        )
        session.commit()
        site_id, session_id = site.id, annotation_session.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/sites/{site_id}/panel")

    html = response.get_data(as_text=True)
    assert f'href="/sessions/{session_id}"' in html


def test_recording_panel_links_session_to_session_detail(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        annotation_session = AnnotationSession(
            started_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            ended_at=datetime(2026, 8, 21, 23, tzinfo=UTC),
            detector_key="EMT\x1f1",
        )
        session.add(annotation_session)
        session.flush()
        taxon = Taxon(rank="species", scientific_name="Eptesicus serotinus")
        session.add(taxon)
        session.flush()
        recording = Recording(
            audio_hash="a".rjust(64, "0"),
            path="a.wav",
            recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            session_id=annotation_session.id,
        )
        session.add(recording)
        session.flush()
        session.add(
            Identification(
                recording_id=recording.id,
                source=IdSource.EMT_GUANO,
                source_version=None,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                raw_label="EPTSER",
                first_seen_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            ),
        )
        session.commit()
        session_id = annotation_session.id

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    client = app.test_client()

    response = client.get(f"/recordings/{'a'.rjust(64, '0')}/panel")

    html = response.get_data(as_text=True)
    assert f'href="/sessions/{session_id}"' in html


def test_recording_panel_links_to_the_details_page(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        session.add(
            Recording(
                audio_hash="g1" * 32,
                path="a.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    response = app.test_client().get(f"/recordings/{'g1' * 32}/panel?verdict=all")

    html = response.get_data(as_text=True)
    assert f'href="/recordings/{"g1" * 32}"' in html

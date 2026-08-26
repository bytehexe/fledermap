from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import flask
import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.services.map_query import MAX_FEATURES
from fledermap.store.models import Identification, Recording, Site, Taxon
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def _app_client(engine: Engine, tmp_path: Path) -> flask.testing.FlaskClient:
    app = create_app(engine, tmp_path / "static", tmp_path / "media")
    return app.test_client()


def test_recordings_geojson_excludes_noise_by_default(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        shown = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
        )
        hidden = Recording(
            audio_hash="b" * 64,
            path="b.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(11 51)", srid=4326),
        )
        session.add_all([shown, hidden])
        session.flush()
        session.add(
            Identification(
                recording_id=shown.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.add(
            Identification(
                recording_id=hidden.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.NOISE,
                first_seen_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()
        shown_hash = shown.audio_hash

    client = _app_client(engine, tmp_path)
    response = client.get("/api/recordings.geojson")

    assert response.status_code == 200
    body = response.get_json()
    assert body["type"] == "FeatureCollection"
    hashes = [f["properties"]["audio_hash"] for f in body["features"]]
    assert hashes == [shown_hash]


def test_recordings_geojson_verdict_all_shows_everything(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        r = Recording(
            audio_hash="c" * 64,
            path="c.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
        )
        session.add(r)
        session.flush()
        session.add(
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.NOISE,
                first_seen_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    client = _app_client(engine, tmp_path)
    response = client.get("/api/recordings.geojson?verdict=all")

    assert len(response.get_json()["features"]) == 1


def test_recordings_geojson_feature_geometry_is_lon_lat(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        r = Recording(
            audio_hash="d" * 64,
            path="d.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(13.4 52.5)", srid=4326),
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

    client = _app_client(engine, tmp_path)
    response = client.get("/api/recordings.geojson")

    feature = response.get_json()["features"][0]
    assert feature["geometry"] == {"type": "Point", "coordinates": [13.4, 52.5]}


def test_sites_geojson_falls_back_to_coordinates_when_unnamed(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(13.4 52.5)", srid=4326),
            radius_m=50.0,
            recording_count=4,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 26, tzinfo=UTC),
        )
        session.add(site)
        session.commit()

    client = _app_client(engine, tmp_path)
    response = client.get("/api/sites.geojson")

    feature = response.get_json()["features"][0]
    # P4-1: Site.name is unpopulated until poiidx naming ships -- fall back
    # to a rounded-coordinate label.
    assert feature["properties"]["name"] == "52.5000, 13.4000"


def test_recordings_geojson_emits_taxon_name(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        named = Recording(
            audio_hash="e" * 64,
            path="e.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
        )
        unidentified = Recording(
            audio_hash="f" * 64,
            path="f.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
        )
        session.add_all([named, unidentified])
        session.flush()
        session.add(
            Identification(
                recording_id=named.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    client = _app_client(engine, tmp_path)
    response = client.get("/api/recordings.geojson?verdict=all")

    features = {
        f["properties"]["audio_hash"]: f["properties"]
        for f in response.get_json()["features"]
    }
    assert features["e" * 64]["taxon_name"] == "Pipistrellus pipistrellus"
    assert features["e" * 64]["taxon_id"] is not None
    assert features["f" * 64]["taxon_name"] is None


def test_recordings_geojson_to_date_includes_the_whole_selected_day(
    engine: Engine,
    tmp_path: Path,
) -> None:
    """A bare `to=2026-08-25` must not silently exclude recordings made on
    the 25th itself (finding 6: `datetime.fromisoformat` parses a bare date
    as that day's midnight, so a naive `<=` comparison would drop the whole
    selected final day)."""
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()
        late_in_the_day = Recording(
            audio_hash="1" * 64,
            path="late.wav",
            recorded_at=datetime(2026, 8, 25, 23, 30, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
        )
        session.add(late_in_the_day)
        session.flush()
        session.add(
            Identification(
                recording_id=late_in_the_day.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=datetime(2026, 8, 25, tzinfo=UTC),
            ),
        )
        session.commit()

    client = _app_client(engine, tmp_path)
    response = client.get(
        "/api/recordings.geojson?from=2026-08-25&to=2026-08-25",
    )

    body = response.get_json()
    assert len(body["features"]) == 1


def test_recordings_geojson_caps_at_max_features_and_reports_truncated(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()

        recordings = [
            Recording(
                audio_hash=f"{i:064x}",
                path=f"{i}.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                geom=WKTElement("POINT(10 50)", srid=4326),
            )
            for i in range(MAX_FEATURES + 1)
        ]
        session.add_all(recordings)
        session.flush()

        identifications = [
            Identification(
                recording_id=r.id,
                source=IdSource.EMT_GUANO,
                verdict=Verdict.SPECIES,
                taxon_id=taxon.id,
                first_seen_at=datetime(2026, 8, 25, tzinfo=UTC),
            )
            for r in recordings
        ]
        session.add_all(identifications)
        session.commit()

    client = _app_client(engine, tmp_path)
    response = client.get("/api/recordings.geojson")

    body = response.get_json()
    assert len(body["features"]) == MAX_FEATURES
    assert body["truncated"] is True


def test_recordings_geojson_not_truncated_under_the_cap(
    engine: Engine,
    tmp_path: Path,
) -> None:
    with OrmSession(engine) as session:
        taxon = Taxon(rank="species", scientific_name="Pipistrellus pipistrellus")
        session.add(taxon)
        session.flush()

        recordings = [
            Recording(
                audio_hash=f"{i:064x}",
                path=f"{i}.wav",
                recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
                geom=WKTElement("POINT(10 50)", srid=4326),
            )
            for i in range(3)
        ]
        session.add_all(recordings)
        session.flush()

        session.add_all(
            [
                Identification(
                    recording_id=r.id,
                    source=IdSource.EMT_GUANO,
                    verdict=Verdict.SPECIES,
                    taxon_id=taxon.id,
                    first_seen_at=datetime(2026, 8, 25, tzinfo=UTC),
                )
                for r in recordings
            ],
        )
        session.commit()

    client = _app_client(engine, tmp_path)
    response = client.get("/api/recordings.geojson")

    body = response.get_json()
    assert len(body["features"]) == 3
    assert body["truncated"] is False


def test_invalid_bbox_returns_400(engine: Engine, tmp_path: Path) -> None:
    client = _app_client(engine, tmp_path)

    response = client.get("/api/recordings.geojson?bbox=not,four,numbers")

    assert response.status_code == 400
    assert "bbox" in response.get_json()["error"]


def test_non_numeric_four_part_bbox_returns_400(
    engine: Engine,
    tmp_path: Path,
) -> None:
    client = _app_client(engine, tmp_path)

    response = client.get("/api/recordings.geojson?bbox=a,b,c,d")

    assert response.status_code == 400
    assert "bbox" in response.get_json()["error"]


def test_invalid_taxon_param_returns_400_not_500(
    engine: Engine,
    tmp_path: Path,
) -> None:
    client = _app_client(engine, tmp_path)

    response = client.get("/api/recordings.geojson?taxon=notanumber")

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_invalid_verdict_param_returns_400_not_500(
    engine: Engine,
    tmp_path: Path,
) -> None:
    client = _app_client(engine, tmp_path)

    response = client.get("/api/recordings.geojson?verdict=bogus")

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_recordings_geojson_filters_by_site(engine: Engine, tmp_path: Path) -> None:
    with OrmSession(engine) as session:
        site = Site(
            centroid=WKTElement("POINT(10 50)", srid=4326),
            radius_m=50.0,
            recording_count=1,
            first_at=datetime(2026, 8, 25, tzinfo=UTC),
            last_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
        session.add(site)
        session.flush()
        at_site = Recording(
            audio_hash="a" * 64,
            path="a.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(10 50)", srid=4326),
            site_id=site.id,
        )
        elsewhere = Recording(
            audio_hash="b" * 64,
            path="b.wav",
            recorded_at=datetime(2026, 8, 25, tzinfo=UTC),
            geom=WKTElement("POINT(11 51)", srid=4326),
        )
        session.add_all([at_site, elsewhere])
        session.commit()
        site_id = site.id

    client = _app_client(engine, tmp_path)
    response = client.get(f"/api/recordings.geojson?verdict=all&site={site_id}")

    hashes = {f["properties"]["audio_hash"] for f in response.get_json()["features"]}
    assert hashes == {"a" * 64}

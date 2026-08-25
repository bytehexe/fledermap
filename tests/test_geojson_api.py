from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import flask
import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.domain.codes import IdSource, Verdict
from fledermap.store.models import Identification, Recording, Site, Taxon
from fledermap.web.app import create_app

pytestmark = pytest.mark.db


def _app_client(engine: Engine, tmp_path: Path) -> flask.testing.FlaskClient:
    app = create_app(engine, tmp_path / "static")
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


def test_invalid_bbox_returns_400(engine: Engine, tmp_path: Path) -> None:
    client = _app_client(engine, tmp_path)

    response = client.get("/api/recordings.geojson?bbox=not,four,numbers")

    assert response.status_code == 400
    assert "bbox" in response.get_json()["error"]

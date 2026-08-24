from __future__ import annotations

from datetime import UTC, datetime

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.geo import decode_point
from fledermap.store.models import Recording

pytestmark = pytest.mark.db


def test_decode_point_round_trips_through_the_database(engine: Engine) -> None:
    with OrmSession(engine) as session:
        recording = Recording(
            audio_hash="d" * 64,
            path="x.wav",
            recorded_at=datetime(2026, 8, 21, 21, tzinfo=UTC),
            geom=WKTElement("POINT(13.4 52.5)", srid=4326),
        )
        session.add(recording)
        session.commit()
        session.refresh(recording)

        decoded = decode_point(recording.geom)

        assert decoded is not None
        lon, lat = decoded
        assert lon == pytest.approx(13.4)
        assert lat == pytest.approx(52.5)


def test_decode_point_returns_none_for_no_geometry() -> None:
    assert decode_point(None) is None


def test_decode_point_returns_none_for_a_non_wkb_value() -> None:
    assert decode_point("not a point") is None

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from fledermap.store.models import (
    LocationRegion,
    Recording,
    RecordingRegion,
    Region,
)

pytestmark = pytest.mark.db


def _recording(session: OrmSession) -> Recording:
    r = Recording(
        audio_hash="a" * 64,
        path="a.wav",
        recorded_at=datetime(2026, 10, 4, tzinfo=UTC),
    )
    session.add(r)
    session.flush()
    return r


def test_region_rows_round_trip(engine: Engine) -> None:
    with OrmSession(engine) as session:
        session.add(Region(id="germany", name="Germany", is_distinct=True))
        session.add(
            LocationRegion(location_key="52.520,13.405", region_ids=["germany"]),
        )
        session.commit()
        assert session.scalars(select(Region.name)).all() == ["Germany"]
        row = session.scalars(select(LocationRegion)).one()
        assert row.region_ids == ["germany"]


def test_recording_region_rejects_an_unknown_region(engine: Engine) -> None:
    with OrmSession(engine) as session:
        r = _recording(session)
        session.add(RecordingRegion(recording_id=r.id, region_id="nowhere"))
        with pytest.raises(IntegrityError):
            session.flush()


def test_deleting_a_recording_cascades_to_recording_region(engine: Engine) -> None:
    with OrmSession(engine) as session:
        session.add(Region(id="germany", name="Germany", is_distinct=False))
        r = _recording(session)
        session.add(RecordingRegion(recording_id=r.id, region_id="germany"))
        session.commit()
        session.delete(r)
        session.commit()
        assert session.scalars(select(RecordingRegion)).all() == []

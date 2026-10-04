from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import procrastinate
import pytest
from geoalchemy2.elements import WKTElement
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session as OrmSession

from fledermap.config import Config
from fledermap.jobs.app import ensure_schema
from fledermap.jobs.tasks import app as jobs_app
from fledermap.jobs.tasks import resolve_regions_task
from fledermap.services import regions, site_naming
from fledermap.store.models import LocationRegion, Recording, RecordingRegion, Region

pytestmark = pytest.mark.db

BREMEN = {"id": "bremen", "name": "Bremen"}
GERMANY = {"id": "germany", "name": "Germany"}
AUSTRIA = {"id": "austria", "name": "Austria"}


def _recording(
    session: OrmSession,
    audio_hash: str,
    lon: float | None,
    lat: float | None,
    *,
    missing: bool = False,
) -> Recording:
    r = Recording(
        audio_hash=audio_hash,
        path=f"{audio_hash}.wav",
        recorded_at=datetime(2026, 10, 4, tzinfo=UTC),
        geom=None if lon is None else WKTElement(f"POINT({lon} {lat})", srid=4326),
        missing_since=datetime(2026, 10, 4, tzinfo=UTC) if missing else None,
    )
    session.add(r)
    session.flush()
    return r


def _fake_poiidx(
    monkeypatch: pytest.MonkeyPatch,
    by_point: dict[tuple[float, float], list[dict[str, Any]]],
    distinct_ids: set[str],
) -> list[BaseGeometry]:
    """Fake `poiidx.list_regions`: a Point returns its candidates, a
    MultiPoint returns every candidate flagged by `distinct_ids`. Returns the
    list of shapes it was called with."""
    calls: list[BaseGeometry] = []

    def fake(shape: BaseGeometry, buffer: float | None = None) -> list[dict[str, Any]]:
        calls.append(shape)
        if isinstance(shape, Point):
            return [{**r, "distinct": None} for r in by_point[(shape.x, shape.y)]]
        seen: dict[str, dict[str, Any]] = {}
        for candidates in by_point.values():
            for r in candidates:
                seen[r["id"]] = r
        return [{**r, "distinct": r["id"] in distinct_ids} for r in seen.values()]

    monkeypatch.setattr(regions.poiidx, "list_regions", fake)
    return calls


def test_location_key_rounds_to_three_decimals() -> None:
    assert regions.location_key(13.40549, 52.52049) == "52.520,13.405"


def test_resolve_pending_stores_every_containing_region(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_poiidx(monkeypatch, {(8.8, 53.1): [BREMEN, GERMANY]}, {"bremen"})
    with OrmSession(engine) as session:
        r = _recording(session, "a" * 64, 8.8, 53.1)
        assert regions.resolve_pending(session) == 1
        session.commit()
        rows = session.scalars(
            select(RecordingRegion.region_id).where(
                RecordingRegion.recording_id == r.id
            )
        ).all()
        assert sorted(rows) == ["bremen", "germany"]
        refreshed = session.get(Recording, r.id)
        assert refreshed is not None
        assert refreshed.region_resolved_at is not None


def test_a_recording_in_no_region_is_resolved_and_not_asked_again(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_poiidx(monkeypatch, {(0.0, 0.0): []}, set())
    with OrmSession(engine) as session:
        r = _recording(session, "b" * 64, 0.0, 0.0)
        regions.resolve_pending(session)
        session.commit()
        refreshed = session.get(Recording, r.id)
        assert refreshed is not None
        assert refreshed.region_resolved_at is not None
        assert session.scalars(select(RecordingRegion)).all() == []
        assert session.scalars(select(LocationRegion.region_ids)).one() == []
        assert regions.resolve_pending(session) == 0
    assert len(calls) == 1


def test_recordings_at_one_location_cost_one_poiidx_call(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_poiidx(monkeypatch, {(8.8, 53.1): [BREMEN]}, {"bremen"})
    with OrmSession(engine) as session:
        _recording(session, "c" * 64, 8.8, 53.1)
        _recording(session, "d" * 64, 8.8, 53.1)
        assert regions.resolve_pending(session) == 2
    assert len(calls) == 1


def test_recordings_without_a_point_are_left_unresolved(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_poiidx(monkeypatch, {}, set())
    with OrmSession(engine) as session:
        r = _recording(session, "e" * 64, None, None)
        assert regions.resolve_pending(session) == 0
        assert r.region_resolved_at is None


def test_refresh_distinct_flags_follows_poiidx(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_poiidx(
        monkeypatch,
        {(8.8, 53.1): [BREMEN, GERMANY], (13.4, 52.5): [GERMANY]},
        {"bremen", "germany"},
    )
    with OrmSession(engine) as session:
        _recording(session, "f" * 64, 8.8, 53.1)
        _recording(session, "1" * 64, 13.4, 52.5)
        regions.resolve_regions(session)
        session.commit()
        flags = {r.id: r.is_distinct for r in session.scalars(select(Region))}
        assert flags == {"bremen": True, "germany": True}
        assert [r.id for r in regions.list_distinct_regions(session)] == [
            "bremen",
            "germany",
        ]


def test_a_recording_going_missing_flips_the_flag_without_new_recordings(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    by_point = {(8.8, 53.1): [BREMEN, GERMANY], (16.4, 48.2): [AUSTRIA]}
    _fake_poiidx(monkeypatch, by_point, {"bremen", "austria"})
    with OrmSession(engine) as session:
        _recording(session, "2" * 64, 8.8, 53.1)
        vienna = _recording(session, "3" * 64, 16.4, 48.2)
        regions.resolve_regions(session)
        session.commit()
        assert {r.id for r in regions.list_distinct_regions(session)} == {
            "bremen",
            "austria",
        }

        vienna.missing_since = datetime(2026, 10, 5, tzinfo=UTC)
        # With only the Bremen point left, poiidx would flag Bremen alone.
        _fake_poiidx(monkeypatch, {(8.8, 53.1): [BREMEN, GERMANY]}, {"bremen"})
        regions.refresh_distinct_flags(session)
        session.commit()
        assert [r.id for r in regions.list_distinct_regions(session)] == ["bremen"]


def test_refresh_with_no_live_points_clears_every_flag(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_poiidx(monkeypatch, {(8.8, 53.1): [BREMEN]}, {"bremen"})
    with OrmSession(engine) as session:
        r = _recording(session, "4" * 64, 8.8, 53.1)
        regions.resolve_regions(session)
        r.missing_since = datetime(2026, 10, 5, tzinfo=UTC)
        before = len(calls)
        regions.refresh_distinct_flags(session)
        assert len(calls) == before  # nothing to ask poiidx about
        assert regions.list_distinct_regions(session) == []


def test_reset_region_data_clears_everything_and_unresolves(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_poiidx(monkeypatch, {(8.8, 53.1): [BREMEN]}, {"bremen"})
    with OrmSession(engine) as session:
        r = _recording(session, "5" * 64, 8.8, 53.1)
        regions.resolve_regions(session)
        session.commit()
        regions.reset_region_data(session)
        session.commit()
        assert session.scalars(select(Region)).all() == []
        assert session.scalars(select(LocationRegion)).all() == []
        assert session.scalars(select(RecordingRegion)).all() == []
        session.refresh(r)
        assert r.region_resolved_at is None


def test_enqueue_is_a_no_op_without_poiidx(engine: Engine) -> None:
    assert regions.enqueue_region_resolution(engine, poiidx_database_url=None) is False


def test_enqueue_defers_one_job_and_dedupes_the_second(engine: Engine) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)
    url = "postgresql://u:p@localhost/poiidx_bats_db"

    assert regions.enqueue_region_resolution(engine, poiidx_database_url=url) is True
    assert regions.enqueue_region_resolution(engine, poiidx_database_url=url) is False


def test_resolve_pending_tolerates_leftover_recording_region_rows(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`backfill-regions --force` resets in its own transaction, outside the
    job's lock, so it can null `region_resolved_at` while a running job's
    `recording_region` rows survive. The next run must overwrite them, not
    die on the duplicate primary key forever."""
    _fake_poiidx(monkeypatch, {(8.8, 53.1): [BREMEN, GERMANY]}, set())
    with OrmSession(engine) as session:
        r = _recording(session, "6" * 64, 8.8, 53.1)
        regions.resolve_pending(session)
        session.commit()
        r.region_resolved_at = None
        session.commit()

        assert regions.resolve_pending(session) == 1
        session.commit()

        rows = session.scalars(
            select(RecordingRegion.region_id).where(
                RecordingRegion.recording_id == r.id
            )
        ).all()
        assert sorted(rows) == ["bremen", "germany"]


def test_resolve_pending_tolerates_a_region_listed_twice_in_one_answer(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_poiidx(monkeypatch, {(8.8, 53.1): [BREMEN, BREMEN]}, set())
    with OrmSession(engine) as session:
        r = _recording(session, "7" * 64, 8.8, 53.1)
        assert regions.resolve_pending(session) == 1
        session.commit()
        rows = session.scalars(
            select(RecordingRegion.region_id).where(
                RecordingRegion.recording_id == r.id
            )
        ).all()
        assert rows == ["bremen"]


def test_new_recordings_at_known_locations_resolve_from_the_cache(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The steady state: after the first run, new recordings at an already
    resolved spot -- including a spot in no region, stored as an empty array
    -- cost no poiidx call."""
    calls = _fake_poiidx(
        monkeypatch,
        {(8.8, 53.1): [BREMEN, GERMANY], (0.0, 0.0): []},
        set(),
    )
    with OrmSession(engine) as session:
        _recording(session, "8" * 64, 8.8, 53.1)
        _recording(session, "9" * 64, 0.0, 0.0)
        assert regions.resolve_pending(session) == 2
        session.commit()
        assert len(calls) == 2

        known = _recording(session, "0" * 64, 8.8, 53.1)
        nowhere = _recording(session, "1" * 63 + "0", 0.0, 0.0)
        assert regions.resolve_pending(session) == 2
        session.commit()

        assert len(calls) == 2  # both came from location_region
        known_rows = session.scalars(
            select(RecordingRegion.region_id).where(
                RecordingRegion.recording_id == known.id
            )
        ).all()
        assert sorted(known_rows) == ["bremen", "germany"]
        assert (
            session.scalars(
                select(RecordingRegion).where(
                    RecordingRegion.recording_id == nowhere.id
                )
            ).all()
            == []
        )
        assert nowhere.region_resolved_at is not None


def _task_context(
    engine: Engine, tmp_path: Path, poiidx_database_url: str | None
) -> procrastinate.JobContext:
    config = Config(
        database_url="postgresql://unused/unused",
        archive_roots=(tmp_path / "archive",),
        media_root=tmp_path / "media",
        poiidx_database_url=poiidx_database_url,
    )
    return cast(
        "procrastinate.JobContext",
        SimpleNamespace(additional_context={"config": config, "engine": engine}),
    )


def test_resolve_regions_task_does_nothing_when_poiidx_was_unconfigured_after_deferral(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_poiidx(monkeypatch, {(8.8, 53.1): [BREMEN]}, {"bremen"})
    connected: list[str] = []
    monkeypatch.setattr(site_naming, "ensure_connected", connected.append)
    with OrmSession(engine) as session:
        r = _recording(session, "2" * 64, 8.8, 53.1)
        session.commit()

        resolve_regions_task(_task_context(engine, tmp_path, None))

        session.refresh(r)
        assert r.region_resolved_at is None
    assert calls == []
    assert connected == []


def test_resolve_regions_task_connects_resolves_and_commits(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_poiidx(monkeypatch, {(8.8, 53.1): [BREMEN]}, {"bremen"})
    connected: list[str] = []
    monkeypatch.setattr(site_naming, "ensure_connected", connected.append)
    url = "postgresql://u:p@localhost/poiidx_bats_db"
    with OrmSession(engine) as session:
        _recording(session, "3" * 64, 8.8, 53.1)
        session.commit()

    resolve_regions_task(_task_context(engine, tmp_path, url))

    assert connected == [url]
    with OrmSession(engine) as fresh:
        assert [r.id for r in regions.list_distinct_regions(fresh)] == ["bremen"]
        assert fresh.scalars(select(Recording.region_resolved_at)).one() is not None

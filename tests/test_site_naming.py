from __future__ import annotations

from datetime import UTC, datetime

import pytest
from geoalchemy2.elements import WKTElement
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.jobs.app import ensure_schema
from fledermap.jobs.tasks import app as jobs_app
from fledermap.services import site_naming
from fledermap.store.models import Site, SiteNameCache


def test_poiidx_connection_kwargs_parses_a_well_formed_url() -> None:
    kwargs = site_naming._poiidx_connection_kwargs(
        "postgresql://poiidx_user:s3cret@localhost:5432/poiidx_bats_db",
    )
    assert kwargs == {
        "host": "localhost",
        "port": 5432,
        "user": "poiidx_user",
        "password": "s3cret",
        "database": "poiidx_bats_db",
    }


def test_poiidx_connection_kwargs_defaults_port_to_5432() -> None:
    kwargs = site_naming._poiidx_connection_kwargs(
        "postgresql://poiidx_user:s3cret@localhost/poiidx_bats_db",
    )
    assert kwargs["port"] == 5432


def test_poiidx_connection_kwargs_rejects_a_url_with_no_password() -> None:
    with pytest.raises(ValueError, match="FLEDERMAP_POIIDX_DATABASE_URL"):
        site_naming._poiidx_connection_kwargs(
            "postgresql://poiidx_user@localhost/poiidx_bats_db",
        )


def test_poiidx_connection_kwargs_rejects_a_url_with_no_database() -> None:
    with pytest.raises(ValueError, match="FLEDERMAP_POIIDX_DATABASE_URL"):
        site_naming._poiidx_connection_kwargs(
            "postgresql://poiidx_user:s3cret@localhost/",
        )


def test_poiidx_connection_kwargs_rejects_a_non_numeric_port() -> None:
    with pytest.raises(ValueError, match="FLEDERMAP_POIIDX_DATABASE_URL"):
        site_naming._poiidx_connection_kwargs(
            "postgresql://poiidx_user:s3cret@localhost:abc/poiidx_bats_db",
        )


def test_poiidx_connection_kwargs_rejects_a_non_postgresql_scheme() -> None:
    with pytest.raises(ValueError, match="FLEDERMAP_POIIDX_DATABASE_URL"):
        site_naming._poiidx_connection_kwargs(
            "mysql://poiidx_user:s3cret@localhost/poiidx_bats_db",
        )


def test_poiidx_connection_kwargs_error_never_echoes_the_password() -> None:
    with pytest.raises(ValueError) as exc_info:
        site_naming._poiidx_connection_kwargs(
            "postgresql://poiidx_user:s3cret@localhost/",
        )
    assert "s3cret" not in str(exc_info.value)


def test_load_filter_config_returns_the_expected_symbols() -> None:
    config = site_naming._load_filter_config()
    symbols = {entry["symbol"] for entry in config}
    assert symbols == {
        "city",
        "town",
        "village",
        "suburb",
        "forest_or_park",
        "water_body",
    }


def test_ensure_connected_calls_poiidx_init_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(site_naming, "_connected", False)
    calls: list[dict[str, object]] = []

    def fake_init(*, filter_config: object, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(site_naming.poiidx, "init", fake_init)

    site_naming.ensure_connected(
        "postgresql://poiidx_user:s3cret@localhost/poiidx_bats_db",
    )
    site_naming.ensure_connected(
        "postgresql://poiidx_user:s3cret@localhost/poiidx_bats_db",
    )

    assert len(calls) == 1
    assert calls[0]["database"] == "poiidx_bats_db"


@pytest.mark.db
def test_name_site_returns_the_cached_value_without_calling_poiidx(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("poiidx must not be called on a cache hit")

    monkeypatch.setattr(site_naming.poiidx, "get_nearest_pois", fail)
    monkeypatch.setattr(site_naming.poiidx, "get_administrative_hierarchy_string", fail)

    with OrmSession(engine) as session:
        session.add(
            SiteNameCache(
                geohash=site_naming._cache_key(13.405, 52.520),
                name="Tiergarten",
                admin_path="Berlin > Mitte",
                fetched_at=datetime(2026, 8, 28, tzinfo=UTC),
            ),
        )
        session.commit()

        result = site_naming.name_site(session, 13.405, 52.520, radius_m=300.0)

    assert result == ("Tiergarten", "Berlin > Mitte")


@pytest.mark.db
def test_name_site_prefers_the_lowest_rank_poi_over_the_nearest(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_nearest_pois",
        lambda *a, **k: [
            {"name": "Nearby Bench", "rank": 23},
            {"name": "Tiergarten", "rank": 16},
        ],
    )
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        result = site_naming.name_site(session, 13.405, 52.520, radius_m=300.0)

    assert result == ("Tiergarten", "Berlin > Mitte")


@pytest.mark.db
def test_name_site_falls_back_to_administrative_hierarchy_when_no_poi_found(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(site_naming.poiidx, "get_nearest_pois", lambda *a, **k: [])
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        result = site_naming.name_site(session, 13.405, 52.520, radius_m=300.0)

    assert result == ("Berlin > Mitte", "Berlin > Mitte")


@pytest.mark.db
def test_name_site_returns_none_when_poiidx_resolves_nothing(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(site_naming.poiidx, "get_nearest_pois", lambda *a, **k: [])
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "",
    )

    with OrmSession(engine) as session:
        result = site_naming.name_site(session, 13.405, 52.520, radius_m=300.0)
        cached = session.scalar(
            select(SiteNameCache).where(
                SiteNameCache.geohash == site_naming._cache_key(13.405, 52.520),
            ),
        )

    assert result is None
    assert cached is None  # deliberately not cached -- see name_site's docstring


@pytest.mark.db
def test_name_site_writes_through_the_cache_on_a_miss(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_nearest_pois",
        lambda *a, **k: [{"name": "Tiergarten", "rank": 16}],
    )
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        site_naming.name_site(session, 13.405, 52.520, radius_m=300.0)
        session.commit()

        cached = session.scalar(
            select(SiteNameCache).where(
                SiteNameCache.geohash == site_naming._cache_key(13.405, 52.520),
            ),
        )

    assert cached is not None
    assert cached.name == "Tiergarten"
    assert cached.admin_path == "Berlin > Mitte"


def _unnamed_site(lon: float, lat: float) -> Site:
    return Site(
        centroid=WKTElement(f"POINT({lon} {lat})", srid=4326),
        radius_m=50.0,
        recording_count=1,
        first_at=datetime(2026, 8, 28, tzinfo=UTC),
        last_at=datetime(2026, 8, 28, tzinfo=UTC),
    )


@pytest.mark.db
def test_enqueue_site_naming_is_a_noop_when_poiidx_is_unconfigured(
    engine: Engine,
) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        session.add(_unnamed_site(13.405, 52.520))
        session.commit()

        count = site_naming.enqueue_site_naming(
            session,
            engine,
            poiidx_database_url=None,
        )

    assert count == 0


@pytest.mark.db
def test_enqueue_site_naming_resolves_a_cache_hit_directly_without_a_job(
    engine: Engine,
) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        session.add(
            SiteNameCache(
                geohash=site_naming._cache_key(13.405, 52.520),
                name="Tiergarten",
                admin_path="Berlin > Mitte",
                fetched_at=datetime(2026, 8, 28, tzinfo=UTC),
            ),
        )
        site = _unnamed_site(13.405, 52.520)
        session.add(site)
        session.commit()
        site_id = site.id

        count = site_naming.enqueue_site_naming(
            session,
            engine,
            poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
        )
        session.commit()

    assert count == 0
    with OrmSession(engine) as session:
        refreshed = session.get(Site, site_id)
        assert refreshed is not None
        assert refreshed.name == "Tiergarten"
        assert refreshed.admin_path == "Berlin > Mitte"


@pytest.mark.db
def test_enqueue_site_naming_defers_a_job_on_a_cache_miss(engine: Engine) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        session.add(_unnamed_site(13.405, 52.520))
        session.commit()

        count = site_naming.enqueue_site_naming(
            session,
            engine,
            poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
        )

    assert count == 1


@pytest.mark.db
def test_enqueue_site_naming_ignores_a_site_that_already_has_a_name(
    engine: Engine,
) -> None:
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        named = _unnamed_site(13.405, 52.520)
        named.name = "Already Named"
        session.add(named)
        session.commit()

        count = site_naming.enqueue_site_naming(
            session,
            engine,
            poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
        )

    assert count == 0


@pytest.mark.db
def test_enqueue_site_naming_queueing_lock_is_coordinate_based_not_id_based(
    engine: Engine,
) -> None:
    """Two Site rows at the same rounded coordinate (as derive_sites would
    produce for the same real-world site across two rebuilds, since it gets
    a new id each time) must defer under the SAME queueing lock -- so a
    stale, still-pending job for the old id doesn't let a duplicate job pile
    up for the new one every 5-minute cycle."""
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        session.add(_unnamed_site(13.405, 52.520))
        session.commit()
        first_count = site_naming.enqueue_site_naming(
            session,
            engine,
            poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
        )

    with OrmSession(engine) as session:
        # A second, different Site row (different id) at the same rounded
        # coordinate -- simulating derive_sites having rebuilt.
        session.add(_unnamed_site(13.405, 52.520))
        session.commit()
        second_count = site_naming.enqueue_site_naming(
            session,
            engine,
            poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
        )

    assert first_count == 1
    assert (
        second_count == 0
    )  # same queueing_lock as the first -- refused as a duplicate

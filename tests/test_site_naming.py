from __future__ import annotations

from datetime import UTC, datetime

import pytest
from geoalchemy2.elements import WKTElement
from shapely.geometry import Point
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession

from fledermap.jobs.app import ensure_schema
from fledermap.jobs.tasks import app as jobs_app
from fledermap.services import site_naming
from fledermap.store.models import Site, SiteNameCache
from fledermap.util.projection import LocalProjection


def _offset_point(lon: float, lat: float, dx_m: float, dy_m: float) -> Point:
    """A point dx_m/dy_m metres from (lon, lat), via the same LocalProjection
    production code uses for the intersects check -- an exact metric offset,
    not a degrees-per-metre approximation."""
    origin = Point(lon, lat)
    projection = LocalProjection(origin)
    local_origin = projection.to_local(origin)
    return projection.to_wgs(Point(local_origin.x + dx_m, local_origin.y + dy_m))


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


def test_min_and_max_rank_match_poiidx_own_constants() -> None:
    """_MIN_RANK/_MAX_RANK are a deliberate hand-mirror of poiidx.osm's
    MIN_RANK/MAX_RANK, not an import (osm.py isn't part of poiidx's public
    API -- poiidx/__init__.py never re-exports it, and poiidx is a real
    pinned dependency, not an editable one this codebase controls). That
    means nothing ties the two copies together automatically -- this test
    is the tie: it fails loudly the day poiidx's own band changes, instead
    of _target_rank silently drifting out of sync (code review finding,
    2026-09-01)."""
    import poiidx.osm

    assert site_naming._MIN_RANK == poiidx.osm.MIN_RANK
    assert site_naming._MAX_RANK == poiidx.osm.MAX_RANK


@pytest.mark.parametrize(
    "site_radius_m",
    [0.5, 5.0, 20.0, 50.0, 100.0, 300.0, 1000.0, 5000.0, 50_000.0, 300_000.0],
)
def test_target_rank_matches_poiidx_calculate_rank(site_radius_m: float) -> None:
    """Pins _target_rank's actual formula shape to poiidx's own
    calculate_rank(radius=...), not just the MIN_RANK/MAX_RANK boundary
    constants above -- a future poiidx formula retune within the same band
    would pass the constants-only test while every non-boundary target
    silently diverged from what poiidx's own scanner would assign (code
    review finding, 2026-09-01). The one deliberate divergence, exercised by
    the 300km case: poiidx returns None below _MIN_RANK ("too coarse to be
    a POI's rank at all"), _target_rank clips to _MIN_RANK instead, since it
    always needs a concrete target to compare candidates against -- see
    _target_rank's own docstring."""
    import poiidx.osm

    poiidx_rank = poiidx.osm.calculate_rank(radius=site_radius_m)
    expected = site_naming._MIN_RANK if poiidx_rank is None else poiidx_rank
    assert site_naming._target_rank(site_radius_m) == expected


def test_load_filter_config_returns_the_expected_symbols() -> None:
    config = site_naming._load_filter_config()
    symbols = {entry["symbol"] for entry in config}
    assert symbols == {
        "city",
        "town",
        "village",
        "suburb",
        "locality",
        "square",
        "forest_or_park",
        "water_body",
    }


def test_load_filter_config_covers_the_tags_found_missing_2026_09_01() -> None:
    """Real field data (2026-09-01) showed most small real sites near
    Hannover found NOTHING within the search radius but the city itself --
    root-caused to gaps in this filter, not a rank-selection bug: e.g. a
    real park tagged landuse=recreation_ground was never indexed at all,
    since nothing in the filter asked poiidx to store it. Content-level
    (not just symbol-level) so a future accidental removal of one of these
    specific tag pairs is caught, not just a whole symbol disappearing."""
    config = site_naming._load_filter_config()
    all_pairs = {
        (key, value)
        for entry in config
        for filter_expr in entry["filters"]
        for key, value in filter_expr.items()
    }
    expected = {
        ("place", "municipality"),
        ("place", "borough"),
        ("place", "farm"),
        ("place", "locality"),
        ("place", "isolated_dwelling"),
        ("place", "square"),
        ("landuse", "recreation_ground"),
        ("leisure", "recreation_ground"),
        ("leisure", "garden"),
        ("waterway", "canal"),
        ("waterway", "stream"),
    }
    assert expected <= all_pairs


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
                geohash=site_naming._cache_key(13.405, 52.520, 50.0),
                name="Tiergarten",
                admin_path="Berlin > Mitte",
                fetched_at=datetime(2026, 8, 28, tzinfo=UTC),
            ),
        )
        session.commit()

        result = site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=50.0,
        )

    assert result == ("Tiergarten", "Berlin > Mitte")


@pytest.mark.db
def test_name_site_force_bypasses_the_cache(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """force=True re-queries poiidx even on a cache hit -- for a coordinate
    whose cached name predates a poiidx filter-config widening or reindex,
    the normal cache-first path would serve the stale answer forever."""
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_nearest_pois",
        lambda *a, **k: [{"name": "Fresh Match", "rank": 16}],
    )
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        session.add(
            SiteNameCache(
                geohash=site_naming._cache_key(13.405, 52.520, 50.0),
                name="Stale Match",
                admin_path="Berlin > Mitte",
                fetched_at=datetime(2026, 8, 28, tzinfo=UTC),
            ),
        )
        session.commit()

        result = site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=50.0,
            force=True,
        )

    assert result == ("Fresh Match", "Berlin > Mitte")


@pytest.mark.db
def test_name_site_force_updates_the_existing_cache_row_in_place(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced refresh must not try to INSERT a second row under the same
    (unique) geohash -- it has to update the existing one."""
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_nearest_pois",
        lambda *a, **k: [{"name": "Fresh Match", "rank": 16}],
    )
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        session.add(
            SiteNameCache(
                geohash=site_naming._cache_key(13.405, 52.520, 50.0),
                name="Stale Match",
                admin_path="Berlin > Mitte",
                fetched_at=datetime(2026, 8, 28, tzinfo=UTC),
            ),
        )
        session.commit()

        site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=50.0,
            force=True,
        )
        session.commit()

        rows = session.scalars(
            select(SiteNameCache).where(
                SiteNameCache.geohash == site_naming._cache_key(13.405, 52.520, 50.0),
            ),
        ).all()

    assert len(rows) == 1
    assert rows[0].name == "Fresh Match"


@pytest.mark.db
def test_name_site_prefers_the_poi_whose_rank_matches_a_small_sites_own_scale(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tiny site (20m radius) wants the most specific name available, not
    the broad one -- generalises the old (buggy) 'lowest rank always wins'
    rule with a rank matched to the site's own scale (design spec SN-7,
    corrected 2026-09-01)."""
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_nearest_pois",
        lambda *a, **k: [
            {"name": "Nearby Bench", "rank": 23, "coordinates": Point(13.405, 52.520)},
            {"name": "Tiergarten", "rank": 16},
        ],
    )
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        result = site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=20.0,
        )

    assert result == ("Nearby Bench", "Berlin > Mitte")


@pytest.mark.db
def test_name_site_prefers_a_broader_poi_when_the_site_is_large(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flip side of the test above: a large site (4km radius -- e.g. a
    transect-derived hotspot, not a stationary point) wants the broad name,
    not the most specific candidate in range. Same two candidates, opposite
    winner, purely from site_radius_m."""
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_nearest_pois",
        lambda *a, **k: [
            {"name": "Nearby Bench", "rank": 23, "coordinates": Point(13.405, 52.520)},
            {"name": "Tiergarten", "rank": 16},
        ],
    )
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        result = site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=4000.0,
        )

    assert result == ("Tiergarten", "Berlin > Mitte")


@pytest.mark.db
def test_name_site_demotes_a_specific_poi_proven_outside_the_site(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A candidate specific enough to matter (rank > 19) whose own geometry
    sits well outside the site loses to a broader candidate that isn't
    checked, even though its rank is a worse match for the site's own scale
    -- reproduces the real 'named after something outside its borders' bug
    found against real field data 2026-09-01."""
    far = _offset_point(13.405, 52.520, dx_m=300.0, dy_m=0.0)
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_nearest_pois",
        lambda *a, **k: [
            {"name": "Nearby Bench", "rank": 23, "coordinates": far},
            {"name": "Tiergarten", "rank": 16},
        ],
    )
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        result = site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=20.0,
        )

    assert result == ("Tiergarten", "Berlin > Mitte")


@pytest.mark.db
def test_name_site_tolerates_a_near_miss_within_the_margin(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A candidate just outside the site's own radius but within the 15m
    tolerance margin (GPS/OSM-digitization noise) is NOT demoted -- found
    against a real site where a 3m miss would otherwise have flipped an
    already-correct name to a needlessly broad one."""
    near_miss = _offset_point(13.405, 52.520, dx_m=30.0, dy_m=0.0)
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_nearest_pois",
        lambda *a, **k: [
            {"name": "Nearby Bench", "rank": 23, "coordinates": near_miss},
            {"name": "Tiergarten", "rank": 16},
        ],
    )
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        result = site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=20.0,
        )

    assert result == ("Nearby Bench", "Berlin > Mitte")


@pytest.mark.db
def test_name_site_never_checks_geometry_for_a_low_rank_candidate(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rank <= 19 is exempt from the intersects check regardless of actual
    distance -- isolates the threshold itself from the far/near behaviour
    covered above. Without the exemption this would still resolve to
    'Tiergarten' by rank alone (2 candidates in this data are *both* far, so
    if geometry mattered for the exempt one too, we couldn't tell); the
    exemption is what makes it certain rather than incidental."""
    far = _offset_point(13.405, 52.520, dx_m=300.0, dy_m=0.0)
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_nearest_pois",
        lambda *a, **k: [
            {"name": "Nearby Bench", "rank": 21, "coordinates": far},
            {"name": "Tiergarten", "rank": 17},
        ],
    )
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        result = site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=20.0,
        )

    assert result == ("Tiergarten", "Berlin > Mitte")


@pytest.mark.db
def test_name_site_exempts_rank_19_but_checks_rank_20(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact _INTERSECTS_RANK_THRESHOLD boundary: rank 19 is exempt
    (no geometry needed), rank 20 is checked. Without demotion, rank 20
    would win here on rank-distance alone (|20-23|=3 < |19-23|=4) -- with
    it, the far rank-20 candidate is demoted and rank 19 wins instead,
    proving the boundary is exactly where the threshold constant says
    (code review test-coverage finding, 2026-09-01)."""
    far = _offset_point(13.405, 52.520, dx_m=300.0, dy_m=0.0)
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_nearest_pois",
        lambda *a, **k: [
            {"name": "Rank Nineteen", "rank": 19},
            {"name": "Rank Twenty", "rank": 20, "coordinates": far},
        ],
    )
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        result = site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=20.0,
        )

    assert result == ("Rank Nineteen", "Berlin > Mitte")


@pytest.mark.db
def test_name_site_breaks_ties_by_rank_distance_among_demoted_candidates(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When every candidate is demoted (none intersects), the tie-break is
    still rank-distance-to-target, not list order. Every existing 'demoted'
    test pairs the far candidate with an exempt low-rank fallback, so
    outside_penalty alone decided the winner -- this is the only test where
    abs(rank - target) actually has to do the deciding (code review
    test-coverage finding, 2026-09-01)."""
    far = _offset_point(13.405, 52.520, dx_m=300.0, dy_m=0.0)
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_nearest_pois",
        lambda *a, **k: [
            {"name": "Rank Twenty", "rank": 20, "coordinates": far},
            {"name": "Rank Twenty Three", "rank": 23, "coordinates": far},
        ],
    )
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        # site_radius_m=20 -> target_rank=23: "Rank Twenty Three" is the
        # closer match (distance 0) even though both are equally demoted.
        result = site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=20.0,
        )

    assert result == ("Rank Twenty Three", "Berlin > Mitte")


@pytest.mark.db
def test_name_site_demotes_a_candidate_with_no_geometry_instead_of_crashing(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Poi.coordinates` is schema-NOT-NULL in poiidx today, but nothing
    here should depend on that holding forever -- a candidate specific
    enough to need checking (rank > 19) with no usable geometry must be
    treated as unverifiable (demoted), not crash the whole resolution
    (code review finding, 2026-09-01)."""
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_nearest_pois",
        lambda *a, **k: [
            {"name": "No Geometry", "rank": 23, "coordinates": None},
            {"name": "Tiergarten", "rank": 16},
        ],
    )
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        result = site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=20.0,
        )

    assert result == ("Tiergarten", "Berlin > Mitte")


@pytest.mark.db
def test_name_site_widens_the_search_radius_to_the_sites_own_extent(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A site bigger than the configured search-radius default must not
    have its own footprint go unsearched."""
    captured: dict[str, object] = {}

    def fake_get_nearest_pois(point: object, **kwargs: object) -> list[object]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(site_naming.poiidx, "get_nearest_pois", fake_get_nearest_pois)
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "",
    )

    with OrmSession(engine) as session:
        site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=1000.0,
        )

    assert captured["max_distance"] == 1000.0


@pytest.mark.db
def test_name_site_does_not_shrink_the_search_radius_below_the_configured_default(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_get_nearest_pois(point: object, **kwargs: object) -> list[object]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(site_naming.poiidx, "get_nearest_pois", fake_get_nearest_pois)
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "",
    )

    with OrmSession(engine) as session:
        site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=10.0,
        )

    assert captured["max_distance"] == 300.0


@pytest.mark.db
def test_name_site_never_passes_buffer_to_poiidx(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither poiidx call is ever given `buffer=`, on ANY site size --
    confirmed 2026-09-01 against the real installed poiidx==0.0.9 (not
    mocked): `PoiIdx.init_regions_by_shape` does
    `local_shape.convex_hull().buffer(buffer)` whenever `buffer is not None`,
    but shapely's `convex_hull` is a PROPERTY, not a method -- calling it
    with `()` invokes the geometry it returns (a Point, say) as if IT were
    callable, crashing with `TypeError: 'Point' object is not callable' on
    every single call, for any buffer value. This is a real poiidx bug, not
    a design choice here, and it isn't buffer-value-dependent, so there is
    no safe value to pass -- omitting `buffer` entirely is the only option
    until poiidx ships a fix (tracked in docs/references.md). An earlier
    version of this code DID pass buffer (to fix a real, separate
    region-confinement gap for large sites) and broke every name_site_task
    run in production the moment it merged -- no test caught it because
    every test here mocks poiidx.get_nearest_pois and never touches the
    real package."""
    nearest_kwargs: dict[str, object] = {}
    admin_kwargs: dict[str, object] = {}

    def fake_get_nearest_pois(point: object, **kwargs: object) -> list[object]:
        nearest_kwargs.update(kwargs)
        return []

    def fake_get_administrative_hierarchy_string(
        point: object, **kwargs: object
    ) -> str:
        admin_kwargs.update(kwargs)
        return ""

    monkeypatch.setattr(site_naming.poiidx, "get_nearest_pois", fake_get_nearest_pois)
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        fake_get_administrative_hierarchy_string,
    )

    with OrmSession(engine) as session:
        site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=1000.0,
        )

    assert "buffer" not in nearest_kwargs
    assert "buffer" not in admin_kwargs


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
        result = site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=50.0,
        )

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
        result = site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=50.0,
        )
        cached = session.scalar(
            select(SiteNameCache).where(
                SiteNameCache.geohash == site_naming._cache_key(13.405, 52.520, 50.0),
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
        site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=50.0,
        )
        session.commit()

        cached = session.scalar(
            select(SiteNameCache).where(
                SiteNameCache.geohash == site_naming._cache_key(13.405, 52.520, 50.0),
            ),
        )

    assert cached is not None
    assert cached.name == "Tiergarten"
    assert cached.admin_path == "Berlin > Mitte"


@pytest.mark.db
def test_name_site_cache_does_not_conflate_sites_of_very_different_scale(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two Sites at the same rounded coordinate but very different
    site_radius_m (a small stationary site and a large transect-derived one
    happening to centre on the same spot) must resolve -- and cache --
    independently. SiteNameCache.geohash is unique, so if the cache key were
    coordinate-only, whichever site resolved first would permanently win the
    slot for both, defeating SN-7's fix for the second one (code review
    finding, 2026-09-01)."""
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_nearest_pois",
        lambda *a, **k: [
            {"name": "Nearby Bench", "rank": 23, "coordinates": Point(13.405, 52.520)},
            {"name": "Tiergarten", "rank": 16},
        ],
    )
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        small_site_result = site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=20.0,
        )
        session.commit()

        large_site_result = site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=4000.0,
        )

    assert small_site_result == ("Nearby Bench", "Berlin > Mitte")
    assert large_site_result == ("Tiergarten", "Berlin > Mitte")


@pytest.mark.db
def test_name_site_cache_separates_sites_whose_target_rank_bucket_would_collide(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The test above uses radii (20 vs 4000) whose _target_rank buckets
    already differ -- it would pass even with the coarser bucketing this
    test catches. _target_rank clips to _MAX_RANK for essentially every
    radius under ~250m, so two sites at 10m and 200m both bucket to the same
    target rank despite genuinely different demotion behaviour (code review
    finding, 2026-09-01: _cache_key must bucket the radius itself, not the
    saturating target-rank derived from it)."""
    far = _offset_point(13.405, 52.520, dx_m=100.0, dy_m=0.0)
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_nearest_pois",
        lambda *a, **k: [
            {"name": "Nearby Bench", "rank": 23, "coordinates": far},
            {"name": "Tiergarten", "rank": 16},
        ],
    )
    monkeypatch.setattr(
        site_naming.poiidx,
        "get_administrative_hierarchy_string",
        lambda *a, **k: "Berlin > Mitte",
    )

    with OrmSession(engine) as session:
        # 100m away is outside a 10m site's padded extent (10+15=25m) --
        # the specific candidate is demoted, so the exempt broad one wins.
        small_site_result = site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=10.0,
        )
        session.commit()

        # ...but inside a 200m site's (200+15=215m) -- not demoted, and its
        # rank is a perfect match for a site this size, so it wins outright.
        large_site_result = site_naming.name_site(
            session,
            13.405,
            52.520,
            radius_m=300.0,
            site_radius_m=200.0,
        )

    assert small_site_result == ("Tiergarten", "Berlin > Mitte")
    assert large_site_result == ("Nearby Bench", "Berlin > Mitte")


def _unnamed_site(lon: float, lat: float, radius_m: float = 50.0) -> Site:
    return Site(
        centroid=WKTElement(f"POINT({lon} {lat})", srid=4326),
        radius_m=radius_m,
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
                geohash=site_naming._cache_key(13.405, 52.520, 50.0),
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
def test_enqueue_site_naming_force_defers_a_job_instead_of_trusting_the_cache(
    engine: Engine,
) -> None:
    """The opposite of the test above: force=True must not take the
    inline-resolve-from-cache shortcut, even though a cache entry exists --
    it defers a job (which re-queries poiidx) instead."""
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        session.add(
            SiteNameCache(
                geohash=site_naming._cache_key(13.405, 52.520, 50.0),
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
            force=True,
        )
        session.commit()

    assert count == 1
    with OrmSession(engine) as session:
        refreshed = session.get(Site, site_id)
        assert refreshed is not None
        assert refreshed.name is None  # not resolved inline -- the job hasn't run


@pytest.mark.db
def test_enqueue_site_naming_force_does_not_collide_with_a_pending_normal_job(
    engine: Engine,
) -> None:
    """A forced run must use a distinct queueing lock from the normal path
    -- otherwise an already-pending normal job for the same coordinate
    silently swallows the forced one as an AlreadyEnqueued duplicate,
    exactly the collision SN's coordinate-based dedup relies on for
    legitimate rebuilds."""
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        session.add(_unnamed_site(13.405, 52.520))
        session.commit()
        normal_count = site_naming.enqueue_site_naming(
            session,
            engine,
            poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
        )

    with OrmSession(engine) as session:
        session.add(_unnamed_site(13.405, 52.520))
        session.commit()
        forced_count = site_naming.enqueue_site_naming(
            session,
            engine,
            poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
            force=True,
        )

    assert normal_count == 1
    assert forced_count == 1  # distinct lock -- not swallowed as a duplicate


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


@pytest.mark.db
def test_enqueue_site_naming_does_not_conflate_queueing_locks_across_scales(
    engine: Engine,
) -> None:
    """The opposite of the test above: two Site rows at the same rounded
    coordinate but genuinely different radius_m are NOT the same site
    reappearing across a rebuild -- each must get its own naming job, not
    have the second silently swallowed as an AlreadyEnqueued duplicate of
    the first (code review finding, 2026-09-01 -- a direct consequence of
    the target-rank cache-bucketing bug _cache_key had at the time)."""
    jobs_app.open(engine)
    ensure_schema(jobs_app, engine)

    with OrmSession(engine) as session:
        session.add(_unnamed_site(13.405, 52.520, radius_m=10.0))
        session.commit()
        first_count = site_naming.enqueue_site_naming(
            session,
            engine,
            poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
        )

    with OrmSession(engine) as session:
        session.add(_unnamed_site(13.405, 52.520, radius_m=200.0))
        session.commit()
        second_count = site_naming.enqueue_site_naming(
            session,
            engine,
            poiidx_database_url="postgresql://u:p@localhost/poiidx_bats_db",
        )

    assert first_count == 1
    assert second_count == 1  # different scale -- its own job, not swallowed

"""`fetch_range` against hand-written, synthetic responses. A real service reply must never be
committed here: IUCN T&C §4 forbids redistributing the data in whole or in part."""

from __future__ import annotations

from typing import Any

import pytest

from fledermap.services.iucn_ranges import IucnFetchError, fetch_range

SQUARE = [[[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]]


class _Response:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _FakeGet:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return _Response(self.payload)


def _feature(
    geometry: dict[str, Any] | None,
    presence: int = 1,
    origin: int = 1,
) -> Any:
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {"presence": presence, "origin": origin},
    }


def test_a_polygon_becomes_a_multipolygon_with_its_codes() -> None:
    get = _FakeGet(
        {
            "type": "FeatureCollection",
            "features": [
                _feature(
                    {"type": "Polygon", "coordinates": SQUARE},
                    presence=2,
                    origin=4,
                ),
            ],
        },
    )

    polygons = fetch_range("Pipistrellus pipistrellus", get=get)

    assert len(polygons) == 1
    assert (polygons[0].presence, polygons[0].origin) == (2, 4)
    assert polygons[0].wkt.startswith("MULTIPOLYGON")


def test_a_multipolygon_is_kept_as_one_polygon_row() -> None:
    get = _FakeGet(
        {
            "features": [
                _feature({"type": "MultiPolygon", "coordinates": [SQUARE, SQUARE]}),
            ],
        },
    )
    assert len(fetch_range("X y", get=get)) == 1


def test_no_features_means_the_service_does_not_know_the_species() -> None:
    assert fetch_range("Nonexistent species", get=_FakeGet({"features": []})) == []


def test_the_query_filters_presence_and_quotes_the_name() -> None:
    get = _FakeGet({"features": []})
    fetch_range("Pipistrellus o'brieni", get=get)

    call = get.calls[0]
    where = call["params"]["where"]
    assert "sci_name = 'Pipistrellus o''brieni'" in where
    assert "presence IN (1,2,3,6)" in where
    assert call["params"]["f"] == "geojson"
    assert call["timeout"] > 0
    assert "fledermap" in call["headers"]["User-Agent"].lower()


def test_an_error_reply_with_http_200_raises_instead_of_reading_as_not_found() -> None:
    """ArcGIS reports a rejected query as HTTP 200 + an `error` object. Reading that as "no
    features" would cache a transient failure as `not_found` forever."""
    get = _FakeGet({"error": {"code": 400, "message": "Unable to complete operation."}})
    with pytest.raises(IucnFetchError, match="rejected"):
        fetch_range("Pipistrellus pipistrellus", get=get)


@pytest.mark.parametrize(
    "payload",
    [
        {"features": [], "exceededTransferLimit": True},
        {"features": [], "properties": {"exceededTransferLimit": True}},
    ],
)
def test_a_truncated_reply_raises(payload: dict[str, Any]) -> None:
    with pytest.raises(IucnFetchError, match="truncated"):
        fetch_range("Pipistrellus pipistrellus", get=_FakeGet(payload))


def test_a_reply_without_a_features_list_raises() -> None:
    with pytest.raises(IucnFetchError, match="features"):
        fetch_range("Pipistrellus pipistrellus", get=_FakeGet({"unexpected": 1}))


def test_a_non_polygon_geometry_raises() -> None:
    get = _FakeGet(
        {"features": [_feature({"type": "Point", "coordinates": [0.0, 0.0]})]},
    )
    with pytest.raises(IucnFetchError, match="geometry"):
        fetch_range("Pipistrellus pipistrellus", get=get)


def test_a_feature_missing_its_presence_raises() -> None:
    feature = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": SQUARE},
        "properties": {"origin": 1},
    }
    with pytest.raises(IucnFetchError, match="presence"):
        fetch_range("Pipistrellus pipistrellus", get=_FakeGet({"features": [feature]}))


def test_a_feature_with_no_geometry_is_skipped() -> None:
    get = _FakeGet({"features": [_feature(None)]})
    assert fetch_range("X y", get=get) == []

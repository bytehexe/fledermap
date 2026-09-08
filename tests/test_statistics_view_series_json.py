# tests/test_statistics_view_series_json.py
"""Pure unit coverage for views/statistics.py's `_series_json`, kept out of
test_statistics_view.py because that module is DB-marked
(`pytestmark = pytest.mark.db`) end-to-end and `_series_json` needs no
database at all.

The point of this test is the bucket-key contract with statistics_charts.js:
`_series_json` deliberately renders the None (Other/single-series) bucket key
as the literal string "null", and `seriesToChartData` reads it back via
`bucket[null]` (a JS object subscript coerces to the same string). Nothing
else asserts this shape -- see the final-review finding this test closes."""

from __future__ import annotations

import json

from fledermap.services.statistics import SeriesByMonth
from fledermap.store.models import Taxon
from fledermap.web.views.statistics import _series_json


def test_series_json_renders_the_other_bucket_key_as_the_string_null() -> None:
    series = SeriesByMonth(
        labels=("Jan", "Feb"),
        taxa=[],
        other_included=False,
        single_species=True,
        buckets=[{None: 3}, {None: 0}],
    )

    result = _series_json(series)

    assert result["buckets"] == [{"null": 3}, {"null": 0}]


def test_series_json_bucket_keys_are_all_strings_so_flasks_sort_keys_json_dumps_does_not_crash() -> (
    None
):
    """Real 500 caught live on /statistics: Flask's DefaultJSONProvider has
    sort_keys=True, and json.dumps(..., sort_keys=True) raises TypeError
    comparing a str key ("null") against an int key (a taxon id) once a
    series mixes a real taxon breakdown with an Other/None bucket -- exactly
    recording_counts_by_month's real shape whenever more than DEFAULT_TOP_N
    taxa exist. A single-species series (the only case the sibling test
    above covers) never has this mix, so it never caught it."""
    taxon = Taxon(id=7, rank="species", scientific_name="Eptesicus serotinus")
    series = SeriesByMonth(
        labels=("Jan", "Feb"),
        taxa=[taxon],
        other_included=True,
        single_species=False,
        buckets=[{7: 2, None: 1}, {7: 0, None: 3}],
    )

    result = _series_json(series)

    assert result["buckets"] == [{"7": 2, "null": 1}, {"7": 0, "null": 3}]
    json.dumps(result, sort_keys=True)  # must not raise TypeError

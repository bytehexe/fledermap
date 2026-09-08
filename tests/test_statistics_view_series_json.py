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

from fledermap.services.statistics import SeriesByMonth
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

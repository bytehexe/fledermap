from __future__ import annotations

import pytest

from fledermap.domain.codes import IdSource
from fledermap.web.params import (
    detector_label,
    parse_bool,
    parse_region_filter,
    parse_source,
    parse_taxon_filter,
    parse_verdict,
)


def test_parse_region_filter_treats_absent_and_empty_as_none() -> None:
    assert parse_region_filter(None) is None
    assert parse_region_filter("") is None


def test_parse_region_filter_passes_the_id_through_unchanged() -> None:
    assert parse_region_filter("europe/germany/bremen") == "europe/germany/bremen"


def test_parse_source_absent_is_none() -> None:
    assert parse_source(None) is None


def test_parse_source_empty_string_is_none() -> None:
    assert parse_source("") is None


def test_parse_source_real_id_source_value() -> None:
    assert parse_source("emt.guano") is IdSource.EMT_GUANO


def test_parse_source_emt_sentinel() -> None:
    assert parse_source("emt") == "emt"


def test_parse_source_invalid_value_raises() -> None:
    with pytest.raises(ValueError, match="emt.nonsense"):
        parse_source("emt.nonsense")


def test_parse_bool_absent_is_false() -> None:
    assert parse_bool(None) is False


def test_parse_bool_empty_string_is_false() -> None:
    assert parse_bool("") is False


def test_parse_bool_present_is_true() -> None:
    assert parse_bool("1") is True


def test_parse_taxon_filter_absent_is_none() -> None:
    assert parse_taxon_filter(None) is None


def test_parse_taxon_filter_empty_string_is_none() -> None:
    assert parse_taxon_filter("") is None


def test_parse_taxon_filter_numeric_is_an_int() -> None:
    assert parse_taxon_filter("42") == 42


def test_parse_taxon_filter_unmapped_sentinel_passes_through() -> None:
    assert parse_taxon_filter("unmapped") == "unmapped"


def test_detector_label_none_falls_back() -> None:
    assert detector_label(None) == "unknown detector"


def test_detector_label_empty_string_falls_back() -> None:
    assert detector_label("") == "unknown detector"


def test_detector_label_bare_separator_falls_back() -> None:
    """`derive.sessions._detector_key` always joins make/serial with `\\x1f`
    (ASCII Unit Separator), so a session with neither field populated still
    gets a *non-empty* `detector_key` of exactly `"\\x1f"` -- never the empty
    string a plain `detector_key or 'unknown detector'` template check was
    written to catch. Caught via Task 13's manual walkthrough against real
    field-recording data (a real derived session renders exactly this key),
    not by any test until now."""
    assert detector_label("\x1f") == "unknown detector"


def test_detector_label_strips_separator_when_make_present() -> None:
    """Matches this project's own real field recordings: `make` populated,
    `serial` blank."""
    assert detector_label("Wildlife Acoustics\x1f") == "Wildlife Acoustics"


def test_detector_label_strips_separator_when_serial_present() -> None:
    assert detector_label("\x1fEMT2-001") == "EMT2-001"


def test_detector_label_joins_both_fields_with_a_space() -> None:
    assert detector_label("EMT\x1f1") == "EMT 1"


def test_parse_verdict_accepts_unidentified() -> None:
    assert parse_verdict("unidentified") == "unidentified"

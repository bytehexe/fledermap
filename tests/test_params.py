from __future__ import annotations

from fledermap.web.params import detector_label


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

from __future__ import annotations

from fledermap.domain.codes import Verdict, sentinel_verdict


def test_recognises_filename_style_noid() -> None:
    assert sentinel_verdict("NoID") is Verdict.NO_ID


def test_recognises_filename_style_noise() -> None:
    assert sentinel_verdict("NOISE") is Verdict.NOISE


def test_recognises_guano_style_no_id_with_space() -> None:
    """GUANO's and wamd's `Species Auto ID` field spell the sentinel with a
    space -- confirmed against real field recordings, 2026-08-26 (both
    chunks in the same file wrote the literal string `No ID`)."""
    assert sentinel_verdict("No ID") is Verdict.NO_ID


def test_is_case_insensitive() -> None:
    assert sentinel_verdict("no id") is Verdict.NO_ID
    assert sentinel_verdict("noise") is Verdict.NOISE


def test_real_species_code_is_not_a_sentinel() -> None:
    assert sentinel_verdict("EPTSER") is None

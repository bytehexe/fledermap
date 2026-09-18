from __future__ import annotations

from fledermap.domain.codes import (
    IdSource,
    Verdict,
    is_emt_automatic,
    is_manual,
    is_multi_support,
    is_only_noise,
    sentinel_verdict,
)


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


def test_is_manual_true_for_manual_and_emt_manual() -> None:
    assert is_manual(IdSource.MANUAL) is True
    assert is_manual(IdSource.EMT_MANUAL) is True


def test_is_manual_false_for_automatic_sources() -> None:
    assert is_manual(IdSource.EMT_GUANO) is False
    assert is_manual(IdSource.BATDETECT2) is False
    assert is_manual(IdSource.FLEDERMAP_NOISE) is False


def test_is_only_noise_true_only_for_fledermap_noise() -> None:
    assert is_only_noise(IdSource.FLEDERMAP_NOISE) is True
    assert is_only_noise(IdSource.BATDETECT2) is False
    assert is_only_noise(IdSource.MANUAL) is False


def test_is_multi_support_true_for_manual_batdetect2_battybirdnet() -> None:
    assert is_multi_support(IdSource.MANUAL) is True
    assert is_multi_support(IdSource.BATDETECT2) is True
    assert is_multi_support(IdSource.BATTYBIRDNET) is True


def test_is_multi_support_false_for_plain_sources() -> None:
    assert is_multi_support(IdSource.EMT_MANUAL) is False
    assert is_multi_support(IdSource.EMT_GUANO) is False
    assert is_multi_support(IdSource.EMT_WAMD) is False
    assert is_multi_support(IdSource.EMT_FILENAME) is False
    assert is_multi_support(IdSource.FLEDERMAP_NOISE) is False


def test_is_emt_automatic_true_for_the_three_device_materializations() -> None:
    assert is_emt_automatic(IdSource.EMT_GUANO) is True
    assert is_emt_automatic(IdSource.EMT_WAMD) is True
    assert is_emt_automatic(IdSource.EMT_FILENAME) is True


def test_is_emt_automatic_false_for_emt_manual_and_other_sources() -> None:
    """`emt.manual` is a human's on-device correction, not another
    materialization of the device's own auto-ID -- it must stay outside the
    combined-EMT display/filter grouping."""
    assert is_emt_automatic(IdSource.EMT_MANUAL) is False
    assert is_emt_automatic(IdSource.MANUAL) is False
    assert is_emt_automatic(IdSource.BATDETECT2) is False
    assert is_emt_automatic(IdSource.FLEDERMAP_NOISE) is False

from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pytest

from fledermap.media.sound_level_spectrum import (
    compute_sound_level_spectrum,
    find_major_peaks_hz,
)

_FIXTURES = Path(__file__).parent / "data" / "sound_level_spectra"


def _sine(freq_hz: float, samplerate_hz: float, duration_s: float) -> np.ndarray:
    n = int(samplerate_hz * duration_s)
    t = np.arange(n) / samplerate_hz
    return np.sin(2 * np.pi * freq_hz * t)


def test_peak_lands_near_the_tone_s_frequency() -> None:
    samplerate_hz = 256_000.0
    result = compute_sound_level_spectrum(
        _sine(40_000.0, samplerate_hz, 0.02), samplerate_hz
    )
    assert result.peak_freq_hz is not None
    assert abs(result.peak_freq_hz - 40_000.0) < 2_000.0


def test_db_is_self_relative_with_zero_at_the_peak() -> None:
    samplerate_hz = 256_000.0
    result = compute_sound_level_spectrum(
        _sine(40_000.0, samplerate_hz, 0.02), samplerate_hz
    )
    assert result.db.max() == 0.0


def test_db_is_clipped_to_the_dynamic_range() -> None:
    samplerate_hz = 256_000.0
    result = compute_sound_level_spectrum(
        _sine(40_000.0, samplerate_hz, 0.02), samplerate_hz, dynamic_range_db=40.0
    )
    assert result.db.min() >= -40.0


def test_a_short_slice_still_returns_output_without_erroring() -> None:
    # Simulates a narrow Select-tool span -- shorter than scipy.signal.welch's default
    # nperseg (256), which would otherwise raise if not handled.
    samplerate_hz = 256_000.0
    short_samples = _sine(40_000.0, samplerate_hz, 0.0005)  # 128 samples
    result = compute_sound_level_spectrum(short_samples, samplerate_hz)
    assert len(result.freqs_hz) > 0
    assert len(result.db) == len(result.freqs_hz)


def test_peak_search_excludes_the_low_frequency_noise_band() -> None:
    # A loud low-frequency component (wind/handling noise, below the cutoff) plus a quieter but
    # genuine "call" component above it -- matches spectrogram.py's own peak-exclusion rationale
    # (CLAUDE.md's Ingest/derived-media notes): without the exclusion, argmax would land on the
    # louder low-frequency component instead of the real call.
    samplerate_hz = 256_000.0
    duration_s = 0.02
    cutoff_hz = 5_000.0
    loud_low_freq = 2.0 * _sine(1_000.0, samplerate_hz, duration_s)
    quiet_call = 0.2 * _sine(40_000.0, samplerate_hz, duration_s)
    result = compute_sound_level_spectrum(
        loud_low_freq + quiet_call, samplerate_hz, peak_min_freq_hz=cutoff_hz
    )
    assert result.peak_freq_hz is not None
    assert abs(result.peak_freq_hz - 40_000.0) < 2_000.0


def test_all_silent_input_raises_no_warning_and_returns_sane_values() -> None:
    # All-zero input (a genuinely reachable case: silent audio in a narrow Select-tool span).
    # Must not raise divide-by-zero warning, and must return valid (no inf/nan) dB values
    # at the floor of the scale (not at 0 dB, which is the loud end).
    samplerate_hz = 256_000.0
    dynamic_range_db = 80.0
    silent_samples = np.zeros(512)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = compute_sound_level_spectrum(
            silent_samples, samplerate_hz, dynamic_range_db=dynamic_range_db
        )
        # Assert no warnings were raised.
        assert len(w) == 0, f"Unexpected warning(s): {[str(x.message) for x in w]}"
    # Assert all dB values are at the floor of the scale (silent end, not loud end).
    assert np.allclose(result.db, -dynamic_range_db), (
        f"Expected all dB values at -{dynamic_range_db}, got {result.db}"
    )
    # Assert all dB values are finite (no inf or nan).
    assert np.isfinite(result.db).all(), "dB array contains inf or nan"
    # Assert reasonable peak frequency (not inf/nan).
    assert result.peak_freq_hz is not None
    assert np.isfinite(result.peak_freq_hz), "peak_freq_hz is inf or nan"


def test_no_peak_is_reported_when_the_loudest_bin_is_the_search_boundary() -> None:
    # A loud tone just BELOW the cutoff (within a bin of it) puts the main lobe across the cutoff; the bins above fall away from the
    # cutoff outward -- so the loudest searched bin is the very first one. That bin marks where the
    # exclusion cut through a still-falling component, not a peak; reporting it would present the
    # cutoff itself as a measurement.
    samplerate_hz = 256_000.0
    result = compute_sound_level_spectrum(
        _sine(4_900.0, samplerate_hz, 0.02), samplerate_hz, peak_min_freq_hz=5_000.0
    )
    assert result.peak_freq_hz is None


def test_a_peak_at_the_first_bin_is_kept_when_nothing_was_excluded() -> None:
    # Without a cutoff (or one below the first bin) the first bin is DC-side but was not created by
    # an exclusion boundary, so the boundary rule does not apply.
    samplerate_hz = 256_000.0
    result = compute_sound_level_spectrum(
        _sine(500.0, samplerate_hz, 0.02), samplerate_hz, peak_min_freq_hz=0.0
    )
    assert result.peak_freq_hz is not None


def _load_fixture(name: str) -> tuple[np.ndarray, np.ndarray]:
    data = json.loads((_FIXTURES / f"{name}.json").read_text())
    freqs_hz = np.arange(data["n_bins"]) * data["bin_width_hz"]
    return freqs_hz, np.array(data["db"])


# Real whole-file spectra with Janna's expected peak counts (spec
# 2026-09-18-fledermap-sound-level-peaks-design.md, "Reference cases"). Frequencies are matched
# within one bin (125 Hz).
@pytest.mark.parametrize(
    ("fixture", "expected_hz"),
    [
        ("three_peaks_11_21_45khz", [11_000.0, 20_900.0, 44_900.0]),
        ("one_dominant_peak_53khz", [53_000.0]),
        ("two_peaks_11_27khz", [10_900.0, 27_000.0]),
    ],
)
def test_reference_recordings_yield_the_expected_major_peaks(
    fixture: str, expected_hz: list[float]
) -> None:
    freqs_hz, db = _load_fixture(fixture)
    peaks = find_major_peaks_hz(freqs_hz, db, peak_min_freq_hz=5_000.0)
    assert peaks == pytest.approx(expected_hz, abs=125.0)


def _spiky_db(
    n_bins: int, heights_db: dict[int, float], floor_db: float = -60.0
) -> np.ndarray:
    """A `floor_db` baseline with a triangular spike (5 bins wide each side) topped at each
    `index: height` -- an unambiguous, hand-countable set of peaks."""
    db = np.full(n_bins, floor_db)
    for index, height in heights_db.items():
        for offset in range(-5, 6):
            j = index + offset
            if 0 <= j < n_bins:
                db[j] = max(db[j], height - abs(offset) * (height - floor_db) / 5)
    return db


def test_at_most_five_peaks_are_kept_the_most_prominent_and_the_global_one() -> None:
    freqs_hz = np.arange(400) * 100.0
    heights = {
        20: -18.0,
        70: -5.0,
        120: 0.0,
        170: -10.0,
        220: -16.0,
        270: -12.0,
        320: -14.0,
    }
    peaks = find_major_peaks_hz(freqs_hz, _spiky_db(400, heights))
    # 7 candidates; the two weakest (-18 at bin 20, -16 at bin 220) are dropped.
    assert peaks == [7_000.0, 12_000.0, 17_000.0, 27_000.0, 32_000.0]


def test_a_prominent_hump_below_the_level_floor_is_not_a_peak() -> None:
    freqs_hz = np.arange(200) * 100.0
    # -30 dB is 30 dB prominent over its surroundings but well under the -20 dB floor.
    peaks = find_major_peaks_hz(freqs_hz, _spiky_db(200, {50: 0.0, 120: -30.0}))
    assert peaks == [5_000.0]


def test_a_bump_that_barely_stands_out_is_not_a_peak() -> None:
    freqs_hz = np.arange(200) * 100.0
    db = _spiky_db(200, {50: 0.0})
    db[120] = -56.0  # 4 dB above the -60 dB floor around it
    assert find_major_peaks_hz(freqs_hz, db) == [5_000.0]


def test_the_global_peak_is_included_even_when_it_is_not_prominent_itself() -> None:
    freqs_hz = np.arange(400) * 100.0
    # The global maximum (0 dB) sits on a -5 dB shoulder running to the left edge, so its own
    # prominence is only 5 dB (under the 8 dB criterion) -- it must still be labelled.
    db = _spiky_db(400, {120: -10.0, 170: -12.0, 220: -14.0, 270: -11.0, 320: -13.0})
    db[:60] = -5.0
    db[58:63] = [-5.0, -3.0, 0.0, -3.0, -5.0]
    peaks = find_major_peaks_hz(freqs_hz, db)
    assert 6_000.0 in peaks
    assert len(peaks) == 5


def test_a_peak_below_the_cutoff_is_not_reported() -> None:
    freqs_hz = np.arange(200) * 100.0
    db = _spiky_db(
        200, {10: 0.0, 120: -6.0}
    )  # 1.0 kHz peak, loudest, under a 5 kHz cutoff
    assert find_major_peaks_hz(freqs_hz, db, peak_min_freq_hz=5_000.0) == [12_000.0]


def test_a_spectrum_still_falling_at_the_cutoff_has_no_peak_there() -> None:
    freqs_hz = np.arange(200) * 100.0
    db = (
        -np.arange(200) * 0.3
    )  # loudest below the cutoff, monotonically falling above it
    assert find_major_peaks_hz(freqs_hz, db, peak_min_freq_hz=5_000.0) == []


def test_compute_reports_both_tones_of_a_two_tone_signal_in_ascending_order() -> None:
    samplerate_hz = 256_000.0
    samples = _sine(40_000.0, samplerate_hz, 0.05) + 0.5 * _sine(
        80_000.0, samplerate_hz, 0.05
    )
    result = compute_sound_level_spectrum(
        samples, samplerate_hz, peak_min_freq_hz=5_000.0
    )
    assert result.peak_freq_hz is not None
    assert len(result.peaks_freq_hz) == 2
    assert result.peaks_freq_hz == sorted(result.peaks_freq_hz)
    assert result.peaks_freq_hz[0] == pytest.approx(40_000.0, abs=500.0)
    assert result.peaks_freq_hz[1] == pytest.approx(80_000.0, abs=500.0)
    assert result.peak_freq_hz in result.peaks_freq_hz


def test_compute_reports_no_peaks_when_the_global_peak_is_the_search_boundary() -> None:
    samplerate_hz = 256_000.0
    result = compute_sound_level_spectrum(
        _sine(4_900.0, samplerate_hz, 0.02), samplerate_hz, peak_min_freq_hz=5_000.0
    )
    assert result.peak_freq_hz is None
    assert result.peaks_freq_hz == []

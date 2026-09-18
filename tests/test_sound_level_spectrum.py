from __future__ import annotations

import warnings

import numpy as np

from fledermap.media.sound_level_spectrum import compute_sound_level_spectrum


def _sine(freq_hz: float, samplerate_hz: float, duration_s: float) -> np.ndarray:
    n = int(samplerate_hz * duration_s)
    t = np.arange(n) / samplerate_hz
    return np.sin(2 * np.pi * freq_hz * t)


def test_peak_lands_near_the_tone_s_frequency() -> None:
    samplerate_hz = 256_000.0
    result = compute_sound_level_spectrum(
        _sine(40_000.0, samplerate_hz, 0.02), samplerate_hz
    )
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
    assert np.isfinite(result.peak_freq_hz), "peak_freq_hz is inf or nan"

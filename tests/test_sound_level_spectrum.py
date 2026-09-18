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

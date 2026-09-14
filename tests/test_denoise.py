from __future__ import annotations

import numpy as np

from fledermap.media.denoise import (
    DEFAULT_CUTOFF_HZ,
    highpass_filter,
    subtract_background_noise,
)


def test_default_cutoff_is_five_khz() -> None:
    assert DEFAULT_CUTOFF_HZ == 5000.0


def test_highpass_filter_removes_energy_below_cutoff() -> None:
    samplerate = 256_000
    duration_s = 0.05
    t = np.arange(int(samplerate * duration_s)) / samplerate
    # A 2kHz tone (below cutoff) plus a 45kHz tone (above cutoff, in the bat-call range).
    low = np.sin(2 * np.pi * 2_000 * t)
    high = np.sin(2 * np.pi * 45_000 * t)
    samples = (low + high) * 10000

    filtered = highpass_filter(samples, samplerate, cutoff_hz=5000.0)

    fft = np.abs(np.fft.rfft(filtered))
    freqs = np.fft.rfftfreq(len(filtered), 1 / samplerate)
    low_band_energy = fft[(freqs > 1000) & (freqs < 3000)].max()
    high_band_energy = fft[(freqs > 40000) & (freqs < 50000)].max()
    # The low tone must be strongly attenuated relative to the high tone, which must survive.
    assert low_band_energy < high_band_energy * 0.1


def test_highpass_filter_is_zero_phase_no_time_shift() -> None:
    """sosfiltfilt (not sosfilt) must be used -- a causal filter would shift
    a sharp transient in time, which would desync the filtered audio from
    the unfiltered spectrogram/oscillogram's own time axis."""
    samplerate = 256_000
    samples = np.zeros(1000)
    samples[500] = 1.0  # a single impulse well above the cutoff's own ringing

    filtered = highpass_filter(samples, samplerate, cutoff_hz=5000.0)

    # The impulse's peak must still be at (or immediately adjacent to) index 500 --
    # a causal (non-zero-phase) filter would shift it later in time.
    assert abs(int(np.argmax(np.abs(filtered))) - 500) <= 2


def test_subtract_background_noise_removes_a_flat_noise_floor() -> None:
    # 3 frequency bins x 10 time columns. Bin 0: constant noise floor of 2.0.
    # Bin 1: a real "call" -- mostly silent, one loud spike of 100.0.
    # Bin 2: all zero (silence).
    sxx = np.zeros((3, 10))
    sxx[0, :] = 2.0
    sxx[1, :] = 0.1
    sxx[1, 5] = 100.0
    sxx[2, :] = 0.0

    result = subtract_background_noise(sxx, percentile=10.0, factor=1.0)

    # The flat noise floor drops to (near) zero everywhere.
    assert result[0].max() < 0.5
    # The call's loud spike survives, clearly distinguishable from its own row's floor.
    assert result[1, 5] > 90.0
    # Never negative (clamped at zero).
    assert (result >= 0).all()


def test_subtract_background_noise_preserves_shape() -> None:
    sxx = np.random.default_rng(0).random((5, 20))
    result = subtract_background_noise(sxx)
    assert result.shape == sxx.shape


def test_subtract_background_noise_default_over_subtracts_more_than_the_floor() -> None:
    """The default percentile (50, the row's median) + factor (2.0) must remove
    MORE than just each row's own noise floor -- confirmed necessary 2026-09-14
    against a real field recording, where subtracting exactly the (10th-
    percentile) floor left the noise floor's own variance, which is what
    actually reads as visual "noise", almost entirely untouched."""
    # A row whose values cluster around 1.0 with real variance (not a flat floor) --
    # closer to what an actual noise floor's spread looks like than a constant.
    rng = np.random.default_rng(1)
    sxx = np.abs(rng.normal(loc=1.0, scale=0.3, size=(1, 200)))

    over_subtracted = subtract_background_noise(sxx)
    floor_only = subtract_background_noise(sxx, percentile=50.0, factor=1.0)

    # Over-subtracting (factor=2.0) must zero out substantially more of the row
    # than subtracting exactly the median once (factor=1.0).
    assert (over_subtracted == 0).sum() > (floor_only == 0).sum()

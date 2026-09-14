from __future__ import annotations

import numpy as np

from fledermap.media.denoise import DEFAULT_CUTOFF_HZ, highpass_filter


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

from __future__ import annotations

import numpy as np

from fledermap.media.denoise import (
    DEFAULT_CUTOFF_HZ,
    highpass_filter,
    spectral_gate,
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
    actually reads as visual "noise", almost entirely untouched. `median_size=1`
    disables the selective-median step (a 1x1 "neighborhood" is just the pixel
    itself) so this test isolates the over-subtraction behavior alone."""
    # A row whose values cluster around 1.0 with real variance (not a flat floor) --
    # closer to what an actual noise floor's spread looks like than a constant.
    rng = np.random.default_rng(1)
    sxx = np.abs(rng.normal(loc=1.0, scale=0.3, size=(1, 200)))

    over_subtracted = subtract_background_noise(sxx, median_size=(1, 1))
    floor_only = subtract_background_noise(
        sxx, percentile=50.0, factor=1.0, median_size=(1, 1)
    )

    # Over-subtracting (factor=2.0) must zero out substantially more of the row
    # than subtracting exactly the median once (factor=1.0).
    assert (over_subtracted == 0).sum() > (floor_only == 0).sum()


def _pulsed_tone_with_noise(
    rng: np.random.Generator,
    *,
    samplerate: int = 256_000,
    duration_s: float = 0.05,
    tone_freq_hz: float = 45_000.0,
    tone_amplitude: float = 20_000.0,
    noise_std: float = 3_000.0,
) -> tuple[np.ndarray, int, int]:
    """A short, loud tone pulse in the middle third of an otherwise-silent
    signal, plus white noise everywhere -- the same "mostly quiet, one real
    call" shape `subtract_background_noise`'s own tests already use, not a
    sustained tone (see `spectral_gate`'s docstring for why a continuous tone
    is the wrong shape to test this technique against). Returns
    (samples, pulse_start_idx, pulse_end_idx)."""
    n = int(samplerate * duration_s)
    t = np.arange(n) / samplerate
    pulse_start, pulse_end = int(n * 0.4), int(n * 0.6)
    clean = np.zeros(n)
    clean[pulse_start:pulse_end] = tone_amplitude * np.sin(
        2 * np.pi * tone_freq_hz * t[pulse_start:pulse_end]
    )
    noise = rng.normal(0, noise_std, n)
    return clean + noise, pulse_start, pulse_end


def test_spectral_gate_reduces_noise_outside_the_call() -> None:
    rng = np.random.default_rng(1)
    samples, pulse_start, _pulse_end = _pulsed_tone_with_noise(rng)

    gated = spectral_gate(samples, samplerate_hz=256_000)

    rms_before = np.sqrt(np.mean(samples[:pulse_start] ** 2))
    rms_after = np.sqrt(np.mean(gated[:pulse_start] ** 2))
    # Noise-only region must be meaningfully quieter after gating.
    assert rms_after < rms_before * 0.6


def test_spectral_gate_preserves_the_call_pulse() -> None:
    rng = np.random.default_rng(1)
    samples, pulse_start, pulse_end = _pulsed_tone_with_noise(rng)

    gated = spectral_gate(samples, samplerate_hz=256_000)

    peak_before = np.max(np.abs(samples[pulse_start:pulse_end]))
    peak_after = np.max(np.abs(gated[pulse_start:pulse_end]))
    # The real call's peak amplitude must largely survive -- this is what
    # distinguishes gating from just re-running the highpass filter.
    assert peak_after > peak_before * 0.6


def test_spectral_gate_output_length_matches_input() -> None:
    rng = np.random.default_rng(2)
    samples, _start, _end = _pulsed_tone_with_noise(rng)

    gated = spectral_gate(samples, samplerate_hz=256_000)

    assert len(gated) == len(samples)


def test_spectral_gate_handles_silence_without_crashing() -> None:
    silence = np.zeros(12_800)

    gated = spectral_gate(silence, samplerate_hz=256_000)

    assert len(gated) == len(silence)
    assert np.max(np.abs(gated)) == 0.0


def test_spectral_gate_handles_a_signal_shorter_than_one_window() -> None:
    # 3 samples, far fewer than any reasonable STFT window -- mirrors
    # render_full_spectrogram_image's nperseg-clamp test coverage for the
    # same "very short/truncated recording" shape.
    rng = np.random.default_rng(3)
    tiny = rng.normal(0, 100, 3)

    gated = spectral_gate(tiny, samplerate_hz=256_000)

    assert len(gated) == 3

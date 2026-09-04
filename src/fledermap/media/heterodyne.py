"""Heterodyne (HET) preview generation and the "peak frequency" helper that
gives HET mode a sensible starting tune value. Pure: reads a WAV file, writes
an Opus file / returns a float. No DB, no queue awareness, matching
`preview.py`'s module shape (design spec
2026-09-04-fledermap-het-playback-design.md section 1)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy import signal

from fledermap.media.opus_pipeline import encode_pcm_as_opus
from fledermap.media.wav_pcm import UnreadableWavError, read_pcm

# Bounds the peak-frequency search window -- NOT a real bandpass filter (a
# bigger, separate design question the already-planned `fledermap.noise`
# classifier/denoising backlog items exist for). Reuses the same low-end
# reasoning this codebase already documents (project CLAUDE.md's "Noise"
# backlog notes: real recordings' low end, below ~10kHz, often carries
# handling/wind noise that would otherwise dominate a naive argmax and mask
# the actual call) and the spectrogram's own 128kHz display ceiling
# (`SpectrogramParams.max_freq_hz`) as the high end, so "peak frequency" and
# "what the spectrogram actually shows" never silently disagree about range.
_PEAK_SEARCH_MIN_HZ = 10_000.0
_PEAK_SEARCH_MAX_HZ = 128_000.0


def compute_peak_frequency_hz(wav_path: Path) -> float:
    """STFT peak-hold over the whole file: for each frequency bin within the
    bounded search window above, take the MAXIMUM power reached at any
    single instant, then return the frequency whose peak-hold value is
    highest -- deliberately independent of `SpectrogramParams`/
    `render_full_spectrogram_image`'s own STFT (changing the spectrogram's
    display tuning, chosen for visualization, must never silently change
    what HET calls "the peak frequency", chosen for audibility). The result
    is directly visible to the user (pre-filled into the frequency spinner
    in HET mode), so a wrong pick is easy to catch by ear against real
    recordings.

    NOT a time-averaged PSD (`scipy.signal.welch`, the original approach) --
    Janna, 2026-09-04, found a real field recording where near-continuous
    background noise at 10-11kHz, present across ~70% of the file, beat a
    brief, loud ~45kHz Pipistrellus call under time-averaging simply by
    being present for so much longer, even though the call was far louder
    in any single instant. Peak-hold favours a short loud transient over a
    persistent quiet tone, matching how a real call actually stands out --
    at the cost of being more sensitive to a single loud spike or click
    (handling noise, a knock) being mistaken for a call. Accepted: the
    auto-computed value is always user-visible and correctable in the
    frequency spinner, the same tolerance the original Welch-based design
    already accepted for its own failure modes."""
    samples, samplerate = read_pcm(wav_path)
    # Clamped rather than left at the default 1024: scipy warns and silently
    # substitutes a shorter window itself for a file with fewer samples than
    # that (e.g. a very short recording) -- clamping up front keeps this
    # function's own test output warning-free without relying on scipy's
    # fallback behavior.
    nperseg = min(1024, len(samples))
    freqs, _times, stft = signal.stft(samples, fs=samplerate, nperseg=nperseg)
    power = np.abs(stft) ** 2
    in_window = (freqs >= _PEAK_SEARCH_MIN_HZ) & (freqs <= _PEAK_SEARCH_MAX_HZ)
    windowed_freqs = freqs[in_window]
    peak_hold = power[in_window].max(axis=1)
    if peak_hold.size == 0:
        raise UnreadableWavError(
            f"cannot compute peak frequency for {wav_path}: no STFT data in the "
            f"{_PEAK_SEARCH_MIN_HZ:.0f}-{_PEAK_SEARCH_MAX_HZ:.0f}Hz search window "
            f"(samplerate too low)"
        )
    return float(windowed_freqs[np.argmax(peak_hold)])


# Rejects the near-`2*tune_freq_hz` sum-frequency component the mix also
# produces, keeping only the audible difference-frequency component. 20kHz
# comfortably covers human hearing while staying well below any plausible
# sum-frequency artifact given the tune frequencies this feature targets
# (bat calls, tens of kHz and up).
_LOWPASS_CUTOFF_HZ = 20_000.0
_LOWPASS_ORDER = 8
_OUTPUT_SAMPLERATE_HZ = 48_000


def render_heterodyne_preview(
    wav_path: Path,
    out_path: Path,
    *,
    tune_freq_hz: float,
) -> None:
    """Mix `wav_path`'s audio down to audible range around `tune_freq_hz`
    (classic heterodyne technique) and render it to `out_path` as Opus."""
    samples, samplerate = read_pcm(wav_path)
    if samplerate <= 2 * _LOWPASS_CUTOFF_HZ:
        raise UnreadableWavError(
            f"cannot render heterodyne preview for {wav_path}: samplerate "
            f"{samplerate}Hz is too low for the {_LOWPASS_CUTOFF_HZ:.0f}Hz lowpass filter"
        )
    t = np.arange(len(samples)) / samplerate
    local_oscillator = np.cos(2 * np.pi * tune_freq_hz * t)
    mixed = samples * local_oscillator

    sos = signal.butter(
        _LOWPASS_ORDER,
        _LOWPASS_CUTOFF_HZ,
        btype="low",
        fs=samplerate,
        output="sos",
    )
    filtered = signal.sosfiltfilt(sos, mixed)

    resampled = signal.resample_poly(filtered, _OUTPUT_SAMPLERATE_HZ, samplerate)
    # Normalise to int16 range headroom-safe -- the mix + filter can produce
    # values outside the original PCM's amplitude range.
    peak = np.max(np.abs(resampled))
    if peak > 0:
        resampled = resampled / peak * 32000
    pcm_int16 = resampled.astype(np.int16)

    encode_pcm_as_opus(
        frames=pcm_int16.tobytes(),
        nchannels=1,
        sampwidth=2,
        framerate=_OUTPUT_SAMPLERATE_HZ,
        out_path=out_path,
    )

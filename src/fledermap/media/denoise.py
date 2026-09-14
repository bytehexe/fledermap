"""Highpass filtering and spectrogram-only background-noise subtraction for
the "Denoise" toggle (design spec
docs/superpowers/specs/2026-09-14-fledermap-denoise-highpass-design.md). Pure:
no DB, no queue awareness, matching every other `media/` module.

`DEFAULT_CUTOFF_HZ` is the one shared constant every caller (SpectrogramParams,
OscillogramParams, the TE/HET routes) defaults to -- a single definition so the
cutoff can't drift between callers."""

from __future__ import annotations

import numpy as np
from scipy import ndimage, signal

DEFAULT_CUTOFF_HZ = 5000.0

# A conventional order balancing rolloff steepness against filter-design edge
# cases at a low cutoff-to-Nyquist ratio (this project's recordings run at
# 256kHz+ sample rates, so a 5kHz cutoff is a small fraction of Nyquist).
_HIGHPASS_ORDER = 4


def highpass_filter(
    samples: np.ndarray,
    samplerate_hz: float,
    cutoff_hz: float,
) -> np.ndarray:
    """Zero-phase Butterworth highpass (`sosfiltfilt`, not `sosfilt` -- a
    causal filter would time-shift a sharp transient, desyncing the filtered
    audio from the unfiltered spectrogram/oscillogram's own time axis).
    Mirrors `heterodyne.py`'s existing `butter` + `sosfiltfilt` lowpass
    idiom, the same established pattern in this codebase."""
    sos = signal.butter(
        _HIGHPASS_ORDER,
        cutoff_hz,
        btype="high",
        fs=samplerate_hz,
        output="sos",
    )
    result: np.ndarray = signal.sosfiltfilt(sos, samples)
    return result


def subtract_background_noise(
    sxx: np.ndarray,
    percentile: float = 50.0,
    factor: float = 2.0,
    median_size: tuple[int, int] = (7, 7),
    preserve_threshold: float = 2.0,
) -> np.ndarray:
    """Per frequency-bin (per row) background-noise subtraction (the classic
    "over-subtraction" spectral-subtraction idiom), followed by a selective
    (edge-preserving) 2D median filter. Operates on the STFT's linear power
    matrix, before any dB conversion -- spectrogram-image-only (see the
    design spec's "Why highpass and background-subtraction use different
    techniques": this is not reconstructed back to audio, so no phase/COLA
    concern applies).

    Step 1, over-subtraction: estimate each bin's noise floor as its own
    `percentile`-th percentile power across all time columns, subtract
    `factor` times that floor from every column in that row, clamp at zero.
    `factor=1.0` (subtracting exactly the floor) only zeroes the bottom
    `percentile`% of pixels in each row -- the noise floor's own variance,
    which is what actually reads as visual "noise" and sits well above that
    single percentile, survives untouched. Confirmed visually 2026-09-14
    against a real field recording (percentile=10, factor=1.0 was barely
    distinguishable from no denoising at all). Over-subtracting pushes that
    whole variance band down towards zero too, at the cost of also eating
    into weak real signal close to the floor -- percentile=50 (the row's
    median) + factor=2.0 visibly cleaned the background while leaving actual
    calls' shape and brightness intact in that same comparison.

    Step 2, selective median filter: over-subtraction alone still leaves
    substantial per-pixel speckle (a single STFT frame's power estimate has
    high variance regardless of the true noise floor -- subtracting a
    scalar shifts brightness down but doesn't reduce that per-pixel
    randomness). A plain 2D median filter removes the speckle but is
    edge-blind: it smooths a call's thin sweep the same as a noise pixel,
    visibly smudging call shape even at a small kernel size -- confirmed
    live 2026-09-14, 7x7 got the background very quiet but blobbed every
    call. The fix is to use the median-filtered estimate only where a pixel
    actually agrees with it (real background); anywhere a pixel stands out
    well above its own smoothed neighborhood -- which is exactly what a
    call pixel does -- the original, unsmoothed value passes through
    untouched. `median_size=(7, 7)` and `preserve_threshold=2.0` were
    chosen by rendering several kernel sizes and thresholds against a real
    field recording side by side; pushing the kernel up to 21x21 and the
    threshold up to 6.0 produced no further visible improvement, so this
    isn't a compromise short of a better result further out -- it's close
    to this technique's practical ceiling on real recordings."""
    noise_floor = np.percentile(sxx, percentile, axis=1, keepdims=True)
    subtracted = np.maximum(sxx - factor * noise_floor, 0.0)
    smoothed = ndimage.median_filter(subtracted, size=median_size)
    result: np.ndarray = np.where(
        subtracted > preserve_threshold * smoothed, subtracted, smoothed
    )
    return result
